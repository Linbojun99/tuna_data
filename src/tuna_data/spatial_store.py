"""Year-partitioned spatial outputs and predicate-filtered, verified reads."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from .storage import checksum, contained


def collection_entry(name, files):
    files = sorted(files, key=lambda f: f["path"])
    return dict(path=name, format="parquet_collection", rows=sum(f["rows"] for f in files), files=files,
                sha256=hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest())


class YearWriter:
    """One file per year; bounded memory even when input chunks mix years."""
    def __init__(self, run, name, schema=None):
        self.run, self.name, self.schema = Path(run), name, schema
        self.writers, self.rows = {}, {}
        (self.run / name).mkdir(parents=True, exist_ok=True)

    def write(self, frame):
        import pyarrow as pa
        import pyarrow.parquet as pq
        if frame.empty:
            return
        if self.schema is None:
            self.schema = pa.Schema.from_pandas(frame, preserve_index=False)
        for year, part in frame.groupby("year", sort=False, observed=True):
            year = int(year)
            if year not in self.writers:
                self.writers[year] = pq.ParquetWriter(self.run / self.name / f"{year}.parquet", self.schema, compression="zstd")
                self.rows[year] = 0
            self.writers[year].write_table(pa.Table.from_pandas(part, schema=self.schema, preserve_index=False))
            self.rows[year] += len(part)

    def close(self):
        for writer in self.writers.values():
            writer.close()

    def entry(self):
        self.close()
        files = [dict(path=f"{self.name}/{year}.parquet", year=year, rows=n,
                      sha256=checksum(self.run / self.name / f"{year}.parquet")) for year, n in self.rows.items()]
        if not files:
            if self.schema is None:
                raise ValueError("Cannot publish a spatial table without a schema")
            import pyarrow as pa
            import pyarrow.parquet as pq
            path = self.run / self.name / "empty.parquet"
            pq.write_table(pa.Table.from_batches([], schema=self.schema), path)
            files = [dict(path=f"{self.name}/empty.parquet", rows=0, sha256=checksum(path))]
        return collection_entry(self.name, files)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _values(value):
    if value is None:
        return None
    return [value] if isinstance(value, (str, int, float)) else list(value)


def read_table(run, name, *, year=None, species=None, gear=None, source=None, rfmo=None,
               unit=None, measure=None, columns=None, include_partial=True, verify=True):
    """Apply Arrow predicates before pandas/R conversion; verify selected files.

    Schema-v1 single-file spatial runs remain readable. A species filter is
    rejected for GFW because these reports have no species attribution.
    """
    import pyarrow as pa
    import pyarrow.dataset as ds
    import pyarrow.parquet as pq
    run = Path(run).expanduser().absolute()
    meta = json.loads((run / "provenance.json").read_text(encoding="utf-8"))
    if meta.get("status") != "complete" or meta.get("schema_version") not in {1, 2}:
        raise ValueError("Incomplete or unsupported spatial run")
    if name not in meta["outputs"]:
        raise KeyError(f"No table {name}; available: {', '.join(meta['outputs'])}")
    entry = meta["outputs"][name]
    filters = {"year": _values(year), "species_code": _values(species), "gear_group": _values(gear),
               "source": _values(source), "region_raw": _values(rfmo), "unit": _values(unit), "measure": _values(measure)}
    if filters["year"] is not None:
        if any(isinstance(v, bool) or not str(v).replace(".0", "").isdigit() or int(v) != float(v) for v in filters["year"]):
            raise ValueError("year must contain integer calendar years")
        filters["year"] = [int(v) for v in filters["year"]]
    if filters["source"] is not None:
        filters["source"] = [str(v).lower() for v in filters["source"]]
    if filters["species_code"] is not None:
        filters["species_code"] = [str(v).upper() for v in filters["species_code"]]
        if name.startswith("gfw_"):
            raise ValueError("GFW activity reports do not identify tuna species; omit species")
    if filters["region_raw"] is not None:
        filters["region_raw"] = [str(v).upper() for v in filters["region_raw"]]
    requested = _values(columns)
    files = entry.get("files", [entry])
    chosen = [f for f in files if all(f.get(k) in values for k, values in filters.items() if values is not None and k in f)]
    # Read only a footer for an empty year selection, preserving column types.
    first = contained(run, (chosen or files)[0]["path"])
    is_csv = first.suffix == ".csv"
    if is_csv:
        if verify and checksum(first) != files[0]["sha256"]:
            raise ValueError(f"Output checksum failed: {name}")
        data = pd.read_csv(first, keep_default_na=False, na_values=[""])
        if "rfmo" in data and "region_raw" not in data:
            filters["rfmo"] = filters.pop("region_raw")
        for key, values in filters.items():
            if values is not None:
                if key not in data:
                    raise ValueError(f"Table {name} has no {key} field")
                data = data[data[key].isin(values)]
        return data[requested] if requested is not None else data
    schema = pq.read_schema(first)
    predicate = None
    for key, values in filters.items():
        if values is None:
            continue
        if key not in schema.names:
            raise ValueError(f"Table {name} has no {key} field")
        rule = ds.field(key).isin(values)
        predicate = rule if predicate is None else predicate & rule
    if not include_partial and "period_status" in schema.names:
        rule = ds.field("period_status") != "partial_year"
        predicate = rule if predicate is None else predicate & rule
    if requested is not None and set(requested) - set(schema.names):
        raise ValueError("Unknown requested columns")
    if not chosen:
        empty = pa.Table.from_batches([], schema=schema)
        return (empty.select(requested) if requested is not None else empty).to_pandas()
    paths = []
    for f in chosen:
        path = contained(run, f["path"])
        if verify and checksum(path) != f["sha256"]:
            raise ValueError(f"Output checksum failed: {f['path']}")
        paths.append(str(path))
    return ds.dataset(paths, format="parquet", schema=schema).to_table(columns=requested, filter=predicate).to_pandas()
