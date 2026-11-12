"""Versioned annual-catch recipes for global and Pacific research tables."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import tomllib
from uuid import uuid4
import platform

import pandas as pd

from .harmonize import CANONICAL, DATASETS, SPECIES, read_source
from .library import Library
from .storage import atomic_json, checksum, contained

SCHEMA_VERSION = 1


def components(species, scope):
    """Mutually exclusive geographic views per species, not all RFMO areas."""
    if scope not in {"global", "pacific"}:
        raise ValueError(f"Unsupported scope: {scope}")
    if species not in SPECIES:
        raise ValueError(f"No reviewed merge rule for species: {species}")
    result = []
    if species in {"YFT", "BET", "SKJ"}:
        result = [("iattc", "EPO"), ("wcpfc", "WCPO")]
    elif species in {"ALB", "PBF"}:
        result = [("wcpfc", "NorthPacific"), ("wcpfc", "SouthPacific")]
    if scope == "global":
        if species in {"ALB", "BET", "SKJ", "YFT"}:
            result += [("iotc", "Indian"), ("iccat", "Atlantic")]
        elif species == "BFT":
            result = [("iccat", "Atlantic")]
        elif species == "SBF":
            result = [("ccsbt", "Global_SBF")]
    return result


def select_catch(table, *, species, scope, start_year=1950, end_year=None):
    """Select authority views without inventing ocean/gear/flag allocations."""
    end_year = int(table.year.max()) if end_year is None else end_year
    selected = pd.Series(False, index=table.index)
    for code in species:
        for source, region in components(code, scope):
            selected |= table.species_code.eq(code) & table.source.eq(source) & table.region.eq(region)
    selected &= table.year.between(start_year, end_year)
    result = table.loc[selected, CANONICAL].copy()
    if result.observation_id.duplicated().any():
        raise ValueError("An observation was selected twice")
    return result.reset_index(drop=True)


def aggregate_catch(table, by=None):
    """Sum within catch basis; missing cells never become zeros or complete totals.

    observed_sum_tonnes is a subtotal, not an estimate of missing catch. Source
    coverage is assessed separately; even a non-missing sum need not be global.
    """
    by = list(by or ["year", "species_code", "gear_group", "catch_basis"])
    if not {"year", "species_code", "catch_basis"}.issubset(by):
        raise ValueError("Aggregation must retain year, species_code and catch_basis")
    if len(by) != len(set(by)) or not set(by).issubset(table.columns):
        raise ValueError("Invalid aggregation columns")
    grouped = table.groupby(by, dropna=False, observed=True, sort=True)
    result = grouped.agg(
        observed_sum_tonnes=("catch_tonnes", lambda x: x.sum(min_count=1)),
        n_records=("catch_tonnes", "size"), n_observed=("catch_tonnes", "count"),
        sources=("source", lambda x: ";".join(sorted(set(x.dropna())))),
        regions=("region", lambda x: ";".join(sorted(set(x.dropna())))),
    ).reset_index()
    result["n_missing"] = result.n_records - result.n_observed
    result["catch_tonnes"] = result.observed_sum_tonnes.where(result.n_missing.eq(0))
    return result


def coverage(table, *, species, scope, start_year, end_year):
    """Record presence of expected source views, not completeness of fisheries."""
    counts = table.groupby(["year", "species_code", "source", "region"], observed=True).agg(
        n_records=("catch_tonnes", "size"), n_observed=("catch_tonnes", "count"))
    rows = []
    for code in species:
        for source, region in components(code, scope):
            for year in range(start_year, end_year + 1):
                key = (year, code, source, region)
                n, observed = counts.loc[key].tolist() if key in counts.index else (0, 0)
                rows.append(dict(year=year, species_code=code, source=source, region=region,
                                 n_records=int(n), n_missing=int(n - observed),
                                 source_view_present=bool(n)))
    return pd.DataFrame(rows, columns=["year", "species_code", "source", "region", "n_records", "n_missing", "source_view_present"])


def _recipe(config):
    path = Path(config).expanduser().absolute() if config else None
    cfg = tomllib.loads(path.read_text(encoding="utf-8")) if path else {}
    if set(cfg) - {"pipeline", "versions"}:
        raise ValueError("Unknown recipe section")
    recipe = dict(start_year=1950, species=list(SPECIES), scopes=["global", "pacific"])
    options = cfg.get("pipeline", {})
    if set(options) - {"start_year", "end_year", "species", "scopes", "gear_overrides"}:
        raise ValueError("Unknown pipeline option")
    recipe.update(options)
    for key, allowed in [("species", SPECIES), ("scopes", {"global", "pacific"})]:
        values = recipe[key]
        if not isinstance(values, list) or not values or len(values) != len(set(values)) or set(values) - set(allowed):
            raise ValueError(f"Invalid {key}: {values}")
    for key in ["start_year", "end_year"]:
        if key in recipe and (type(recipe[key]) is not int or not 1800 <= recipe[key] <= 2200):
            raise ValueError(f"Invalid {key}")
    if recipe.get("end_year", 2200) < recipe["start_year"]:
        raise ValueError("end_year precedes start_year")
    versions = cfg.get("versions", {})
    if set(versions) - set(DATASETS) or any(not isinstance(v, str) or not re.fullmatch(r"[0-9a-f]{64}", v) for v in versions.values()):
        raise ValueError("versions must map source names to full SHA-256 strings")
    overrides, evidence = {}, []
    if options.get("gear_overrides"):
        override_path = Path(options["gear_overrides"]).expanduser()
        if not override_path.is_absolute():
            override_path = path.parent / override_path
        crosswalk = pd.read_csv(override_path, dtype=str, keep_default_na=False)
        if set(crosswalk) != {"source", "gear_code", "gear_group", "evidence"}:
            raise ValueError("Gear override columns: source,gear_code,gear_group,evidence")
        if crosswalk.duplicated(["source", "gear_code"]).any():
            raise ValueError("Duplicate gear overrides")
        for row in crosswalk.to_dict("records"):
            if row["source"] not in DATASETS or any(not v.strip() for v in row.values()):
                raise ValueError("Gear override needs a known source, nonempty codes and evidence")
            overrides.setdefault(row["source"], {})[row["gear_code"]] = row["gear_group"]
            evidence.append(row)
    return recipe, versions, overrides, evidence


def build_annual(config=None, *, library=None, output=None):
    """Build private derived tables. No downloads; each run is immutable.

    output is a parent directory, default <library>/processed/annual. Repeated
    runs get separate folders; latest.json is written only after success.
    """
    import pyarrow  # fail before reading data if the parquet extra is missing
    recipe, versions, overrides, evidence = _recipe(config)
    library = library or Library()
    needed = {source for scope in recipe["scopes"] for code in recipe["species"]
              for source, _ in components(code, scope)}
    if not needed:
        raise ValueError("No supported source views for this species/scope combination")
    frames, provenance = [], {}
    for source in DATASETS:
        if source not in needed:
            continue
        table, record = read_source(library, source, versions.get(source), overrides)
        frames.append(table)
        provenance[source] = {"dataset_id": DATASETS[source], "sha256": record["sha256"],
                              "original_filename": record["filename"],
                              "source_url": record.get("source_url"), "retrieved_at": record.get("retrieved_at"),
                              "standardized_rows": len(table), "year_min": int(table.year.min()),
                              "year_max": int(table.year.max())}
    canonical = pd.concat([t[CANONICAL] for t in frames], ignore_index=True)
    if canonical.observation_id.duplicated().any():
        raise ValueError("Source records overlap by identity")
    end_year = recipe.get("end_year", int(canonical.year.max()))
    if end_year < recipe["start_year"]:
        raise ValueError("No years available at or after start_year")
    parent = Path(output).expanduser().absolute() if output else library.root / "processed" / "annual"
    run_id = "Annual_catch_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex[:8]
    run_dir = parent / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    outputs, selected_counts = {}, {}

    def save(name, frame, csv=False):
        relative = name + (".csv" if csv else ".parquet")
        path = run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if csv:
            frame.to_csv(path, index=False, na_rep="")
        else:
            frame.to_parquet(path, index=False)
        outputs[name] = dict(path=relative, rows=len(frame), sha256=checksum(path))

    for table in frames:
        source = str(table.source.iloc[0])
        save("standardized/" + source, table)
    for scope in recipe["scopes"]:
        merged = select_catch(canonical, species=recipe["species"], scope=scope,
                              start_year=recipe["start_year"], end_year=end_year)
        if merged.empty:
            raise ValueError(f"No selected observations for {scope}; review recipe")
        save(scope + "_catch", merged)
        selected_counts[scope] = len(merged)
        cov = coverage(merged, species=recipe["species"], scope=scope,
                       start_year=recipe["start_year"], end_year=end_year)
        save(scope + "_coverage", cov, csv=True)
        presence = cov.groupby(["year", "species_code"], as_index=False).agg(
            all_source_views_present=("source_view_present", "all"),
            expected_source_views=("source_view_present", "size"),
            present_source_views=("source_view_present", "sum"))
        for suffix, by in [("by_gear", None), ("by_source_gear", ["year", "species_code", "source", "region", "gear_group", "catch_basis"])]:
            totals = aggregate_catch(merged, by=by).merge(presence, on=["year", "species_code"], how="left", validate="many_to_one")
            save(scope + "_" + suffix, totals, csv=True)
        audit = canonical.assign(selected=canonical.observation_id.isin(merged.observation_id))
        audit = audit.groupby(["source", "region", "species_code", "year", "catch_basis", "selected"], observed=True).size().reset_index(name="n_records")
        save(scope + "_selection_audit", audit, csv=True)
    gears = canonical.groupby(["source", "gear_code_raw", "gear_mapping_code", "gear_label_raw", "gear_group", "gear_mapping_status"], dropna=False, observed=True).size().reset_index(name="n_records")
    save("gear_crosswalk", gears, csv=True)
    # Totals by view, not over the mutually overlapping WCPFC geography views.
    source_checks = aggregate_catch(canonical, by=["year", "species_code", "source", "region", "catch_basis"])
    save("source_checks", source_checks, csv=True)
    if evidence:
        pd.DataFrame(evidence).to_csv(run_dir / "gear_overrides.csv", index=False)
    locked = dict(recipe, end_year=end_year)
    if evidence:
        locked["gear_overrides"] = "gear_overrides.csv"
    lock_lines = ["# Re-run against the same source snapshots and selection period.", "[pipeline]"]
    lock_lines += [f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in locked.items()]
    lock_lines += ["", "[versions]"] + [f'{s} = "{p["sha256"]}"' for s, p in provenance.items()]
    (run_dir / "recipe.lock.toml").write_text("\n".join(lock_lines) + "\n", encoding="utf-8")
    code_hash = hashlib.sha256()
    for name in ["harmonize.py", "pipeline.py"]:
        code_hash.update(Path(__file__).with_name(name).read_bytes())
    metadata = dict(schema_version=SCHEMA_VERSION, status="complete", run_id=run_id,
                    recipe=locked, sources=provenance, outputs=outputs, selected_rows=selected_counts,
                    adapter_sha256=code_hash.hexdigest(), python_version=platform.python_version(),
                    pandas_version=pd.__version__, pyarrow_version=pyarrow.__version__,
                    unmapped_gear_rows=int(canonical.gear_mapping_status.eq("unmapped").sum()),
                    warnings=[
                        "Catch bases remain separate: these tables are not a harmonized global retained-catch total.",
                        "Presence of source views does not establish completeness of fisheries or of each gear.",
                        "WCPFC geographic views overlap; only the species-specific authority selection is additive geographically.",
                        "Pacific gear tables exclude SBF: the CCSBT global gear marginal cannot be split by ocean or flag.",
                        "Missing source cells/years are not zeros; source fleet/flag names are not a harmonized ISO country series.",
                        "GFW hours and sampled catch/effort tables are excluded from annual nominal-catch sums.",
                    ])
    atomic_json(run_dir / "provenance.json", metadata)
    atomic_json(parent / "latest.json", {"run_id": run_id, "schema_version": SCHEMA_VERSION})
    return {"run_dir": str(run_dir), "tables": outputs, "selected_rows": selected_counts,
            "unmapped_gear_rows": metadata["unmapped_gear_rows"]}


def read_processed(name, *, run=None, library=None, verify=True):
    """Read a completed output by name, optionally from a fixed run directory."""
    if run is None:
        parent = (library or Library()).root / "processed" / "annual"
        latest = json.loads((parent / "latest.json").read_text(encoding="utf-8"))
        run_dir = contained(parent, latest["run_id"])
    else:
        run_dir = Path(run).expanduser().absolute()
    meta = json.loads((run_dir / "provenance.json").read_text(encoding="utf-8"))
    if meta.get("status") != "complete" or meta.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Incomplete or unsupported pipeline run")
    entry = meta["outputs"][name]
    path = contained(run_dir, entry["path"])
    if verify and checksum(path) != entry["sha256"]:
        raise ValueError(f"Output checksum failed: {name}")
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    # Output strings such as the literal flag code NA must not become missing.
    return pd.read_csv(path, keep_default_na=False, na_values=[""])
