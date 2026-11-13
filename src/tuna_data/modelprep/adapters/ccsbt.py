"""CCSBT public catch and effort (CE) workbooks as canonical strata.

Products (SBF is the only species with catch in either workbook):

* ``ccsbt_ll``: CEData_Longline.xlsx, sheet CEData_Longline. One row per YEAR x MONTH x
  COUNTRY CODE x TARGET SPECIES x CCSBT STATISTICAL AREA x 5-degree cell, with
  NUMBER OF HOOKS (a count, not hundreds) and NUMBER OF SBT RETAINED (fish).
* ``ccsbt_surface``: CEData_Surface.xlsx, sheet CEData_Surface. One row per YEAR x MONTH x
  COUNTRY CODE x GEAR CODE (PS, BB) x area x 1-degree cell, with NUMBER OF HOURS SEARCHED
  and WEIGHT (Kg) OF SBT RETAINED (kg; usually visual estimates).

Source conventions (CCSBT SBT data page and the CECodes sheets):

* LATITUDE/LONGITUDE are the NORTH-west corner of the cell (schema.nw_corner_cell).
  Blank coordinates stay unlocated, never (0, 0). Statistical areas are irregular
  polygons: they are kept as region_raw text and never turned into coordinates.
* Empty cells mean missing or empty values, never zero: blank catch is 'blank',
  blank effort is 'missing'. Explicit 0 catch is a reported zero. Explicit 0 effort
  is not documented as real (all 0-hour surface rows have blank weight), so it is
  'missing' with a coded-zero note.
* TARGET SPECIES (NZ, KR and ZA only) labels the effort, not the catch: it is part of
  the stratum key and kept as the note 'target:<code>'; school_type stays ''.
* Headers contain line breaks ('NUMBER\\nOF\\nHOOKS'). They are matched ignoring case
  and whitespace; effort_field/catch_field hold the header text with each line break
  shown as one space. Unknown non-empty columns stop the adapter for review.

Stratum notes (';'-joined flags, never deletions): target:<code>, area_NH, area_missing,
month_missing, tw_pre1996_estimated (TW before 1996: SBF estimated from mixed bluefin
data), jp_rtmp_era (JP from 1995: Real Time Monitoring Program data), null_island_review
((0, 0) corner kept as a cell), incomplete_coordinates, invalid_coordinates,
off_grid_corner, hooks_coded_zero / hours_coded_zero, invalid_effort, suspect_effort
(the fleet-gear-year median effort per stratum is more than 10 times the fleet-gear
median over the whole file; computed on the full file so it does not depend on the
years filter), no_effort_field (the fleet-gear reports no effort anywhere in the file),
duplicate_rows_aggregated, duplicate_mixed_effort, duplicate_mixed_catch,
catch_without_effort, no_values (no usable effort and no catch value) and
visual_estimate (surface weights).

CatchByYMGOLoLa (raised all-flag tonnes without effort) is deliberately not a product.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ...harmonize import GEARS
from ..schema import AdapterResult, ProductSpec, finalize, geometry_frame, nw_corner_cell, parse_values, time_resolution

SOURCE = "ccsbt"
CATCH_BASIS = "ccsbt_ce_retained"
SPECIES_CODE = "SBF"
# CCSBT gear codes -> labels of harmonize.GEARS["ccsbt"]. LL: longline workbook (no gear
# column); BB "Baitboat (Pole and Line)" and PS "Purse Seine": CECodes_Surface; GILL, HAND
# and TROL: Gear Codes sheet of the CCSBT catch-by-gear-and-cell workbook.
GEAR_LABELS = {"LL": "Longline", "PS": "Purse Seine", "BB": "Pole and Line",
               "GILL": "Gill Net", "HAND": "Handline", "TROL": "Troll"}
SUSPECT_EFFORT_FACTOR = 10.0

_PLACE = {"YEAR": "year", "MONTH": "month", "COUNTRY CODE": "fleet", "CCSBT STATISTICAL AREA": "area",
          "LATITUDE": "lat", "LONGITUDE": "lon"}

PRODUCTS = {
    "ccsbt_ll": ProductSpec(
        product="ccsbt_ll", source=SOURCE, dataset_id="ccsbt/cedata_longline", member="CEData_Longline",
        view_group="ccsbt_ll", zeros_explicit=True, catch_basis=CATCH_BASIS,
        description=("CCSBT longline catch and effort: month x country x target species x statistical area x "
                     "5-degree cell (north-west corner); hooks and SBF retained in numbers."),
        extra=dict(
            fields={**_PLACE, "TARGET SPECIES": "target", "NUMBER OF HOOKS": "effort",
                    "NUMBER OF SBT RETAINED": "catch"},
            key=("year", "month", "fleet", "target", "area", "lat", "lon"),
            cell=5, gear="LL", effort_unit="hooks", catch_unit="fish", zero_effort_note="hooks_coded_zero",
            catch_note="",
            # (fleet, first year, last year, note); None = open-ended.
            fleet_years=(("TW", None, 1995, "tw_pre1996_estimated"), ("JP", 1995, None, "jp_rtmp_era")),
        )),
    "ccsbt_surface": ProductSpec(
        product="ccsbt_surface", source=SOURCE, dataset_id="ccsbt/cedata_surface", member="CEData_Surface",
        view_group="ccsbt_surface", zeros_explicit=True, catch_basis=CATCH_BASIS,
        description=("CCSBT surface (purse seine, pole-and-line) catch and effort: month x country x gear x "
                     "statistical area x 1-degree cell (north-west corner); hours searched and SBF retained "
                     "in kg (visual estimates)."),
        extra=dict(
            fields={**_PLACE, "GEAR CODE": "gear", "NUMBER OF HOURS SEARCHED": "effort",
                    "WEIGHT (KG) OF SBT RETAINED": "catch"},
            key=("year", "month", "fleet", "gear", "area", "lat", "lon"),
            cell=1, gear=None, effort_unit="hours_searched", catch_unit="kg", zero_effort_note="hours_coded_zero",
            catch_note="visual_estimate", fleet_years=(),
        )),
}


def _normal(header):
    return re.sub(r"\s+", " ", str(header)).strip().upper()


def _label(header):
    return re.sub(r"\s+", " ", str(header)).strip()


def _qc(check, count, note=""):
    return {"check": check, "count": int(count), "note": note}


def _say(progress, message):
    if callable(progress):
        progress(message)


def _listed(text, items, limit=10):
    """QC note text, followed by up to `limit` items when there are any."""
    items = [str(i) for i in items]
    if not items:
        return text
    more = f" (+{len(items) - limit} more)" if len(items) > limit else ""
    return f"{text}: {', '.join(items[:limit])}{more}"


def _floats(text, name):
    return parse_values(text, name=name).to_numpy(dtype=float, na_value=np.nan)


def _integers(text, name, low, high):
    values = _floats(text, name)
    known = ~np.isnan(values)
    bad = known & ((values % 1 != 0) | (values < low) | (values > high))
    if bad.any():
        raise ValueError(f"Invalid {name}: {sorted(set(text[bad]))[:5]}")
    return values


def _text_number(values):
    """Key text for parsed numbers: '' for missing, no trailing '.0' for integers."""
    return pd.Series([("" if np.isnan(v) else str(int(v)) if float(v).is_integer() else repr(float(v)))
                      for v in values], dtype=object)


def _columns(raw, fields, product, qc):
    """Select the expected columns by normalized header; keep worksheet row numbers."""
    if not isinstance(raw.index, pd.RangeIndex) or raw.index.start != 0 or raw.index.step != 1:
        raise ValueError(f"{product}: expected one row per worksheet row below the header")
    names = [_normal(c) for c in raw.columns]
    repeated = sorted({n for n in names if names.count(n) > 1})
    if repeated:
        raise ValueError(f"CCSBT {product} has repeated columns {repeated}")
    missing = [f for f in fields if f not in names]
    if missing:
        raise ValueError(f"CCSBT {product} schema changed: missing columns {missing}; "
                         f"found {[_label(c) for c in raw.columns]}")
    data, labels, ignored = {}, {}, []
    for column, name in zip(raw.columns, names):
        text = raw[column].astype("string").str.strip().fillna("").astype(object)
        if name in fields:
            data[fields[name]] = text
            labels[fields[name]] = _label(column)
        elif text.ne("").any():
            raise ValueError(f"CCSBT {product} has an unreviewed column {_label(column)!r}; review it before adapting")
        else:
            ignored.append(_label(column))
    if ignored:
        qc.append(_qc("empty_columns_ignored", len(ignored), "; ".join(ignored)))
    table = pd.DataFrame(data, index=raw.index)
    # The header is worksheet row 1 and pandas keeps blank rows inside the used range
    # (read_excel skip_blank_lines=False), so index + 2 is the worksheet row number.
    table.insert(0, "row", raw.index.to_numpy() + 2)
    blank = table.drop(columns="row").eq("").all(axis=1).to_numpy()
    qc.append(_qc("blank_rows_skipped", blank.sum(),
                  _listed("worksheet rows with no value in any column", table.row[blank])))
    return table[~blank].reset_index(drop=True), labels


def _suspect_years(group, year, effort):
    """(group, year) whose median effort per stratum exceeds SUSPECT_EFFORT_FACTOR x the group median."""
    ok = effort > 0
    frame = pd.DataFrame({"g": group[ok].to_numpy(), "y": year[ok], "e": effort[ok]})
    if frame.empty:
        return set()
    overall = frame.groupby("g").e.median()
    by_year = frame.groupby(["g", "y"]).e.median()
    ratio = by_year.to_numpy() / overall.reindex(by_year.index.get_level_values("g")).to_numpy()
    return {(g, int(y)) for (g, y), r in zip(by_year.index, ratio) if r > SUSPECT_EFFORT_FACTOR}


def _append(notes, mask, label):
    """Append label (str or per-row Series) to the ';'-joined notes where mask is True."""
    mask = np.asarray(mask, dtype=bool)
    if not mask.any():
        return notes
    labels = label if isinstance(label, str) else label[mask]
    current = notes[mask]
    notes[mask] = np.where(current.eq(""), labels, current + ";" + labels)
    return notes


def _merge(group):
    """One stratum from several source rows with the same natural key (additive fields summed)."""
    out = group.iloc[0].to_dict()
    out["source_rows"] = ";".join(str(r) for r in sorted(group.row))
    out["row"] = group.row.min()
    extra = ["duplicate_rows_aggregated"]
    statuses = set(group.effort_status)
    if statuses == {"reported"}:
        out["effort"] = float(group.effort.sum())
    elif len(statuses) == 1:          # all missing, or all invalid
        out["effort"], out["effort_status"] = np.nan, statuses.pop()
    else:                             # partial effort would bias CPUE: keep it missing
        out["effort"], out["effort_status"] = np.nan, "missing"
        extra.append("duplicate_mixed_effort")
    statuses = set(group.catch_status)
    if statuses <= {"positive", "zero"}:
        total = float(group["catch"].sum())
        out["catch"], out["catch_status"] = total, ("positive" if total > 0 else "zero")
    else:
        out["catch"], out["catch_status"] = np.nan, "blank"
        if statuses != {"blank"}:     # some rows blank, some with values
            extra.append("duplicate_mixed_catch")
    notes = []
    for text in group.notes:
        notes += [n for n in text.split(";") if n and n not in notes]
    out["notes"] = ";".join(notes + extra)
    return out


def adapt(lib, product, *, version=None, years=None, progress=None):
    """Canonical strata, effort and catch for one CCSBT CE product.

    One stratum per natural key (month x country x target or gear x area x cell); the
    only multi-row key in the 2026 longline file (NZ 1995-12 BET, area 5, cell -35/175)
    is summed and keeps both worksheet rows in source_rows. Effort and SBF catch are one
    row each per stratum, with blank values kept as missing/blank, never zero.
    """
    if product not in PRODUCTS:
        raise ValueError(f"Unknown CCSBT product {product!r}; choose from {sorted(PRODUCTS)}")
    spec = PRODUCTS[product]
    cfg = spec.extra
    record = lib.record(spec.dataset_id, version)   # pin the snapshot before reading
    sha = record["sha256"]
    # source_member is the worksheet read (spec.member); dataset_id + sha256 pin the workbook.
    _say(progress, f"{product}: reading {record.get('filename', spec.dataset_id)} sheet {spec.member} @ {sha[:12]}")
    raw = lib.read_table(spec.dataset_id, version=sha, sheet_name=spec.member, dtype=str, keep_default_na=False)
    qc = [_qc("rows_read", len(raw), f"rows below the header of worksheet {spec.member} "
              "(trailing formatted empty rows are not data rows)")]
    t, labels = _columns(raw, cfg["fields"], product, qc)
    del raw

    # Parse and validate every row of the file first (cheap; failures do not depend on years).
    year = _integers(t.year, f"{product} YEAR", 1900, 2100)
    gone = np.isnan(year)
    qc.append(_qc("rows_dropped_missing_year", gone.sum(),
                  _listed("structurally invalid (no YEAR); worksheet rows", t.row[gone])))
    t, year = t[~gone].reset_index(drop=True), year[~gone]
    month = _integers(t.month, f"{product} MONTH", 1, 12)
    lat = _floats(t.lat, f"{product} LATITUDE")
    lon = _floats(t.lon, f"{product} LONGITUDE")
    effort = _floats(t.effort, f"{product} {labels['effort']}")
    catch = _floats(t["catch"], f"{product} {labels['catch']}")
    if (catch < 0).any():
        raise ValueError(f"Negative values in {product} {labels['catch']}: review the source")
    fleet = t.fleet
    gear = t.gear if "gear" in t else pd.Series(cfg["gear"], index=t.index, dtype=object)
    group = fleet + "|" + gear

    # Fleet-gear properties from the whole file, so notes do not depend on the years filter.
    has_effort = set(group[effort > 0])
    no_effort = sorted(set(group) - has_effort)
    suspect = _suspect_years(group, year, effort)

    if years is not None:
        wanted = {int(y) for y in ([years] if isinstance(years, (int, np.integer)) else years)}
        keep = np.isin(year, sorted(wanted))
        qc.append(_qc("rows_outside_years", (~keep).sum(), f"dropped by the years filter (kept {len(wanted)} years)"))
        t, year, month, lat, lon, effort, catch = (t[keep].reset_index(drop=True), year[keep], month[keep],
                                                   lat[keep], lon[keep], effort[keep], catch[keep])
        fleet, gear, group = (x[keep].reset_index(drop=True) for x in (fleet, gear, group))
    qc.append(_qc("rows_used", len(t), "source rows entering strata"))
    _say(progress, f"{product}: {len(t)} rows")

    # Geometry: exact NW-corner cells; unusable coordinates stay unlocated.
    size = cfg["cell"]
    cells, invalid = [], np.zeros(len(t), dtype=bool)
    for i, (la, lo) in enumerate(zip(lat, lon)):
        try:
            cells.append(nw_corner_cell(la, lo, size))
        except ValueError:
            invalid[i] = True
            cells.append(nw_corner_cell(np.nan, np.nan, size))
    geo = geometry_frame(cells, t.index) if cells else pd.DataFrame(
        {c: pd.Series(dtype="Float64") for c in ["lon", "lat", "west", "south", "grid_width", "grid_height"]}
        | {"geometry_status": pd.Series(dtype=object)})
    located = geo.geometry_status.eq("located").to_numpy()
    off_grid = located & ((np.nan_to_num(lat) % size != 0) | (np.nan_to_num(lon) % size != 0))
    incomplete = np.isnan(lat) ^ np.isnan(lon)
    null_island = (lat == 0) & (lon == 0)
    area = t.area.map(lambda a: str(int(float(a))) if re.fullmatch(r"\d+(\.0+)?", a) else a)
    lat_text, lon_text = _text_number(lat), _text_number(lon)
    grid_raw = (t.lat + "," + t.lon).where(t.lat.ne("") | t.lon.ne(""), "")

    # Row statuses. Blank catch is 'blank' (missing or empty cell), never zero.
    zero_effort = effort == 0
    negative_effort = effort < 0
    effort_status = np.select([effort > 0, negative_effort], ["reported", "invalid"], "missing")
    effort_value = np.where(effort > 0, effort, np.nan)
    catch_status = np.select([catch > 0, catch == 0], ["positive", "zero"], "blank")

    notes = pd.Series("", index=t.index, dtype=object)
    if "target" in t:
        notes = _append(notes, t.target.ne(""), "target:" + t.target)
    notes = _append(notes, area.eq("NH"), "area_NH")
    notes = _append(notes, area.eq(""), "area_missing")
    notes = _append(notes, np.isnan(month), "month_missing")
    for code, first, last, label in cfg["fleet_years"]:
        mask = (fleet.eq(code).to_numpy() & (year >= (-np.inf if first is None else first))
                & (year <= (np.inf if last is None else last)))
        notes = _append(notes, mask, label)
    notes = _append(notes, null_island, "null_island_review")
    notes = _append(notes, incomplete, "incomplete_coordinates")
    notes = _append(notes, invalid, "invalid_coordinates")
    notes = _append(notes, off_grid, "off_grid_corner")
    notes = _append(notes, zero_effort, cfg["zero_effort_note"])
    notes = _append(notes, negative_effort, "invalid_effort")
    notes = _append(notes, [(g, int(y)) in suspect for g, y in zip(group, year)], "suspect_effort")
    notes = _append(notes, group.isin(no_effort), "no_effort_field")

    parts = {"year": _text_number(year), "month": _text_number(month), "fleet": fleet, "target": t.get("target"),
             "gear": gear, "area": area, "lat": lat_text, "lon": lon_text}
    if any(parts[k].str.contains("|", regex=False).any() for k in cfg["key"]):
        raise ValueError(f"CCSBT {product}: '|' inside a key field")
    key = parts[cfg["key"][0]].copy()
    for name in cfg["key"][1:]:
        key = key + "|" + parts[name]
    rows = pd.DataFrame({
        "row": t.row, "key": key, "year": year, "month_start": np.where(np.isnan(month), 1, month),
        "month_end": np.where(np.isnan(month), 12, month), "grid_raw": grid_raw,
        **{c: geo[c] for c in ["west", "south", "grid_width", "grid_height", "lon", "lat", "geometry_status"]},
        "region_raw": area, "fleet": fleet, "gear_raw": gear,
        "gear_group": gear.map(lambda g: GEARS["ccsbt"].get(GEAR_LABELS.get(g, ""), f"unmapped:ccsbt:{g or 'MISSING'}")),
        "reporting_group": group, "effort": effort_value, "effort_status": effort_status,
        "catch": catch, "catch_status": catch_status, "notes": notes,
    })

    # Aggregate repeated natural keys (additive fields summed, all worksheet rows listed).
    repeated = rows.key.duplicated(keep=False).to_numpy()
    single = rows[~repeated].assign(source_rows=rows.row[~repeated].astype(str))
    merged = [_merge(g) for _, g in rows[repeated].groupby("key", sort=False)]
    if merged:
        extra = pd.DataFrame(merged, columns=single.columns).astype(single.dtypes.to_dict())
        single = pd.concat([single, extra], ignore_index=True)
    s = single.sort_values("row", kind="stable").reset_index(drop=True)
    qc.append(_qc("duplicate_strata_aggregated", len(merged), _listed(
        f"natural key {'|'.join(cfg['key'])} repeated; {labels['effort']} and {labels['catch']} summed",
        [f"{m['key']} (worksheet rows {m['source_rows']})" for m in merged])))
    qc.append(_qc("duplicate_rows_merged", repeated.sum() - len(merged), "extra source rows folded into those strata"))

    # Stratum status and stratum-level notes.
    effort_ok = s.effort_status.eq("reported").to_numpy()
    catch_known = s.catch_status.isin(["positive", "zero"]).to_numpy()
    lacks_field = s.reporting_group.isin(no_effort).to_numpy()
    s["stratum_status"] = np.where(~effort_ok & catch_known, "catch_only", "reported")
    s["notes"] = _append(s.notes.copy(), ~effort_ok & catch_known & ~lacks_field, "catch_without_effort")
    s["notes"] = _append(s.notes.copy(), ~effort_ok & ~catch_known, "no_values")
    if cfg["catch_note"]:
        s["notes"] = _append(s.notes.copy(), catch_known, cfg["catch_note"])
    s["stratum_id"] = product + "|" + s.key

    strata = s.assign(source=SOURCE, product=product, view_group=spec.view_group, dataset_id=spec.dataset_id,
                      source_sha256=sha, source_member=spec.member, school_type="")
    strata["time_resolution"] = time_resolution(strata.month_start, strata.month_end)
    effort_frame = pd.DataFrame({"stratum_id": s.stratum_id, "year": s.year, "effort_unit": cfg["effort_unit"],
                                 "effort": s.effort, "effort_status": s.effort_status,
                                 "effort_field": labels["effort"], "effort_scope": "stratum"})
    catch_frame = pd.DataFrame({"stratum_id": s.stratum_id, "year": s.year, "species_code": SPECIES_CODE,
                                "catch_unit": cfg["catch_unit"], "catch": s["catch"], "catch_status": s.catch_status,
                                "catch_field": labels["catch"], "catch_basis": spec.catch_basis})
    strata, effort_frame, catch_frame = finalize(strata, effort_frame, catch_frame)

    def count(mask):
        return int(np.asarray(mask, dtype=bool).sum())

    def noted(label):
        return count(strata.notes.str.split(";").map(lambda n: label in n))

    status = strata.stratum_status
    qc += [
        _qc("strata", len(strata), "one per natural key"),
        _qc("masked_strata", count(status.eq("masked")), "CCSBT CE has no confidentiality marker; empty cells are "
            "missing/blank values, and strata below the 3-vessel rule are absent from the file, not masked"),
        _qc("catch_only_strata", count(status.eq("catch_only")), "SBF value but no usable effort"),
        _qc("surrogate_area_strata", count(strata.geometry_status.eq("surrogate_area")),
            "statistical areas are irregular polygons kept in region_raw, never used as cells"),
        _qc("unlocated_strata", count(strata.geometry_status.eq("unlocated")),
            "blank LATITUDE and/or LONGITUDE (never placed at 0,0)"),
        _qc("incomplete_coordinates", noted("incomplete_coordinates"), "only one of LATITUDE/LONGITUDE reported"),
        _qc("invalid_coordinates", noted("invalid_coordinates"), "corner outside the globe; unlocated"),
        _qc("null_island_review", noted("null_island_review"), "LATITUDE 0 and LONGITUDE 0 kept as the "
            f"cell 0-{size}S x 0-{size}E (longline: area-11 Atlantic cells next to other lat-0 cells); review"),
        _qc("off_grid_corner", noted("off_grid_corner"), f"corner not on the {size}-degree grid"),
        _qc("effort_reported", count(effort_frame.effort_status.eq("reported")), cfg["effort_unit"] + " > 0"),
        _qc("effort_missing", count(effort_frame.effort_status.eq("missing")), "blank or coded-zero effort (NA)"),
        _qc("coded_zero_effort", noted(cfg["zero_effort_note"]), "explicit 0 effort not documented as real: "
            "effort_status 'missing' (NA)"),
        _qc("effort_invalid", count(effort_frame.effort_status.eq("invalid")), "negative effort (NA)"),
        _qc("catch_positive", count(catch_frame.catch_status.eq("positive")), f"SBF {cfg['catch_unit']} > 0"),
        _qc("catch_zero", count(catch_frame.catch_status.eq("zero")), "explicit 0"),
        _qc("catch_blank", count(catch_frame.catch_status.eq("blank")), "empty cell: missing or no SBF, not zero"),
        _qc("no_values_strata", noted("no_values"), "no usable effort and no catch value (kept, flagged)"),
        _qc("no_effort_field", noted("no_effort_field"),
            _listed("fleet|gear without any effort value in the whole file", no_effort)),
        _qc("suspect_effort", noted("suspect_effort"), _listed(
            f"fleet|gear-years whose median effort per stratum is > {SUSPECT_EFFORT_FACTOR:g} x the fleet|gear "
            "median over the whole file", [f"{g} {y}" for g, y in sorted(suspect)])),
        _qc("month_missing", noted("month_missing"), "blank MONTH: stratum spans the year (months 1-12)"),
        _qc("area_missing", noted("area_missing"), "blank CCSBT STATISTICAL AREA"),
    ]
    if product == "ccsbt_ll":
        targets = strata.notes.str.extract(r"(?:^|;)target:([^;]+)", expand=False).dropna()
        qc += [
            _qc("area_NH", noted("area_NH"), "statistical area 'NH' (not in the CECodes list)"),
            _qc("tw_pre1996_estimated", noted("tw_pre1996_estimated"), "TW before 1996: SBF estimated from mixed bluefin data"),
            _qc("jp_rtmp_era", noted("jp_rtmp_era"), "JP from 1995: Real Time Monitoring Program data (series break)"),
            _qc("target_labels", len(targets), _listed(
                "TARGET SPECIES (a targeting label, not catch) kept as note target:<code>",
                [f"{k} {v}" for k, v in targets.value_counts().sort_index().items()])),
        ]
    else:
        qc.append(_qc("visual_estimate", noted("visual_estimate"), "SBF weights are usually approximate visual estimates"))
    _say(progress, f"{product}: {len(strata)} strata")
    return AdapterResult(strata, effort_frame, catch_frame, record=record, qc=qc)
