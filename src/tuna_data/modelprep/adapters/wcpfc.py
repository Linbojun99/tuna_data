"""WCPFC public-domain catch and effort by flag, as canonical modelprep strata.

Products
--------
wcpfc_ll       wcpfc/827   longline, flag x 5x5 degree x month: hooks (HHOOKS x 100);
                           ALB, YFT, BET and SWO in tonnes (_C) and numbers of fish (_N).
wcpfc_ps       wcpfc/1005  purse seine, flag x 1x1 degree x quarter, one stratum per set
                           type (UNA, LOG, DFAD, AFAD, OTH): sets; SKJ, YFT, BET tonnes.
wcpfc_ps_days  wcpfc/1005  the same rows pooled over set types (view group wcpfc_ps):
                           DAYS; SKJ, YFT, BET tonnes summed over set types.
wcpfc_lp       wcpfc/830   pole-and-line, flag x 1x1 degree x quarter: DAYS; SKJ, YFT,
                           BET tonnes.

Source conventions (Longline.pdf, Purse_seine.pdf and Pole_and_line.pdf ship in
each ZIP; the rules below were checked against every row of the current releases)
------------------------------------------------------------------------------
* One CSV row is one native stratum. Keys are CSV line numbers (pandas index + 2)
  and, for wcpfc_ps, the set type.
* LAT/LON give the SOUTH-WEST corner of the cell (spatial_geometry.wcpfc_cell):
  '05S' is 5S-0 and '180W' is the cell just east of the dateline. cwp_grid codes
  the same cell on the corner nearest (0, 0); it is kept as grid_raw and checked,
  but never used for geometry.
* Three-vessel rule (RAP paragraph 9): suppressed cells keep their time and area,
  but every catch and effort field is set to 0. Longline also blanks flag_code
  (two spaces); purse-seine and pole-and-line masks usually KEEP the flag. A row
  is therefore masked when all its numeric fields are 0, whatever its flag.
  Masked strata carry NA effort and catch, never zeros.
* Inside shown strata, zeros are listed explicitly (zeros_explicit=True).
* HHOOKS is hundreds of hooks. DAYS (days fishing and searching) is a total for
  the whole stratum over all set types, so it is never split or summed over set
  types. Set counts are estimates and can be fractional.
* Raising is undocumented, so catch_basis stays 'wcpfc_public_reported'.
"""
from __future__ import annotations

from dataclasses import dataclass
import time

import numpy as np
import pandas as pd

from ...harmonize import GEARS
from ...spatial_geometry import wcpfc_cell
from .. import schema
from ..schema import AdapterResult, ProductSpec

SOURCE = "wcpfc"
REGION = "WCPFC"
CATCH_BASIS = "wcpfc_public_reported"
SCHOOLS = ("UNA", "LOG", "DFAD", "AFAD", "OTH")
# Identifier fields (any case); every other column is a numeric measure, parsed strictly.
KEY_FIELDS = {"yy", "mm", "qtr", "flag_code", "lat5", "lon5", "lat1", "lon1", "cwp_grid"}
CWP_SIZE = {"5": 1.0, "6": 5.0}  # CWP grid size digit: 5 = 1x1, 6 = 5x5 degrees
TEMPERATE_LAT = 25               # purse-seine cells wholly poleward of 25 degrees
SOUTH_LIMIT = -60                # the Convention Area does not extend south of 60S
# Stratum notes (';'-joined QC flags). Flags never delete rows.
NOTES = {
    "units_disagree": "longline: tonnes > 0 with fish == 0, or the reverse, for ALB/YFT/BET/SWO; both kept as reported",
    "all_catch_zero_with_effort": "effort > 0 but every catch field 0, all species (e.g. JP longline at 35-45N, "
                                  "1952-1983): possibly unreported catch",
    "catch_without_sets": "purse-seine set type with SKJ/YFT/BET catch > 0 but SETS 0: effort missing (NA), "
                          "stratum catch_only",
    "catch_without_effort": "catch > 0 but the effort field is 0 or blank in a shown row: effort missing (NA)",
    "coded_zero_effort": "effort field 0 in a shown row without catch: effort missing (NA)",
    "invalid_effort": "negative effort: effort NA, status 'invalid'",
    "negative_catch": "a catch field is negative: written as 'blank' (NA)",
    "temperate_ps_area": "purse-seine cell wholly north of 25N (temperate Japanese purse seine)",
    "temperate_ps_area_south": "purse-seine cell wholly south of 25S (temperate purse seine off New Zealand "
                               "and Australia)",
    "low_disclosure_product": "pole-and-line product with about 98% of rows suppressed; shown strata are mostly JP",
    "implausible_location": "cell wholly south of 60S, outside the Convention Area",
    "cwp_grid_mismatch": "cwp_grid does not code the LAT/LON south-west-corner cell; geometry follows LAT/LON",
    "blank_flag_with_data": "blank flag_code (fewer than 3 vessels) but non-zero values",
    "duplicate_natural_key": "another row has the same YY, period, flag_code, LAT and LON; kept separately, "
                             "not aggregated",
}


def _spec(product, dataset_id, member, view_group, description, **extra):
    return ProductSpec(product=product, source=SOURCE, dataset_id=dataset_id, member=member,
                       view_group=view_group, zeros_explicit=True, catch_basis=CATCH_BASIS,
                       description=description, extra=extra)


_PURSE_SEINE = dict(gear="S", period="qtr", lat="LAT1", lon="LON1", size=1, species=("SKJ", "YFT", "BET"),
                    coverage_member="WCPFC_S_PUBLIC_BY_YY_QTR_FLAG_1x1_coverage.csv")
PRODUCTS = {
    "wcpfc_ll": _spec(
        "wcpfc_ll", "wcpfc/827", "WCPFC_L_PUBLIC_BY_YY_MM_FLAG.csv", "wcpfc_ll",
        "WCPFC longline by flag, 5x5 degree cells (south-west corner), year-month: hooks "
        "(HHOOKS x 100) and ALB/YFT/BET/SWO catch in tonnes and numbers of fish; "
        "three-vessel-rule suppressed strata are masked.",
        layout="row", gear="L", period="MM", lat="LAT5", lon="LON5", size=5,
        effort_field="HHOOKS", effort_unit="hooks", effort_factor=100,
        species=("ALB", "YFT", "BET", "SWO"), units=(("_C", "tonnes"), ("_N", "fish")),
        coverage_member="WCPFC_L_PUBLIC_BY_YY_MM_FLAG_Coverage.csv"),
    "wcpfc_ps": _spec(
        "wcpfc_ps", "wcpfc/1005", "WCPFC_S_PUBLIC_BY_YY_QTR_FLAG_1x1.csv", "wcpfc_ps",
        "WCPFC purse seine by flag, 1x1 degree cells, year-quarter, one stratum per set type "
        "(UNA, LOG, DFAD, AFAD, OTH): sets and SKJ/YFT/BET catch in tonnes; suppressed rows "
        "are one masked stratum each.",
        layout="school", effort_unit="sets", **_PURSE_SEINE),
    "wcpfc_ps_days": _spec(
        "wcpfc_ps_days", "wcpfc/1005", "WCPFC_S_PUBLIC_BY_YY_QTR_FLAG_1x1.csv", "wcpfc_ps",
        "Alternative view of wcpfc_ps pooled over set types: DAYS (days fishing and searching, "
        "a stratum total) and SKJ/YFT/BET tonnes summed over set types.",
        layout="pooled", effort_field="DAYS", effort_unit="days_fishing_searching", effort_factor=1,
        **_PURSE_SEINE),
    "wcpfc_lp": _spec(
        "wcpfc_lp", "wcpfc/830", "WCPFC_P_PUBLIC_BY_YY_QTR_FLAG_1x1.csv", "wcpfc_lp",
        "WCPFC pole-and-line by flag, 1x1 degree cells, year-quarter: DAYS (days fishing and "
        "searching) and SKJ/YFT/BET catch in tonnes; about 98% of rows are suppressed.",
        layout="row", gear="P", period="qtr", lat="LAT1", lon="LON1", size=1,
        effort_field="DAYS", effort_unit="days_fishing_searching", effort_factor=1,
        species=("SKJ", "YFT", "BET"), units=(("_C", "tonnes"),),
        coverage_member="WCPFC_P_PUBLIC_BY_YY_QTR_FLAG_1x1_coverage.csv",
        product_notes=("low_disclosure_product",)),
}


@dataclass
class _Rows:
    """Per source row: strata columns, numeric fields (raw units) and QC flags."""
    base: pd.DataFrame
    values: pd.DataFrame
    masked: np.ndarray
    notes: np.ndarray
    catch_columns: list


def adapt(lib, product, *, version=None, years=None, progress=None):
    """Canonical strata, effort and catch for one WCPFC product (see the module docstring).

    version pins a library snapshot (sha256; None = current); years keeps only
    those years (iterable of int) before any parsing beyond YY; progress is an
    optional callable that receives short status messages.
    """
    if product not in PRODUCTS:
        raise ValueError(f"Unknown WCPFC product {product!r}; choose from {sorted(PRODUCTS)}")
    spec = PRODUCTS[product]
    x = spec.extra
    started = time.perf_counter()
    record = lib.record(spec.dataset_id, version)  # pin the snapshot before reading anything
    sha = record["sha256"]
    _say(progress, f"{product}: reading {spec.dataset_id} {spec.member} @ {sha[:12]}")
    raw = lib.read_table(spec.dataset_id, member=spec.member, version=sha, dtype=str, keep_default_na=False)
    raw.columns = [str(c).strip() for c in raw.columns]
    raw = raw.rename(columns={_column(raw, x["period"]): x["period"]})  # MM/mm and QTR/qtr vary
    _require(raw, _required(x), spec.member)
    raw.index = pd.RangeIndex(2, len(raw) + 2)  # CSV line numbers; line 1 is the header
    qc = [_qc("rows_read", len(raw), f"data rows in {spec.member}")]
    year = _integers(raw["YY"], "YY")
    wanted = None
    if years is not None:
        wanted = sorted({int(y) for y in years})
        keep = year.isin(wanted).to_numpy(dtype=bool)
        qc.append(_qc("rows_outside_years", int((~keep).sum()),
                      f"dropped before processing; kept {len(wanted)} requested years"))
        raw, year = raw[keep], year[keep]
    qc.append(_qc("rows_processed", len(raw), "rows left after the year filter"))
    if raw.empty:
        frames = [schema.empty(columns) for columns in
                  (schema.STRATA_COLUMNS, schema.EFFORT_COLUMNS, schema.CATCH_COLUMNS)]
        return AdapterResult(*schema.finalize(*frames), record=record, qc=qc)
    rows = _rows(raw, year, spec, sha, qc)
    _say(progress, f"{product}: building strata from {len(raw)} rows")
    if x["layout"] == "school":
        strata, effort, catch = _school_strata(rows, spec, qc)
    else:
        strata, effort, catch = _row_strata(rows, spec, qc)
    qc += _coverage(lib, spec, sha, rows, wanted)
    strata, effort, catch = schema.finalize(strata, effort, catch)
    qc += [_qc("strata_rows", len(strata), ";".join(f"{k}={v}" for k, v in strata.stratum_status.value_counts().items())),
           _qc("effort_rows", len(effort), ";".join(f"{k}={v}" for k, v in effort.effort_status.value_counts().items())),
           _qc("catch_rows", len(catch), ";".join(f"{k}={v}" for k, v in catch.catch_status.value_counts().items()))]
    _say(progress, f"{product}: {len(strata)} strata in {time.perf_counter() - started:.1f} s")
    return AdapterResult(strata, effort, catch, record=record, qc=qc)


# --------------------------------------------------------------------------- rows

def _rows(raw, year, spec, sha, qc):
    x = spec.extra
    numeric = [c for c in raw.columns if c.lower() not in KEY_FIELDS]
    values = pd.DataFrame({c: schema.parse_values(raw[c], name=f"{spec.dataset_id} {spec.member} {c}")
                           for c in numeric}, index=raw.index, columns=numeric)
    effort_columns = [c for c in numeric if c in ("HHOOKS", "DAYS") or c.startswith("SETS_")]
    catch_columns = [c for c in numeric if c not in effort_columns]
    negative = values[catch_columns].lt(0).fillna(False).to_numpy(dtype=bool).any(axis=1)
    # Three-vessel rule: every catch and effort field set to 0 (flag blank or kept).
    masked = ~values.fillna(0).ne(0).to_numpy(dtype=bool).any(axis=1)
    fleet = raw["flag_code"].astype("string").str.strip().fillna("")
    blank_flag = fleet.eq("").to_numpy(dtype=bool)
    qc += [_qc("masked_rows", masked.sum(), "every numeric field 0 (three-vessel rule): masked strata, "
                                            "effort and catch NA"),
           _qc("masked_rows_blank_flag", (masked & blank_flag).sum(), "masked rows with flag_code blank"),
           _qc("masked_rows_flag_kept", (masked & ~blank_flag).sum(), "masked rows that keep their flag_code")]

    period = _integers(raw[x["period"]], x["period"])
    if x["period"] == "MM":
        if not period.between(1, 12).all():
            raise ValueError(f"Month outside 1-12 in {spec.member}")
        start = end = period
    else:
        months = {int(q): schema.quarter_months(q) for q in period.unique()}
        start = period.map(lambda q: months[int(q)][0]).astype("Int64")
        end = period.map(lambda q: months[int(q)][1]).astype("Int64")

    lat, lon, size = x["lat"], x["lon"], x["size"]
    pairs = raw[[lat, lon]].drop_duplicates()
    cells = schema.geometry_frame([wcpfc_cell(a, b, size) for a, b in zip(pairs[lat], pairs[lon])],
                                  pd.MultiIndex.from_frame(pairs))
    geo = cells.reindex(pd.MultiIndex.from_frame(raw[[lat, lon]]))
    geo.index = raw.index
    located = geo.geometry_status.eq("located").to_numpy(dtype=bool)
    south = geo.south.to_numpy(dtype=float, na_value=np.nan)
    north = south + geo.grid_height.to_numpy(dtype=float, na_value=np.nan)
    grid = raw["cwp_grid"].astype("string").str.strip().fillna("")
    triples = pd.MultiIndex.from_arrays([grid, raw[lat], raw[lon]])
    first = ~triples.duplicated()
    cwp_ok = (pd.Series(_cwp_matches(grid[first], geo[first], size), index=triples[first])
              .reindex(triples).to_numpy(dtype=bool))

    gear = x["gear"]
    base = pd.DataFrame(index=raw.index)
    base["source"] = SOURCE
    base["product"] = spec.product
    base["view_group"] = spec.view_group
    base["dataset_id"] = spec.dataset_id
    base["source_sha256"] = sha
    base["source_member"] = spec.member
    base["source_rows"] = raw.index.astype(str)
    base["year"] = year
    base["month_start"], base["month_end"] = start, end
    base["time_resolution"] = schema.time_resolution(start, end)
    base["grid_raw"] = grid
    for column in ["west", "south", "grid_width", "grid_height", "lon", "lat", "geometry_status"]:
        base[column] = geo[column]
    base["region_raw"] = REGION
    base["fleet"] = fleet
    base["gear_raw"] = gear
    base["gear_group"] = GEARS[SOURCE].get(gear, f"unmapped:{SOURCE}:{gear}")
    base["reporting_group"] = fleet + "|" + gear

    effort_positive = values[effort_columns].fillna(0).gt(0).to_numpy(dtype=bool).any(axis=1)
    catch_zero = values[catch_columns].eq(0).fillna(False).to_numpy(dtype=bool).all(axis=1)
    key = ["YY", x["period"], "flag_code", lat, lon]
    duplicated = raw.duplicated(key, keep=False).to_numpy(dtype=bool)
    flags = [
        (duplicated, "duplicate_natural_key"),
        (blank_flag & ~masked, "blank_flag_with_data"),
        (located & ~cwp_ok, "cwp_grid_mismatch"),
        (np.nan_to_num(north, nan=np.inf) <= SOUTH_LIMIT, "implausible_location"),
        (~masked & effort_positive & catch_zero, "all_catch_zero_with_effort"),
        (negative, "negative_catch"),
    ]
    units = dict(x.get("units", ()))
    if "_C" in units and "_N" in units:
        disagree = np.zeros(len(raw), dtype=bool)
        for code in x["species"]:
            tonnes, fish = values[code + "_C"], values[code + "_N"]
            d = ((tonnes.gt(0) & fish.eq(0)) | (fish.gt(0) & tonnes.eq(0))).fillna(False).to_numpy(dtype=bool) & ~masked
            qc.append(_qc(f"units_disagree_{code}", d.sum(), f"{code}_C > 0 with {code}_N == 0 or the reverse; "
                                                             "both kept as reported"))
            disagree |= d
        flags.append((disagree, "units_disagree"))
    if gear == "S":
        flags.append((np.nan_to_num(south, nan=-np.inf) >= TEMPERATE_LAT, "temperate_ps_area"))
        flags.append((np.nan_to_num(north, nan=np.inf) <= -TEMPERATE_LAT, "temperate_ps_area_south"))
    for flag in x.get("product_notes", ()):
        flags.append((np.ones(len(raw), dtype=bool), flag))   # product-level flag on every stratum
    notes = np.full(len(raw), "", dtype=object)
    for mask, flag in flags:
        notes = _flag(notes, mask, flag)
        qc.append(_qc(flag, mask.sum(), "source rows flagged: " + NOTES[flag]))
    qc += [_qc("unlocated_rows", (~located).sum(), "no usable LAT/LON; never placed at (0, 0)"),
           _qc("surrogate_area_rows", 0, "WCPFC uses regular 1x1 and 5x5 cells only"),
           _qc("coded_zero_effort_masked", (masked & values[effort_columns].eq(0).fillna(False)
                                            .to_numpy(dtype=bool).all(axis=1)).sum(),
               "effort coded 0 by the three-vessel rule: masked, effort NA (not zero)")]
    return _Rows(base, values, masked, notes, catch_columns)


def _cwp_matches(grid, geo, size):
    """True where cwp_grid (corner nearest (0, 0) + quadrant) codes the LAT/LON cell."""
    parts = grid.str.extract(r"^(\d)([1-4])(\d\d)(\d\d)(\d{3})(\d\d)$")
    cwp_size = parts[0].map(CWP_SIZE).astype(float).to_numpy()
    quadrant = pd.to_numeric(parts[1]).to_numpy(dtype=float)
    lat = pd.to_numeric(parts[2]).to_numpy(dtype=float)
    lon = pd.to_numeric(parts[4]).to_numpy(dtype=float)
    south = np.where(np.isin(quadrant, [1, 4]), lat, -lat - size)
    west = (np.where(np.isin(quadrant, [1, 2]), lon, -lon - size) + 180) % 360 - 180
    minutes = (parts[3].eq("00") & parts[5].eq("00")).fillna(False).to_numpy(dtype=bool)
    return (minutes & (cwp_size == size)
            & (south == geo.south.to_numpy(dtype=float, na_value=np.nan))
            & (west == geo.west.to_numpy(dtype=float, na_value=np.nan)))


# ----------------------------------------------------------------------- products

def _row_strata(r, spec, qc):
    """wcpfc_ll, wcpfc_lp and wcpfc_ps_days: one stratum per source row."""
    x = spec.extra
    pooled = x["layout"] == "pooled"
    strata = r.base.copy()
    strata["stratum_id"] = spec.product + "|" + strata["source_rows"]
    strata["school_type"] = "all" if pooled else ""
    raw_effort = r.values[x["effort_field"]]
    status = _effort_status(raw_effort, r.masked)
    value = (raw_effort * x["effort_factor"]).round(6).to_numpy(dtype=float, na_value=np.nan)
    value = np.where(status == "reported", value, np.nan)

    catch_parts, fields_by_catch = [], []
    for code in x["species"]:
        if pooled:
            fields = [f"{code}_C_{school}" for school in SCHOOLS]
            parts = r.values[fields]
            # Same unit, so summing set types is legitimate; any blank or negative part leaves NA.
            amount = parts.where(parts.ge(0).fillna(True)).sum(axis=1, min_count=len(fields)).round(6)
            fields_by_catch.append((code, "tonnes", "+".join(fields), amount))
        else:
            for suffix, unit in x["units"]:
                fields_by_catch.append((code, unit, code + suffix, r.values[code + suffix]))
    no_info = ~r.masked & np.column_stack([v.isna().to_numpy(dtype=bool) for *_, v in fields_by_catch]).all(axis=1)
    any_catch = r.values[r.catch_columns].fillna(0).gt(0).to_numpy(dtype=bool).any(axis=1)
    notes = r.notes
    notes = _flag(notes, ~r.masked & (status == "missing") & any_catch, "catch_without_effort")
    notes = _flag(notes, ~r.masked & (status == "missing") & ~any_catch
                  & raw_effort.eq(0).fillna(False).to_numpy(dtype=bool), "coded_zero_effort")
    notes = _flag(notes, status == "invalid", "invalid_effort")
    qc += [_qc("catch_without_effort", (~r.masked & (status == "missing") & any_catch).sum(),
               f"{x['effort_field']}: " + NOTES["catch_without_effort"]),
           _qc("invalid_effort", (status == "invalid").sum(), f"{x['effort_field']}: " + NOTES["invalid_effort"]),
           _qc("rows_dropped", 0, "every processed source row is one stratum")]
    strata["stratum_status"] = np.select([r.masked, status != "reported", no_info],
                                         ["masked", "catch_only", "effort_only"], "reported")
    strata["notes"] = notes
    ids = strata["stratum_id"].to_numpy()
    year = strata["year"].to_numpy()
    effort = pd.DataFrame({"stratum_id": ids, "year": year, "effort_unit": x["effort_unit"], "effort": value,
                           "effort_status": status, "effort_field": x["effort_field"], "effort_scope": "stratum"})
    for code, unit, field, amount in fields_by_catch:
        catch_parts.append(_catch_frame(ids, year, code, unit, field, amount, r.masked, no_info, spec.catch_basis))
    return strata.reset_index(drop=True), effort, pd.concat(catch_parts, ignore_index=True)


def _school_strata(r, spec, qc):
    """wcpfc_ps: masked rows stay whole; shown rows melt into set-type strata."""
    x = spec.extra
    species = x["species"]
    masked = r.masked
    parts, efforts, catches = [], [], []

    m = r.base[masked].copy()
    m["stratum_id"] = spec.product + "|" + m["source_rows"]
    m["school_type"] = ""
    m["stratum_status"] = "masked"
    m["notes"] = r.notes[masked]
    m["_order"] = m.index.to_numpy() * 10
    parts.append(m)
    ids, year = m["stratum_id"].to_numpy(), m["year"].to_numpy()
    efforts.append(pd.DataFrame({"stratum_id": ids, "year": year, "effort_unit": x["effort_unit"], "effort": np.nan,
                                 "effort_status": "masked", "effort_field": "+".join(f"SETS_{s}" for s in SCHOOLS),
                                 "effort_scope": "stratum"}))
    for code in species:
        field = "+".join(f"{code}_C_{s}" for s in SCHOOLS)
        catches.append(pd.DataFrame({"stratum_id": ids, "year": year, "species_code": code, "catch_unit": "tonnes",
                                     "catch": np.nan, "catch_status": "masked", "catch_field": field,
                                     "catch_basis": spec.catch_basis}))

    unused = without_sets = other_only = invalid = 0
    covered = masked.copy()
    by_school = []
    for i, school in enumerate(SCHOOLS, start=1):
        sets = r.values[f"SETS_{school}"]
        fields = [f"{code}_C_{school}" for code in species]
        tuna = r.values[fields]
        positive = tuna.gt(0).fillna(False).to_numpy(dtype=bool).any(axis=1)
        has_sets = sets.ne(0).fillna(False).to_numpy(dtype=bool)
        emit = ~masked & (has_sets | positive)
        covered |= emit
        unused += (~masked & ~emit).sum()
        other = f"OTH_C_{school}"
        if other in r.values:
            other_only += (~masked & ~emit & r.values[other].gt(0).fillna(False).to_numpy(dtype=bool)).sum()
        s = r.base[emit].copy()
        s["stratum_id"] = spec.product + "|" + s["source_rows"] + "|" + school
        s["school_type"] = school
        status = _effort_status(sets[emit], np.zeros(emit.sum(), dtype=bool))
        no_catch = tuna[emit].isna().to_numpy(dtype=bool).all(axis=1)
        s["stratum_status"] = np.select([status != "reported", no_catch], ["catch_only", "effort_only"], "reported")
        missing = (status == "missing") & positive[emit]
        notes = _flag(r.notes[emit], missing, "catch_without_sets")
        s["notes"] = _flag(notes, status == "invalid", "invalid_effort")
        s["_order"] = s.index.to_numpy() * 10 + i
        without_sets += missing.sum()
        invalid += (status == "invalid").sum()
        by_school.append(f"{school}={emit.sum()} ({missing.sum()} without sets)")
        parts.append(s)
        ids, year = s["stratum_id"].to_numpy(), s["year"].to_numpy()
        value = np.where(status == "reported", sets[emit].to_numpy(dtype=float, na_value=np.nan), np.nan)
        efforts.append(pd.DataFrame({"stratum_id": ids, "year": year, "effort_unit": x["effort_unit"], "effort": value,
                                     "effort_status": status, "effort_field": f"SETS_{school}",
                                     "effort_scope": "school"}))
        for code, field in zip(species, fields):
            catches.append(_catch_frame(ids, year, code, "tonnes", field, r.values.loc[emit, field],
                                        np.zeros(emit.sum(), dtype=bool), no_catch, spec.catch_basis))
    qc += [_qc("school_strata", sum(len(p) for p in parts[1:]), "; ".join(by_school)),
           _qc("school_slots_not_emitted", unused, "set type with SETS 0 and no SKJ/YFT/BET catch in a shown row: "
                                                  "set type not used, not a zero-catch stratum"),
           _qc("school_slots_other_species_only", other_only, "not emitted: no sets and no SKJ/YFT/BET catch, "
                                                              "but OTH_C > 0"),
           _qc("catch_without_sets", without_sets, NOTES["catch_without_sets"]),
           _qc("invalid_effort", invalid, "SETS: " + NOTES["invalid_effort"]),
           _qc("rows_dropped", (~covered).sum(), "shown source rows without any set-type stratum (no sets and "
                                                 "no SKJ/YFT/BET catch in any set type)")]
    strata = pd.concat(parts).sort_values("_order", kind="stable")
    order = pd.Series(np.arange(len(strata)), index=strata["stratum_id"].to_numpy())
    effort = _ordered(pd.concat(efforts, ignore_index=True), order)
    catch = _ordered(pd.concat(catches, ignore_index=True), order)
    return strata.drop(columns="_order").reset_index(drop=True), effort, catch


# ------------------------------------------------------------------------ helpers

def _effort_status(value, masked):
    """'reported' > 0; 0 and blank are 'missing' (a WCPFC 0 is suppression, not zero)."""
    status = np.full(len(value), "missing", dtype=object)
    status[value.gt(0).fillna(False).to_numpy(dtype=bool)] = "reported"
    status[value.lt(0).fillna(False).to_numpy(dtype=bool)] = "invalid"
    status[np.asarray(masked, dtype=bool)] = "masked"
    return status


def _catch_frame(ids, year, code, unit, field, amount, masked, no_info, basis):
    v = pd.Series(amount).astype("Float64")
    unknown = v.isna().to_numpy(dtype=bool) | v.lt(0).fillna(False).to_numpy(dtype=bool)
    conditions = [np.asarray(masked, dtype=bool), np.asarray(no_info, dtype=bool), unknown,
                  v.gt(0).fillna(False).to_numpy(dtype=bool), v.eq(0).fillna(False).to_numpy(dtype=bool)]
    status = np.select(conditions, ["masked", "no_catch_info", "blank", "positive", "zero"], "")
    if (status == "").any():
        raise ValueError(f"Unclassified {field} values")
    value = np.where(np.isin(status, ["positive", "zero"]), v.to_numpy(dtype=float, na_value=np.nan), np.nan)
    return pd.DataFrame({"stratum_id": ids, "year": year, "species_code": code, "catch_unit": unit,
                         "catch": value, "catch_status": status, "catch_field": field, "catch_basis": basis})


def _ordered(table, order):
    position = table["stratum_id"].map(order).to_numpy()
    return table.iloc[np.argsort(position, kind="stable")].reset_index(drop=True)


def _flag(notes, mask, flag):
    notes = np.asarray(notes, dtype=object).copy()
    mask = np.asarray(mask, dtype=bool)
    if mask.any():
        current = notes[mask]
        notes[mask] = np.where(current == "", flag, current + ";" + flag)
    return notes


def _coverage(lib, spec, sha, r, wanted):
    """Check shown effort and strata per year against the publisher's coverage table."""
    member = spec.extra.get("coverage_member")
    unit, field = ("hks", "HHOOKS") if spec.extra["gear"] == "L" else ("days", "DAYS")
    try:
        cov = lib.read_table(spec.dataset_id, member=member, version=sha, dtype=str, keep_default_na=False)
        cov.columns = [str(c).strip().lower() for c in cov.columns]
        yy = _integers(cov["yy"], "coverage yy").to_numpy()
        shown_all = [schema.parse_values(cov[c], name=f"{member} {c}").fillna(0).to_numpy(dtype=float)
                     for c in [f"{unit}_reported", f"{unit}_all", "strata_reported", "strata_all"]]
    except (KeyError, ValueError, FileNotFoundError) as exc:
        return [_qc("coverage_check_skipped", 0, f"{member}: {exc}")]
    reported, total, strata_reported, strata_all = shown_all
    shown = ~r.masked
    year = r.base["year"].to_numpy(dtype=int)
    data_years = sorted(set(year))
    effort = pd.Series(r.values[field].to_numpy(dtype=float, na_value=0.0)[shown]).groupby(year[shown]).sum()
    counts = pd.Series(np.ones(shown.sum())).groupby(year[shown]).sum()
    in_data = np.isin(yy, data_years)
    effort_bad, strata_bad = [], []
    for y in data_years:
        hit = np.flatnonzero(yy == y)
        if len(hit) != 1 or not np.isclose(effort.get(y, 0.0), reported[hit[0]], rtol=1e-9, atol=0.011):
            effort_bad.append(y)
        if len(hit) != 1 or counts.get(y, 0) != strata_reported[hit[0]]:
            strata_bad.append(y)
    outside = ~in_data if wanted is None else (~in_data & np.isin(yy, wanted))
    share = 100 * reported[in_data].sum() / total[in_data].sum() if total[in_data].sum() else float("nan")
    return [
        _qc("coverage_years_checked", len(data_years), f"{member}: shown {field} and shown rows per year"),
        _qc("coverage_effort_mismatch_years", len(effort_bad),
            f"years where shown {field} differs from {unit}_reported {effort_bad[:10]}"),
        _qc("coverage_strata_mismatch_years", len(strata_bad),
            f"years where shown rows differ from strata_reported {strata_bad[:10]}"),
        _qc("coverage_suppressed_strata", int(round((strata_all - strata_reported)[in_data].sum())),
            "strata_all - strata_reported over checked years; suppressed flags can share one masked row, "
            "so masked_rows is not a census"),
        _qc("coverage_effort_shown", int(round(reported[in_data].sum())),
            f"{field} in shown strata = {share:.2f}% of {unit}_all over checked years"),
        _qc("coverage_years_without_rows", outside.sum(),
            f"coverage years with no data rows {sorted(int(y) for y in yy[outside])[:10]} (not zero years)"),
    ]


def _required(x):
    columns = ["YY", x["period"], "flag_code", x["lat"], x["lon"], "cwp_grid"]
    if x["layout"] in ("school", "pooled"):
        columns += ["DAYS"] + [f"SETS_{s}" for s in SCHOOLS]
        columns += [f"{code}_C_{s}" for code in x["species"] for s in SCHOOLS]
    else:
        columns += [x["effort_field"]] + [code + suffix for code in x["species"] for suffix, _ in x["units"]]
    return columns


def _column(raw, name):
    found = [c for c in raw.columns if c.lower() == name.lower()]
    return found[0] if len(found) == 1 else name


def _require(raw, columns, member):
    absent = [c for c in columns if c not in raw.columns]
    if absent:
        raise ValueError(f"WCPFC {member} schema changed; missing columns: {absent}")


def _integers(values, name):
    numbers = schema.parse_values(values, name=name)
    if numbers.isna().any() or (numbers % 1 != 0).any():
        raise ValueError(f"Missing or non-integral {name}")
    return numbers.astype("Int64")


def _qc(check, count, note=""):
    return {"check": check, "count": int(count), "note": note}


def _say(progress, message):
    if callable(progress):
        progress(message)
