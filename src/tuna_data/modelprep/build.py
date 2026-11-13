"""Build and read immutable model-preparation runs (canonical catch-effort strata)."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path
import platform
import re
import time
import tomllib
from uuid import uuid4

import numpy as np
import pandas as pd

from ..library import Library
from ..spatial_store import YearWriter
from ..storage import atomic_json, checksum, contained
from . import adapters
from .schema import CATCH_COLUMNS, EFFORT_COLUMNS, STRATA_COLUMNS, finalize, validate

SCHEMA_VERSION = 1
_INT = {"year", "month_start", "month_end"}
_FLOAT = {"west", "south", "grid_width", "grid_height", "lon", "lat", "effort", "catch"}
NOTES = [
    "Public aggregated catch-effort data only; no logbook, observer or personal CPUE data.",
    "Effort units and catch units (fish, tonnes, kg) are separate rows and are never summed or converted.",
    "Products in one view_group are alternative views of the same catch: analyse one per source.",
    "zero_reported = explicit 0 from a reporting group that reports the species/unit somewhere in the series; zeros next to a positive in another unit are unit_incomplete.",
    "inferred_zero_candidate rows carry NA catch; use them only in labelled sensitivity analyses.",
    "Masked strata are confidential suppressions, never zeros.",
]


def _pa_schema(columns, extra=()):
    import pyarrow as pa
    fields = []
    for column in [*columns, *extra]:
        kind = pa.int64() if column in _INT else pa.float64() if column in _FLOAT else pa.string()
        fields.append(pa.field(column, kind))
    return pa.schema(fields)


def _parse_config(config):
    path = Path(config).expanduser().absolute() if config else None
    cfg = tomllib.loads(path.read_text(encoding="utf-8")) if path else {}
    if set(cfg) - {"modelprep", "versions"}:
        raise ValueError("Unknown modelprep recipe section")
    registry = adapters.products()
    options = dict(products=[name for name, (_, spec) in registry.items() if spec.default], years=None)
    new = cfg.get("modelprep", {})
    if set(new) - set(options):
        raise ValueError(f"Unknown modelprep option: {sorted(set(new) - set(options))}")
    options.update(new)
    products = options["products"]
    if not isinstance(products, list) or not products or len(products) != len(set(products)):
        raise ValueError("products must be a non-empty list without duplicates")
    unknown = set(products) - set(registry)
    if unknown:
        raise ValueError(f"Unknown products {sorted(unknown)}; available: {', '.join(registry)}")
    years = options["years"]
    if years is not None:
        if (not isinstance(years, list) or len(years) != 2 or any(type(y) is not int for y in years)
                or not 1800 <= years[0] <= years[1] <= 2200):
            raise ValueError("years must be [first, last] integers")
    versions = cfg.get("versions", {})
    if any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in versions.values()):
        raise ValueError("versions must map dataset IDs to full SHA-256 strings")
    return options, versions


BLANK_ZERO_SHARE = 0.5   # groups writing explicit zeros in >= this share of non-positive cells use blanks for "missing"


def confirm_zeros(strata, catch, *, zeros_explicit, catch_basis, effort=None):
    """Turn raw catch statuses into final ones using each reporting group's own reports.

    1. An explicit 0 in one unit while the same stratum and species is positive in another
       unit is a missing unit, not an absence: unit_incomplete (NA).
    2. A reporting group (fleet x gear, set type, or ICCAT data set) 'reports' a species and
       unit when it has a positive value for it anywhere in the product's series. Its explicit
       zeros are zero_reported; zeros of groups that never report it are zero_unconfirmed.
       Whole-series confirmation avoids dropping genuine zeros in years with no catch.
    3. Blank cells are decided once per group x species x unit over all years: groups that
       write explicit zeros in at least BLANK_ZERO_SHARE of their non-positive cells use blanks
       for missing values (not_reported); groups that rarely write zeros but report the
       species leave zeros blank (inferred_zero_candidate, NA catch).
    4. Positives-only sources: strata with reported effort and no catch row for a species/unit
       their group reports get an inferred_zero_candidate row (NA catch).
    """
    gkey = ["reporting_group", "species_code", "catch_unit"]
    c = catch.merge(strata[["stratum_id", "reporting_group"]], on="stratum_id", how="left", validate="many_to_one")
    raw = c.catch_status.astype("string")
    positive = raw.eq("positive")
    other_unit = c.loc[positive, ["stratum_id", "species_code"]].drop_duplicates().assign(_positive_species=True)
    c = c.merge(other_unit, on=["stratum_id", "species_code"], how="left")
    cross = raw.eq("zero") & c._positive_species.eq(True).fillna(False)
    reports = c.loc[positive, gkey].drop_duplicates().assign(_reports=True)
    nonpositive = c.loc[raw.isin(["zero", "blank"]) & ~cross, gkey].assign(_zero=raw[raw.isin(["zero", "blank"]) & ~cross].eq("zero"))
    share = nonpositive.groupby(gkey, observed=True)._zero.mean().rename("_zero_share").reset_index()
    c = c.merge(reports, on=gkey, how="left").merge(share, on=gkey, how="left")
    rep = c._reports.eq(True).fillna(False)
    writes_zeros = c._zero_share.ge(BLANK_ZERO_SHARE).fillna(False)
    status = raw.copy()
    status[cross] = "unit_incomplete"
    zero = raw.eq("zero") & ~cross
    status[zero & rep] = "zero_reported"
    status[zero & ~rep] = "zero_unconfirmed"
    blank = raw.eq("blank")
    status[blank & writes_zeros] = "not_reported"
    status[blank & ~writes_zeros & rep] = "inferred_zero_candidate"
    status[blank & ~writes_zeros & ~rep] = "not_reported"
    c["catch_status"] = status
    c["catch"] = c["catch"].astype("Float64").mask(cross)
    out = c[CATCH_COLUMNS]
    if not zeros_explicit:
        if effort is not None:
            with_effort = set(effort.loc[effort.effort_status.eq("reported"), "stratum_id"])
            eligible = strata.loc[strata.stratum_id.isin(with_effort), ["stratum_id", "reporting_group", "year"]]
        else:
            eligible = strata.loc[strata.stratum_status.isin(["reported", "effort_only"]), ["stratum_id", "reporting_group", "year"]]
        candidates = eligible.merge(reports.drop(columns="_reports"), on="reporting_group", how="inner")
        have = out[["stratum_id", "species_code", "catch_unit"]].assign(_have=True)
        candidates = candidates.merge(have, on=["stratum_id", "species_code", "catch_unit"], how="left")
        candidates = candidates[candidates._have.isna()]
        if len(candidates):
            n = len(candidates)
            add = pd.DataFrame({"stratum_id": candidates.stratum_id.to_numpy(), "year": candidates.year.to_numpy(),
                                "species_code": candidates.species_code.to_numpy(),
                                "catch_unit": candidates.catch_unit.to_numpy(),
                                "catch": pd.array([pd.NA] * n, dtype="Float64"),
                                "catch_status": ["inferred_zero_candidate"] * n,
                                "catch_field": [""] * n, "catch_basis": [catch_basis] * n})
            out = pd.concat([out.astype({"catch": "Float64"}), add.astype(out.dtypes.to_dict() | {"catch": "Float64"})],
                            ignore_index=True)
    out = out.copy()
    out["catch"] = pd.to_numeric(out["catch"]).astype("Float64")
    out["year"] = out["year"].astype("Int64")
    for column in ["stratum_id", "species_code", "catch_unit", "catch_status", "catch_field", "catch_basis"]:
        out[column] = out[column].astype("string").fillna("")
    return out


def _counts(product, table, frame, columns):
    rows = []
    for column in columns:
        for value, n in frame[column].value_counts(dropna=False).items():
            rows.append(dict(product=product, table=table, column=column, value=str(value), n=int(n)))
    return rows


def _code_hash():
    """Fingerprint of the code that shapes the tables (ignores macOS '._' sidecar files)."""
    h = hashlib.sha256()
    here = Path(__file__).parent
    pkg = here.parent
    paths = [*here.glob("*.py"), *(here / "adapters").glob("*.py"),
             *(pkg / n for n in ["spatial_geometry.py", "harmonize.py", "library.py", "storage.py", "spatial_store.py"])]
    for path in sorted(p for p in paths if not p.name.startswith(".")):
        h.update(path.relative_to(pkg).as_posix().encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def build_modelprep(config=None, *, library=None, output=None, progress=print):
    """Build canonical strata/effort/catch tables for the selected products.

    Reads pinned raw snapshots only (no downloads). Each run is immutable;
    latest.json advances only after every product succeeded.
    """
    import pyarrow as pa
    lib = library or Library()
    options, versions = _parse_config(config)
    registry = adapters.products()
    readable = {d for name in options["products"] for d in (registry[name][1].dataset_id, *registry[name][1].extra_datasets)}
    unknown = sorted(set(versions) - readable)
    if unknown:
        raise ValueError(f"[versions] pins datasets that no selected product reads: {unknown}")
    for dataset, sha in versions.items():
        lib.record(dataset, sha)   # fail before building when a pinned snapshot is absent
    years = range(options["years"][0], options["years"][1] + 1) if options["years"] else None
    parent = Path(output).expanduser().absolute() if output else lib.root / "processed" / "modelprep"
    run_id = "ModelPrep_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    stage = parent / f".{run_id}.building"
    stage.mkdir(parents=True, exist_ok=False)
    writers = {
        "strata": YearWriter(stage, "strata", _pa_schema(STRATA_COLUMNS)),
        "effort": YearWriter(stage, "effort", _pa_schema(EFFORT_COLUMNS, ["product"])),
        "catch": YearWriter(stage, "catch", _pa_schema(CATCH_COLUMNS, ["product"])),
    }
    products, counts, qc, sources = [], [], [], {}
    try:
        for name in options["products"]:
            source, spec = registry[name]
            if progress:
                progress(f"Model prep: {name} ({spec.dataset_id})")
            started = time.monotonic()
            adapt = adapters.module(source).adapt
            kwargs = dict(version=versions.get(spec.dataset_id), years=years, progress=progress)
            if "versions" in inspect.signature(adapt).parameters:
                kwargs["versions"] = {d: versions[d] for d in spec.extra_datasets if d in versions}
            result = adapt(lib, name, **kwargs)
            strata, effort, catch = finalize(result.strata, result.effort, result.catch)
            if (strata["product"] != name).any():
                raise ValueError(f"Adapter {source} returned rows for another product than {name}")
            catch = confirm_zeros(strata, catch, zeros_explicit=spec.zeros_explicit, catch_basis=spec.catch_basis,
                                  effort=effort)
            validate(strata, effort, catch, final=True)
            record = result.record
            versions.setdefault(spec.dataset_id, record["sha256"])
            if versions[spec.dataset_id] != record["sha256"]:
                raise ValueError(f"{name} read {record['sha256']} but {spec.dataset_id} is pinned to {versions[spec.dataset_id]}")
            sources[spec.dataset_id] = {k: record.get(k) for k in ["sha256", "filename", "source_url", "retrieved_at"]}
            for extra in result.extra_records:
                dataset = extra["dataset_id"]
                if versions.setdefault(dataset, extra["sha256"]) != extra["sha256"]:
                    raise ValueError(f"{name} read {dataset} {extra['sha256']} but it is pinned to {versions[dataset]}")
                sources[dataset] = {k: extra.get(k) for k in ["sha256", "filename", "source_url", "retrieved_at"]}
            writers["strata"].write(strata[STRATA_COLUMNS])
            writers["effort"].write(effort[EFFORT_COLUMNS].assign(product=name))
            writers["catch"].write(catch[CATCH_COLUMNS].assign(product=name))
            counts += _counts(name, "strata", strata, ["stratum_status", "geometry_status", "time_resolution", "gear_group"])
            counts += _counts(name, "effort", effort.assign(unit_status=effort.effort_unit + ":" + effort.effort_status), ["unit_status"])
            counts += _counts(name, "catch", catch.assign(species_unit_status=catch.species_code + ":" + catch.catch_unit + ":" + catch.catch_status), ["species_unit_status"])
            qc += [dict(product=name, **q) for q in result.qc]
            products.append(dict(product=name, source=source, dataset_id=spec.dataset_id, member=spec.member,
                                 view_group=spec.view_group, zeros_explicit=spec.zeros_explicit,
                                 catch_basis=spec.catch_basis, description=spec.description, sha256=record["sha256"],
                                 strata_rows=len(strata), effort_rows=len(effort), catch_rows=len(catch),
                                 year_min=int(strata.year.min()) if len(strata) else None,
                                 year_max=int(strata.year.max()) if len(strata) else None,
                                 seconds=round(time.monotonic() - started, 1)))
            del strata, effort, catch, result
        outputs = {name: writer.entry() for name, writer in writers.items()}
        for name, frame in [("products", pd.DataFrame(products).drop(columns="seconds")), ("status_counts", pd.DataFrame(counts)),
                            ("adapter_qc", pd.DataFrame(qc, columns=["product", "check", "count", "note"]))]:
            frame.to_csv(stage / f"{name}.csv", index=False)
            outputs[name] = dict(path=f"{name}.csv", rows=len(frame), sha256=checksum(stage / f"{name}.csv"))
        lock = ["# Rebuild from the same source snapshots.", "[modelprep]",
                f"products = {json.dumps(options['products'])}"]
        if options["years"]:
            lock.append(f"years = {json.dumps(options['years'])}")
        lock += ["", "[versions]"] + [f'{json.dumps(k)} = "{v["sha256"]}"' for k, v in sources.items()]
        (stage / "recipe.lock.toml").write_text("\n".join(lock) + "\n", encoding="utf-8")
        metadata = dict(schema_version=SCHEMA_VERSION, status="complete", run_id=run_id, recipe=options,
                        sources=sources, products=products, outputs=outputs, adapter_sha256=_code_hash(),
                        python_version=platform.python_version(), pandas_version=pd.__version__,
                        pyarrow_version=pa.__version__, notes=NOTES)
        atomic_json(stage / "provenance.json", metadata)
    except BaseException:
        for writer in writers.values():
            writer.close()
        raise   # the stage folder stays for diagnosis; latest.json is not touched
    target = parent / run_id
    os.replace(stage, target)
    atomic_json(parent / "latest.json", dict(run_id=run_id, schema_version=SCHEMA_VERSION))
    return dict(run_dir=str(target), products=products)


def run_directory(run=None, library=None):
    if run is not None:
        return Path(run).expanduser().absolute()
    parent = (library or Library()).root / "processed" / "modelprep"
    pointer = parent / "latest.json"
    if not pointer.is_file():
        raise FileNotFoundError("No model-prep run yet: run build_modelprep() / tuna_modelprep_build() first")
    return contained(parent, json.loads(pointer.read_text(encoding="utf-8"))["run_id"])


def run_metadata(run=None, library=None):
    root = run_directory(run, library)
    meta = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    if meta.get("status") != "complete" or meta.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Incomplete or unsupported model-prep run")
    return root, meta


def _listify(value):
    if value is None:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    return [value] if isinstance(value, (str, int, float)) else list(value)


def read_modelprep(table="strata", *, run=None, library=None, product=None, source=None, year=None,
                   species=None, columns=None, verify=True, **equals):
    """Read strata, effort or catch with Arrow predicates and checksum checks.

    Filters: product, source (strata only), year, species (catch only) and any
    other column=value(s) keyword (e.g. effort_unit="hooks").
    """
    import pyarrow.dataset as ds
    import pyarrow.parquet as pq
    root, meta = run_metadata(run, library)
    if table in {"products", "status_counts", "adapter_qc"}:
        entry = meta["outputs"][table]
        path = contained(root, entry["path"])
        if verify and checksum(path) != entry["sha256"]:
            raise ValueError(f"Output checksum failed: {table}")
        return pd.read_csv(path, keep_default_na=False, na_values=[""])
    if table not in {"strata", "effort", "catch"}:
        raise ValueError("table must be strata, effort, catch, products, status_counts or adapter_qc")
    entry = meta["outputs"][table]
    years = _listify(year)
    if years is not None:
        years = [int(y) for y in years]
    files = [f for f in entry["files"] if years is None or f.get("year") in years]
    filters = dict(equals)
    for name, value in [("product", product), ("source", source), ("species_code", species)]:
        if value is not None:
            filters[name] = value
    # Verify bytes before parsing anything (a damaged footer must fail as a checksum error).
    paths = []
    for f in (files or entry["files"][:1]):
        path = contained(root, f["path"])
        if verify and checksum(path) != f["sha256"]:
            raise ValueError(f"Output checksum failed: {f['path']}; re-sync the library copy or rebuild this run")
        paths.append(str(path))
    schema = pq.read_schema(paths[0])
    predicate = None
    for name, value in filters.items():
        values = _listify(value)
        if values is None:
            continue
        if name not in schema.names:
            raise ValueError(f"{table} has no column {name}")
        if name == "species_code":
            values = [str(v).upper() for v in values]
        rule = ds.field(name).isin(values)
        predicate = rule if predicate is None else predicate & rule
    requested = _listify(columns)
    if not files:
        empty = schema.empty_table()
        frame = (empty if requested is None else empty.select(requested)).to_pandas()
    else:
        frame = ds.dataset(paths, format="parquet", schema=schema).to_table(columns=requested, filter=predicate).to_pandas()
    for column in frame.columns:
        if column in _INT:
            frame[column] = frame[column].astype("Int64")
        elif column in _FLOAT:
            frame[column] = frame[column].astype("Float64")
        else:
            frame[column] = frame[column].astype("string")
    return frame
