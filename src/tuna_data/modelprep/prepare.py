"""Species-specific analysis tables for CPUE standardization and SDMs.

Both functions read a model-prep run, apply species/stock/gear rules in a fixed
order and log every step (rows before/after) so the cleaning is auditable:

1. products (one view per source)      6. time and spatial resolution
2. gear                                7. set type (purse seine)
3. stock / authoritative source        8. effort in one unit (reported, > 0; set-type scope)
4. fleets                              9. catch in one unit, zero policy
5. default exclusions (cited)         10. derived columns and QC flags
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from ..spatial_geometry import grid_status
from .build import read_modelprep, run_metadata
from .schema import TIME_RESOLUTIONS
from .species import (CPUE_CEILINGS, GEAR_RULES, assign_stock, exclusion_mask, exclusions_for, gear_rule, stocks)

ZERO_POLICIES = {
    "reported": {"positive", "zero_reported"},
    "positives_only": {"positive"},
    "with_inferred": {"positive", "zero_reported", "inferred_zero_candidate"},
    "with_unconfirmed": {"positive", "zero_reported", "zero_unconfirmed"},
    "with_candidates": {"positive", "zero_reported", "zero_unconfirmed", "inferred_zero_candidate"},
}
KEY_COLUMNS = ["species_code", "stock_id", "source", "product", "gear_group", "gear_raw", "fleet", "school_type",
               "year", "month_start", "month_end", "quarter", "time_resolution", "west", "south", "grid_width",
               "grid_height", "lon", "lat", "region_raw"]


class _Log:
    def __init__(self):
        self.rows = []

    def step(self, rule_id, description, before, after):
        self.rows.append(dict(step=len(self.rows) + 1, rule_id=rule_id, description=description,
                              rows_before=int(before), rows_after=int(after), removed=int(before - after)))

    def warn(self, rule_id, message, rows):
        self.step(rule_id, "WARNING: " + message, rows, rows)
        warnings.warn(message, stacklevel=4)

    def frame(self):
        return pd.DataFrame(self.rows, columns=["step", "rule_id", "description", "rows_before", "rows_after", "removed"])


def _values(value):
    if value is None:
        return None
    if isinstance(value, np.generic):
        value = value.item()
    return [value] if isinstance(value, (str, int, float)) else list(value)


def _years(years):
    values = _values(years)
    return None if values is None else sorted({int(y) for y in values})


def _flag(frame, mask, label):
    mask = mask.fillna(False).astype(bool)
    current = frame["qc_flags"].astype("string")
    frame.loc[mask, "qc_flags"] = np.where(current[mask].eq(""), label, current[mask] + ";" + label)


def _check(species, *, stock, zeros, time_resolution):
    known = {s.stock_id for s in stocks(species)}
    unknown = set(_values(stock) or []) - known
    if unknown:
        raise ValueError(f"Unknown {species} stock(s) {sorted(unknown)}; choose {', '.join(sorted(known))}")
    if zeros not in ZERO_POLICIES:
        raise ValueError(f"zeros must be one of {', '.join(ZERO_POLICIES)}")
    bad = set(_values(time_resolution) or []) - TIME_RESOLUTIONS
    if bad:
        raise ValueError(f"Unknown time_resolution {sorted(bad)}; choose {', '.join(sorted(TIME_RESOLUTIONS))}")


def _strata(species, gear, rule, *, stock, products, years, months, fleets, exclude_fleets, time_resolution,
            max_cell_degrees, school_types, apply_exclusions, run, library, verify, log, use_rule_fleets=True):
    root, meta = run_metadata(run, library)
    available = {p["product"]: p for p in meta["products"]}
    wanted = _values(products) or list(rule.products)
    missing = [p for p in wanted if p not in available]
    wanted = [p for p in wanted if p in available]
    groups = [available[p]["view_group"] for p in wanted]
    if len(groups) != len(set(groups)):
        raise ValueError("Choose one product per view_group: alternative views of the same catch cannot be combined")
    if not wanted:
        raise ValueError(f"None of the products {list(rule.products)} are in this model-prep run; rebuild with them")
    strata = read_modelprep("strata", run=root, product=wanted, year=_years(years), verify=verify)
    log.step("R01", f"read strata from products {wanted}" + (f" (not in run: {missing})" if missing else ""),
             len(strata), len(strata))

    def keep(frame, mask, rule_id, description):
        out = frame.loc[pd.Series(mask, index=frame.index).fillna(False).astype(bool)].copy()
        log.step(rule_id, description, len(frame), len(out))
        return out

    if months is not None:
        m = {int(x) for x in _values(months)}
        inside = pd.Series(True, index=strata.index)
        for month in set(range(1, 13)) - m:
            inside &= ~((strata.month_start <= month) & (strata.month_end >= month)).fillna(False)
        strata = keep(strata, inside, "R01m", f"months {sorted(m)} (every month of the stratum requested)")
    strata = keep(strata, strata.gear_group.eq(gear), "R02", f"gear_group == {gear}")
    stock_id, stock_flag = assign_stock(strata, species)
    strata = strata.assign(stock_id=stock_id, stock_flag=stock_flag, qc_flags="")
    wanted_stocks = _values(stock)
    mask = strata.stock_id.ne("")
    if wanted_stocks:
        mask &= strata.stock_id.isin(wanted_stocks)
    strata = keep(strata, mask, "R03", f"stock assignment ({'any ' + species + ' stock' if not wanted_stocks else wanted_stocks}); "
                                       "authoritative source, whole-cell containment")
    fleet_list = _values(fleets) if fleets is not None else ((list(rule.fleets) or None) if use_rule_fleets else None)
    if fleet_list:
        strata = keep(strata, strata.fleet.str.strip().isin(fleet_list), "R04", f"fleets {fleet_list}")
    if exclude_fleets:
        strata = keep(strata, ~strata.fleet.str.strip().isin(_values(exclude_fleets)), "R04x",
                      f"exclude fleets {_values(exclude_fleets)}")
    if apply_exclusions:
        for x in exclusions_for(species, gear):
            mask = exclusion_mask(strata, x)
            if x.drop or x.dedupe:
                strata = keep(strata, ~mask, x.rule_id, x.description)
            elif mask.any():
                _flag(strata, mask, x.rule_id)
                log.step(x.rule_id, "flag only: " + x.description, len(strata), len(strata))
    allowed_time = _values(time_resolution) or ["month", "quarter"]
    strata = keep(strata, strata.time_resolution.isin(allowed_time), "R05", f"time_resolution in {allowed_time}")
    ok = strata.geometry_status.eq("located") & (strata.grid_width <= max_cell_degrees) & (strata.grid_height <= max_cell_degrees)
    strata = keep(strata, ok, "R06", f"located cells no coarser than {max_cell_degrees} degrees "
                                     "(unlocated and surrogate areas removed)")
    strata = keep(strata, strata.stratum_status.isin(["reported", "effort_only"]), "R06s",
                  "stratum not masked and not catch-only")
    if rule.by_school:
        strata = keep(strata, ~strata.school_type.isin(["", "all"]), "R07",
                      "purse seine: one set type per stratum (pooled strata removed)")
    if school_types:
        requested = _values(school_types)
        absent = sorted(set(requested) - set(strata.school_type))
        strata = keep(strata, strata.school_type.isin(requested), "R07s", f"school/set types {requested}")
        if absent:
            log.warn("R07w", f"requested set types {absent} match no strata (present: {sorted(set(strata.school_type)) or 'none'})", len(strata))
    return root, meta, strata, wanted


def _attach(root, meta, strata, products, years, species, rule, *, catch_unit, effort_unit, zeros, verify, log):
    units = {p: (effort_unit or rule.unit_for(p)) for p in products}
    effort = read_modelprep("effort", run=root, product=products, year=_years(years), effort_unit=sorted(set(units.values())),
                            effort_status="reported", verify=verify,
                            columns=["stratum_id", "product", "effort_unit", "effort", "effort_scope"])
    effort = effort[effort.effort_unit.eq(effort["product"].map(units))].drop(columns="product")
    before = len(strata)
    data = strata.merge(effort, on="stratum_id", how="inner", validate="one_to_one")
    unit = "/".join(sorted(set(units.values())))
    log.step("R08", "reported effort > 0 in unit " + ", ".join(f"{p}: {u}" for p, u in units.items()), before, len(data))
    if rule.by_school:
        before = len(data)
        data = data[data.effort_scope.eq("school")].copy()
        log.step("R08s", "set-type CPUE: effort must be set-type specific (effort_scope 'school'; "
                         "stratum totals repeated across set types removed)", before, len(data))
    cunits = {p: (catch_unit or rule.catch_unit_for(p)) for p in products}
    allowed = ZERO_POLICIES[zeros]
    catch = read_modelprep("catch", run=root, product=products, year=_years(years), species=species,
                           catch_unit=sorted(set(cunits.values())), verify=verify,
                           columns=["stratum_id", "product", "catch_unit", "catch", "catch_status", "catch_basis"])
    catch = catch[catch.catch_unit.eq(catch["product"].map(cunits)) & catch.stratum_id.isin(set(data.stratum_id))]
    excluded = catch[~catch.catch_status.isin(allowed)]
    catch = catch[catch.catch_status.isin(allowed)].drop(columns="product")
    before = len(data)
    data = data.merge(catch, on="stratum_id", how="inner", validate="one_to_one")
    cunit = "/".join(sorted(set(cunits.values())))
    log.step("R09", f"{species} catch in " + ", ".join(f"{p}: {u}" for p, u in cunits.items())
             + f"; statuses {sorted(allowed)} (zero policy '{zeros}')", before, len(data))
    candidate = data.catch_status.eq("inferred_zero_candidate")
    data.loc[candidate, "catch"] = 0.0
    if candidate.any():
        _flag(data, candidate, "inferred_zero")
    held = excluded[excluded.catch_status.eq("inferred_zero_candidate")].groupby("product").size()
    if len(held):
        counts = ", ".join(f"{p}: {n:,}" for p, n in held.items())
        log.warn("W01", f"{counts} strata with effort have no recorded {species} catch (inferred zero candidates; e.g. "
                        "IOTC lists positive catches only, CCSBT JP/TW leave zeros blank). They are excluded under "
                        f"zero policy '{zeros}'. For encounter/delta models use zeros='with_inferred' (flagged "
                        "'inferred_zero') and test sensitivity.", len(data))
    return data, unit, cunit


def _finish(data, species, rule):
    data = data.copy()
    data["species_code"] = species
    data["quarter"] = ((data.month_start - 1) // 3 + 1).astype("Int64")
    data.loc[data.time_resolution.ne("month") & data.time_resolution.ne("quarter"), "quarter"] = pd.NA
    data["positive"] = data["catch"].gt(0)
    data["cpue"] = data["catch"] / data["effort"] * rule.cpue_scale
    data["cpue_scale"] = rule.cpue_scale
    ceiling = CPUE_CEILINGS.get((species, rule.gear_group))
    if ceiling is not None:
        per_hook = data.catch_unit.eq("fish") & data.effort_unit.eq("hooks") & ((data["catch"] / data["effort"]) > ceiling)
        _flag(data, per_hook, f"cpue_above_{ceiling}_per_hook")
    _flag(data, data["notes"].astype("string").ne(""), "source_notes")
    return data


OUTPUT = KEY_COLUMNS + ["effort", "effort_unit", "effort_scope", "catch", "catch_unit", "catch_status", "cpue",
                        "cpue_scale", "positive", "qc_flags", "stock_flag", "notes", "catch_basis", "stratum_id",
                        "source_rows", "dataset_id", "source_sha256", "view_group"]


def cpue_table(species, gear="longline", *, stock=None, products=None, years=None, months=None, fleets=None,
               exclude_fleets=None, catch_unit=None, effort_unit=None, zeros="reported", time_resolution=None,
               max_cell_degrees=5.0, school_types=None, apply_exclusions=True, allow_unrecommended=False,
               composition=False, run=None, library=None, verify=True, return_log=False):
    """One row per stratum with effort, the target species' catch and nominal CPUE.

    Defaults follow GEAR_RULES (e.g. longline: fish per 1000 hooks; purse seine:
    catch per set within one set type). zeros='reported' keeps positives and
    confirmed zeros only; 'with_inferred' adds inferred zeros (flagged). Rows are
    never aggregated: model fleet, set type, product and cell explicitly. The
    filter log is in .attrs['filter_log'] (or returned with return_log=True).
    """
    species = str(species).upper()
    rule = gear_rule(species, gear)
    _check(species, stock=stock, zeros=zeros, time_resolution=time_resolution)
    if not rule.recommended and not allow_unrecommended:
        raise ValueError(f"{species} {gear} CPUE is not recommended: {rule.note} "
                         "Pass allow_unrecommended=True for exploratory use.")
    log = _Log()
    root, meta, strata, used = _strata(species, gear, rule, stock=stock, products=products, years=years, months=months,
                                       fleets=fleets, exclude_fleets=exclude_fleets, time_resolution=time_resolution,
                                       max_cell_degrees=max_cell_degrees, school_types=school_types,
                                       apply_exclusions=apply_exclusions, run=run, library=library, verify=verify, log=log)
    data, unit, cunit = _attach(root, meta, strata, used, years, species, rule, catch_unit=catch_unit,
                                effort_unit=effort_unit, zeros=zeros, verify=verify, log=log)
    data = _finish(data, species, rule)
    if composition and len(data):
        units = {p: (catch_unit or rule.catch_unit_for(p)) for p in used}
        comp = read_modelprep("catch", run=root, product=used, year=sorted(set(data.year.dropna().astype(int))),
                              catch_unit=sorted(set(units.values())), catch_status=["positive", "zero_reported"],
                              verify=verify, columns=["stratum_id", "product", "species_code", "catch_unit", "catch"])
        comp = comp[comp.catch_unit.eq(comp["product"].map(units)) & comp.stratum_id.isin(set(data.stratum_id))
                    & comp.species_code.ne(species)]
        wide = comp.pivot_table(index="stratum_id", columns="species_code", values="catch", aggfunc="first")
        wide.columns = [f"comp_{c}" for c in wide.columns]
        data = data.merge(wide, left_on="stratum_id", right_index=True, how="left")
        log.step("C01", "composition: other species' catch in the same unit (positive or confirmed zero; "
                        "NA = not reported, e.g. absent from positives-only IOTC rows)", len(data), len(data))
    columns = OUTPUT + [c for c in data.columns if c.startswith("comp_")]
    data = data[columns].sort_values(["source", "product", "year", "month_start", "fleet", "south", "west"]).reset_index(drop=True)
    filter_log = log.frame()
    data.attrs.update(filter_log=filter_log, run_id=meta["run_id"], species=species, gear=gear,
                      catch_unit=cunit, effort_unit=unit, cpue_scale=rule.cpue_scale, zeros=zeros, rule_note=rule.note)
    return (data, filter_log) if return_log else data


def sdm_table(species, gear=None, *, stock=None, products=None, years=None, months=None, fleets=None,
              exclude_fleets=None, zeros="reported", time_resolution=None, max_cell_degrees=5.0,
              grid=None, period=None, apply_exclusions=True, allow_unrecommended=False, gfw=False,
              gfw_missing="raise", run=None, library=None, verify=True, return_log=False):
    """Presence/absence and abundance per stratum (or per aggregated cell and period).

    presence = 1 for positive catch, 0 for zeros allowed by the zero policy; other
    statuses are dropped. Gears whose zeros are not absences for the species
    (GearRule.absences False, e.g. SKJ longline, PBF) contribute presences only.
    With gear=None only recommended gears are used unless allow_unrecommended=True.
    CPUE-only defaults such as the BFT Japanese-longline fleet limit do not apply.
    Effort stays in its own unit (offset = log effort).

    grid (degrees) and period ('month', 'quarter', 'year') aggregate retained strata
    whose cells nest wholly inside the target cell: presence = any positive among
    them, absence = all retained strata are allowed zeros (strata removed by earlier
    rules are not consulted). Product, gear, set type and units stay separate;
    effort and catch are summed only within one unit; QC counts are kept.
    gfw=True (needs grid and period) adds GFW apparent fishing hours of the source's
    own RFMO region (never a union of overlapping regions) with gfw_status:
    'covered' (> 0 h), 'covered_zero' (report covers the cell-period, no activity),
    'not_covered' (no report: before 2012 or after the latest date) or 'skipped'
    (report failed its checksum with gfw_missing='skip').
    """
    species = str(species).upper()
    _check(species, stock=stock, zeros=zeros, time_resolution=time_resolution)
    if gfw and (not grid or period not in ("month", "quarter", "year")):
        raise ValueError("gfw=True needs grid (degrees) and period 'month', 'quarter' or 'year'")
    if gear:
        rule = gear_rule(species, gear)
        if not rule.recommended and not allow_unrecommended:
            raise ValueError(f"{species} {gear} is not recommended: {rule.note} Pass allow_unrecommended=True.")
        gears = [gear]
    else:
        gears = list(GEAR_RULES.get(species, {}))
    frames, logs = [], []
    for g in gears:
        rule = gear_rule(species, g)
        log = _Log()
        if not gear and not rule.recommended and not allow_unrecommended:
            log.step("skip", f"{g}: not recommended for {species} ({rule.note}); pass allow_unrecommended=True", 0, 0)
            logs.append(log.frame().assign(gear_group=g))
            continue
        try:
            root, meta, strata, used = _strata(species, g, rule, stock=stock, products=products, years=years,
                                               months=months, fleets=fleets, exclude_fleets=exclude_fleets,
                                               time_resolution=time_resolution, max_cell_degrees=max_cell_degrees,
                                               school_types=None, apply_exclusions=apply_exclusions, run=run,
                                               library=library, verify=verify, log=log, use_rule_fleets=False)
        except ValueError as exc:
            if gear:
                raise
            log.step("skip", f"{g}: {exc}", 0, 0)
            logs.append(log.frame().assign(gear_group=g))
            continue
        data, unit, cunit = _attach(root, meta, strata, used, years, species, rule, catch_unit=None,
                                    effort_unit=None, zeros=zeros, verify=verify, log=log)
        data = _finish(data, species, rule)
        if not rule.absences:
            before = len(data)
            data = data[data.positive].copy()
            log.step("R10", f"{g} zeros are not {species} absences ({rule.note}): presences only", before, len(data))
        data["presence"] = data["positive"].astype("Int64")
        data["offset_log_effort"] = np.log(data["effort"].astype(float))
        if grid or period:
            data = _aggregate(data, grid=grid, period=period, log=log)
        if gfw and len(data):
            data = _add_gfw(data, size=float(grid), period=period, missing=gfw_missing, library=library, log=log)
        frames.append(data.assign(gear_group=g))
        logs.append(log.frame().assign(gear_group=g))
    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    filter_log = pd.concat(logs, ignore_index=True) if logs else pd.DataFrame()
    data.attrs.update(filter_log=filter_log, species=species, zeros=zeros, grid=grid, period=period)
    return (data, filter_log) if return_log else data


def _period_columns(data, period):
    if period in (None, "native"):
        return data, ["year", "month_start", "month_end"]
    if period == "month":
        keep = data.time_resolution.eq("month")
        return data[keep].assign(month=data.month_start), ["year", "month"]
    if period == "quarter":
        keep = data.time_resolution.isin(["month", "quarter"])
        return data[keep].assign(quarter=((data.month_start - 1) // 3 + 1).astype("Int64")), ["year", "quarter"]
    if period == "year":
        return data, ["year"]
    raise ValueError("period must be 'native', 'month', 'quarter' or 'year'")


def _union(values):
    tokens = sorted({t for v in values for t in str(v).split(";") if t and t != "<NA>"})
    return ";".join(tokens)


def _aggregate(data, *, grid, period, log):
    before = len(data)
    if grid:
        cells = grid_status(data[["geometry_status", "west", "south", "grid_width", "grid_height"]].assign(
            geometry_status="located"), size=float(grid))
        data = data.assign(map_west=cells.map_west, map_south=cells.map_south, map_size=float(grid))
        data = data[data.map_west.notna()]
        log.step("A01", f"cells nesting wholly inside {grid} degree cells (others removed, never split)", before, len(data))
    else:
        data = data.assign(map_west=data.west, map_south=data.south, map_size=data.grid_width)
    before = len(data)
    products_before = set(data["product"])
    data, time_keys = _period_columns(data, period)
    log.step("A02", f"period '{period or 'native'}' (strata with a finer or equal native period only)", before, len(data))
    lost = sorted(products_before - set(data["product"]))
    if lost:
        log.warn("A02w", f"period '{period}' removed every stratum of {lost} (their native period is coarser); "
                         "use period='quarter' or 'year' to keep them", len(data))
    keys = ["species_code", "stock_id", "source", "product", "gear_group", "school_type", "effort_unit", "catch_unit",
            *time_keys, "map_west", "map_south", "map_size"]
    data = data.assign(_inferred=data.catch_status.eq("inferred_zero_candidate"),
                       _zero=data.catch_status.eq("zero_reported"), _flagged=data.qc_flags.ne(""))
    g = data.groupby(keys, dropna=False, observed=True, sort=True)
    out = g.agg(effort=("effort", "sum"), catch=("catch", "sum"), n_strata=("stratum_id", "size"),
                n_positive=("positive", "sum"), n_zero_reported=("_zero", "sum"), n_inferred_zero=("_inferred", "sum"),
                n_flagged=("_flagged", "sum"), qc_flags=("qc_flags", _union), stock_flags=("stock_flag", _union),
                effort_scope=("effort_scope", _union), fleets=("fleet", lambda x: ";".join(sorted(set(x)))),
                stratum_ids=("stratum_id", lambda x: ";".join(x))).reset_index()
    out["presence"] = (out.n_positive > 0).astype("Int64")
    out["lon"] = out.map_west + out.map_size / 2
    out["lat"] = out.map_south + out.map_size / 2
    out["offset_log_effort"] = np.log(out.effort.astype(float))
    log.step("A03", "aggregate: effort and catch summed within one unit; presence = any positive; "
                    "QC counts kept (n_inferred_zero, n_flagged)", len(data), len(out))
    return out


GFW_GEAR = {"longline": ["drifting_longlines"], "purse_seine": ["tuna_purse_seines"], "pole_and_line": ["pole_and_line"]}


def _add_gfw(data, *, size, period, missing, library, log):
    from .gfw import gfw_effort
    time_keys = {"month": ["year", "month"], "quarter": ["year", "quarter"], "year": ["year"]}[period]
    parts, notes = [], []
    for (source, gear_group), part in data.groupby(["source", "gear_group"]):
        hours = gfw_effort(source.upper(), years=sorted(set(part.year.dropna().astype(int))), gear_types=GFW_GEAR.get(gear_group),
                           grid=size, period=period, library=library, missing=missing)
        covered, skipped = hours.attrs.get("covered_months", {}), hours.attrs.get("skipped_years", [])
        merged = part.merge(hours[[*time_keys, "map_west", "map_south", "hours"]].rename(columns={"hours": "gfw_hours"}),
                            on=[*time_keys, "map_west", "map_south"], how="left")
        year = merged.year.astype("Int64")
        last = year.map(lambda y: covered.get(int(y)) if pd.notna(y) else None)
        if period == "month":
            in_report = last.notna() & (merged.month.astype("Float64") <= last.astype("Float64"))
        elif period == "quarter":
            in_report = last.notna() & ((merged.quarter.astype("Float64") - 1) * 3 + 1 <= last.astype("Float64"))
        else:
            in_report = last.notna()
        in_report = in_report.fillna(False).astype(bool)
        status = pd.Series("not_covered", index=merged.index, dtype="string")
        status[in_report & merged.gfw_hours.notna()] = "covered"
        status[in_report & merged.gfw_hours.isna()] = "covered_zero"
        status[year.isin(skipped).fillna(False)] = "skipped"
        merged["gfw_hours"] = merged.gfw_hours.where(~status.eq("covered_zero"), 0.0)
        merged["gfw_status"] = status
        parts.append(merged)
        notes.append(f"{source.upper()}/{gear_group}: spatial run {hours.attrs.get('spatial_run', '?')}"
                     + (f", skipped years {skipped}" if skipped else ""))
        if skipped:
            log.warn("G01w", f"GFW {source.upper()} years {skipped} failed their checksum and were skipped (gfw_status 'skipped')", len(merged))
    out = pd.concat(parts, ignore_index=True)
    counts = out.gfw_status.value_counts().to_dict()
    log.step("G01", f"GFW apparent fishing hours, own RFMO region only ({'; '.join(notes)}); status counts {counts}",
             len(data), len(out))
    return out
