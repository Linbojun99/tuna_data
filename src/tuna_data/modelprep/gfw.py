"""GFW apparent fishing hours as an effort covariate, streamed per report year.

Hours are AIS-inferred activity of all vessels, not catch and not tuna-specific.
RFMO report regions overlap, so hours are taken from one region at a time and
are never added across regions. Files are verified and read in batches.
"""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import re

import pandas as pd

from ..library import Library
from ..storage import checksum, contained


def spatial_run(run=None, library=None):
    if run is not None:
        return Path(run).expanduser().absolute()
    parent = (library or Library()).root / "processed" / "spatial"
    pointer = parent / "latest.json"
    if not pointer.is_file():
        raise FileNotFoundError("No spatial run: build GFW layers with tuna_spatial_build() first")
    return contained(parent, json.loads(pointer.read_text(encoding="utf-8"))["run_id"])


def gfw_effort(rfmo, *, years=None, gear_types=None, grid=5.0, period="month", run=None, library=None,
               verify=True, missing="raise", batch_size=1_000_000):
    """Hours per grid cell and period for one RFMO report region.

    Returns columns year, month|quarter (unless period='year'), map_west,
    map_south, map_size, hours, gfw_period_status, rfmo, gear_types. Cells absent
    from the result had no recorded activity. attrs: covered_months {year: last
    month with coverage}, skipped_years (checksum failures with missing='skip';
    the default raises) and spatial_run.
    """
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.dataset as ds
    if period not in ("month", "quarter", "year"):
        raise ValueError("period must be 'month', 'quarter' or 'year'")
    if missing not in ("raise", "skip"):
        raise ValueError("missing must be 'raise' or 'skip'")
    root = spatial_run(run, library)
    meta = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    rfmo = str(rfmo).upper()
    wanted = None if years is None else {int(y) for y in years}
    size = float(grid)
    keys = {"month": ["year", "month"], "quarter": ["year", "quarter"], "year": ["year"]}[period]
    frames, skipped, covered = [], [], {}
    for name, entry in sorted(meta["outputs"].items()):
        match = re.fullmatch(rf"gfw_{rfmo.lower()}_(\d{{4}})_native", name)
        if not match or (wanted is not None and int(match[1]) not in wanted):
            continue
        path = contained(root, entry["path"])
        if verify and checksum(path) != entry["sha256"]:
            if missing == "skip":
                skipped.append(int(match[1]))
                continue
            raise ValueError(f"GFW file failed its checksum: {entry['path']}. Re-sync the library copy "
                             "(Google Drive) or rebuild the spatial run; pass gfw_missing='skip' to omit that year.")
        dataset = ds.dataset(str(path), format="parquet")
        year = int(match[1])
        head = dataset.head(1, columns=["report_end_exclusive"]).column(0).to_pylist()
        last = date.fromisoformat(head[0]) - timedelta(days=1) if head and head[0] else date(year, 12, 31)
        covered[year] = last.month if last.year == year else 12   # last calendar month with AIS coverage
        flt = ds.field("gear_group").isin(list(gear_types)) if gear_types else None
        parts = []
        for batch in dataset.to_batches(columns=["year", "month_start", "lon", "lat", "value", "period_status"],
                                        filter=flt, batch_size=batch_size):
            if batch.num_rows == 0:
                continue
            t = pa.Table.from_batches([batch])
            west = pc.multiply(pc.floor(pc.add(pc.divide(t["lon"], size), 1e-9)), size)
            south = pc.multiply(pc.floor(pc.add(pc.divide(t["lat"], size), 1e-9)), size)
            cols = {"year": t["year"], "map_west": west, "map_south": south, "value": t["value"],
                    "period_status": t["period_status"]}
            if period == "month":
                cols["month"] = t["month_start"]
            elif period == "quarter":
                cols["quarter"] = pc.add(pc.divide(pc.subtract(t["month_start"], 1), 3), 1)
            grouped = pa.table(cols).group_by([*keys, "map_west", "map_south", "period_status"]).aggregate([("value", "sum")])
            parts.append(grouped.to_pandas())
        if parts:
            part = pd.concat(parts, ignore_index=True)
            frames.append(part.groupby([*keys, "map_west", "map_south", "period_status"], as_index=False)["value_sum"].sum())
    columns = [*keys, "map_west", "map_south", "map_size", "hours", "gfw_period_status", "rfmo", "gear_types"]
    if not frames:
        out = pd.DataFrame(columns=columns)
    else:
        out = pd.concat(frames, ignore_index=True).rename(columns={"value_sum": "hours", "period_status": "gfw_period_status"})
        out["map_size"] = size
        out["rfmo"] = rfmo
        out["gear_types"] = ";".join(gear_types) if gear_types else "all"
        for column in keys:
            out[column] = out[column].astype("Int64")
        out = out[columns]
    out.attrs.update(skipped_years=skipped, covered_months=covered, spatial_run=root.name)
    return out
