"""Canonical catch-effort strata shared by CPUE and SDM preparation.

Every public product is translated into three linked tables, keyed by
``stratum_id`` and partitioned by ``year``:

* ``strata``: one row per source stratum (time x area x fleet x gear x set type);
* ``effort``: one row per stratum x effort unit. Units are never summed;
* ``catch``: one row per stratum x species x catch unit. Units are never summed.

Adapters never impute zeros, coordinates, units or effort. They report what the
source says, using the raw catch statuses below. The build step then confirms
zeros against what each reporting group (fleet x gear, or ICCAT data set) reports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

import pandas as pd

from ..spatial_geometry import rectangle

TARGET_SPECIES = ("YFT", "BET", "SKJ", "ALB", "PBF", "BFT", "SBF")
# Swordfish is kept as a composition covariate for longline targeting clusters.
EXTRA_SPECIES = ("SWO",)
SPECIES = TARGET_SPECIES + EXTRA_SPECIES

STRATA_COLUMNS = [
    "stratum_id",        # unique in a run: "<product>|<key>"
    "source",            # iattc | wcpfc | iotc | iccat | ccsbt
    "product",           # adapter product name, e.g. "wcpfc_ll"
    "view_group",        # products in one group are alternative views: never combine them
    "dataset_id", "source_sha256", "source_member",
    "source_rows",       # ';'-joined source row ids (CSV line numbers, worksheet rows or StrataIDs)
    "year", "month_start", "month_end", "time_resolution",
    "grid_raw", "west", "south", "grid_width", "grid_height", "lon", "lat", "geometry_status",
    "region_raw",        # area label as reported (RFMO, ocean or statistical area)
    "fleet",             # flag or fleet code as reported; '' when the product has none
    "gear_raw", "gear_group", "school_type",  # school/set type as reported; '' when none
    "reporting_group",   # unit for zero confirmation, e.g. "<fleet>|<gear_raw>" or "dset:<DSetID>"
    "stratum_status",    # reported | masked | effort_only | catch_only
    "notes",             # ';'-joined QC flags, e.g. "suspect_effort"
]
EFFORT_COLUMNS = ["stratum_id", "year", "effort_unit", "effort", "effort_status", "effort_field", "effort_scope"]
CATCH_COLUMNS = ["stratum_id", "year", "species_code", "catch_unit", "catch", "catch_status", "catch_field", "catch_basis"]

TIME_RESOLUTIONS = {"month", "quarter", "year", "multi_month"}
GEOMETRY_STATUS = {"located", "unlocated", "surrogate_area"}
STRATUM_STATUS = {"reported", "masked", "effort_only", "catch_only"}
# Canonical effort units. Unmapped source codes are written as "<source>:<code>".
EFFORT_UNITS = {
    "hooks", "sets", "successful_sets", "fishing_days", "days_fishing_searching",
    "days_at_sea", "fishing_hours", "hours_at_sea", "standardized_fishing_hours",
    "hours_searched", "trips",
}
EFFORT_STATUS = {"reported", "zero", "missing", "masked", "invalid"}
EFFORT_SCOPE = {"stratum", "school"}
CATCH_UNITS = {"fish", "tonnes", "kg"}
# What adapters may emit.
RAW_CATCH_STATUS = {"positive", "zero", "blank", "masked", "unit_incomplete", "no_catch_info"}
# What the build step writes after zero confirmation.
CATCH_STATUS = {
    "positive",                 # catch > 0
    "zero_reported",            # explicit 0, and the reporting group reports this species/unit that year
    "zero_unconfirmed",         # explicit 0 from a group that never reports this species/unit that year
    "inferred_zero_candidate",  # no value, but the group reports the species/unit that year (sensitivity only)
    "not_reported",             # blank/NULL value; unknown
    "masked",                   # confidential/suppressed stratum
    "unit_incomplete",          # part of the catch was reported only in another unit
    "no_catch_info",            # effort-only record (e.g. ICCAT CatchUnit '..')
}
_RAW_UNIT = re.compile(r"^(iattc|wcpfc|iotc|iccat|ccsbt):[A-Za-z0-9_. /-]+$")


@dataclass(frozen=True)
class ProductSpec:
    """One public product handled by an adapter."""
    product: str
    source: str
    dataset_id: str
    member: str               # ZIP member, worksheet or MDB table; '' for single-file datasets
    view_group: str
    zeros_explicit: bool      # False when the source lists positive catches only (IOTC CE)
    catch_basis: str
    description: str
    default: bool = True      # included by the default build recipe
    extra: dict = field(default_factory=dict)
    extra_datasets: tuple = ()  # other library datasets the adapter reads (pinned in the lock)


@dataclass
class AdapterResult:
    strata: pd.DataFrame
    effort: pd.DataFrame
    catch: pd.DataFrame
    record: dict              # the library record actually read (sha256 pinned)
    qc: list = field(default_factory=list)   # [{"check": str, "count": int, "note": str}]
    extra_records: list = field(default_factory=list)   # records of extra_datasets actually read


def empty(columns):
    return pd.DataFrame({c: pd.Series(dtype="object") for c in columns})


def parse_values(values, *, name, missing=("", "NA", "N/A", "NaN", "nan", "NULL")):
    """Strictly parse text to Float64. Listed tokens become NA; unknown tokens raise."""
    text = pd.Series(values, copy=False).astype("string").str.strip()
    blank = text.isna() | text.isin(missing)
    numbers = pd.to_numeric(text.mask(blank), errors="coerce")
    bad = numbers.isna() & ~blank
    if bad.any():
        sample = sorted(set(text[bad].head(5)))
        raise ValueError(f"Unparseable values in {name}: {sample}")
    return numbers.astype("Float64")


def geometry_frame(cells, index):
    """Rectangles from spatial_geometry dicts (rectangle/cwp_iotc/...)."""
    geo = pd.DataFrame(list(cells), index=index)
    for column in ["lon", "lat", "west", "south", "grid_width", "grid_height"]:
        geo[column] = pd.to_numeric(geo[column], errors="coerce").astype("Float64")
    return geo


def nw_corner_cell(lat, lon, size):
    """CCSBT convention: (lat, lon) is the NORTH-west corner of a size x size cell."""
    if pd.isna(lat) or pd.isna(lon):
        return rectangle(pd.NA, pd.NA, pd.NA, pd.NA)
    return rectangle(float(lon), float(lat) - size, size, size)


def quarter_months(quarter):
    """(month_start, month_end) for quarter 1-4."""
    q = int(quarter)
    if q not in (1, 2, 3, 4):
        raise ValueError(f"Invalid quarter: {quarter}")
    return 3 * q - 2, 3 * q


def time_resolution(month_start, month_end):
    start, end = pd.Series(month_start).astype("Int64"), pd.Series(month_end).astype("Int64")
    out = pd.Series("multi_month", index=start.index, dtype="string")
    out[start.eq(end).fillna(False)] = "month"
    quarter = start.isin([1, 4, 7, 10]) & (end - start).eq(2)
    out[quarter.fillna(False)] = "quarter"
    out[(start.eq(1) & end.eq(12)).fillna(False)] = "year"
    return out


def _strings(frame, numeric):
    for column in frame.columns:
        if column not in numeric:
            frame[column] = frame[column].astype("string").fillna("")
    return frame


def finalize(strata, effort, catch):
    """Coerce dtypes and validate the canonical contract; return the three frames."""
    strata = strata.reindex(columns=STRATA_COLUMNS).copy()
    effort = effort.reindex(columns=EFFORT_COLUMNS).copy()
    catch = catch.reindex(columns=CATCH_COLUMNS).copy()
    for column in ["year", "month_start", "month_end"]:
        strata[column] = pd.to_numeric(strata[column], errors="raise").astype("Int64")
    for column in ["west", "south", "grid_width", "grid_height", "lon", "lat"]:
        strata[column] = pd.to_numeric(strata[column], errors="raise").astype("Float64")
    _strings(strata, {"year", "month_start", "month_end", "west", "south", "grid_width", "grid_height", "lon", "lat"})
    effort["year"] = pd.to_numeric(effort["year"], errors="raise").astype("Int64")
    effort["effort"] = pd.to_numeric(effort["effort"], errors="raise").astype("Float64")
    _strings(effort, {"year", "effort"})
    catch["year"] = pd.to_numeric(catch["year"], errors="raise").astype("Int64")
    catch["catch"] = pd.to_numeric(catch["catch"], errors="raise").astype("Float64")
    _strings(catch, {"year", "catch"})
    validate(strata, effort, catch, final=False)
    return strata, effort, catch


def validate(strata, effort, catch, *, final=True):
    """Raise ValueError when a table breaks the canonical contract."""
    problems = []
    if strata.stratum_id.duplicated().any():
        problems.append("duplicate stratum_id")
    if strata.year.isna().any():
        problems.append("missing year")
    months = pd.concat([strata.month_start.dropna(), strata.month_end.dropna()])
    if ((months < 1) | (months > 12)).any():
        problems.append("month outside 1-12")
    if (strata.month_end < strata.month_start).fillna(False).any():
        problems.append("month_end before month_start")
    for column, allowed in [("time_resolution", TIME_RESOLUTIONS), ("geometry_status", GEOMETRY_STATUS),
                            ("stratum_status", STRATUM_STATUS)]:
        unknown = set(strata[column]) - allowed
        if unknown:
            problems.append(f"{column} values {sorted(unknown)[:5]}")
    located = strata.geometry_status.eq("located")
    if strata.loc[located, ["west", "south", "grid_width", "grid_height"]].isna().any().any():
        problems.append("located stratum without rectangle")
    ids = set(strata.stratum_id)
    for name, table, key in [("effort", effort, ["stratum_id", "effort_unit"]),
                             ("catch", catch, ["stratum_id", "species_code", "catch_unit"])]:
        if not set(table.stratum_id) <= ids:
            problems.append(f"{name} rows without a stratum")
        if table.duplicated(key).any():
            problems.append(f"duplicate {name} key {key}")
        if table.year.isna().any():
            problems.append(f"{name} without year")
    unknown_units = {u for u in set(effort.effort_unit) if u not in EFFORT_UNITS and not _RAW_UNIT.match(u)}
    if unknown_units:
        problems.append(f"effort_unit values {sorted(unknown_units)[:5]}")
    for column, allowed in [("effort_status", EFFORT_STATUS), ("effort_scope", EFFORT_SCOPE)]:
        unknown = set(effort[column]) - allowed
        if unknown:
            problems.append(f"{column} values {sorted(unknown)[:5]}")
    if (effort.effort < 0).fillna(False).any():
        problems.append("negative effort (use effort_status 'invalid' and NA)")
    reported = effort.effort_status.eq("reported")
    if not (effort.loc[reported, "effort"] > 0).fillna(False).all():
        problems.append("'reported' effort must be > 0 (not NA)")
    if effort.loc[effort.effort_status.isin(["missing", "masked", "invalid"]), "effort"].notna().any():
        problems.append("missing/masked/invalid effort must be NA")
    unknown = set(catch.catch_unit) - CATCH_UNITS
    if unknown:
        problems.append(f"catch_unit values {sorted(unknown)[:5]}")
    unknown = set(catch.species_code) - set(SPECIES)
    if unknown:
        problems.append(f"species_code values {sorted(unknown)[:5]}")
    allowed = CATCH_STATUS if final else RAW_CATCH_STATUS
    unknown = set(catch.catch_status) - allowed
    if unknown:
        problems.append(f"catch_status values {sorted(unknown)[:5]}")
    if (catch.catch < 0).fillna(False).any():
        problems.append("negative catch")
    positive = catch.catch_status.eq("positive")
    if not (catch.loc[positive, "catch"] > 0).fillna(False).all():
        problems.append("'positive' catch must be > 0 (not NA)")
    zero = catch.catch_status.isin(["zero", "zero_reported", "zero_unconfirmed"])
    if not catch.loc[zero, "catch"].eq(0).fillna(False).all():
        problems.append("zero statuses must carry catch == 0 (not NA)")
    years = strata.set_index("stratum_id")["year"]
    for name, table in [("effort", effort), ("catch", catch)]:
        if len(table) and not table["stratum_id"].map(years).eq(table["year"]).fillna(False).all():
            problems.append(f"{name} year differs from its stratum year")
    if strata.reporting_group.eq("").any():
        problems.append("empty reporting_group")
    unknown_value = catch.catch_status.isin(["blank", "masked", "unit_incomplete", "no_catch_info",
                                             "not_reported", "inferred_zero_candidate"])
    if catch.loc[unknown_value, "catch"].notna().any():
        problems.append("unknown-value statuses must carry catch NA")
    if problems:
        raise ValueError("Canonical catch-effort contract violated: " + "; ".join(problems))
