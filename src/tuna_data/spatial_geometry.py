"""Source-specific rectangular grid decoding; no coordinate imputation."""
from __future__ import annotations

import re
import math

import pandas as pd


def rectangle(west, south, width, height):
    values = [west, south, width, height]
    if any(pd.isna(v) for v in values):
        return dict(lon=pd.NA, lat=pd.NA, west=pd.NA, south=pd.NA,
                    grid_width=pd.NA, grid_height=pd.NA, geometry_status="unlocated")
    if not all(math.isfinite(float(v)) for v in values):
        raise ValueError("Non-finite grid coordinates")
    west = (float(west) + 180) % 360 - 180
    south, width, height = map(float, [south, width, height])
    if width <= 0 or height <= 0 or width > 360 or south < -90 or south + height > 90:
        raise ValueError("Invalid grid extent")
    return dict(lon=(west + width / 2 + 180) % 360 - 180, lat=south + height / 2,
                west=west, south=south, grid_width=width, grid_height=height, geometry_status="located")


def cwp_iotc(code):
    """IOTC 7-digit CWP: grid type, quadrant, absolute origin-facing corner.

    Type 4 is IOTC's 20x20 convention. Quadrant 4 is NW (negative longitude).
    Irregular/non-grid fishing-ground codes remain unlocated, never (0, 0).
    """
    code = str(code).strip()
    if not re.fullmatch(r"[1-6][1-4][0-9]{5}", code):
        return rectangle(pd.NA, pd.NA, pd.NA, pd.NA)
    height, width = {"1": (30, 30), "2": (10, 20), "3": (10, 10),
                     "4": (20, 20), "5": (1, 1), "6": (5, 5)}[code[0]]
    quadrant, lat, lon = int(code[1]), int(code[2:4]), int(code[4:7])
    if lon >= 180 or lat >= 90:
        raise ValueError(f"Out-of-range CWP grid: {code}")
    south = lat if quadrant in {1, 4} else -lat - height
    west = lon if quadrant in {1, 2} else -lon - width
    return rectangle(west, south, width, height)


def hemisphere(value, axis):
    value = str(value).strip().upper()
    if value in {"", "NA", "N/A"}:
        return pd.NA
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([NSEW])", value)
    if not match or match[2] not in ("NS" if axis == "lat" else "EW"):
        raise ValueError(f"Invalid {axis} coordinate: {value}")
    number = float(match[1]) * (-1 if match[2] in "SW" else 1)
    if abs(number) > (90 if axis == "lat" else 180):
        raise ValueError(f"Out-of-range {axis}")
    return number


def wcpfc_cell(lat, lon, size):
    # WCPFC LAT/LON fields explicitly denote the south-west corner, not CWP's
    # corner nearest the origin. A southern latitude must NOT be shifted twice.
    return rectangle(hemisphere(lon, "lon"), hemisphere(lat, "lat"), size, size)


def centered_cell(lat, lon, size):
    return rectangle(lon - size / 2, lat - size / 2, size, size)


def ccsbt_cell(lat, lon):
    # CCSBT's published grids use the NORTH-west corner.
    return rectangle(lon, lat - 5, 5, 5)


def grid_status(table, size=5):
    """Coarsen only cells wholly inside one target cell; never disaggregate."""
    t = table.copy()
    located = t.geometry_status.eq("located")
    west = (t.west.astype("Float64") // size) * size
    south = (t.south.astype("Float64") // size) * size
    inside = ((t.west.astype("Float64") + t.grid_width.astype("Float64") <= west + size + 1e-8)
              & (t.south.astype("Float64") + t.grid_height.astype("Float64") <= south + size + 1e-8))
    eligible = located & inside.fillna(False)
    t["grid_status"] = "coarser_or_misaligned"
    t.loc[~located, "grid_status"] = "unlocated"
    t.loc[eligible, "grid_status"] = "eligible"
    t["map_west"] = west.where(eligible)
    t["map_south"] = south.where(eligible)
    t["map_lon"] = (west + size / 2).where(eligible)
    t["map_lat"] = (south + size / 2).where(eligible)
    t["map_size"] = float(size)
    return t


def pacific_selection(table):
    """EPO authority east of 150W, WCPFC authority west; retain exclusions."""
    t = table.copy()
    t["region_selection"] = "selected"
    located = t.geometry_status.eq("located")
    t.loc[~located, "region_selection"] = "unlocated"
    west = t.west.astype("Float64")
    east = west + t.grid_width.astype("Float64")
    iattc = t.source.eq("iattc") & located
    wcpfc = t.source.eq("wcpfc") & located
    epo = (west >= -150) & (east <= 0)
    wcpo = ((west >= 0) & (east <= 180)) | ((west >= -180) & (east <= -150))
    t.loc[iattc & ~epo.fillna(False), "region_selection"] = "outside_EPO_partition"
    t.loc[wcpfc & ~wcpo.fillna(False), "region_selection"] = "outside_WCPO_partition"
    # SBF uses the CCSBT spatial product; do not add SBF records reported elsewhere.
    t.loc[t.species_code.eq("SBF") & ~t.source.eq("ccsbt"), "region_selection"] = "SBF_CCSBT_authority"
    t["in_pacific"] = t.source.isin(["iattc", "wcpfc"]) | (t.source.eq("ccsbt") & t.region_raw.eq("Pacific"))
    return t
