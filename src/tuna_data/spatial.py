"""Reproducible spatial data layers for RFMO catch/effort and GFW activity."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tomllib
from uuid import uuid4
import zipfile

import numpy as np
import pandas as pd

from .cleaning import gfw_hours
from .gfw import validate_report
from .harmonize import SPECIES
from .library import Library
from .pipeline import read_processed
from .spatial_adapters import COLUMNS, PRODUCTS, adapt, _base, _emit, _period
from .spatial_geometry import grid_status, pacific_selection
from .storage import atomic_json, checksum, contained

RFMO_KEYS = ["source", "product", "year", "measure", "unit", "catch_basis", "species_code", "gear_group",
             "map_west", "map_south", "map_lon", "map_lat", "map_size", "in_pacific"]
GFW_KEYS = ["source", "product", "year", "region_raw", "unit", "gear_group",
            "map_west", "map_south", "map_lon", "map_lat", "map_size"]
EXTRA = ["grid_status", "map_west", "map_south", "map_lon", "map_lat", "map_size", "region_selection", "in_pacific"]


def _partial(t, keys):
    return t.groupby(keys, dropna=False, observed=True, sort=False).agg(
        observed_sum=("value", "sum"), n_records=("value", "size"), n_observed=("value", "count")).reset_index()


def _combine(parts, keys):
    if not parts:
        return pd.DataFrame(columns=keys + ["observed_value", "n_records", "n_observed", "n_missing", "value"])
    t = pd.concat(parts, ignore_index=True).groupby(keys, dropna=False, observed=True, sort=True)[
        ["observed_sum", "n_records", "n_observed"]].sum().reset_index()
    t["n_missing"] = t.n_records - t.n_observed
    t["observed_value"] = t.observed_sum.where(t.n_observed.gt(0))
    t["value"] = t.observed_value.where(t.n_missing.eq(0))
    return t.drop(columns="observed_sum")


def csv_chunks(path, member, chunksize=100000):
    """Keep the ZIP stream open for the whole chunk iterator; retain line indices."""
    with zipfile.ZipFile(path) as archive:
        with archive.open(member) as stream:
            yield from pd.read_csv(stream, dtype=str, keep_default_na=False, chunksize=chunksize)


def _parse_config(config):
    cfg = tomllib.loads(Path(config).expanduser().read_text(encoding="utf-8")) if config else {}
    if set(cfg) - {"spatial", "versions", "gfw_reports"}:
        raise ValueError("Unknown spatial recipe section")
    options = dict(start_year=2024, end_year=2024, species=list(SPECIES),
                   gfw_rfmos=["IATTC", "WCPFC", "ICCAT", "IOTC", "CCSBT"], gfw_grid_degrees=1)
    new = cfg.get("spatial", {})
    if set(new) - set(options):
        raise ValueError("Unknown spatial recipe option")
    options.update(new)
    if any(type(options[k]) is not int or not 1800 <= options[k] <= 2200 for k in ["start_year", "end_year"]):
        raise ValueError("Invalid spatial years")
    if options["start_year"] > options["end_year"]:
        raise ValueError("Invalid spatial period")
    if not isinstance(options["species"], list) or not options["species"] or set(options["species"]) - set(SPECIES):
        raise ValueError("Unsupported spatial species")
    rfmos = options["gfw_rfmos"]
    if not isinstance(rfmos, list) or set(rfmos) - {"IATTC", "WCPFC", "ICCAT", "IOTC", "CCSBT"}:
        raise ValueError("Invalid GFW RFMO list")
    if len(rfmos) != len(set(rfmos)) or len(options["species"]) != len(set(options["species"])):
        raise ValueError("Duplicate spatial selection")
    if options["gfw_grid_degrees"] not in [.5, 1, 2, 5]:
        raise ValueError("GFW display grid must be 0.5, 1, 2 or 5 degrees")
    versions = cfg.get("versions", {})
    if any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in versions.values()):
        raise ValueError("Spatial versions need full SHA-256 strings")
    return options, versions, cfg.get("gfw_reports", {})


def _gfw_id(lib, rfmo, year, fixed):
    key = f"{rfmo}:{year}"
    if key in fixed:
        return fixed[key]
    prefix = f"gfw/{rfmo.lower()}_{year}-01-01_{year+1}-01-01_"
    catalogue = lib.catalog()
    matches = catalogue[catalogue.id.str.startswith(prefix)].sort_values("retrieved_at", ascending=False)
    for dataset in matches.id:
        p = lib.record(dataset).get("provenance", {}).get("request", {})
        if (p.get("group-by") == "FLAGANDGEARTYPE" and p.get("temporal-resolution") == "MONTHLY"
                and p.get("spatial-resolution") == "LOW" and p.get("spatial-aggregation") == "false"
                and not any(k.startswith("filters") for k in p)):
            return dataset
    raise ValueError(f"No complete monthly 0.1-degree GFW report for {rfmo} {year}; download it first")


def _catdis_id(lib, versions):
    """Use the newest downloaded CATDIS annual release, or the locked release."""
    pattern = r"iccat/cdis([0-9]{2})([0-9]{2})_all"
    pinned = [k for k in versions if re.fullmatch(pattern, k)]
    if len(pinned) > 1:
        raise ValueError("Multiple CATDIS releases pinned; choose one all-species view")
    if pinned:
        return pinned[0]
    candidates = [k for k in lib.catalog().id if re.fullmatch(pattern, k)]
    if not candidates:
        raise ValueError("No ICCAT CATDIS all-species release in the library")
    # Release ids encode 1950-YY; 50..99 are historical 1900s, 00..49 are 2000s.
    def end_year(k):
        yy = int(re.fullmatch(pattern, k)[2])
        return 1900 + yy if yy >= 50 else 2000 + yy
    return max(candidates, key=end_year)


def _adapt_gfw(raw, dataset, sha, member, rfmo, year, size):
    r = gfw_hours(raw)
    if r[["lon", "lat", "fishing_hours"]].isna().any().any() or not np.isfinite(r[["lon", "lat", "fishing_hours"]].astype(float)).all().all():
        raise ValueError("Missing or non-finite GFW coordinates/hours")
    for col in ["flag", "geartype", "date"]:
        if col not in r:
            raise ValueError("GFW report schema changed")
    if not r.date.str.fullmatch(f"{year}-[0-9]{{2}}").all():
        raise ValueError("GFW report contains dates outside requested year")
    r["year"] = r.date.str[:4]
    r["month"] = r.date.str[5:7]
    b = _base(r, "gfw_" + rfmo.lower(), dataset, sha, member)
    _period(b, r, "year", "month")
    b["lon"] = (r.lon + 180) % 360 - 180
    b["lat"] = r.lat
    b["grid_width"], b["grid_height"] = .1, .1
    b["grid_raw"] = raw["Lat"].astype(str) + "," + raw["Lon"].astype(str)
    b["geometry_status"] = "reported_coordinate"
    b["gear_raw"], b["gear_group"] = r.geartype, r.geartype
    b["flag_raw"] = r.flag
    b["region_raw"] = rfmo
    b["catch_basis"] = "gfw_AIS_apparent_fishing_hours"
    t = _emit(b, r, "fishing_hours", unit="hours", measure="apparent_fishing_hours")
    t["value_raw"] = raw["Apparent Fishing Hours"].astype("string")
    # Reported coordinates are used as bin anchors. Do not infer the native
    # cell's exact clipped footprint or use these bins to deduplicate RFMOs.
    t["map_west"] = np.floor(t.lon / size + 1e-9) * size
    t["map_south"] = np.floor(t.lat / size + 1e-9) * size
    t["map_lon"], t["map_lat"] = t.map_west + size / 2, t.map_south + size / 2
    t["map_size"] = float(size)
    t["grid_status"] = "reported_coordinate_binned"
    t["region_selection"] = "separate_RFMO_partition_no_union"
    t["in_pacific"] = rfmo in {"IATTC", "WCPFC"}
    return t


def build_spatial(config=None, *, library=None, output=None, progress=print):
    """Write native records plus annual map cells. Existing raw files are immutable.

    RFMO map cells keep source, product, unit and statistical basis separate.
    GFW partitions overlap and MUST NOT be summed across region_raw.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq
    lib = library or Library()
    options, versions, fixed_reports = _parse_config(config)
    first, last = options["start_year"], options["end_year"]
    parent = Path(output).expanduser().absolute() if output else lib.root / "processed" / "spatial"
    run_id = "Spatial_" + f"{first}-{last}_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    run = parent / run_id
    run.mkdir(parents=True, exist_ok=False)
    integer = {"source_row", "year", "month_start", "month_end"}
    numeric = {"lon", "lat", "west", "south", "grid_width", "grid_height", "value",
               "map_west", "map_south", "map_lon", "map_lat", "map_size"}
    schema = pa.schema([(c, pa.bool_() if c == "in_pacific" else pa.int64() if c in integer
                         else pa.float64() if c in numeric else pa.string()) for c in COLUMNS + EXTRA])
    sources, outputs, audit_parts, map_parts, reports, gfw_parts = {}, {}, [], [], {}, []

    def snapshot(dataset):
        record = lib.record(dataset, versions.get(dataset))
        path = lib.path(dataset, version=record["sha256"])
        sources[dataset] = {k: record.get(k) for k in ["sha256", "filename", "source_url", "retrieved_at"]}
        sources[dataset]["bytes"] = path.stat().st_size
        return record, path

    def save(name, frame, csv=False):
        filename = name + (".csv" if csv else ".parquet")
        if csv:
            frame.to_csv(run / filename, index=False, na_rep="")
        else:
            frame.to_parquet(run / filename, index=False)
        outputs[name] = dict(path=filename, rows=len(frame), sha256=checksum(run / filename))

    native_path = run / "rfmo_native.parquet"
    native_rows = 0
    input_counts = []
    with pq.ParquetWriter(native_path, schema, compression="zstd") as writer:
        for product, (dataset, selector) in PRODUCTS.items():
            if product == "iccat_catdis":
                dataset = _catdis_id(lib, versions)
            if product == "ccsbt_grid" and "SBF" not in options["species"]:
                continue
            if progress:
                progress(f"Spatial: {product}")
            record, path = snapshot(dataset)
            if product == "ccsbt_grid":
                member = record["filename"] + "#" + selector
                chunks = [lib.read_table(dataset, version=record["sha256"], sheet_name=selector,
                                         header=6, dtype=str, keep_default_na=False)]
                year_col = "Calendar\nYear"
            else:
                with zipfile.ZipFile(path) as z:
                    members = [m for m in z.namelist() if not Path(m).name.startswith(".") and not m.endswith("/")]
                chosen = [m for m in members if selector == Path(m).name or (selector in Path(m).name and m.endswith(".csv"))]
                if len(chosen) != 1:
                    raise ValueError(f"Ambiguous spatial member for {product}: {chosen}")
                member = chosen[0]
                chunks = csv_chunks(path, member)
                year_col = "Year" if product.startswith("iattc") else "YY" if product.startswith("wcpfc") else "YEAR" if product.startswith("iotc") else "YearC"
            read_rows, kept_rows, emitted_rows = 0, 0, 0
            for raw in chunks:
                read_rows += len(raw)
                year = pd.to_numeric(raw[year_col], errors="raise")
                raw = raw[year.between(first, last)]
                if product in {"iotc_ca", "iccat_catdis"}:
                    sp = "SPECIES_CODE" if product == "iotc_ca" else "SpeciesCode"
                    raw = raw[raw[sp].isin(options["species"])]
                kept_rows += len(raw)
                if raw.empty:
                    continue
                t = adapt(raw, product, dataset=dataset, sha=record["sha256"], member=member, species=options["species"])
                if t.empty:
                    continue
                t = grid_status(pacific_selection(t), size=5)
                writer.write_table(pa.Table.from_pandas(t[COLUMNS + EXTRA], schema=schema, preserve_index=False))
                emitted_rows += len(t)
                native_rows += len(t)
                keys = ["source", "product", "year", "measure", "unit", "species_code", "gear_group",
                        "value_status", "geometry_status", "grid_status", "region_selection"]
                audit_parts.append(_partial(t, keys))
                keep = t.region_selection.eq("selected") & t.grid_status.eq("eligible")
                map_parts.append(_partial(t.loc[keep], RFMO_KEYS))
            input_counts.append(dict(product=product, dataset_id=dataset, source_member=member,
                                     read_rows=read_rows, selected_source_rows=kept_rows, emitted_measurements=emitted_rows))
    outputs["rfmo_native"] = dict(path=native_path.name, rows=native_rows, sha256=checksum(native_path))
    cells = _combine(map_parts, RFMO_KEYS)
    catch = cells[cells.measure.eq("catch")]
    save("global_catch_cells", catch)
    save("pacific_catch_cells", catch[catch.in_pacific])
    save("rfmo_effort_cells", cells[cells.measure.eq("effort")])
    audit_keys = ["source", "product", "year", "measure", "unit", "species_code", "gear_group",
                  "value_status", "geometry_status", "grid_status", "region_selection"]
    save("rfmo_quality", _combine(audit_parts, audit_keys), csv=True)
    save("source_inventory", pd.DataFrame(input_counts), csv=True)

    for rfmo in options["gfw_rfmos"]:
        for year in range(first, last + 1):
            if progress:
                progress(f"Spatial: GFW {rfmo} {year}")
            dataset = _gfw_id(lib, rfmo, year, fixed_reports)
            record, path = snapshot(dataset)
            request = record.get("provenance", {}).get("request", {})
            expected = {"date-range": f"{year}-01-01,{year+1}-01-01", "region-id": rfmo,
                        "group-by": "FLAGANDGEARTYPE", "temporal-resolution": "MONTHLY",
                        "spatial-resolution": "LOW", "spatial-aggregation": "false"}
            if any(request.get(k) != v for k, v in expected.items()) or any(k.startswith("filters") for k in request):
                raise ValueError("GFW source does not match the requested unfiltered annual profile")
            validate_report(path, request)
            with zipfile.ZipFile(path) as z:
                csvs = [m for m in z.namelist() if m.endswith(".csv")]
                if len(csvs) != 1:
                    raise ValueError("Expected one GFW activity CSV")
                geo = z.read("layer-reference-geometry/geometry.geojson")
            reports[f"{rfmo}:{year}"] = dataset
            sources[dataset]["request"] = request
            geometry_file = f"gfw_{rfmo.lower()}_{year}_region.geojson"
            (run / geometry_file).write_bytes(geo)
            name = f"gfw_{rfmo.lower()}_{year}_native"
            filename = name + ".parquet"
            count, observed, months = 0, 0.0, set()
            with pq.ParquetWriter(run / filename, schema, compression="zstd") as writer:
                for raw in csv_chunks(path, csvs[0]):
                    t = _adapt_gfw(raw, dataset, record["sha256"], csvs[0], rfmo, year, options["gfw_grid_degrees"])
                    if t.value.isna().any() or t.lon.isna().any() or t.lat.isna().any():
                        raise ValueError("Missing GFW hours or coordinates")
                    writer.write_table(pa.Table.from_pandas(t[COLUMNS + EXTRA], schema=schema, preserve_index=False))
                    count += len(t)
                    observed += float(t.value.sum())
                    months.update(t.month_start)
                    gfw_parts.append(_partial(t, GFW_KEYS))
            outputs[name] = dict(path=filename, rows=count, sha256=checksum(run / filename))
            sources[dataset]["reported_hours"] = observed
            sources[dataset]["months_present"] = sorted(int(m) for m in months)
            sources[dataset]["region_geometry_file"] = geometry_file
    if gfw_parts:
        gfw_cells = _combine(gfw_parts, GFW_KEYS)
        for key, dataset in reports.items():
            rfmo, year = key.split(":")
            total = gfw_cells.loc[gfw_cells.region_raw.eq(rfmo) & gfw_cells.year.eq(int(year)), "observed_value"].sum()
            if not np.isclose(total, sources[dataset]["reported_hours"], rtol=1e-10, atol=1e-6):
                raise ValueError("GFW annual cell aggregation did not conserve hours")
        save("gfw_cells", gfw_cells)
    lines = ["# Source snapshots and spatial recipe; all paths resolved from the library.", "[spatial]"]
    lines += [f"{k} = {json.dumps(v)}" for k, v in options.items()]
    lines += ["", "[versions]"] + [f'{json.dumps(k)} = "{v["sha256"]}"' for k, v in sources.items()]
    lines += ["", "[gfw_reports]"] + [f"{json.dumps(k)} = {json.dumps(v)}" for k, v in reports.items()]
    (run / "recipe.lock.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    h = hashlib.sha256()
    for f in ["spatial.py", "spatial_adapters.py", "spatial_geometry.py", "harmonize.py"]:
        h.update(Path(__file__).with_name(f).read_bytes())
    metadata = dict(schema_version=1, status="complete", run_id=run_id, recipe=options, sources=sources,
                    outputs=outputs, adapter_sha256=h.hexdigest(), pandas_version=pd.__version__, pyarrow_version=pa.__version__,
                    notes=[
                        "Map cells retain source/product/unit/catch basis. This is not a uniform global total-catch estimator.",
                        "RFMO 5-degree cells contain disclosed observed subtotals; value is NA when any constituent is missing.",
                        "WCPFC blank-flag or all-zero strata are treated as suppressed/ambiguous, not observed zero catch.",
                        "IATTC longline uses the weight-preferred view; number-only records are retained as a separate residual basis.",
                        "ICCAT CATDIS estimates include landings and dead discards raised to nominal catches.",
                        "Cells coarser than 5 degrees or crossing a target cell are retained natively and audited, not split.",
                        "Pacific spatial authority split is 150W; this does not harmonize sampling or reporting coverage.",
                        "GFW reports overlap. Keep region_raw separate; no cross-RFMO union, vessel-count sums, or tuna-species inference.",
                    ])
    atomic_json(run / "provenance.json", metadata)
    atomic_json(parent / "latest.json", dict(run_id=run_id, schema_version=1))
    return dict(run_dir=str(run), tables=outputs, rfmo_native_measurements=native_rows)


def read_spatial(name="global_catch_cells", *, run=None, library=None):
    if run is None:
        parent = (library or Library()).root / "processed" / "spatial"
        latest = json.loads((parent / "latest.json").read_text(encoding="utf-8"))
        run = contained(parent, latest["run_id"])
    return read_processed(name, run=run)
