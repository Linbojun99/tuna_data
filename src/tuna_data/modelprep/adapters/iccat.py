"""ICCAT Task 2 catch and effort (T2CE) as canonical catch-effort strata.

Source: table t2ce of ICCAT's public MS Access build (dataset iccat/t2ce), read
with mdbtools through Library.read_table (NULL arrives as ''), plus its Flags
(fleet codes) and TimeStrata (period codes) tables from the same snapshot.

One stratum = one ICCAT data set (DSetID: one fleet, gear, year and file type)
x TimePeriodID x square (SquareTypeCode, QuadID, Lat, Lon) x SchoolTypeCode.

* Records are wide: effort (Eff1/Eff1Type, Eff2/Eff2Type) and one column per
  species, all in the record's CatchUnit: 'kg', 'nr' (number of fish) or '..'
  (no catch information).
* 'kg' and 'nr' records of one key form ONE stratum with catch rows in both
  units. ICCAT repeats each record of an 'nw' data set once per unit with the
  same effort, so effort comes from the kg records only (from the nr records
  when the key has no kg record); any difference is flagged
  'effort_differs_between_units'.
* Several records can share a key (single sets or trips). Effort is summed per
  effort type and catch per species within one CatchUnit, and every StrataID is
  listed in source_rows.
* CatchUnit '..' records (all species NULL) form effort-only strata
  (key + '|..'). Their effort is never merged into catch strata.
* NULL and 0 differ: a NULL species value is 'blank', 0 is 'zero'. Effort types
  '' and '-none-' always carry Eff = 0 and mean that no effort was reported: no
  effort row. A typed 0 is not documented as a real zero: 'missing' + note.
* Effort of one type is 'reported' only when every record of the stratum
  reports it with a positive value; otherwise it is 'missing' (NA) with note
  'effort_partial', so a partial sum is never divided into the full catch.
* QuadID gives the corner nearest (0, 0): 1 NE, 2 SE, 3 SW, 4 NW (t2ce-ENG.pdf,
  CATDIS readme); SquareTypeCode is 'lat x lon' (5x10 = 5 deg lat x 10 deg lon).
  Negative or out-of-range positions are unlocated ('invalid_coordinates'); cells
  far outside the Atlantic/Mediterranean are kept and flagged 'suspect_quadrant'.
* effort_scope is 'school' for FAD/FSC strata, except effort types that a data set
  repeats on its FAD and FSC records of one cell and period (PS fishing hours and
  hours at sea of most ETRO fleets): 'stratum', note
  'effort_repeated_across_school_types'.
* Other notes: file type ('not_official_report', 'working_group_estimate', ...),
  'quarter_order_assumed', 'effort_only_sibling' (a catch and an effort-only
  stratum share one key) and 'stratum_in_other_dataset' (another DSetID reports
  the same fleet, gear, period, square and school type: pick one effort source).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ...harmonize import GEARS
from ...spatial_geometry import rectangle
from ..schema import (CATCH_COLUMNS, EFFORT_COLUMNS, SPECIES, STRATA_COLUMNS, AdapterResult, ProductSpec, empty,
                      finalize, parse_values, quarter_months, time_resolution)

SOURCE = "iccat"
PRODUCT = "iccat_t2ce"
PRODUCTS = {
    PRODUCT: ProductSpec(
        product=PRODUCT, source=SOURCE, dataset_id="iccat/t2ce", member="t2ce", view_group="iccat_t2ce",
        zeros_explicit=True, catch_basis="iccat_t2ce_landings_dead_discards",
        description=("ICCAT Task 2 catch and effort (MS Access table t2ce): nominal, partly sampled catch in kg "
                     "and numbers of fish with effort, by data set, month/quarter/year, 1x1-10x20 square and "
                     "school type. Values include dead discards, not live discards."),
        extra={"lookup_tables": ("Flags", "TimeStrata"), "catch_units": {"kg": "kg", "nr": "fish"},
               "effort_only_catch_unit": ".."}),
}

# Natural key of a stratum (DSetID fixes fleet, gear, GearGrpCode, FileTypeCode and year).
KEY = ["DSetID", "FleetID", "GearCode", "YearC", "TimePeriodID", "SquareTypeCode", "QuadID", "Lat", "Lon",
       "SchoolTypeCode"]
# Schema species with a t2ce column, in schema order (t2ce has no PBF column).
T2CE_SPECIES = ("BFT", "ALB", "YFT", "BET", "SKJ", "SBF", "SWO")
SPECIES_COLUMNS = tuple(s for s in SPECIES if s in T2CE_SPECIES)
TEXT_COLUMNS = KEY + ["GearGrpCode", "FileTypeCode", "Eff1Type", "Eff2Type", "DSetTypeID", "CatchUnit"]
READ_COLUMNS = ["StrataID", "DSetID", "FleetID", "GearGrpCode", "GearCode", "FileTypeCode", "YearC", "TimePeriodID",
                "SquareTypeCode", "QuadID", "Lat", "Lon", "SchoolTypeCode", "Eff1", "Eff1Type", "Eff2", "Eff2Type",
                "DSetTypeID", "CatchUnit", *SPECIES_COLUMNS]
CATCH_UNITS = {"kg": "kg", "nr": "fish"}
EFFORT_ONLY = ".."
NO_EFFORT_TYPES = ("", "-none-")
EFFORT_UNITS = {
    "NO.HOOKS": "hooks", "NO.SETS": "sets", "SUC.SETS": "successful_sets", "D.FISH": "fishing_days",
    "D.AT SEA": "days_at_sea", "FISH.HOUR": "fishing_hours", "HOURS.SEA": "hours_at_sea", "NO.TRIPS": "trips",
}
_RAW_CODE = re.compile(r"^[A-Za-z0-9_. /-]+$")
# SquareTypeCode -> (height = degrees of latitude, width = degrees of longitude).
SQUARES = {"1x1": (1, 1), "5x5": (5, 5), "5x10": (5, 10), "10x10": (10, 10), "10x20": (10, 20)}
# TimePeriodID -> (month_start, month_end): 1-12 months, 13-16 quarters (13 = Q1 assumed), 17 year.
PERIODS = {**{m: (m, m) for m in range(1, 13)}, **{12 + q: quarter_months(q) for q in range(1, 5)}, 17: (1, 12)}
PERIOD_TYPES = {**{m: "mm" for m in range(1, 13)}, **{12 + q: "qq" for q in range(1, 5)}, 17: "yy"}
OFFICIAL_FILE_TYPE = "OF-REP"
FILE_TYPE_NOTES = {"WG-EST": "working_group_estimate", "P-SAMP": "port_sampling", "DR-ICC": "data_recovery"}
SCHOOL_TYPES = ("FAD", "FSC")
# A data set repeats an effort type across school types when FAD and FSC strata of the
# same cell and period carry identical values in >= 90% of at least 3 comparisons.
REPEAT_MIN_PAIRS, REPEAT_SHARE = 3, 0.9


def _text(values):
    """Stripped strings as an object array ('' for NULL), parsed once per distinct value."""
    codes, uniques = pd.factorize(pd.Series(values, copy=False), use_na_sentinel=True)
    clean = np.array([str(u).strip() for u in uniques] + [""], dtype=object)
    return clean[codes]


def _numbers(values, name):
    """parse_values() once per distinct value; float array with NaN for NULL."""
    codes, uniques = pd.factorize(pd.Series(values, copy=False), use_na_sentinel=True)
    parsed = parse_values(pd.Series(np.asarray(uniques, dtype=object), dtype=object), name=name)
    parsed = parsed.to_numpy(dtype="float64", na_value=np.nan)
    out = np.full(len(codes), np.nan)
    known = codes >= 0
    out[known] = parsed[codes[known]]
    return out


def _far_outside_atlantic(lon, lat):
    """Cell centre far outside the Atlantic and Mediterranean (a likely quadrant error).

    A deliberately coarse box: east of 45E or west of 100W in the north, and in
    the tropics east of 20E (Africa, Red Sea, Indian Ocean); in the south east of
    30E, west of 75W, south of 60S, or east of 20E north of 34S (Africa).
    """
    if lat >= 0:
        return lon < -100 or lon > 45 or (lat < 30 and lon > 20)
    return lon < -75 or lon > 30 or lat < -60 or (lat > -34 and lon > 20)


def t2ce_cell(square, quad, lat, lon):
    """Decode one T2CE square to (spatial_geometry rectangle dict, ';'-joined notes).

    Lat/Lon are unsigned degrees of the corner nearest (0, 0); QuadID 1 NE, 2 SE,
    3 SW, 4 NW. 'none' squares and QuadID 0 are unlocated (never placed at 0, 0).
    """
    unlocated = rectangle(pd.NA, pd.NA, pd.NA, pd.NA)
    square = str(square).strip()
    try:
        quad = float(str(quad).strip())
        lat, lon = float(str(lat).strip()), float(str(lon).strip())
    except ValueError:
        quad = lat = lon = np.nan
    if square.lower() == "none" or quad == 0:
        return unlocated, ""
    if square not in SQUARES:
        return unlocated, "unknown_square_type"
    height, width = SQUARES[square]
    if (quad not in (1, 2, 3, 4) or not (np.isfinite(lat) and np.isfinite(lon)) or lat < 0 or lon < 0
            or lat + height > 90 or lon + width > 180):
        return unlocated, "invalid_coordinates"
    south = lat if quad in (1, 4) else -(lat + height)
    west = lon if quad in (1, 2) else -(lon + width)
    cell = rectangle(west, south, width, height)
    notes = []
    if lat % height or lon % width:
        notes.append("misaligned_cell")
    if _far_outside_atlantic(cell["lon"], cell["lat"]):
        notes.append("suspect_quadrant")
    return cell, ";".join(notes)


def _effort_unit(code):
    if code in EFFORT_UNITS:
        return EFFORT_UNITS[code]
    raw = code or "UNTYPED"
    if not _RAW_CODE.match(raw):
        raise ValueError(f"Unreviewed ICCAT effort type code: {code!r}")
    return f"{SOURCE}:{raw}"


def _check_periods(periods, used):
    """Stop when ICCAT's TimeStrata table no longer matches the decoding used here."""
    if periods is None:
        return
    if {"TimeStrataID", "TimeStrata"} - set(periods.columns):
        raise ValueError("ICCAT TimeStrata table changed: expected TimeStrataID and TimeStrata")
    table = dict(zip(_numbers(periods["TimeStrataID"], "TimeStrataID"), _text(periods["TimeStrata"])))
    wrong = sorted(int(p) for p in used if p in PERIOD_TYPES and table.get(float(p)) != PERIOD_TYPES[p])
    if wrong:
        raise ValueError(f"ICCAT TimeStrata codes {wrong} no longer mean {[PERIOD_TYPES[p] for p in wrong]}")


def _fleet_codes(flags):
    if {"FleetID", "FleetCode"} - set(flags.columns):
        raise ValueError("ICCAT Flags table changed: expected FleetID and FleetCode")
    ids, codes = _text(flags["FleetID"]), _text(flags["FleetCode"])
    if len(set(ids)) != len(ids):
        raise ValueError("ICCAT Flags.FleetID must be unique")
    return {i: c for i, c in zip(ids, codes) if c}


def _join_notes(n, items):
    """';'-join flags in the given order. items: (flag, mask) or (None, array of note strings)."""
    out = np.full(n, "", dtype=object)
    for flag, mask in items:
        if flag is None:
            values = np.asarray(mask, dtype=object)
            m = values != ""
            add = values[m]
        else:
            m = np.asarray(mask, dtype=bool)
            add = flag
        if m.any():
            current = out[m]
            out[m] = np.where(current == "", add, current + ";" + add)
    return out


def _source_rows(sidx, sid, n_strata):
    """';'-joined StrataIDs per stratum, ascending (rows are already sorted by StrataID)."""
    order = np.argsort(sidx, kind="stable")
    ids = sid[order].astype(str)
    cuts = np.flatnonzero(np.diff(sidx[order])) + 1
    joined = np.array([";".join(part) for part in np.split(ids, cuts)], dtype=object)
    if len(joined) != n_strata:
        raise AssertionError("source row grouping failed")
    return joined


def _repeated_school_effort(e_s, e_unit, e_value, e_reported, s_cell, s_school, s_dset):
    """(DSetID, effort_unit) pairs whose FAD and FSC strata repeat one cell-period effort value."""
    school = s_school[e_s]
    m = e_reported & np.isin(school, SCHOOL_TYPES)
    if not m.any():
        return set()
    d = pd.DataFrame({"dset": s_dset[e_s[m]], "cell": s_cell[e_s[m]], "unit": e_unit[m], "school": school[m],
                      "value": e_value[m]})
    wide = d.set_index(["dset", "cell", "unit", "school"])["value"].unstack("school")
    if not set(SCHOOL_TYPES) <= set(wide.columns):
        return set()
    pairs = wide.dropna(subset=list(SCHOOL_TYPES))
    if pairs.empty:
        return set()
    same = pd.Series(np.isclose(pairs["FAD"].to_numpy(float), pairs["FSC"].to_numpy(float), rtol=1e-9, atol=0.0),
                     index=pairs.index)
    stats = same.groupby(level=["dset", "unit"]).agg(["size", "sum"])
    hit = stats[(stats["size"] >= REPEAT_MIN_PAIRS) & (stats["sum"] >= REPEAT_SHARE * stats["size"])]
    return set(hit.index)


def build_tables(raw, flags, periods=None, *, product=PRODUCT, sha256="", member="", years=None, progress=None):
    """Canonical (strata, effort, catch, qc) from t2ce records read as text (NULL = '').

    raw: the t2ce table with at least READ_COLUMNS; flags: the Flags table;
    periods: the TimeStrata table (None skips that check). Frames are not yet
    finalized; adapt() passes them through schema.finalize().
    """
    spec = PRODUCTS[product]
    say = progress or (lambda message: None)
    qc = []

    def add(check, count, note):
        qc.append({"check": check, "count": int(count), "note": note})

    absent = set(READ_COLUMNS) - set(raw.columns)
    if absent:
        raise ValueError(f"ICCAT t2ce schema changed; missing columns: {sorted(absent)}")
    add("rows_read", len(raw), "t2ce records read (all years)")
    year = _numbers(raw["YearC"], "YearC")
    if np.isnan(year).any() or (year % 1 != 0).any():
        raise ValueError("ICCAT YearC must be an integer in every record")
    if years is not None:
        keep = np.isin(year, np.array(sorted({int(y) for y in years}), dtype=float))
        add("rows_outside_years", (~keep).sum(), "dropped before processing: YearC outside the requested years")
        raw, year = raw.loc[keep], year[keep]
    add("species_not_in_source", sum(s not in T2CE_SPECIES for s in SPECIES),
        "schema species without a t2ce column (PBF): no catch rows")
    if not len(raw):
        return empty(STRATA_COLUMNS), empty(EFFORT_COLUMNS), empty(CATCH_COLUMNS), qc

    sid = _numbers(raw["StrataID"], "StrataID")
    if np.isnan(sid).any() or (sid % 1 != 0).any():
        raise ValueError("ICCAT StrataID must be an integer in every record")
    sid = sid.astype(np.int64)
    if len(np.unique(sid)) != len(sid):
        raise ValueError("ICCAT StrataID must be unique; no automatic deduplication")
    order = np.argsort(sid, kind="stable")
    raw = raw.iloc[order].reset_index(drop=True)
    sid, year = sid[order], year[order].astype(np.int64)
    n = len(raw)
    say(f"{product}: {n:,} records; parsing")

    text = {c: _text(raw[c]) for c in TEXT_COLUMNS}
    unit = text["CatchUnit"]
    unknown = sorted(set(unit) - {*CATCH_UNITS, EFFORT_ONLY})
    if unknown:
        raise ValueError(f"Unreviewed ICCAT CatchUnit values: {unknown}")
    is_eo, is_kg, is_nr = unit == EFFORT_ONLY, unit == "kg", unit == "nr"
    values, negative = {}, np.zeros(n, dtype=bool)
    for code in SPECIES_COLUMNS:
        v = _numbers(raw[code], code)
        negative |= v < 0
        values[code] = np.where(v < 0, np.nan, v)
    eff = {i: (text[f"Eff{i}Type"], _numbers(raw[f"Eff{i}"], f"Eff{i}")) for i in (1, 2)}
    del raw
    add("records_kg", is_kg.sum(), "CatchUnit 'kg' records (catch in kg)")
    add("records_nr", is_nr.sum(), "CatchUnit 'nr' records (catch in numbers of fish)")
    add("records_effort_only", is_eo.sum(), "CatchUnit '..' records (no catch information): effort-only strata")
    stray_catch = is_eo & np.column_stack([~np.isnan(values[c]) for c in SPECIES_COLUMNS]).any(axis=1)
    add("catch_values_without_unit", stray_catch.sum(), "'..' records with species values: ignored and flagged")
    add("negative_catch_records", negative.sum(), "records with negative species values: set to NA and flagged")

    # Strata: (natural key, catch vs effort-only) in order of their first StrataID.
    codes = pd.DataFrame({c: pd.factorize(text[c])[0] for c in KEY})
    family = codes.groupby(KEY, sort=False).ngroup().to_numpy()
    cell_family = codes.drop(columns="SchoolTypeCode").assign(eo=is_eo).groupby(
        [c for c in KEY if c != "SchoolTypeCode"] + ["eo"], sort=False).ngroup().to_numpy()
    other_dataset = codes.drop(columns="DSetID").assign(eo=is_eo).groupby(
        [c for c in KEY if c != "DSetID"] + ["eo"], sort=False).ngroup().to_numpy()
    sidx, _ = pd.factorize(family * 2 + is_eo)
    first = np.unique(sidx, return_index=True)[1]
    ns = len(first)
    s_eo = is_eo[first]
    s = {c: text[c][first] for c in TEXT_COLUMNS}
    key = s[KEY[0]].copy()
    for c in KEY[1:]:
        key = key + "|" + s[c]
    key = np.where(s_eo, key + "|" + EFFORT_ONLY, key)
    stratum_id = (product + "|") + key
    n_kg = np.bincount(sidx[is_kg], minlength=ns)
    n_nr = np.bincount(sidx[is_nr], minlength=ns)
    n_eo = np.bincount(sidx[is_eo], minlength=ns)
    has_kg, has_nr = n_kg > 0, n_nr > 0
    add("strata_catch", (~s_eo).sum(), "catch strata (kg and/or nr records of one key)")
    add("strata_effort_only", s_eo.sum(), "effort-only strata (key + '|..')")
    add("strata_kg_and_nr", (has_kg & has_nr).sum(), "kg and nr records merged into one stratum; effort taken once")
    multi = (n_kg > 1) | (n_nr > 1) | (n_eo > 1)
    add("strata_multi_record", multi.sum(), "strata summing several records of one CatchUnit (sets or trips)")
    add("records_in_multi_record_strata", multi[sidx].sum(), "records aggregated into multi-record strata")
    inconsistent = np.zeros(ns, dtype=bool)
    for c in ["GearGrpCode", "FileTypeCode"]:
        inconsistent[np.unique(sidx[text[c] != s[c][sidx]])] = True
    add("inconsistent_dataset_attributes", inconsistent.sum(),
        "strata whose records differ in GearGrpCode/FileTypeCode")

    # Effort: one row per stratum x effort unit, from the kg records (nr when no kg).
    say(f"{product}: {ns:,} strata; effort")
    parts = []
    for i, (types, v) in eff.items():
        untyped = np.isin(types, NO_EFFORT_TYPES)
        silent = untyped & (np.isnan(v) | (v == 0))
        add(f"eff{i}_untyped_zero", (untyped & (v == 0)).sum(),
            f"Eff{i}Type '' or '-none-' with Eff{i} = 0: no effort reported, no effort row")
        rows = np.flatnonzero(~silent)
        parts.append(pd.DataFrame({"rec": rows, "code": types[rows], "value": v[rows], "field": i}))
    long = pd.concat(parts, ignore_index=True)
    lookup = {c: _effort_unit(c) for c in long["code"].unique()}
    long["unit"] = long["code"].map(lookup)
    untyped_value = long["code"].isin(NO_EFFORT_TYPES).to_numpy()
    add("effort_type_missing", untyped_value.sum(), "untyped effort values kept as iccat:UNTYPED/iccat:-none-")
    twice = long.duplicated(["rec", "unit"], keep="first").to_numpy()
    add("duplicate_effort_type", twice.sum(), "Eff2 of the same type as Eff1 in one record: Eff2 ignored")
    flagged_rec = {"effort_type_missing": long["rec"].to_numpy()[untyped_value],
                   "duplicate_effort_type": long["rec"].to_numpy()[twice]}
    long = long[~twice]
    source = is_eo | is_kg | (is_nr & ~has_kg[sidx])
    n_records = np.bincount(sidx[source], minlength=ns)
    rec = long["rec"].to_numpy()
    lv = long["value"].to_numpy(float)
    frame = pd.DataFrame({"s": sidx[rec], "unit": long["unit"].to_numpy(), "src": source[rec],
                          "pos": lv > 0, "zero": lv == 0, "neg": lv < 0, "value": np.where(lv > 0, lv, 0.0), "raw": lv, "f1": long["field"].to_numpy() == 1,
                          "f2": long["field"].to_numpy() == 2})

    both = has_kg & has_nr
    mirror = frame[both[frame["s"].to_numpy()] & ~frame["src"].to_numpy()]   # nr records of kg+nr strata
    differs = np.zeros(ns, dtype=bool)
    if len(mirror):
        main = frame[both[frame["s"].to_numpy()] & frame["src"].to_numpy()]
        a = main.groupby(["s", "unit"])["raw"].agg(["sum", "count", "size"])
        b = mirror.groupby(["s", "unit"])["raw"].agg(["sum", "count", "size"])
        j = a.join(b, how="outer", lsuffix="_kg", rsuffix="_nr")
        same = (j["size_kg"].eq(j["size_nr"]) & j["count_kg"].eq(j["count_nr"])
                & np.isclose(j["sum_kg"].fillna(0).to_numpy(float), j["sum_nr"].fillna(0).to_numpy(float),
                             rtol=1e-9, atol=1e-9))
        differs[np.unique(j.index.get_level_values("s")[~same.to_numpy()])] = True
    differs &= both
    rec_differ = both & (n_kg != n_nr)
    nw_rows = (text["DSetTypeID"] == "nw") & ~is_eo
    mirror_missing = (np.bincount(sidx[nw_rows], minlength=ns) > 0) & (has_kg != has_nr) & ~s_eo
    add("effort_differs_between_units", differs.sum(),
        "kg+nr strata whose nr records carry other effort: kg effort kept")
    add("unit_records_differ", rec_differ.sum(), "kg+nr strata with different numbers of kg and nr records")
    add("unit_mirror_missing", mirror_missing.sum(), "strata of 'nw' records lacking the kg or the nr record")

    g = frame[frame["src"].to_numpy()].groupby(["s", "unit"], sort=True).agg(
        total=("value", "sum"), n_pos=("pos", "sum"), n_zero=("zero", "sum"), n_neg=("neg", "sum"),
        f1=("f1", "max"), f2=("f2", "max")).reset_index()
    e_s = g["s"].to_numpy(dtype=np.int64)
    e_unit = g["unit"].to_numpy(dtype=object)
    nrec = n_records[e_s]
    n_pos, n_zero, n_neg = g["n_pos"].to_numpy(), g["n_zero"].to_numpy(), g["n_neg"].to_numpy()
    invalid = n_neg > 0
    reported = ~invalid & (n_pos == nrec)
    coded_zero = ~invalid & (n_pos == 0) & (n_zero > 0)
    partial = ~invalid & (n_pos > 0) & (n_pos < nrec)
    value_missing = ~invalid & ~reported & ~coded_zero & ~partial
    e_status = np.where(invalid, "invalid", np.where(reported, "reported", "missing"))
    e_value = np.where(reported, g["total"].to_numpy(float), np.nan)
    e_field = np.where(g["f1"] & g["f2"], "Eff1;Eff2", np.where(g["f1"], "Eff1", "Eff2"))
    add("effort_rows", len(g), "effort rows (stratum x effort unit)")
    add("effort_invalid", invalid.sum(), "effort rows with a negative value: 'invalid', NA")
    add("effort_coded_zero", coded_zero.sum(),
        "effort rows whose typed value is 0 (not documented as a real zero): 'missing', NA")
    add("effort_partial", partial.sum(), "effort rows reported by only some records of the stratum: 'missing', NA")
    add("effort_value_missing", value_missing.sum(), "typed effort without a value: 'missing', NA")
    e_note = {name: np.bincount(e_s[m], minlength=ns) > 0 for name, m in
              [("invalid_effort", invalid), ("coded_zero_effort", coded_zero), ("effort_partial", partial),
               ("effort_value_missing", value_missing)]}
    has_reported = np.bincount(e_s[reported], minlength=ns) > 0
    has_effort_row = np.bincount(e_s, minlength=ns) > 0

    s_school = np.where(s["SchoolTypeCode"] == "n/a", "", s["SchoolTypeCode"])
    e_scope = np.where(s_school[e_s] != "", "school", "stratum").astype(object)
    repeated = _repeated_school_effort(e_s, e_unit, e_value, reported, cell_family[first], s_school, s["DSetID"])
    rep_rows = np.zeros(len(g), dtype=bool)
    if repeated:
        pairs = pd.MultiIndex.from_arrays([s["DSetID"][e_s], e_unit])
        rep_rows = pairs.isin(list(repeated)) & np.isin(s_school[e_s], SCHOOL_TYPES)
        e_scope[rep_rows] = "stratum"
    rep_strata = np.bincount(e_s[rep_rows], minlength=ns) > 0
    add("effort_repeated_across_school_types", rep_strata.sum(),
        f"FAD/FSC strata of {len(repeated)} data set x unit pairs that repeat one cell-period effort value "
        "across school types: effort_scope 'stratum'")

    # Catch: one row per stratum x species x unit, kg and fish kept apart.
    say(f"{product}: catch")
    c_parts, blank_mixed = [], np.zeros(ns, dtype=bool)
    for u_order, (code, unit_name) in enumerate(CATCH_UNITS.items()):
        m = unit == code
        if not m.any():
            continue
        grp = pd.DataFrame({c: values[c][m] for c in SPECIES_COLUMNS}).groupby(sidx[m], sort=True)
        sums, counts, sizes = grp.sum(min_count=1), grp.count(), grp.size().to_numpy()
        idx = sums.index.to_numpy()
        for sp_order, sp in enumerate(SPECIES_COLUMNS):
            cnt = counts[sp].to_numpy()
            blank = cnt < sizes
            blank_mixed[idx[blank & (cnt > 0)]] = True
            v = np.where(blank, np.nan, sums[sp].to_numpy(float))
            status = np.where(blank, "blank", np.where(v > 0, "positive", "zero"))
            c_parts.append(pd.DataFrame({"s": idx, "u": u_order, "sp": sp_order, "species_code": sp,
                                         "catch_unit": unit_name, "catch": v, "catch_status": status,
                                         "catch_field": sp}))
    if c_parts:
        catch = pd.concat(c_parts, ignore_index=True).sort_values(["s", "u", "sp"], kind="stable")
    else:
        catch = pd.DataFrame({"s": np.array([], dtype=np.int64), "species_code": [], "catch_unit": [],
                              "catch": np.array([], dtype=float), "catch_status": [], "catch_field": []})
    add("catch_blank_in_some_records", blank_mixed.sum(),
        "strata where a species is NULL in only some records: 'blank'")

    # Geometry, time, fleet and gear per stratum (each distinct square decoded once).
    say(f"{product}: geometry and notes")
    grid_raw = s["SquareTypeCode"] + ":" + s["QuadID"] + ":" + s["Lat"] + ":" + s["Lon"]
    cell = pd.factorize(grid_raw)[0]
    cell_first = np.unique(cell, return_index=True)[1]
    decoded = [t2ce_cell(s["SquareTypeCode"][i], s["QuadID"][i], s["Lat"][i], s["Lon"][i]) for i in cell_first]
    geo = pd.DataFrame([d[0] for d in decoded]).iloc[cell].reset_index(drop=True)
    geo_note = np.array([d[1] for d in decoded], dtype=object)[cell]
    for column in ["lon", "lat", "west", "south", "grid_width", "grid_height"]:
        geo[column] = pd.to_numeric(geo[column], errors="coerce").astype(float)
    no_square = (geo["geometry_status"].eq("unlocated").to_numpy() & (geo_note == ""))
    add("unlocated_no_square", no_square.sum(), "SquareTypeCode 'none' or QuadID 0: unlocated, never (0, 0)")
    for flag in ["invalid_coordinates", "unknown_square_type", "misaligned_cell", "suspect_quadrant"]:
        add(flag, pd.Series(geo_note).str.contains(flag, regex=False).sum(),
            {"invalid_coordinates": "negative or out-of-range Lat/Lon or QuadID: unlocated",
             "unknown_square_type": "unreviewed SquareTypeCode: unlocated",
             "misaligned_cell": "Lat/Lon not a multiple of the square size (kept, flagged)",
             "suspect_quadrant": "cell decodes far outside the Atlantic/Mediterranean (kept, flagged)"}[flag])
    period = _numbers(s["TimePeriodID"], "TimePeriodID")
    _check_periods(periods, {int(p) for p in np.unique(period[~np.isnan(period)]) if p % 1 == 0})
    known = np.array([p in PERIODS for p in period])
    month_start = np.array([PERIODS[p][0] if p in PERIODS else np.nan for p in period], dtype=float)
    month_end = np.array([PERIODS[p][1] if p in PERIODS else np.nan for p in period], dtype=float)
    quarter = (period >= 13) & (period <= 16)
    add("strata_quarter_period", quarter.sum(), "TimePeriodID 13-16 read as Q1-Q4 (order assumed, not documented)")
    add("strata_year_period", (period == 17).sum(), "TimePeriodID 17: whole year, never spread over months")
    add("unknown_time_period", (~known).sum(), "unreviewed TimePeriodID: months NA, 'multi_month'")
    fleets = _fleet_codes(flags)
    fleet = np.array([fleets.get(f, f) for f in s["FleetID"]], dtype=object)
    unresolved = np.array([f not in fleets for f in s["FleetID"]])
    add("fleet_code_unresolved", unresolved.sum(), "FleetID not in Flags: FleetID kept as fleet")
    gear_map = GEARS[SOURCE]
    gear_group = np.array([gear_map.get(g, f"unmapped:{SOURCE}:{g or 'MISSING'}") for g in s["GearGrpCode"]],
                          dtype=object)
    unmapped = sorted({g for g, grp_ in zip(s["GearGrpCode"], gear_group) if grp_.startswith("unmapped:")})
    add("strata_gear_unmapped", pd.Series(gear_group).str.startswith("unmapped:").sum(),
        f"GearGrpCode without a harmonize.GEARS['iccat'] group: {', '.join(unmapped) or 'none'}")
    file_type = s["FileTypeCode"]
    not_official = file_type != OFFICIAL_FILE_TYPE
    add("strata_not_official_report", not_official.sum(), f"FileTypeCode other than {OFFICIAL_FILE_TYPE}")
    add("strata_working_group_estimate", (file_type == "WG-EST").sum(),
        "FileTypeCode WG-EST (SCRS working-group estimates)")

    catch_strata = ~s_eo
    catch_only = catch_strata & ~has_reported
    no_effort = s_eo & ~has_effort_row
    fam_s = family[first]
    sibling = np.bincount(fam_s, minlength=fam_s.max() + 1)[fam_s] > 1
    other_s = other_dataset[first]
    in_other = np.bincount(other_s, minlength=other_s.max() + 1)[other_s] > 1
    rec_flags = {k: np.bincount(sidx[v], minlength=ns) > 0 for k, v in flagged_rec.items()}
    add("catch_without_effort", catch_only.sum(), "catch strata without reported effort: 'catch_only'")
    add("no_effort_reported", no_effort.sum(), "effort-only strata whose records report no effort at all (kept)")
    add("effort_only_sibling", sibling.sum(), "catch and effort-only strata sharing one natural key (both flagged)")
    add("stratum_in_other_dataset", in_other.sum(),
        "same fleet, gear, period, square and school type also reported by another DSetID: choose one effort source")
    add("strata_masked", 0, "t2ce has no confidentiality masking")
    add("surrogate_area", 0, "t2ce has no surrogate area codes ('none' squares are unlocated)")
    add("rows_dropped", 0, "no records dropped other than rows_outside_years")
    notes = _join_notes(ns, [
        ("not_official_report", not_official),
        *[(flag, file_type == code) for code, flag in FILE_TYPE_NOTES.items()],
        ("quarter_order_assumed", quarter),
        ("unknown_time_period", ~known),
        (None, geo_note),
        ("fleet_code_unresolved", unresolved),
        ("inconsistent_dataset_attributes", inconsistent),
        ("effort_differs_between_units", differs),
        ("unit_records_differ", rec_differ),
        ("unit_mirror_missing", mirror_missing),
        ("effort_repeated_across_school_types", rep_strata),
        ("effort_partial", e_note["effort_partial"]),
        ("coded_zero_effort", e_note["coded_zero_effort"]),
        ("invalid_effort", e_note["invalid_effort"]),
        ("effort_value_missing", e_note["effort_value_missing"]),
        ("effort_type_missing", rec_flags["effort_type_missing"]),
        ("duplicate_effort_type", rec_flags["duplicate_effort_type"]),
        ("catch_without_effort", catch_only),
        ("no_effort_reported", no_effort),
        ("effort_only_sibling", sibling),
        ("stratum_in_other_dataset", in_other),
        ("invalid_catch", np.bincount(sidx[negative], minlength=ns) > 0),
        ("catch_blank_in_some_records", blank_mixed),
        ("catch_values_without_unit", np.bincount(sidx[stray_catch], minlength=ns) > 0),
    ])
    s_year = year[first]
    strata = pd.DataFrame({
        "stratum_id": stratum_id, "source": SOURCE, "product": product, "view_group": spec.view_group,
        "dataset_id": spec.dataset_id, "source_sha256": sha256, "source_member": member,
        "source_rows": _source_rows(sidx, sid, ns), "year": s_year, "month_start": month_start,
        "month_end": month_end,
        "time_resolution": time_resolution(pd.Series(month_start), pd.Series(month_end)).to_numpy(dtype=object),
        "grid_raw": grid_raw,
        "west": geo["west"].to_numpy(), "south": geo["south"].to_numpy(),
        "grid_width": geo["grid_width"].to_numpy(), "grid_height": geo["grid_height"].to_numpy(),
        "lon": geo["lon"].to_numpy(), "lat": geo["lat"].to_numpy(),
        "geometry_status": geo["geometry_status"].to_numpy(dtype=object), "region_raw": "ICCAT",
        "fleet": fleet, "gear_raw": s["GearCode"], "gear_group": gear_group, "school_type": s_school,
        "reporting_group": "dset:" + s["DSetID"],
        "stratum_status": np.where(s_eo, "effort_only", np.where(has_reported, "reported", "catch_only")),
        "notes": notes,
    })[STRATA_COLUMNS]
    effort = pd.DataFrame({"stratum_id": stratum_id[e_s], "year": s_year[e_s], "effort_unit": e_unit,
                           "effort": e_value, "effort_status": e_status, "effort_field": e_field,
                           "effort_scope": e_scope})[EFFORT_COLUMNS]
    cs = catch["s"].to_numpy()
    catch = catch.assign(stratum_id=stratum_id[cs], year=s_year[cs], catch_basis=spec.catch_basis)[CATCH_COLUMNS]
    return strata, effort.reset_index(drop=True), catch.reset_index(drop=True), qc


def adapt(lib, product, *, version=None, years=None, progress=None):
    """Read one pinned iccat/t2ce snapshot and return canonical strata, effort and catch."""
    if product not in PRODUCTS:
        raise ValueError(f"Unknown ICCAT product {product}; available: {', '.join(PRODUCTS)}")
    spec = PRODUCTS[product]
    record = lib.record(spec.dataset_id, version)
    sha = record["sha256"]   # pin before listing or reading
    members = [m for m in lib.members(spec.dataset_id, version=sha) if m.lower().endswith((".mdb", ".accdb"))]
    if len(members) != 1:
        raise ValueError(f"Expected one Access database in {spec.dataset_id}, found {members}")
    member = members[0]
    say = progress or (lambda message: None)
    read = dict(member=member, version=sha, dtype=str, keep_default_na=False)
    flags = lib.read_table(spec.dataset_id, table="Flags", **read)
    periods = lib.read_table(spec.dataset_id, table="TimeStrata", **read)
    say(f"{product}: reading {member} table {spec.member} with mdbtools")
    # The table goes straight into build_tables so it can be freed while strata are built.
    strata, effort, catch, qc = build_tables(_read_t2ce(lib, spec, read), flags, periods, product=product,
                                             sha256=sha, member=member, years=years, progress=say)
    return AdapterResult(*finalize(strata, effort, catch), record=record, qc=qc)


def _read_t2ce(lib, spec, read):
    try:
        return lib.read_table(spec.dataset_id, table=spec.member, usecols=READ_COLUMNS, **read)
    except ValueError as exc:
        if "Usecols" in str(exc):
            raise ValueError(f"ICCAT t2ce schema changed: {exc}") from exc
        raise
