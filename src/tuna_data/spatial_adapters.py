"""Adapters for selected RFMO spatial products; units and views stay separate."""
from __future__ import annotations

import re
import pandas as pd

from .harmonize import GEARS, measurement
from .spatial_geometry import centered_cell, ccsbt_cell, cwp_iotc, wcpfc_cell

PRODUCTS = {
    "iattc_ll": ("iattc/publiclltunabillfish", "PublicLLTunaBillfishMt.csv"),
    "iattc_ps": ("iattc/publicpstuna", "PublicPSTunaFlag.csv"),
    "iattc_lp": ("iattc/publiclptunaflag", "PublicLPTunaFlag.csv"),
    "wcpfc_ll": ("wcpfc/827", "WCPFC_L_PUBLIC_BY_YY_MM_FLAG.csv"),
    "wcpfc_ps": ("wcpfc/1005", "WCPFC_S_PUBLIC_BY_YY_QTR_FLAG_1x1.csv"),
    "wcpfc_lp": ("wcpfc/830", "WCPFC_P_PUBLIC_BY_YY_QTR_FLAG_1x1.csv"),
    "iotc_ca": ("iotc/iotc_datasets_ce", "-CA-"),
    "iotc_ef": ("iotc/iotc_datasets_ce", "-EF-"),
    "iccat_catdis": ("iccat/cdis5024_all", ".csv"),
    "ccsbt_grid": ("ccsbt/catchbyymgolola", "CatchByYMGOLoLa"),
}
COLUMNS = [
    "record_id", "source", "product", "dataset_id", "source_sha256", "source_member",
    "source_row", "value_field", "year", "month_start", "month_end", "time_resolution",
    "grid_raw", "lon", "lat", "west", "south", "grid_width", "grid_height", "geometry_status",
    "gear_raw", "gear_group", "flag_raw", "fleet_raw", "school_type", "species_code",
    "measure", "unit", "value_raw", "value", "value_status", "catch_basis", "region_raw",
]


def require(raw, fields):
    absent = set(fields) - set(raw)
    if absent:
        raise ValueError(f"Spatial schema changed: {sorted(absent)}")


def signed(values):
    s = values.astype("string").str.strip()
    return pd.to_numeric(s.mask(s.isin(["", "NA", "N/A", "NaN"])), errors="raise").astype("Float64")


def _geometry(frame, cells):
    geo = pd.DataFrame(cells, index=frame.index)
    for column in geo:
        frame[column] = geo[column]


def _base(raw, product, dataset, sha, member):
    t = pd.DataFrame(index=raw.index, columns=COLUMNS)
    t["source"] = product.split("_")[0]
    t["product"] = product
    t["dataset_id"] = dataset
    t["source_sha256"] = sha
    t["source_member"] = member
    t["source_row"] = raw.index + (8 if product == "ccsbt_grid" else 2)
    t["flag_raw"] = ""
    t["fleet_raw"] = ""
    t["school_type"] = ""
    t["grid_raw"] = ""
    return t


def _period(t, raw, year, start=None, end=None, quarter=None):
    require(raw, [year])
    t["year"] = measurement(raw[year]).astype("Int64")
    if quarter:
        q = measurement(raw[quarter])
        if (~q.isin([1, 2, 3, 4])).any():
            raise ValueError("Invalid quarter")
        t["month_start"], t["month_end"] = (q - 1) * 3 + 1, q * 3
        t["time_resolution"] = "quarter"
    elif start:
        t["month_start"] = measurement(raw[start])
        t["month_end"] = measurement(raw[end or start])
        t["time_resolution"] = "reported_period"
        t.loc[t.month_start.eq(t.month_end).fillna(False), "time_resolution"] = "month"
        t.loc[t.month_start.isna(), "time_resolution"] = "unspecified_within_year"
    else:
        t["time_resolution"] = "year"
    for c in ["year", "month_start", "month_end"]:
        nums = pd.to_numeric(t[c], errors="raise").astype("Float64")
        if (nums.dropna() % 1 != 0).any():
            raise ValueError("Non-integral time field")
        t[c] = nums.astype("Int64")
    if t.year.isna().any() or (t.month_start.dropna() < 1).any() or (t.month_end.dropna() > 12).any() or (t.month_end < t.month_start).any():
        raise ValueError("Invalid spatial time period")


def _gear(t, raw_codes, source):
    t["gear_raw"] = raw_codes
    mapping = GEARS[source].copy()
    if source == "iccat":
        mapping["oth"] = "other"
    if source == "ccsbt":
        mapping = {"LL": "longline", "PS": "purse_seine", "BB": "pole_and_line", "GILL": "gillnet",
                   "HAND": "handline", "TROL": "troll", "SURF": "surface_unspecified", "UNCL": "unknown"}
    t["gear_group"] = t.gear_raw.map(mapping).fillna("unmapped:" + source + ":" + t.gear_raw.astype("string"))


def _emit(base, raw, field, *, unit, species="", measure="catch", school=None, missing=None, factor=1):
    require(raw, [field])
    t = base.copy()
    t["value_field"] = field
    t["species_code"] = species
    t["measure"] = measure
    t["unit"] = unit
    t["value_raw"] = raw[field].astype("string")
    t["value"] = measurement(raw[field]) * factor
    t["value_status"] = t.value.isna().map({True: "missing", False: "reported"})
    if school is not None:
        t["school_type"] = school
    if missing is not None:
        mask, status = missing
        t.loc[mask.fillna(False), "value"] = pd.NA
        t.loc[mask.fillna(False), "value_status"] = status
    t["record_id"] = (t.dataset_id + "|" + t.source_sha256 + "|" + t.source_member + "|"
                       + t.source_row.astype(str) + "|" + field)
    for c in t:
        if c not in {"source_row", "year", "month_start", "month_end", "lon", "lat", "west", "south",
                     "grid_width", "grid_height", "value"}:
            t[c] = t[c].astype("string").fillna("")
    for c in ["lon", "lat", "west", "south", "grid_width", "grid_height", "value"]:
        t[c] = t[c].astype("Float64")
    return t


def adapt(raw, product, *, dataset, sha, member, species):
    """Return measurement-long records, with effort emitted once (not per species)."""
    b = _base(raw, product, dataset, sha, member)
    frames = []
    source = product.split("_")[0]
    if source == "iattc":
        ll = product == "iattc_ll"
        size, lat, lon = (5, "LatC5", "LonC5") if ll else (1, "LatC1", "LonC1")
        require(raw, [lat, lon, "Flag"])
        _period(b, raw, "Year", "Month")
        _geometry(b, [centered_cell(y, x, size) for y, x in zip(signed(raw[lat]), signed(raw[lon]))])
        b["flag_raw"] = raw.Flag
        b["region_raw"] = "EPO"
        _gear(b, {"iattc_ll": "LL", "iattc_ps": "PS", "iattc_lp": "LP"}[product], source)
        b["catch_basis"] = "iattc_spatial_retained_unraised"
        if ll:
            # Only the weight-preferred view is selected. The number-preferred
            # file is an alternative view of the same catch, not extra records.
            ncols, wcols = [c for c in raw if c.endswith("n")], [c for c in raw if c.endswith("mt")]
            n = raw[ncols].apply(measurement)
            w = raw[wcols].apply(measurement)
            row_numbers_only = w.sum(axis=1).eq(0) & n.sum(axis=1).gt(0)
            for code in species:
                if code + "mt" not in raw:
                    continue
                only_n = row_numbers_only | (n[code + "n"].gt(0) & w[code + "mt"].eq(0))
                frames.append(_emit(b, raw, code + "mt", unit="tonnes", species=code,
                                    missing=(only_n, "weight_unavailable_number_reported")))
                # These are residual number-only catches in this preferred view.
                frames.append(_emit(b.assign(catch_basis="iattc_number_only_residual"), raw, code + "n",
                                    unit="fish", species=code,
                                    missing=(w[code + "mt"].gt(0) & n[code + "n"].eq(0), "number_not_in_weight_view")))
            frames.append(_emit(b, raw, "Hooks", unit="hooks", measure="effort"))
        else:
            for code in species:
                if code in raw:
                    frames.append(_emit(b, raw, code, unit="tonnes", species=code))
            frames.append(_emit(b, raw, "NumSets", unit="sets", measure="effort"))
    elif source == "wcpfc":
        ll, ps = product == "wcpfc_ll", product == "wcpfc_ps"
        size, lat, lon = (5, "LAT5", "LON5") if ll else (1, "LAT1", "LON1")
        require(raw, [lat, lon, "flag_code", "cwp_grid"])
        _period(b, raw, "YY", "MM" if ll else None, quarter=None if ll else "qtr")
        _geometry(b, [wcpfc_cell(y, x, size) for y, x in zip(raw[lat], raw[lon])])
        b["grid_raw"] = raw.cwp_grid
        b["flag_raw"] = raw.flag_code  # retain blanks; they carry masking information
        b["region_raw"] = "WCPFC"
        _gear(b, "L" if ll else ("S" if ps else "P"), source)
        b["catch_basis"] = "wcpfc_public_spatial_reported"
        numeric = [c for c in raw if c in {"HHOOKS", "DAYS"} or "_C" in c or "_N" in c or c.startswith("SETS_")]
        values = raw[numeric].apply(measurement)
        masked = raw.flag_code.str.strip().eq("") | values.eq(0).all(axis=1)
        miss = (masked, "suppressed_or_all_zero_stratum")
        if ps:
            for school in ["UNA", "LOG", "DFAD", "AFAD", "OTH"]:
                for code in species:
                    field = code + "_C_" + school
                    if field in raw:
                        frames.append(_emit(b, raw, field, unit="tonnes", species=code, school=school, missing=miss))
                frames.append(_emit(b, raw, "SETS_" + school, unit="sets", measure="effort", school=school, missing=miss))
        else:
            for code in species:
                for suffix, unit in [("_C", "tonnes"), ("_N", "fish")]:
                    if code + suffix in raw:
                        frames.append(_emit(b, raw, code + suffix, unit=unit, species=code, missing=miss))
        frames.append(_emit(b, raw, "HHOOKS" if ll else "DAYS", unit="hooks" if ll else "fishing_days",
                            measure="effort", factor=100 if ll else 1, missing=miss))
    elif source == "iotc":
        require(raw, ["FISHING_GROUND_CODE", "GEAR_CODE", "FLEET_CODE"])
        _period(b, raw, "YEAR", "MONTH_START", "MONTH_END")
        _geometry(b, [cwp_iotc(c) for c in raw.FISHING_GROUND_CODE])
        b["grid_raw"] = raw.FISHING_GROUND_CODE
        b["flag_raw"], b["fleet_raw"] = raw.FLEET_CODE, raw.FLEET_CODE
        b["region_raw"] = "Indian"
        _gear(b, raw.GEAR_CODE, source)
        b["catch_basis"] = "iotc_georeferenced_reported"
        if product == "iotc_ca":
            b["school_type"] = raw.CATCH_SCHOOL_TYPE_CODE
            units = {"MT": "tonnes", "NO": "fish"}
            if set(raw.CATCH_UNIT_CODE) - set(units):
                raise ValueError("Unreviewed IOTC catch unit")
            t = _emit(b, raw, "CATCH", unit=raw.CATCH_UNIT_CODE.map(units), species=raw.SPECIES_CODE)
            frames.append(t[t.species_code.isin(species)])
        else:
            b["school_type"] = raw.SCHOOL_TYPE_CODE
            frames.append(_emit(b, raw, "EFFORT", unit="iotc:" + raw.EFFORT_UNIT_CODE, measure="effort"))
    elif source == "iccat":
        require(raw, ["yLat5ctoid", "xLon5ctoid", "CWPCode", "GearGrp", "FlagName", "FleetCode", "SchoolType", "SpeciesCode"])
        _period(b, raw, "YearC", quarter="Trimester")
        _geometry(b, [centered_cell(y, x, 5) for y, x in zip(signed(raw.yLat5ctoid), signed(raw.xLon5ctoid))])
        b["grid_raw"] = raw.CWPCode
        b["flag_raw"], b["fleet_raw"], b["school_type"] = raw.FlagName, raw.FleetCode, raw.SchoolType
        b["region_raw"] = "Atlantic"
        b["catch_basis"] = "iccat_CATDIS_raised_landings_dead_discards"
        _gear(b, raw.GearGrp, source)
        t = _emit(b, raw, "Catch_t", unit="tonnes", species=raw.SpeciesCode)
        frames.append(t[t.species_code.isin(species)])
    elif source == "ccsbt":
        _period(b, raw, "Calendar\nYear", "Month")
        _geometry(b, [ccsbt_cell(y, x) for y, x in zip(signed(raw.Latitude), signed(raw.Longitude))])
        # Latitude is signed, so it is parsed independently of nonnegative catch.
        b["region_raw"] = raw.Ocean
        b["catch_basis"] = "ccsbt_spatial_reported"
        _gear(b, raw.Gear, source)
        frames.append(_emit(b, raw, "Tonnes", unit="tonnes", species="SBF"))
    else:
        raise ValueError(product)
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    result = pd.concat(frames, ignore_index=True)
    if result.record_id.duplicated().any():
        raise ValueError("Duplicate source measurement identity")
    return result
