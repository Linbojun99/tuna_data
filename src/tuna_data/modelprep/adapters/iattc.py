"""IATTC public-domain catch and effort as canonical modelprep strata.

All products are EPO, monthly, on IATTC's documented cell centres, with retained
catch as submitted by flags (IATTC does no unit conversion).

* ``iattc_ll``: industrial longline by year, month, flag and 5x5 cell.
  PublicLLTunaBillfishNum.csv (number-preferred) and PublicLLTunaBillfishMt.csv
  (weight-preferred) are row-aligned views of the SAME strata, so each stratum is
  built from one Num/Mt row pair and is never stacked. Where a flag reported a
  catch in both units, the Num view keeps the numbers (its ``<sp>mt`` then holds
  only weight-only catch) and the Mt view keeps the weight (its ``<sp>n`` then
  holds only number-only catch). Fish (Num ``<sp>n``) are therefore complete only
  when Num ``<sp>mt`` == 0, and tonnes (Mt ``<sp>mt``) only when Mt ``<sp>n`` == 0;
  otherwise the value is 'unit_incomplete'.
  KOR 1987-2001 rows are identical in both views and carry the same fish in both
  units (inferred from mean weights and annual totals; not confirmed by IATTC).
  Where a species has n > 0 and mt > 0 there, both units are complete. A species
  reported in one unit only in those rows keeps the general rule: next to 124
  fish, 0.00 t means the weight was not reported, not zero tonnes.
  Hooks == 0 is missing effort, never a zero.
* ``iattc_ps_settype`` / ``iattc_ps_flag``: purse seine by year, month, set type
  or flag and 1x1 cell. They are two partitions of the same sets (view_group
  'iattc_ps'): never combine them.
* ``iattc_lp``: pole and line by year, month, flag and 1x1 cell, 1978-2015.
  Read from iattc/publiclptunaflag only; iattc/publiclptuna has the same bytes.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ...harmonize import GEARS
from ...spatial_geometry import centered_cell, rectangle
from .. import schema
from ..schema import AdapterResult, ProductSpec

SOURCE = "iattc"
REGION = "EPO"
CATCH_BASIS = "iattc_flag_reported_retained"
NUM_VIEW = "PublicLLTunaBillfishNum.csv"   # dual-unit catch kept in <sp>n
MT_VIEW = "PublicLLTunaBillfishMt.csv"     # dual-unit catch kept in <sp>mt
KOR_UNSPLIT = ("KOR", 1987, 2001)          # same fish in n and mt, not split between views
SUSPECT_EFFORT = (("KOR", 2023), ("CHN", 2023))
HIGH_FISH_PER_HOOK = 0.2                   # all species, Num view
# IATTC Convention Area: American coast to 150W, between 50N and 50S.
CONVENTION_AREA = {"west": -150.0, "south": -50.0, "north": 50.0}
_LL_CATCH = re.compile(r"([A-Z]{3})(n|mt)")
_SET_CATCH = re.compile(r"[A-Z]{3}")
_EPS = 1e-6

NOTES = {
    "hooks_coded_zero": "Hooks == 0: missing effort coded as 0 (effort NA, status 'missing')",
    "sets_coded_zero": "NumSets == 0: missing effort coded as 0 (effort NA, status 'missing')",
    "invalid_effort": "negative effort (effort NA, status 'invalid')",
    "catch_without_effort": "positive catch but no usable effort",
    "suspect_effort": "flag-year with implausibly low total hooks (KOR 2023, CHN 2023)",
    "high_catch_per_hook": f"more than {HIGH_FISH_PER_HOOK} fish per hook, all species (Num view)",
    "kor_dual_unit_unsplit": "KOR 1987-2001: same fish reported in both units, views identical",
    "negative_catch": "negative catch value, written as 'blank' (NA)",
    "pooled_flag_other": "'Other' pools flags with 3 or fewer vessels or companies; membership changes",
    "historical_only": "IATTC received no pole-and-line data after 2015",
    "off_grid_centre": "cell centre not on the product's regular grid",
    "outside_convention_area": "cell extends beyond 150W, 50N or 50S",
    "invalid_coordinates": "coordinates outside valid ranges; stratum left unlocated",
    "duplicate_natural_key": "another row has the same natural key; kept separately, not aggregated",
}


def _spec(product, dataset_id, member, view_group, description, **extra):
    return ProductSpec(product=product, source=SOURCE, dataset_id=dataset_id, member=member,
                       view_group=view_group, zeros_explicit=True, catch_basis=CATCH_BASIS,
                       description=description, extra=extra)


PRODUCTS = {
    "iattc_ll": _spec(
        "iattc_ll", "iattc/publiclltunabillfish", f"{NUM_VIEW};{MT_VIEW}", "iattc_ll",
        "EPO industrial longline by year, month, flag and 5x5 cell: hooks, plus retained catch in fish "
        "(number-preferred view) and tonnes (weight-preferred view). One stratum per row-aligned Num/Mt pair.",
        members={"fish": NUM_VIEW, "tonnes": MT_VIEW}, gear_raw="LL", grid_size=5,
        lat="LatC5", lon="LonC5", group="Flag", effort_field="Hooks", effort_unit="hooks",
        effort_scope="stratum",
        complete_when={"fish": "Num-view <sp>mt == 0", "tonnes": "Mt-view <sp>n == 0",
                       "both": "KOR 1987-2001 species with n > 0 and mt > 0, identical in both views"},
        kor_unsplit=KOR_UNSPLIT, suspect_effort=SUSPECT_EFFORT, high_fish_per_hook=HIGH_FISH_PER_HOOK),
    "iattc_ps_settype": _spec(
        "iattc_ps_settype", "iattc/publicpstuna", "PublicPSTunaSetType.csv", "iattc_ps",
        "EPO purse seine by year, month, set type (DEL dolphin, NOA unassociated, OBJ floating object; "
        "flags pooled) and 1x1 cell: sets and retained tonnes. Same sets as iattc_ps_flag; never combine.",
        gear_raw="PS", grid_size=1, lat="LatC1", lon="LonC1", group="SetType",
        effort_field="NumSets", effort_unit="sets", effort_scope="school"),
    "iattc_ps_flag": _spec(
        "iattc_ps_flag", "iattc/publicpstuna", "PublicPSTunaFlag.csv", "iattc_ps",
        "EPO purse seine by year, month, flag (flags with 3 or fewer vessels or companies pooled as "
        "'Other'; set types pooled) and 1x1 cell: sets and retained tonnes. Same sets as iattc_ps_settype.",
        gear_raw="PS", grid_size=1, lat="LatC1", lon="LonC1", group="Flag",
        effort_field="NumSets", effort_unit="sets", effort_scope="stratum"),
    "iattc_lp": _spec(
        "iattc_lp", "iattc/publiclptunaflag", "PublicLPTunaFlag.csv", "iattc_lp",
        "EPO pole and line by year, month, flag and 1x1 cell, 1978-2015 (no pole-and-line data received "
        "after 2015): sets and retained tonnes. iattc/publiclptuna is the same file; do not add it.",
        gear_raw="LP", grid_size=1, lat="LatC1", lon="LonC1", group="Flag",
        effort_field="NumSets", effort_unit="sets", effort_scope="stratum", notes=("historical_only",)),
}


def adapt(lib, product, *, version=None, years=None, progress=None):
    """Canonical strata, effort and catch for one IATTC product (see module docstring)."""
    if product not in PRODUCTS:
        raise ValueError(f"Unknown IATTC product {product!r}; choose from {sorted(PRODUCTS)}")
    spec = PRODUCTS[product]
    record = lib.record(spec.dataset_id, version)  # pin the snapshot before any read
    wanted = None if years is None else {int(y) for y in years}
    build = _longline if product == "iattc_ll" else _sets
    strata, effort, catch, qc = build(lib, spec, record["sha256"], wanted, progress or (lambda message: None))
    strata, effort, catch = schema.finalize(strata, effort, catch)
    if progress:
        progress(f"{product}: {len(strata):,} strata, {len(effort):,} effort and {len(catch):,} catch rows")
    return AdapterResult(strata, effort, catch, record=record, qc=qc)


# ---------------------------------------------------------------- reading

def _qc(check, count, note=""):
    return {"check": check, "count": int(count), "note": note}


def _read(lib, spec, member, sha, progress):
    progress(f"{spec.product}: reading {spec.dataset_id} {member} @ {sha[:12]}")
    for attempt in range(3):
        try:
            raw = lib.read_table(spec.dataset_id, member=member, version=sha, dtype=str, keep_default_na=False)
            break
        except FileNotFoundError:
            raise
        except OSError:  # transient cloud-mount read error; the checksum is verified again on retry
            if attempt == 2:
                raise
    raw.columns = [str(c).strip() for c in raw.columns]
    return raw.reset_index(drop=True)


def _require(raw, columns, member):
    missing = [c for c in columns if c not in raw.columns]
    if missing:
        raise ValueError(f"IATTC schema changed in {member}: missing {missing}")


def _values(raw, column, member):
    """Strictly parsed float64 values (NaN for blanks); unknown tokens raise."""
    return schema.parse_values(raw[column], name=f"{member}:{column}").to_numpy("float64", na_value=np.nan)


def _select(raw, years, member, qc):
    """Keep rows with a valid year and month (and in `years`), before parsing anything else."""
    year, month = _values(raw, "Year", member), _values(raw, "Month", member)
    with np.errstate(invalid="ignore"):
        valid = (year % 1 == 0) & (month % 1 == 0) & (month >= 1) & (month <= 12)
    raw = raw.assign(_line=np.arange(2, len(raw) + 2))  # CSV line: header is line 1
    qc.append(_qc("rows_dropped_invalid_time", (~valid).sum(),
                  f"{member}: missing or non-integral Year, or Month outside 1-12"))
    keep = valid
    if years is not None:
        inside = np.isin(year, sorted(years))
        qc.append(_qc("rows_outside_years", (valid & ~inside).sum(), f"{member}: excluded by the years filter"))
        keep = valid & inside
    out = raw.loc[keep].reset_index(drop=True)
    out["_year"], out["_month"] = year[keep].astype("int64"), month[keep].astype("int64")
    return out


def _text(raw, column):
    return raw[column].astype("string").str.strip().fillna("").to_numpy(object)


def _same(a, b):
    """Element-wise equality that treats two blanks (NaN) as equal."""
    return (a == b) | (np.isnan(a) & np.isnan(b))


# ---------------------------------------------------------------- products

def _longline(lib, spec, sha, years, progress):
    cfg, qc, views = spec.extra, [], {}
    for unit, member in cfg["members"].items():
        raw = _read(lib, spec, member, sha, progress)
        _require(raw, ["Year", "Month", cfg["group"], cfg["lat"], cfg["lon"], cfg["effort_field"]], member)
        qc.append(_qc("rows_read", len(raw), member))
        views[unit] = _select(raw, years, member, qc)
    num, mt, how = _align(views["fish"], views["tonnes"], cfg)
    qc.append(_qc("views_paired", len(num), f"Num/Mt row pairs ({how})"))

    hooks = _values(num, cfg["effort_field"], NUM_VIEW)
    if not np.array_equal(hooks, _values(mt, cfg["effort_field"], MT_VIEW), equal_nan=True):
        raise ValueError("IATTC LL Num and Mt views disagree on Hooks; review before use")
    columns = {c for c in num.columns if _LL_CATCH.fullmatch(c)}
    if columns != {c for c in mt.columns if _LL_CATCH.fullmatch(c)}:
        raise ValueError("IATTC LL Num and Mt views have different catch columns")
    codes = sorted({c[:3] for c in columns})
    missing = [s + u for s in codes for u in ("n", "mt") if s + u not in columns]
    if missing:
        raise ValueError(f"IATTC LL catch needs both units per species; missing {missing}")
    n_view = {c: _values(num, c, NUM_VIEW) for c in sorted(columns)}
    mt_view = {c: _values(mt, c, MT_VIEW) for c in sorted(columns)}

    rows = len(num)
    flag, year = _text(num, cfg["group"]), num["_year"].to_numpy()
    has_info, positive, negative = np.zeros(rows, bool), np.zeros(rows, bool), np.zeros(rows, bool)
    identical = np.ones(rows, bool)
    for c in sorted(columns):
        a, b = n_view[c], mt_view[c]
        has_info |= ~np.isnan(a) | ~np.isnan(b)
        positive |= (a > 0) | (b > 0)
        negative |= (a < 0) | (b < 0)
        identical &= _same(a, b)
    qc.append(_qc("views_identical_rows", identical.sum(), "rows equal in both views (single-unit or unsplit catch)"))
    who, first, last = cfg["kor_unsplit"]
    kor = (flag == who) & (year >= first) & (year <= last)
    qc.append(_qc("kor_window_rows_split", (kor & ~identical).sum(),
                  "KOR 1987-2001 rows that differ between views: general unit rule applied"))

    parts, unsplit_cells = [], 0
    for s in [s for s in schema.SPECIES if s in codes]:
        n_num, mt_num, n_mt, mt_mt = n_view[s + "n"], n_view[s + "mt"], mt_view[s + "n"], mt_view[s + "mt"]
        unsplit = kor & _same(n_num, n_mt) & _same(mt_num, mt_mt) & (n_num > 0) & (mt_num > 0)
        unsplit_cells += int(unsplit.sum())
        parts.append((s, "fish", s + "n", n_num, (mt_num == 0) | unsplit))
        parts.append((s, "tonnes", s + "mt", mt_mt, (n_mt == 0) | unsplit))
    qc.append(_qc("kor_dual_unit_cells", unsplit_cells,
                  "KOR 1987-2001 species-cells with n > 0 and mt > 0 treated as the same fish (both units complete)"))

    fish = np.nansum(np.vstack([n_view[s + "n"] for s in codes]), axis=0) if codes else np.zeros(rows)
    usable = hooks > 0
    per_hook = np.divide(fish, hooks, out=np.zeros(rows), where=usable)
    suspect = np.zeros(rows, bool)
    for code, when in cfg["suspect_effort"]:
        suspect |= (flag == code) & (year == when)
    flags = [("hooks_coded_zero", hooks == 0), ("invalid_effort", hooks < 0),
             ("suspect_effort", suspect), ("high_catch_per_hook", usable & (per_hook > cfg["high_fish_per_hook"])),
             ("kor_dual_unit_unsplit", kor & identical), ("negative_catch", negative)]
    rows_id = np.where(num["_line"].to_numpy() == mt["_line"].to_numpy(), num["_line"].astype(str),
                       num["_line"].astype(str) + ";" + mt["_line"].astype(str))
    qc.append(_qc("views_row_misaligned", (num["_line"].to_numpy() != mt["_line"].to_numpy()).sum(),
                  "pairs whose Num and Mt line numbers differ (source_rows lists Num;Mt)"))
    return _assemble(spec, sha, num, NUM_VIEW, rows_id=rows_id, label=flag, effort=hooks, parts=parts,
                     has_info=has_info, positive=positive, flags=flags, qc=qc)


def _align(num, mt, cfg):
    """Pair the Num and Mt rows of each stratum; both views must hold the same strata."""
    def key(raw, member):
        return pd.DataFrame({"year": raw["_year"].to_numpy(), "month": raw["_month"].to_numpy(),
                             "flag": _text(raw, cfg["group"]), "lat": _values(raw, cfg["lat"], member),
                             "lon": _values(raw, cfg["lon"], member)})
    kn, km = key(num, NUM_VIEW), key(mt, MT_VIEW)
    if kn.equals(km):
        return num, mt, "row-aligned: identical keys in identical order"
    try:
        pairs = kn.reset_index().merge(km.reset_index(), on=list(kn.columns), how="outer",
                                       indicator=True, validate="one_to_one")
    except pd.errors.MergeError as exc:
        raise ValueError(f"IATTC LL views cannot be paired: duplicate stratum keys ({exc})") from None
    lonely = pairs["_merge"].value_counts()
    if lonely.get("left_only", 0) or lonely.get("right_only", 0):
        raise ValueError(f"IATTC LL Num and Mt views hold different strata: {lonely.get('left_only', 0)} "
                         f"only in Num, {lonely.get('right_only', 0)} only in Mt")
    pairs = pairs.sort_values("index_x")
    return (num.iloc[pairs.index_x.to_numpy()].reset_index(drop=True),
            mt.iloc[pairs.index_y.to_numpy()].reset_index(drop=True), "joined on Year, Month, Flag, LatC5, LonC5")


def _sets(lib, spec, sha, years, progress):
    cfg, member = spec.extra, spec.member
    raw = _read(lib, spec, member, sha, progress)
    keys = ["Year", "Month", cfg["group"], cfg["lat"], cfg["lon"], cfg["effort_field"]]
    _require(raw, keys, member)
    qc = [_qc("rows_read", len(raw), member)]
    unused = [c for c in raw.columns if c not in keys and not _SET_CATCH.fullmatch(c)]
    qc.append(_qc("unused_columns", len(unused), ", ".join(unused)))
    raw = _select(raw, years, member, qc)
    codes = [c for c in raw.columns if _SET_CATCH.fullmatch(c)]
    values = {c: _values(raw, c, member) for c in codes}
    rows = len(raw)
    has_info, positive, negative = np.zeros(rows, bool), np.zeros(rows, bool), np.zeros(rows, bool)
    for v in values.values():
        has_info |= ~np.isnan(v)
        positive |= v > 0
        negative |= v < 0
    sets = _values(raw, cfg["effort_field"], member)
    parts = [(s, "tonnes", s, values[s], None) for s in schema.SPECIES if s in values]
    label = _text(raw, cfg["group"])
    flags = [("sets_coded_zero", sets == 0), ("invalid_effort", sets < 0), ("negative_catch", negative)]
    if cfg["group"] == "Flag":
        flags.append(("pooled_flag_other", label == "Other"))
    qc.append(_qc("all_zero_catch_strata", (has_info & ~positive & (sets > 0)).sum(),
                  "sets with zero retained catch of every species: explicit zeros"))
    return _assemble(spec, sha, raw, member, rows_id=raw["_line"].astype(str).to_numpy(object), label=label,
                     effort=sets, parts=parts, has_info=has_info, positive=positive, flags=flags, qc=qc)


# ---------------------------------------------------------------- assembly

def _assemble(spec, sha, raw, member, *, rows_id, label, effort, parts, has_info, positive, flags, qc):
    """One stratum per source row (pair); effort per stratum; catch per stratum x species x unit."""
    cfg, product, rows = spec.extra, spec.product, len(raw)
    year, month = raw["_year"].to_numpy(), raw["_month"].to_numpy()
    lat, lon = _values(raw, cfg["lat"], member), _values(raw, cfg["lon"], member)
    geo, geo_flags = _geometry(lat, lon, cfg["grid_size"])
    by_set = cfg["group"] == "SetType"
    fleet = np.full(rows, "", dtype=object) if by_set else label
    school = label if by_set else np.full(rows, "", dtype=object)
    gear_raw = cfg["gear_raw"]
    reporting_group = ("settype:" + school if by_set else fleet) + "|" + gear_raw

    with np.errstate(invalid="ignore"):
        effort_status = np.select([np.isnan(effort), effort < 0, effort == 0], ["missing", "invalid", "missing"],
                                  "reported")
    usable = effort_status == "reported"
    stratum_status = np.select([usable & has_info, usable, has_info], ["reported", "effort_only", "catch_only"], "")
    keep = stratum_status != ""
    flags = list(flags) + [(note, np.ones(rows, bool)) for note in cfg.get("notes", ())]
    flags.append(("catch_without_effort", ~usable & positive))
    duplicate = pd.DataFrame({"y": year, "m": month, "g": label, "lat": lat, "lon": lon}).duplicated(keep=False)
    flags += geo_flags + [("duplicate_natural_key", duplicate.to_numpy())]
    notes = _notes(rows, flags)

    ids = np.array([f"{product}|{line}" for line in raw["_line"].to_numpy()], dtype=object)
    strata = pd.DataFrame({
        "stratum_id": ids, "source": SOURCE, "product": product, "view_group": spec.view_group,
        "dataset_id": spec.dataset_id, "source_sha256": sha, "source_member": spec.member,
        "source_rows": rows_id, "year": year, "month_start": month, "month_end": month,
        "time_resolution": schema.time_resolution(month, month).to_numpy(object),
        "grid_raw": (f"{cfg['lat']}=" + raw[cfg["lat"]].astype("string").str.strip() + f",{cfg['lon']}="
                     + raw[cfg["lon"]].astype("string").str.strip()).fillna("").to_numpy(object),
        **{c: geo[c].to_numpy("float64", na_value=np.nan)
           for c in ["west", "south", "grid_width", "grid_height", "lon", "lat"]},
        "geometry_status": geo["geometry_status"].to_numpy(object), "region_raw": REGION,
        "fleet": fleet, "gear_raw": gear_raw,
        "gear_group": GEARS["iattc"].get(gear_raw, f"unmapped:iattc:{gear_raw}"),
        "school_type": school, "reporting_group": reporting_group, "stratum_status": stratum_status,
        "notes": notes,
    })
    effort_frame = pd.DataFrame({
        "stratum_id": ids, "year": year, "effort_unit": cfg["effort_unit"],
        "effort": np.where(usable, effort, np.nan), "effort_status": effort_status,
        "effort_field": cfg["effort_field"], "effort_scope": cfg["effort_scope"]})
    catch, statuses = _catch(ids, year, parts, ~has_info, spec.catch_basis)

    per = "Num/Mt row pair" if "members" in cfg else "source row"
    qc.append(_qc("strata", keep.sum(), f"one stratum per {per}; rows never aggregated"))
    qc.append(_qc("duplicates_aggregated", 0, "no aggregation; duplicate natural keys are only flagged"))
    qc.append(_qc("rows_dropped_empty", (~keep).sum(), "no usable effort and no catch information"))
    qc.append(_qc("masked_strata", 0, "IATTC suppresses no cells; PS/LP pool small flags into 'Other' instead"))
    qc.append(_qc("effort_coded_zero", (effort == 0).sum(), f"{cfg['effort_field']} == 0 written as missing (NA)"))
    qc.append(_qc("effort_missing_or_invalid", (keep & ~usable).sum(), "strata without usable effort (catch_only)"))
    located = geo["geometry_status"].to_numpy() == "located"
    qc.append(_qc("unlocated", (keep & ~located).sum(), "no usable coordinates (never imputed)"))
    qc.append(_qc("surrogate_area", 0, "IATTC uses a regular grid only"))
    for unit in dict.fromkeys(p[1] for p in parts):
        count = sum(int((st[keep] == "unit_incomplete").sum()) for p, st in zip(parts, statuses) if p[1] == unit)
        qc.append(_qc(f"unit_incomplete_{unit}", count, "catch partly reported only in the other unit (NA)"))
    for name, mask in flags:
        qc.append(_qc(f"note:{name}", (np.asarray(mask, bool) & keep).sum(), NOTES.get(name, "")))
    k = len(parts)
    return (strata[keep].reset_index(drop=True), effort_frame[keep].reset_index(drop=True),
            catch[np.repeat(keep, k)].reset_index(drop=True), qc)


def _catch(ids, year, parts, no_info, basis):
    """Stratum-major catch rows with raw statuses; values only for positive and zero."""
    rows, k = len(ids), len(parts)
    labels = np.array(["no_catch_info", "blank", "blank", "unit_incomplete", "positive", "zero"], dtype=object)
    statuses, values = [], []
    for species, unit, field, value, complete in parts:
        complete = np.ones(rows, bool) if complete is None else complete
        with np.errstate(invalid="ignore"):
            code = np.select([no_info, np.isnan(value), value < 0, ~complete, value > 0], [0, 1, 2, 3, 4], 5)
        statuses.append(labels[code])  # shared label objects, not one string per row
        values.append(np.where(code >= 4, value, np.nan))
    if not k:
        return schema.empty(schema.CATCH_COLUMNS), statuses
    frame = pd.DataFrame({
        "stratum_id": np.repeat(ids, k), "year": np.repeat(year, k),
        "species_code": np.tile(np.array([p[0] for p in parts], dtype=object), rows),
        "catch_unit": np.tile(np.array([p[1] for p in parts], dtype=object), rows),
        "catch": np.column_stack(values).ravel(), "catch_status": np.column_stack(statuses).ravel(),
        "catch_field": np.tile(np.array([p[2] for p in parts], dtype=object), rows), "catch_basis": basis})
    return frame, statuses


def _geometry(lat, lon, size):
    """Exact size x size rectangles around IATTC's documented cell centres."""
    rows = len(lat)
    columns = ["lon", "lat", "west", "south", "grid_width", "grid_height"]
    if not rows:
        empty = pd.DataFrame({c: pd.Series(dtype="Float64") for c in columns})
        empty["geometry_status"] = pd.Series(dtype=object)
        return empty, [(n, np.zeros(0, bool)) for n in ["invalid_coordinates", "off_grid_centre",
                                                         "outside_convention_area"]]
    codes, _ = pd.factorize(pd.Series(lat).astype(str) + "|" + pd.Series(lon).astype(str))
    first = pd.Series(np.arange(rows)).groupby(codes).first().to_numpy()  # first row of each code
    cells, invalid = [], []
    for y, x in zip(lat[first], lon[first]):
        bad = False
        if np.isnan(y) or np.isnan(x):
            cell = rectangle(pd.NA, pd.NA, pd.NA, pd.NA)
        elif not (-90 <= y <= 90 and -180 <= x <= 360):
            cell, bad = rectangle(pd.NA, pd.NA, pd.NA, pd.NA), True
        else:
            try:
                cell = centered_cell(float(y), float(x), size)
            except ValueError:
                cell, bad = rectangle(pd.NA, pd.NA, pd.NA, pd.NA), True
        cells.append(cell)
        invalid.append(bad)
    unique = schema.geometry_frame(cells, range(len(cells)))
    geo = unique.iloc[codes].reset_index(drop=True)
    west, south = (geo[c].to_numpy("float64", na_value=np.nan) for c in ["west", "south"])
    height = geo["grid_height"].to_numpy("float64", na_value=np.nan)
    with np.errstate(invalid="ignore"):
        off_grid = (np.abs(west / size - np.round(west / size)) > _EPS) | (
            np.abs(south / size - np.round(south / size)) > _EPS)
        outside = ((west < CONVENTION_AREA["west"] - _EPS) | (south < CONVENTION_AREA["south"] - _EPS)
                   | (south + height > CONVENTION_AREA["north"] + _EPS))
    return geo, [("invalid_coordinates", np.asarray(invalid)[codes]), ("off_grid_centre", off_grid),
                 ("outside_convention_area", outside)]


def _notes(rows, flags):
    out = np.full(rows, "", dtype=object)
    for name, mask in flags:
        mask = np.asarray(mask, bool)
        if mask.any():
            current = out[mask]
            out[mask] = np.where(current == "", name, current + ";" + name)
    return out
