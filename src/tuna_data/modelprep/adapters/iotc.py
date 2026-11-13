"""IOTC geo-referenced catch-and-effort (CE) products as canonical strata.

Source: dataset ``iotc/iotc_datasets_ce`` (zip ``IOTC-DATASETS-<release>-CE-...``) with two
long CSV members read as text (``keep_default_na=False``; ``NA`` is a real gear code):

* ``*-CA-*.csv``: retained catch, one row per stratum x species category x species x unit;
* ``*-EF-*.csv``: effort, one row per stratum x effort unit.

Keys (verified on the 2026-08-13 release): K6 = YEAR, MONTH_START, MONTH_END,
FISHING_GROUND_CODE, FLEET_CODE, GEAR_CODE; S7 = K6 + school type, where
CA.CATCH_SCHOOL_TYPE_CODE matches EF.SCHOOL_TYPE_CODE (IOTC list FD/FS/LA/LS/UNCL). EF is
unique on S7 + EFFORT_UNIT_CODE and CA on S7 + SPECIES_CODE + CATCH_UNIT_CODE for the schema
species; a violation stops the adapter (nothing is summed silently). QUARTER (annual rows are
coded Q1), FISHERY_* codes (PS catch is PSFS/PSLS, PS effort PSOT) and labels are never keys.

Products:

* ``iotc_ce``: every gear outside the purse-seine family; one stratum per S7 key, the full
  outer join of EF and CA.
* ``iotc_ce_ps_school``: purse-seine family, S7 strata with a reported school type
  (FS, LS, LA, FD, ...). UNCL rows belong to the pooled view only.
* ``iotc_ce_ps_pooled``: purse-seine family at K6 (school_type 'all'). Catch is summed over
  school types within species x unit; effort comes from EF rows with SCHOOL_TYPE_CODE 'UNCL'.
  Where the same K6 and unit also has school-specific effort, the UNCL row is only the
  unclassified remainder (IOTC's 2025 pooled CESurface equals UNCL + school rows), so it is
  kept as effort_status 'missing' (NA) with note 'pooled_effort_partial'. Units reported only
  by school type are left to the school view (note 'school_effort_not_pooled').

The two purse-seine products are alternative views of the same catch (view_group 'iotc_ps').
CA lists positive catches only (zeros_explicit=False): an absent species is not reported,
never zero. Catch units MT -> tonnes and NO -> fish stay separate. When a stratum lists a
species in one unit only while its reporting group (fleet|gear) reports that species in the
other unit that year, a 'unit_incomplete' row (NA) blocks any zero inference for that unit;
pooled sums that miss a school component in one unit are 'unit_incomplete' too.

Provenance: ``source_member`` lists the contributing members (CA first, ';'-joined) and
``source_rows`` holds 'CA:<line>' / 'EF:<line>' tokens, line = CSV line number (pandas
index + 2), for every CA row of the stratum (all species) and every EF row used.

Geometry: ``cwp_iotc`` (IOTC grid-size table); 9xxxxxx and irregular codes stay unlocated.
Regular-looking codes that IOTC substituted for named irregular areas (2025-10-13 CELongline
and CESurface iGrid names) become 'surrogate_area' for the fleets that used those names,
keeping the decoded rectangle, with note 'surrogate:<names>' (see ``surrogate_lookup``).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd
from pandas.api.types import union_categoricals

from ...harmonize import GEARS
from ...spatial_geometry import cwp_iotc, rectangle
from ..schema import (AdapterResult, ProductSpec, SPECIES, finalize, geometry_frame, parse_values,
                      time_resolution)

SOURCE = "iotc"
DATASET = "iotc/iotc_datasets_ce"
REGION = "IOTC"
CATCH_BASIS = "iotc_ce_reported"
MEMBERS = "*-CA-*.csv;*-EF-*.csv"


def _spec(product, view_group, gears, key, description):
    return ProductSpec(product=product, source=SOURCE, dataset_id=DATASET, member=MEMBERS, extra_datasets=("iotc/iotc_datasets_celongline", "iotc/iotc_datasets_cesurface"),
                       view_group=view_group, zeros_explicit=False, catch_basis=CATCH_BASIS,
                       description=description, extra={"gears": gears, "key": key})


PRODUCTS = {
    "iotc_ce": _spec(
        "iotc_ce", "iotc_ce", "other", "S7",
        "IOTC CE, all gears except the purse-seine family: one stratum per year x months x grid x "
        "fleet x gear x school type; effort per unit, positive catches in tonnes or fish."),
    "iotc_ce_ps_school": _spec(
        "iotc_ce_ps_school", "iotc_ps", "purse_seine", "S7",
        "IOTC CE purse-seine family by reported school type (FS/LS/LA/FD): school-specific catch "
        "and effort; strata with catch but school effort not reported are catch_only."),
    "iotc_ce_ps_pooled": _spec(
        "iotc_ce_ps_pooled", "iotc_ps", "purse_seine", "K6",
        "IOTC CE purse-seine family pooled over school types (school_type 'all'): catch summed "
        "per species x unit; effort from UNCL (pooled) rows only, withheld (NA) where the same unit "
        "also has school-specific rows."),
}

K6 = ["YEAR", "MONTH_START", "MONTH_END", "FISHING_GROUND_CODE", "FLEET_CODE", "GEAR_CODE"]
S7 = K6 + ["SCHOOL"]
CA_COLUMNS = K6 + ["FISHERY_GROUP_CODE", "CATCH_SCHOOL_TYPE_CODE", "SPECIES_CODE", "CATCH_UNIT_CODE", "CATCH"]
EF_COLUMNS = K6 + ["FISHERY_GROUP_CODE", "SCHOOL_TYPE_CODE", "EFFORT_UNIT_CODE", "EFFORT"]
CHUNK_ROWS = 250_000
POOLED = "UNCL"   # IOTC school type 'Unknown': unclassified (pooled) catch or effort

# Purse-seine family: IOTC legacy gear groups PS and PSS (data.iotc.org LEGACY_GEARS: purse
# seines, small purse seines and ring nets). CE rows also carry FISHERY_GROUP_CODE 'PS' for
# them (PS, PSS, RIN, RNOF in 2026); either criterion puts a gear in the family, and a gear
# that would fall on both sides stops the adapter.
PS_GEARS = frozenset({"PS", "PSFS", "PSLS", "PSOB", "PSP", "PSS", "RIN", "RINP", "RNOF"})

# IOTC LEGACY_EFFORT_UNITS (data.iotc.org, v1.0.0, 2023-05-12): DAYS 'Number of days at sea',
# FDAYS 'Number of fishing days', FHOURS 'Number of hours fishing', HOURS 'Number of hours at
# sea', STDHR 'Number of hours fishing standardized'. Every other code (BOATS, NETS net-days,
# MD, MH, LINES, HRSRH, and NG, which the list does not define) stays 'iotc:<CODE>'.
EFFORT_UNITS = {"HOOKS": "hooks", "SETS": "sets", "FDAYS": "fishing_days", "FHOURS": "fishing_hours",
                "HOURS": "hours_at_sea", "STDHR": "standardized_fishing_hours", "TRIPS": "trips",
                "DAYS": "days_at_sea"}
CATCH_UNITS = {"MT": "tonnes", "NO": "fish", "KG": "kg"}   # IOTC LEGACY_CATCH_UNITS

# Effort per stratum and month above which a value is flagged 'implausible_effort' (never
# dropped). FHOURS: 744 h = 31 days x 24 h, the bound requested for one month. HOOKS and NETS
# (net-days), checked on the 2026-08-13 release: every value above the cap is MDG 2025 grid
# 6215045 (LLCO/TROL 2.8e7-1.5e8 hooks, GILL 6.2e7-1.7e8 net-days, the same numbers recurring
# across gears); the largest other values are TWN LL 1991 (1.4e7 hooks) and TWN GIOF 1987
# (1.0e6 net-days).
IMPLAUSIBLE_PER_MONTH = {"FHOURS": 744.0, "HOOKS": 2.0e7, "NETS": 1.0e7}
# Fleet-gear-year effort tables identical to another year's table, at this size or larger.
MIN_REPEATED_ROWS = 6
# IOTC fleet notes (CE reference workbook NOTES): catches of non-reporting longliners
# estimated by the IOTC Secretariat, mostly from the number of vessels.
SECRETARIAT_ESTIMATES = frozenset({"NEICE", "NEIFR"})

SURROGATE_DATASETS = ("iotc/iotc_datasets_celongline", "iotc/iotc_datasets_cesurface")
# Fallback when a 2025 dataset is not in the library: 'Fleet Gear iGrid Grid' rows that
# reproduce surrogate_lookup() on the 2025-10-13 releases (CELongline sha256 46e2fa4115c1...,
# CESurface sha256 64c37f6277c2...): every named iGrid, plus one numeric row for each gear
# that reported a named fleet-grid pair only as a regular cell.
_ATOLLS = {"5100073": "GAVILI GDHTHI", "5101073": "LMAAM", "5102072": "DHKUDA", "5102073": "KOLHUM",
           "5103072": "NNILAN RALIF", "5103073": "FELID MMADU", "5104073": "KMALE MALE",
           "5105072": "BAA RKAN", "5105073": "FAADHI SMILAD", "5106072": "HDHKUL",
           "5106073": "NTHILA SHAV", "5200073": "ADDU FUVAHM"}
STATIC_SURROGATE_ROWS = {
    "iotc/iotc_datasets_celongline": [
        ("OMN", "LL", "OMAN", "2120060"), ("IDN", "FLL", "6205110", "6205110"),
        ("IDN", "FLL", "6205115", "6205115"), ("MDV", "LL", "5102072", "5102072"),
        ("MDV", "LL", "5103073", "5103073"), ("MDV", "LL", "5106073", "5106073"),
        ("MYS", "FLL", "6105100", "6105100"), ("THA", "LL", "6105095", "6105095")],
    "iotc/iotc_datasets_cesurface": [
        ("IDN", "PSS", "BACEH", "6105095"), ("IDN", "PSS", "PRIGI", "6205110"),
        ("IDN", "PSS", "BALI", "6205115"), ("IRN", "PSS", "IRN02", "6125045"),
        ("IRN", "PS", "IRN13", "6125050"), ("IRN", "PSS", "IRN10", "6125050"),
        ("IRN", "PSS", "IRN17_OLD", "6125055"), ("IRN", "PSS", "IRN181", "6125055"),
        ("MDV", "BB", "NRALIF", "5103072"), ("MDV", "BB", "SRALIF", "5103072"),
        ("MYS", "PSS", "KEDAH", "6100100"), ("MYS", "PSS", "PENANG", "6100100"),
        ("MYS", "PSS", "PERAK", "6100100"), ("MYS", "PSS", "SELANGOR", "6100100"),
        ("MYS", "PSS", "PERLIS", "6105100"), ("THA", "PSS", "ANDAM", "6105095"),
        ("THA", "PSS", "INOCE", "6105095"),
        *[("MDV", gear, name, grid) for grid, names in _ATOLLS.items()
          for name in names.split() for gear in ("BB", "BBM", "BBN")],
        *[("MDV", "BBOF", grid, grid) for grid in _ATOLLS]],
}


# ----------------------------------------------------------------------------- surrogates
def surrogate_lookup(table):
    """Irregular areas behind regular grid codes, from 2025 CE tables with iGrid and Grid.

    Returns ``(names, exempt, exempt_years)``; ``exempt_years`` holds (fleet, gear, grid,
    year) rows that the tables show only with numeric iGrids in a year in which no gear of
    that fleet named the cell (e.g. MDV BB atoll codes reported as real 1x1 cells from 2013).

    ``table`` has text columns Fleet, Gear, iGrid and Grid (space padding allowed). A
    non-numeric iGrid names an irregular area for which IOTC wrote the regular code in Grid.
    Returns ``(names, exempt)``: ``names`` maps (fleet, grid) to '+'-joined area names, and
    ``exempt`` holds the (fleet, gear, grid) combinations of those pairs that the tables show
    only with numeric iGrids, i.e. gears that reported the cell as a regular grid.
    """
    t = pd.DataFrame({c: table[c].astype("string").fillna("").str.strip()
                      for c in ["Fleet", "Gear", "iGrid", "Grid"]})
    t["Year"] = (table["Year"].astype("string").fillna("").str.strip() if "Year" in table
                 else pd.Series("", index=table.index, dtype="string"))
    named = t.iGrid.ne("") & ~t.iGrid.str.fullmatch(r"[0-9]+").fillna(False)
    names = (t[named].groupby(["Fleet", "Grid"]).iGrid
             .agg(lambda s: "+".join(sorted(set(s)))).to_dict())
    pairs = pd.Series([(f, g) in names for f, g in zip(t.Fleet, t.Grid)], index=t.index, dtype=bool)
    gears = t[pairs].assign(named=named[pairs]).groupby(["Fleet", "Gear", "Grid"]).named.any()
    dated = t[pairs & t.Year.ne("")].assign(named=named[pairs & t.Year.ne("")])
    named_years = set(map(tuple, dated.loc[dated.named, ["Fleet", "Grid", "Year"]].drop_duplicates().to_numpy()))
    numeric = dated.loc[~dated.named, ["Fleet", "Gear", "Grid", "Year"]].drop_duplicates()
    exempt_years = {(f, g, c, y) for f, g, c, y in numeric.to_numpy() if (f, c, y) not in named_years}
    return names, set(gears[~gears].index), exempt_years


def surrogate_table(lib, versions=None):
    """Rows (Fleet, Gear, Year, iGrid, Grid) of the 2025 releases, the records read, and qc.

    A pinned version (versions[dataset]) must be readable; only unpinned, missing datasets
    fall back to the static rows."""
    frames, qc, records = [], [], []
    for dataset in SURROGATE_DATASETS:
        check = "surrogate_source_" + dataset.rsplit("_", 1)[-1]
        pinned = (versions or {}).get(dataset)
        try:
            record = lib.record(dataset, pinned)
            sha = record["sha256"]
            members = [m for m in lib.members(dataset, version=sha) if m.lower().endswith(".csv")]
            if len(members) != 1:
                raise ValueError(f"{dataset}: expected one CSV member, found {members}")
            raw = lib.read_table(dataset, member=members[0], version=sha, dtype=str, keep_default_na=False,
                                 usecols=["Fleet", "Gear", "Year", "iGrid", "Grid"])
        except (KeyError, FileNotFoundError, ValueError) as exc:
            if pinned:
                raise
            # Missing, unsynced or changed release: the static rows reproduce the 2025 lookup.
            rows = STATIC_SURROGATE_ROWS[dataset]
            frames.append(pd.DataFrame(rows, columns=["Fleet", "Gear", "iGrid", "Grid"]))
            qc.append(dict(check=check, count=len(rows),
                           note=f"static fallback rows: {dataset} unavailable ({type(exc).__name__}: {exc})"))
            continue
        frames.append(raw[["Fleet", "Gear", "Year", "iGrid", "Grid"]])
        records.append(dict(record, dataset_id=dataset))
        qc.append(dict(check=check, count=len(raw), note=f"rows read: {dataset} sha256 {sha} member {members[0]}"))
    return pd.concat(frames, ignore_index=True), qc, records


# ----------------------------------------------------------------------------- reading
def _members(archive):
    names = [n for n in archive.namelist() if not n.endswith("/") and not Path(n).name.startswith(".")]
    found = {}
    for tag in ("CA", "EF"):
        hits = [n for n in names if f"-{tag}-" in Path(n).name and n.lower().endswith(".csv")]
        if len(hits) != 1:
            raise ValueError(f"IOTC CE layout changed: expected one -{tag}- CSV member, found {hits}")
        found[tag] = hits[0]
    return found


def _category(values):
    cat = values.astype("category")
    if not cat.cat.categories.str.strip().equals(cat.cat.categories):
        cat = values.str.strip().astype("category")
    return cat


def _period(chunk):
    numbers = [pd.to_numeric(chunk[c].str.strip(), errors="coerce") for c in ("YEAR", "MONTH_START", "MONTH_END")]
    year, start, end = numbers
    ok = year.notna() & start.notna() & end.notna()
    ok &= year.mod(1).eq(0) & start.mod(1).eq(0) & end.mod(1).eq(0)
    ok &= start.between(1, 12) & end.between(1, 12) & start.le(end)
    return [n.where(ok, 0).astype("int64") for n in numbers] + [ok]


def _purse_seine(frame):
    return frame.GEAR_CODE.isin(PS_GEARS) | frame.FISHERY_GROUP_CODE.eq("PS")


def _empty_rows(tag):
    columns = {"row": "int64", "YEAR": "int64", "MONTH_START": "int64", "MONTH_END": "int64",
               "FISHING_GROUND_CODE": "category", "FLEET_CODE": "category", "GEAR_CODE": "category",
               "FISHERY_GROUP_CODE": "category", "SCHOOL": "category"}
    columns.update({"SPECIES_CODE": "category", "CATCH_UNIT_CODE": "category", "target": "bool", "CATCH": "Float64"}
                   if tag == "CA" else {"UNIT": "category", "EFFORT": "Float64"})
    return pd.DataFrame({c: pd.Series(dtype=d) for c, d in columns.items()})


def _read(archive, member, tag, *, wanted=None, family=None):
    """Stream one member: needed columns only, valid periods, requested years and gears."""
    school = "CATCH_SCHOOL_TYPE_CODE" if tag == "CA" else "SCHOOL_TYPE_CODE"
    columns = CA_COLUMNS if tag == "CA" else EF_COLUMNS
    codes = ["FISHING_GROUND_CODE", "FLEET_CODE", "GEAR_CODE", "FISHERY_GROUP_CODE", school]
    codes += ["SPECIES_CODE", "CATCH_UNIT_CODE"] if tag == "CA" else ["EFFORT_UNIT_CODE"]
    counts = dict(read=0, invalid_time=0, outside_years=0, other_gears=0)
    parts, pairs = [_empty_rows(tag)], []
    with archive.open(member) as stream:
        try:
            reader = pd.read_csv(stream, usecols=columns, dtype=str, keep_default_na=False,
                                 encoding="utf-8-sig", chunksize=CHUNK_ROWS)
        except ValueError as exc:
            raise ValueError(f"IOTC CE schema changed in {member}: {exc}") from exc
        for chunk in reader:
            counts["read"] += len(chunk)
            for column in codes:
                chunk[column] = _category(chunk[column])
            pairs.append(chunk[["GEAR_CODE", "FISHERY_GROUP_CODE"]].drop_duplicates())
            year, start, end, keep = _period(chunk)
            counts["invalid_time"] += int((~keep).sum())
            if wanted is not None:
                inside = year.isin(wanted)
                counts["outside_years"] += int((keep & ~inside).sum())
                keep &= inside
            if family is not None:
                mine = _purse_seine(chunk) if family == "purse_seine" else ~_purse_seine(chunk)
                counts["other_gears"] += int((keep & ~mine).sum())
                keep &= mine
            frame = pd.DataFrame({"row": chunk.index.to_numpy(dtype="int64") + 2, "YEAR": year,
                                  "MONTH_START": start, "MONTH_END": end}, index=chunk.index)
            for column in ["FISHING_GROUND_CODE", "FLEET_CODE", "GEAR_CODE", "FISHERY_GROUP_CODE"]:
                frame[column] = chunk[column]
            frame["SCHOOL"] = chunk[school]
            if tag == "CA":
                frame["SPECIES_CODE"], frame["CATCH_UNIT_CODE"] = chunk.SPECIES_CODE, chunk.CATCH_UNIT_CODE
                target = keep & chunk.SPECIES_CODE.isin(SPECIES)
                frame["target"] = target
                frame["CATCH"] = pd.Series(pd.NA, index=chunk.index, dtype="Float64")
                if target.any():
                    frame.loc[target, "CATCH"] = parse_values(chunk.loc[target, "CATCH"], name=f"{member} CATCH")
            else:
                frame["UNIT"] = chunk.EFFORT_UNIT_CODE
                frame["EFFORT"] = parse_values(chunk.EFFORT, name=f"{member} EFFORT")
            parts.append(frame[keep.to_numpy()])
    return _stack(parts), pairs, counts


def _stack(frames):
    """Concatenate frames (same columns) whose categorical columns differ in categories."""
    data = {}
    for column in frames[0].columns:
        series = [f[column] for f in frames]
        if isinstance(series[0].dtype, pd.CategoricalDtype):
            parts = [s.astype(str).astype("category") if not isinstance(s.dtype, pd.CategoricalDtype) else s
                     for s in series]
            data[column] = pd.Series(union_categoricals(parts, sort_categories=True))
        else:
            data[column] = pd.concat(series, ignore_index=True)
    return pd.DataFrame(data)


def _family_gears(pairs):
    """Purse-seine gears; a gear on both sides of the rule stops the adapter."""
    if not pairs:
        return set()
    t = pd.concat([p.astype(str) for p in pairs], ignore_index=True).drop_duplicates()
    t["ps"] = _purse_seine(t)
    sides = t.groupby("GEAR_CODE").ps.nunique()
    if (sides > 1).any():
        raise ValueError(f"IOTC gears both in and out of the purse-seine family: {sorted(sides[sides > 1].index)}")
    return set(t.loc[t.ps, "GEAR_CODE"])


def _repeated_tables(ef):
    """(fleet, gear, year) whose effort table equals another year's table of that fleet-gear."""
    if ef.empty:
        return set()
    hashed = pd.util.hash_pandas_object(pd.DataFrame({
        "start": ef.MONTH_START, "end": ef.MONTH_END, "grid": ef.FISHING_GROUND_CODE.astype(str),
        "school": ef.SCHOOL.astype(str), "unit": ef.UNIT.astype(str),
        "effort": ef.EFFORT.to_numpy(dtype="float64", na_value=np.nan)}), index=False).to_numpy()
    t = pd.DataFrame({"fleet": ef.FLEET_CODE.astype(str), "gear": ef.GEAR_CODE.astype(str),
                      "year": ef.YEAR, "h": hashed}).sort_values(["fleet", "gear", "year", "h"])
    g = t.groupby(["fleet", "gear", "year"], sort=False).h
    s = pd.DataFrame({"n": g.size(), "sig": g.agg(lambda h: hashlib.sha1(h.to_numpy().tobytes()).hexdigest())})
    s = s[s.n >= MIN_REPEATED_ROWS].reset_index()
    s = s[s.duplicated(["fleet", "gear", "sig"], keep=False)]
    return set(zip(s.fleet, s.gear, s.year))


# ----------------------------------------------------------------------------- building
def _keys_frame(frame, key):
    out = pd.DataFrame({c: frame[c] for c in key})
    for c in key:
        if isinstance(out[c].dtype, pd.CategoricalDtype):
            out[c] = out[c].astype(str)
    return out


def _geometry(codes):
    """Exact rectangles per distinct code; codes outside the CWP ranges stay unlocated."""
    cells, invalid = [], set()
    for code in codes:
        try:
            cells.append(cwp_iotc(code))
        except ValueError:
            cells.append(rectangle(pd.NA, pd.NA, pd.NA, pd.NA))
            invalid.add(code)
    if not cells:
        cells = [rectangle(pd.NA, pd.NA, pd.NA, pd.NA)]
        return geometry_frame(cells, index=pd.Index([""], name="grid_raw")).iloc[:0], invalid
    return geometry_frame(cells, index=pd.Index(codes, name="grid_raw")), invalid


def _flag(values):
    """Plain numpy booleans from (nullable) comparisons; NA counts as False."""
    return pd.Series(values).astype("boolean").fillna(False).to_numpy(dtype=bool)


def _notes(index):
    notes = pd.Series("", index=index, dtype=object)

    def add(mask, token):
        mask = pd.Series(np.asarray(mask, dtype=bool), index=index)
        if mask.any():
            value = token if isinstance(token, str) else pd.Series(token, index=index)[mask]
            current = notes[mask]
            notes[mask] = np.where(current.eq(""), value, current + ";" + value)
    return notes, add


def _effort_rows(e, strata, scope_by_school, withheld=None):
    """One row per stratum x effort unit, as reported; units are never combined."""
    e = e.reset_index(drop=True)
    if e.duplicated(["sid", "UNIT"]).any():
        raise ValueError("IOTC EF has duplicate rows for one stratum and effort unit; review before aggregating")
    unit_raw = e.UNIT.astype(str)
    bad = sorted({u for u in unit_raw if not u or not all(ch.isalnum() or ch in "_. /-" for ch in u)})
    if bad:
        raise ValueError(f"Unreviewed IOTC effort unit codes: {bad}")
    value = e.EFFORT.astype("Float64")
    positive, zero, negative = _flag(value > 0), _flag(value.eq(0)), _flag(value < 0)
    out = pd.DataFrame({"sid": e.sid.astype("int64"), "unit_raw": unit_raw,
                        "effort_unit": unit_raw.map(EFFORT_UNITS).fillna("iotc:" + unit_raw),
                        "effort": value.where(positive),
                        "effort_status": np.select([positive, negative], ["reported", "invalid"], "missing"),
                        "effort_field": "EFFORT"})
    out["effort_scope"] = (np.where(e.SCHOOL.astype(str).eq(POOLED), "stratum", "school")
                           if scope_by_school else "stratum")
    out["coded_zero"] = zero
    months = (e.MONTH_END - e.MONTH_START + 1).to_numpy(dtype="float64")
    cap = unit_raw.map(IMPLAUSIBLE_PER_MONTH).to_numpy(dtype="float64")
    out["implausible"] = np.nan_to_num(value.to_numpy(dtype="float64", na_value=np.nan) - cap * months, nan=-1.0) > 0
    out["withheld"] = np.zeros(len(out), dtype=bool) if withheld is None else np.asarray(withheld, dtype=bool)
    out.loc[out.withheld, "effort"] = pd.NA
    out.loc[out.withheld, "effort_status"] = "missing"
    out = out.merge(strata[["sid", "stratum_id", "year"]], on="sid", how="left")
    return out.sort_values(["sid", "unit_raw"], kind="stable").reset_index(drop=True)


def _catch_rows(c, strata):
    """Catch per stratum x species x unit; sums over school types only within one unit."""
    columns = ["sid", "species_code", "catch_unit", "catch", "catch_status"]
    if c.empty:
        return pd.DataFrame(columns=columns), 0
    unit_raw = c.CATCH_UNIT_CODE.astype(str)
    unknown = sorted(set(unit_raw) - set(CATCH_UNITS))
    if unknown:
        raise ValueError(f"Unreviewed IOTC catch units: {unknown}")
    t = pd.DataFrame({"sid": c.sid.to_numpy(), "species_code": c.SPECIES_CODE.astype(str).to_numpy(),
                      "catch_unit": unit_raw.map(CATCH_UNITS).to_numpy(), "school": c.SCHOOL.astype(str).to_numpy(),
                      "value": c.CATCH.astype("Float64").to_numpy()})
    t["blank"] = t.value.isna().to_numpy(dtype=bool)
    if _flag(t.value < 0).any():
        raise ValueError("Negative IOTC catch values; review the release")
    g = t.groupby(["sid", "species_code", "catch_unit"], sort=True)
    a = pd.DataFrame({"catch": g.value.sum(min_count=1), "rows": g.size(), "schools": g.school.nunique(),
                      "blank": g.blank.any()}).reset_index()
    if (a.rows > a.schools).any():
        raise ValueError("IOTC CA has duplicate rows for one stratum, species and unit; review before aggregating")
    components = t.groupby(["sid", "species_code"]).school.nunique().rename("components").reset_index()
    a = a.merge(components, on=["sid", "species_code"])
    partial = (a.schools < a.components).to_numpy(dtype=bool)
    blank = a.blank.to_numpy(dtype=bool)
    a["catch_status"] = np.select([partial, blank, _flag(a["catch"] > 0)], ["unit_incomplete", "blank", "positive"],
                                  "zero")
    a["catch"] = a["catch"].astype("Float64")
    a.loc[partial | blank, "catch"] = pd.NA
    aggregated = int(a.rows.sum() - len(a))
    # A species reported in one unit only, by a group reporting it in the other unit that year.
    group = strata.set_index("sid")[["reporting_group", "year"]]
    present = a[["sid", "species_code", "catch_unit"]].join(group, on="sid")
    reports = present[["reporting_group", "year", "species_code", "catch_unit"]].drop_duplicates()
    candidates = present[["sid", "species_code", "reporting_group", "year"]].drop_duplicates().merge(
        reports, on=["reporting_group", "year", "species_code"])
    missing = candidates.merge(present[["sid", "species_code", "catch_unit"]], how="left", indicator=True,
                               on=["sid", "species_code", "catch_unit"])
    missing = missing[missing._merge.eq("left_only")][["sid", "species_code", "catch_unit"]].copy()
    missing["catch"] = pd.Series(pd.NA, index=missing.index, dtype="Float64")
    missing["catch_status"] = "unit_incomplete"
    out = (pd.concat([f[columns] for f in (a, missing) if len(f)], ignore_index=True) if len(missing)
           else a[columns].copy())
    out["catch"] = out["catch"].astype("Float64")
    return out.sort_values(["sid", "species_code", "catch_unit"], kind="stable").reset_index(drop=True), aggregated


def adapt(lib, product, *, version=None, years=None, progress=None, versions=None):
    """Canonical strata, effort and catch for one IOTC CE product (see module docstring)."""
    if product not in PRODUCTS:
        raise ValueError(f"Unknown IOTC product: {product}")
    spec = PRODUCTS[product]
    record = lib.record(spec.dataset_id, version)
    sha = record["sha256"]
    path = lib.path(spec.dataset_id, version=sha)
    say = progress or (lambda message: None)
    wanted = None if years is None else {int(y) for y in years}
    family = spec.extra["gears"]
    qc = []

    def count(check, n, note):
        qc.append(dict(check=check, count=int(n), note=note))

    with zipfile.ZipFile(path) as archive:
        members = _members(archive)
        say(f"{product}: reading {members['EF']}")
        ef_all, ef_pairs, ef_counts = _read(archive, members["EF"], "EF")
        say(f"{product}: reading {members['CA']}")
        ca, ca_pairs, ca_counts = _read(archive, members["CA"], "CA", wanted=wanted, family=family)
    ps_gears = _family_gears(ef_pairs + ca_pairs)
    repeated = _repeated_tables(ef_all)
    in_family = ef_all.GEAR_CODE.astype(str).isin(ps_gears).to_numpy(dtype=bool)
    if family != "purse_seine":
        in_family = ~in_family
    in_years = (ef_all.YEAR.isin(wanted).to_numpy(dtype=bool) if wanted is not None
                else np.ones(len(ef_all), dtype=bool))
    ef_counts["outside_years"] = int((~in_years).sum())
    ef_counts["other_gears"] = int((in_years & ~in_family).sum())
    ef = ef_all[in_years & in_family].reset_index(drop=True)
    del ef_all
    for tag, counts in [("CA", ca_counts), ("EF", ef_counts)]:
        t = tag.lower()
        count(f"{t}_rows_read", counts["read"], members[tag])
        count(f"{t}_rows_dropped_invalid_period", counts["invalid_time"], "unparseable year/months or months outside 1-12")
        count(f"{t}_rows_outside_years", counts["outside_years"], "not in the requested years")
        count(f"{t}_rows_other_products", counts["other_gears"],
              f"gears of the {'non-' if family == 'purse_seine' else ''}purse-seine products")
    count("purse_seine_gears", len(ps_gears), "purse-seine family: " + ",".join(sorted(ps_gears)))
    count("repeated_effort_tables", len(repeated),
          f"fleet-gear-years whose EF table (>= {MIN_REPEATED_ROWS} rows) equals another year's; all years checked")

    # ---- product views
    key = S7 if spec.extra["key"] == "S7" else K6
    uncl_ef = ef.SCHOOL.astype(str).eq(POOLED).to_numpy(dtype=bool)
    school_ef = pooled_k6 = None
    if product == "iotc_ce_ps_school":
        uncl_ca = ca.SCHOOL.astype(str).eq(POOLED).to_numpy(dtype=bool)
        count("ca_uncl_rows_left_to_pooled_view", uncl_ca.sum(), "school type UNCL: iotc_ce_ps_pooled only")
        count("ef_uncl_rows_left_to_pooled_view", uncl_ef.sum(), "school type UNCL: iotc_ce_ps_pooled only")
        pooled_k6 = _keys_frame(ef[uncl_ef], K6).drop_duplicates()
        ca = ca[~uncl_ca].reset_index(drop=True)
        ef = ef[~uncl_ef].reset_index(drop=True)
    elif product == "iotc_ce_ps_pooled":
        school_ef = ef[~uncl_ef].reset_index(drop=True)
        count("ef_school_rows_not_pooled", len(school_ef), "EF rows with a school type: iotc_ce_ps_school only")
        ef = ef[uncl_ef].reset_index(drop=True)

    # ---- strata: full outer join of CA (all species) and EF rows on the key
    say(f"{product}: joining {len(ca):,} CA and {len(ef):,} EF rows")
    rows = _stack([ca[key + ["row"]].assign(tag="CA"), ef[key + ["row"]].assign(tag="EF")])
    sid = rows.groupby(key, observed=True, sort=True).ngroup().to_numpy(dtype="int64")
    ca["sid"], ef["sid"] = sid[:len(ca)], sid[len(ca):]
    n = int(sid.max()) + 1 if len(sid) else 0
    first = rows.assign(sid=sid).drop_duplicates("sid").sort_values("sid")
    strata = _keys_frame(first, key).reset_index(drop=True)
    strata.insert(0, "sid", np.arange(n))
    has_ca = np.bincount(ca.sid.to_numpy(dtype="int64"), minlength=n) > 0
    has_ef = np.bincount(ef.sid.to_numpy(dtype="int64"), minlength=n) > 0
    order = np.argsort(sid, kind="stable")        # CA rows before EF rows, rows ascending
    tokens = (rows.tag.astype(str) + ":" + rows.row.astype("int64").astype(str)).to_numpy()[order]
    strata["source_rows"] = pd.Series(tokens).groupby(sid[order], sort=True).agg(";".join).reindex(range(n)).to_numpy()
    strata["source_member"] = np.select([has_ca & has_ef, has_ca], [members["CA"] + ";" + members["EF"], members["CA"]],
                                        members["EF"])
    del rows, tokens

    school = strata["SCHOOL"] if "SCHOOL" in strata else pd.Series("all", index=strata.index)
    strata["school_type"] = school
    strata["stratum_id"] = (product + "|" + strata.YEAR.astype(str) + "|" + strata.MONTH_START.astype(str) + "|"
                            + strata.MONTH_END.astype(str) + "|" + strata.FISHING_GROUND_CODE + "|" + strata.FLEET_CODE
                            + "|" + strata.GEAR_CODE + "|" + school)
    strata["source"], strata["product"], strata["view_group"] = SOURCE, product, spec.view_group
    strata["dataset_id"], strata["source_sha256"] = spec.dataset_id, sha
    strata["year"], strata["month_start"], strata["month_end"] = strata.YEAR, strata.MONTH_START, strata.MONTH_END
    strata["time_resolution"] = time_resolution(strata.month_start, strata.month_end).to_numpy()
    strata["grid_raw"] = strata.FISHING_GROUND_CODE
    geo, invalid = _geometry(pd.unique(strata.grid_raw))
    strata = strata.join(geo, on="grid_raw")
    strata["region_raw"] = REGION
    strata["fleet"], strata["gear_raw"] = strata.FLEET_CODE, strata.GEAR_CODE
    strata["gear_group"] = strata.gear_raw.map(GEARS[SOURCE]).fillna("unmapped:" + SOURCE + ":" + strata.gear_raw)
    strata["reporting_group"] = strata.fleet + "|" + strata.gear_raw
    strata["stratum_status"] = np.select([has_ca & has_ef, has_ef], ["reported", "effort_only"], "catch_only")

    # surrogate areas: fleets that named the irregular area behind a regular code
    say(f"{product}: surrogate areas")
    table, surrogate_qc, extra_records = surrogate_table(lib, versions)
    qc.extend(surrogate_qc)
    names, exempt, exempt_years = surrogate_lookup(table)
    area = pd.Series([names.get((f, g), "") for f, g in zip(strata.fleet, strata.grid_raw)], index=strata.index)
    exempted = pd.Series([(f, r, g) in exempt or (f, r, g, str(y)) in exempt_years
                          for f, r, g, y in zip(strata.fleet, strata.gear_raw, strata.grid_raw, strata.year)],
                         index=strata.index, dtype=bool)
    surrogate = area.ne("") & ~exempted & strata.geometry_status.eq("located")
    strata.loc[surrogate, "geometry_status"] = "surrogate_area"

    # ---- effort and catch
    withheld = None
    if school_ef is not None:
        partial_keys = _keys_frame(school_ef, K6).assign(UNIT=school_ef.UNIT.astype(str).to_numpy()).drop_duplicates()
        probe = _keys_frame(ef, K6).assign(UNIT=ef.UNIT.astype(str).to_numpy())
        withheld = probe.merge(partial_keys, how="left", indicator=True)._merge.eq("both").to_numpy()
    effort = _effort_rows(ef, strata, scope_by_school=key == S7, withheld=withheld)
    catch, aggregated = _catch_rows(ca[ca.target.to_numpy()], strata)
    catch = catch.merge(strata[["sid", "stratum_id", "year"]], on="sid", how="left")

    # ---- notes (flags, not deletions)
    notes, add = _notes(strata.index)
    add(strata.fleet.isin(SECRETARIAT_ESTIMATES), "secretariat_estimate")
    add(surrogate, "surrogate:" + area)
    add(strata.grid_raw.isin(invalid), "invalid_grid_code")
    by_sid = effort.groupby("sid")[["coded_zero", "implausible", "withheld"]].any().reindex(range(n), fill_value=False)
    add(by_sid.coded_zero.to_numpy(), "effort_coded_zero")
    add(by_sid.implausible.to_numpy(), "implausible_effort")
    repeated_rows = pd.Series([(f, g, y) in repeated for f, g, y in zip(strata.fleet, strata.gear_raw, strata.year)],
                              index=strata.index, dtype=bool)
    add(repeated_rows & has_ef, "repeated_effort_table")
    if pooled_k6 is not None:
        catch_only = strata.stratum_status.eq("catch_only")
        pooled = _keys_frame(strata, K6).merge(pooled_k6, how="left", indicator=True)._merge.eq("both").to_numpy()
        add(catch_only & pooled, "pooled_effort_only")
        count("pooled_effort_only_strata", (catch_only & pooled).sum(),
              "catch_only school strata whose K6 has UNCL (pooled) effort in iotc_ce_ps_pooled")
    if school_ef is not None:
        add(by_sid.withheld.to_numpy(), "pooled_effort_partial")
        uncl_units = _keys_frame(ef, K6).assign(UNIT=ef.UNIT.astype(str).to_numpy()).drop_duplicates()
        only_school = partial_keys.merge(uncl_units, how="left", indicator=True)
        only_school = only_school[only_school._merge.eq("left_only")][K6].drop_duplicates()
        located = _keys_frame(strata, K6).assign(sid=strata.sid).merge(only_school, how="inner")
        add(strata.sid.isin(located.sid), "school_effort_not_pooled")
        school_k6 = _keys_frame(school_ef, K6).drop_duplicates()
        excluded = school_k6.merge(_keys_frame(strata, K6), how="left", indicator=True)._merge.eq("left_only").sum()
        count("k6_school_effort_only_not_stratum", excluded,
              "K6 with school-specific effort but no catch and no UNCL effort (strata of iotc_ce_ps_school only)")
        count("pooled_effort_partial_rows", effort.withheld.sum(),
              "UNCL effort withheld (NA, missing): the same K6 and unit also has school-specific effort")
        count("school_effort_not_pooled_strata", len(located), "units reported only by school type")
    strata["notes"] = notes

    # ---- QC counts
    status = strata.stratum_status.value_counts()
    for value in ["reported", "effort_only", "catch_only"]:
        count(f"strata_{value}", status.get(value, 0), "stratum_status")
    count("strata_masked", status.get("masked", 0),
          "IOTC CE has no confidentiality masking tokens; a suppressed stratum would be an absent row")
    count("strata_without_schema_species", (has_ca & ~strata.sid.isin(catch.sid)).sum(),
          "catch information for other species only (no catch rows)")
    count("unlocated_strata", strata.geometry_status.eq("unlocated").sum(), "9xxxxxx or irregular grid codes")
    count("invalid_grid_code_strata", strata.grid_raw.isin(invalid).sum(), "7-digit codes outside the CWP range")
    count("surrogate_area_strata", surrogate.sum(),
          "regular codes standing for named areas: " + ",".join(sorted(set(area[surrogate]))))
    count("surrogate_exempt_strata", (area.ne("") & exempted).sum(), "gears that reported a named cell only as a grid")
    count("secretariat_estimate_strata", strata.fleet.isin(SECRETARIAT_ESTIMATES).sum(), "fleets NEICE/NEIFR")
    count("effort_rows", len(effort), "one per stratum x unit; units never summed")
    count("effort_coded_zero_rows", effort.coded_zero.sum(), "EFFORT == 0: effort_status 'missing', NA")
    count("effort_invalid_rows", effort.effort_status.eq("invalid").sum(), "negative EFFORT: 'invalid', NA")
    for unit, cap in IMPLAUSIBLE_PER_MONTH.items():
        count(f"implausible_effort_{unit}", (effort.implausible & effort.unit_raw.eq(unit)).sum(),
              f"{unit} > {cap:g} per stratum-month")
    count("repeated_effort_table_strata", (repeated_rows & has_ef).sum(), "strata in repeated fleet-gear-year EF tables")
    count("ca_rows_schema_species", ca.target.sum(), "CA rows of schema species (catch rows)")
    count("ca_rows_other_species", (~ca.target).sum(), "CA rows of other species: catch information only")
    count("catch_rows_aggregated", aggregated, "school-type rows summed into pooled rows (same species and unit)")
    count("duplicates_aggregated", 0, "EF and CA keys verified unique; duplicates stop the adapter")
    count("catch_unit_incomplete_rows", catch.catch_status.eq("unit_incomplete").sum(),
          "NA: species reported in the other unit only, or a pooled sum missing a school component")

    columns = ["stratum_id", "source", "product", "view_group", "dataset_id", "source_sha256", "source_member",
               "source_rows", "year", "month_start", "month_end", "time_resolution", "grid_raw", "west", "south",
               "grid_width", "grid_height", "lon", "lat", "geometry_status", "region_raw", "fleet", "gear_raw",
               "gear_group", "school_type", "reporting_group", "stratum_status", "notes"]
    catch["catch_field"], catch["catch_basis"] = "CATCH", spec.catch_basis
    say(f"{product}: {n:,} strata, {len(effort):,} effort rows, {len(catch):,} catch rows")
    return AdapterResult(*finalize(strata[columns], effort, catch), record=record, qc=qc, extra_records=extra_records)
