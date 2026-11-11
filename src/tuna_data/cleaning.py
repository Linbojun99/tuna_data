"""Conservative reusable cleaning; preserve source rows, units and missing codes."""
from __future__ import annotations

import re
import pandas as pd


def clean_columns(table):
    """Normalize headers only. Refuse collisions instead of overwriting columns."""
    names = [re.sub(r"[^a-z0-9]+", "_", str(c).strip().lower()).strip("_") for c in table.columns]
    if len(names) != len(set(names)):
        raise ValueError("Header normalization would create duplicate names")
    result = table.copy()
    result.columns = names
    return result


def numeric_columns(table, columns, *, missing=("", "NA", "N/A", "NaN")):
    """Convert selected measurements only; unknown codes raise, never become zero."""
    result = table.copy()
    for column in columns:
        values = result[column].astype("string").str.strip()
        result[column] = pd.to_numeric(values.mask(values.isin(missing)), errors="raise")
    return result


def gfw_hours(table):
    """Support the observed original report and locally merged GFW CSV schemas."""
    result = clean_columns(table)
    result = result.rename(columns={"apparent_fishing_hours": "fishing_hours", "time_range": "date"})
    if len(result.columns) != len(set(result.columns)):
        raise ValueError("Ambiguous GFW fields after renaming")
    required = {"lat", "lon", "fishing_hours"}
    if not required.issubset(result.columns):
        raise ValueError(f"Missing GFW fields: {required - set(result.columns)}")
    result = numeric_columns(result, ["lat", "lon", "fishing_hours"])
    if ((result["lat"].abs() > 90) | (result["lon"].abs() > 180) | (result["fishing_hours"] < 0)).any():
        raise ValueError("Invalid GFW coordinates or negative fishing hours")
    result.attrs["measurement"] = "apparent_fishing_hours"
    return result
