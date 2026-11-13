"""WCPFC modelprep adapter on tiny synthetic ZIPs in a temporary library (no real data)."""
import csv
import zipfile

import numpy as np
import pandas as pd
import pytest

from tuna_data.library import Library
from tuna_data.modelprep import adapters, schema
from tuna_data.modelprep.adapters import wcpfc
from tuna_data.spatial_geometry import wcpfc_cell

SCHOOLS = ["UNA", "LOG", "DFAD", "AFAD", "OTH"]
LL = ["YY", "MM", "flag_code", "LAT5", "LON5", "cwp_grid", "HHOOKS"] + [
    f"{code}_{unit}" for code in ["ALB", "YFT", "BET", "MLS", "BLM", "BUM", "SWO", "OTH"] for unit in "CN"]
PS = (["YY", "qtr", "flag_code", "LAT1", "LON1", "cwp_grid", "DAYS"] + [f"SETS_{s}" for s in SCHOOLS]
      + [f"{code}_C_{s}" for s in SCHOOLS for code in ["SKJ", "YFT", "BET", "OTH"]])
LP = ["YY", "qtr", "flag_code", "LAT1", "LON1", "cwp_grid", "DAYS", "SKJ_C", "YFT_C", "BET_C", "OTH_C"]
FILES = {"wcpfc/827": "WCPFC_L_PUBLIC_BY_YY_MM_FLAG.csv", "wcpfc/1005": "WCPFC_S_PUBLIC_BY_YY_QTR_FLAG_1x1.csv",
         "wcpfc/830": "WCPFC_P_PUBLIC_BY_YY_QTR_FLAG_1x1.csv"}


def cwp(size, south, west):
    """CWP code written independently of wcpfc_cell: corner nearest (0, 0) plus quadrant."""
    quadrant = (1 if west >= 0 else 4) if south >= 0 else (2 if west >= 0 else 3)
    lat = south if south >= 0 else -(south + size)
    lon = west if west >= 0 else -(west + size)
    return f"{ {1: 5, 5: 6}[size] }{quadrant}{lat:02d}00{lon:03d}00"


def row(columns, **values):
    out = dict.fromkeys(columns, "0")
    out.update({k: str(v) for k, v in values.items()})
    return out


def library(tmp_path, dataset, rows, columns, *, coverage=None, name="lib"):
    """Import a source-like ZIP (all fields quoted, CRLF) into a temporary Library."""
    archive = tmp_path / f"{dataset.split('/')[1]}.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(FILES[dataset], pd.DataFrame(rows, columns=columns)
                   .to_csv(index=False, quoting=csv.QUOTE_ALL, lineterminator="\r\n"))
        if coverage is not None:
            member, frame = coverage
            z.writestr(member, frame.to_csv(index=False, lineterminator="\r\n"))
    lib = Library(tmp_path / name)
    lib.import_file(archive, dataset)
    return lib


def by_id(frame):
    return frame.set_index("stratum_id")


def catch_of(result, stratum_id, code, unit):
    c = result.catch
    hit = c[c.stratum_id.eq(stratum_id) & c.species_code.eq(code) & c.catch_unit.eq(unit)]
    assert len(hit) == 1
    return hit.iloc[0]


def qc(result):
    return {q["check"]: q["count"] for q in result.qc}


def note_tokens(result):
    notes = result.strata.notes
    return set(notes[notes.ne("")].str.split(";").explode())


def test_products_are_registered_with_wcpfc_conventions():
    assert adapters.module("wcpfc") is wcpfc
    assert set(wcpfc.PRODUCTS) == {"wcpfc_ll", "wcpfc_ps", "wcpfc_ps_days", "wcpfc_lp"}
    for name, spec in wcpfc.PRODUCTS.items():
        assert spec.product == name and spec.source == "wcpfc" and spec.zeros_explicit
        assert spec.catch_basis == "wcpfc_public_reported"
    ps, days = wcpfc.PRODUCTS["wcpfc_ps"], wcpfc.PRODUCTS["wcpfc_ps_days"]
    assert (ps.dataset_id, ps.member, ps.view_group) == (days.dataset_id, days.member, days.view_group) == (
        "wcpfc/1005", "WCPFC_S_PUBLIC_BY_YY_QTR_FLAG_1x1.csv", "wcpfc_ps")
    assert wcpfc.PRODUCTS["wcpfc_ll"].view_group == "wcpfc_ll" and wcpfc.PRODUCTS["wcpfc_lp"].view_group == "wcpfc_lp"
    with pytest.raises(ValueError, match="Unknown WCPFC product"):
        wcpfc.adapt(None, "wcpfc_xx")


def test_sw_corner_geometry_and_dateline_cells():
    # LAT/LON are south-west corners: a southern latitude is not shifted again.
    one = wcpfc_cell("01S", "179E", 1)
    assert (one["west"], one["south"], one["lon"], one["lat"]) == (179, -1, 179.5, -0.5)
    # '180W' is the first cell east of the dateline (180-179W / 180-175W), never 180E-181E.
    east = wcpfc_cell("01S", "180W", 1)
    assert (east["west"], east["lon"], east["grid_width"]) == (-180, -179.5, 1)
    assert wcpfc_cell("05S", "180W", 5)["lon"] == -177.5
    assert wcpfc_cell("00N", "179W", 1)["west"] == -179
    assert cwp(5, -5, 175) == "62000017500" and cwp(1, -1, -180) == "53000017900"


def test_longline_masking_hooks_units_and_flags(tmp_path):
    rows = [
        row(LL, YY=2024, MM=3, flag_code="JP", LAT5="05S", LON5="175E", cwp_grid=cwp(5, -5, 175), HHOOKS="1550.03",
            ALB_C="0.5", ALB_N=0, YFT_C=0, YFT_N=0, BET_C="4.2", BET_N=30, SWO_C=0, SWO_N=2, MLS_C=1, MLS_N=1),
        # Suppressed (three-vessel rule): flag is two spaces and every field is 0.
        row(LL, YY=2024, MM=3, flag_code="  ", LAT5="05S", LON5="180W", cwp_grid=cwp(5, -5, -180)),
        # Shown effort, every catch field 0 (JP at 35-40N in the real file).
        row(LL, YY=2024, MM=4, flag_code="JP", LAT5="40N", LON5="150E", cwp_grid=cwp(5, 40, 150), HHOOKS="1861.34"),
        # Suppressed row at an impossible position (the real file has a few at 60-85S).
        row(LL, YY=2024, MM=4, flag_code="  ", LAT5="85S", LON5="140W", cwp_grid=cwp(5, -85, -140)),
    ]
    lib = library(tmp_path, "wcpfc/827", rows, LL)
    result = wcpfc.adapt(lib, "wcpfc_ll")
    sha = lib.record("wcpfc/827")["sha256"]
    assert result.record["sha256"] == sha
    s, e = by_id(result.strata), by_id(result.effort)
    assert list(s.index) == ["wcpfc_ll|2", "wcpfc_ll|3", "wcpfc_ll|4", "wcpfc_ll|5"]
    assert s.source_rows.tolist() == ["2", "3", "4", "5"] and set(s.source_sha256) == {sha}
    assert s.stratum_status.tolist() == ["reported", "masked", "reported", "masked"]
    assert s.fleet.tolist() == ["JP", "", "JP", ""] and s.reporting_group.tolist() == ["JP|L", "|L", "JP|L", "|L"]
    assert set(s.gear_raw) == {"L"} and set(s.gear_group) == {"longline"} and set(s.school_type) == {""}
    assert set(s.time_resolution) == {"month"} and s.month_start.tolist() == s.month_end.tolist() == [3, 3, 4, 4]
    assert set(s.view_group) == {"wcpfc_ll"} and set(s.region_raw) == {"WCPFC"}
    # SW-corner 5x5 geometry, including the cell east of the dateline.
    assert s.loc["wcpfc_ll|2", ["west", "south", "lon", "lat", "grid_width"]].tolist() == [175, -5, 177.5, -2.5, 5]
    assert s.loc["wcpfc_ll|3", ["west", "lon"]].tolist() == [-180, -177.5]
    assert s.loc["wcpfc_ll|2", "grid_raw"] == "62000017500"
    # HHOOKS is hundreds of hooks; suppressed effort is masked NA, not zero.
    assert e.loc["wcpfc_ll|2", "effort"] == 155003
    assert (e.loc["wcpfc_ll|2", ["effort_unit", "effort_status", "effort_field", "effort_scope"]].tolist()
            == ["hooks", "reported", "HHOOKS", "stratum"])
    assert pd.isna(e.loc["wcpfc_ll|3", "effort"]) and e.loc["wcpfc_ll|3", "effort_status"] == "masked"
    # Fish and tonnes stay separate rows; only ALB/YFT/BET/SWO (no billfish/OTH, no SKJ).
    c = result.catch
    assert set(c.species_code) == {"ALB", "YFT", "BET", "SWO"} and set(c.catch_unit) == {"tonnes", "fish"}
    assert c.groupby("stratum_id").size().eq(8).all()
    assert catch_of(result, "wcpfc_ll|2", "ALB", "tonnes")[["catch", "catch_status", "catch_field"]].tolist() == [
        0.5, "positive", "ALB_C"]
    assert catch_of(result, "wcpfc_ll|2", "ALB", "fish")[["catch", "catch_status"]].tolist() == [0, "zero"]
    assert catch_of(result, "wcpfc_ll|2", "BET", "fish")["catch"] == 30
    masked = c[c.stratum_id.isin(["wcpfc_ll|3", "wcpfc_ll|5"])]
    assert len(masked) == 16 and masked.catch_status.eq("masked").all() and masked["catch"].isna().all()
    assert set(c.catch_basis) == {"wcpfc_public_reported"}
    assert s.loc["wcpfc_ll|2", "notes"] == "units_disagree"
    assert s.loc["wcpfc_ll|4", "notes"] == "all_catch_zero_with_effort"
    assert s.loc["wcpfc_ll|5", "notes"] == "implausible_location"
    zero = c[c.stratum_id.eq("wcpfc_ll|4")]
    assert zero.catch_status.eq("zero").all() and zero["catch"].eq(0).all()
    checks = qc(result)
    assert checks["masked_rows"] == checks["masked_rows_blank_flag"] == 2
    assert checks["units_disagree_ALB"] == checks["units_disagree_SWO"] == 1 and checks["units_disagree_YFT"] == 0
    assert checks["cwp_grid_mismatch"] == 0 and checks["unlocated_rows"] == 0
    assert checks["coverage_check_skipped"] == 0  # no coverage member in this synthetic ZIP
    assert note_tokens(result) <= set(wcpfc.NOTES)
    schema.validate(result.strata, result.effort, result.catch, final=False)


def ps_rows():
    return [
        # Shown: UNA and DFAD sets; OTH catch without OTH sets; LOG has only non-tuna catch.
        row(PS, YY=2024, qtr=2, flag_code="JP", LAT1="01S", LON1="179E", cwp_grid=cwp(1, -1, 179), DAYS="10.5",
            SETS_UNA="2.5", SETS_DFAD=3, SKJ_C_UNA=20, YFT_C_UNA=5, SKJ_C_DFAD=30, YFT_C_DFAD=2, BET_C_DFAD=1,
            SKJ_C_OTH=4, OTH_C_LOG="1.5"),
        # Suppressed rows: one keeps its flag (most PS/LP masks do), one has a blank flag.
        row(PS, YY=2024, qtr=3, flag_code="KR", LAT1="00N", LON1="180W", cwp_grid=cwp(1, 0, -180)),
        row(PS, YY=2024, qtr=4, flag_code="  ", LAT1="02S", LON1="160E", cwp_grid=cwp(1, -2, 160)),
        # Temperate Japanese purse seine north of 25N: OTH sets catching other species only.
        row(PS, YY=2024, qtr=1, flag_code="JP", LAT1="34N", LON1="140E", cwp_grid=cwp(1, 34, 140), DAYS=3,
            SETS_OTH=1, OTH_C_OTH=50),
        # Temperate purse seine south of 25S.
        row(PS, YY=2024, qtr=1, flag_code="NZ", LAT1="38S", LON1="175E", cwp_grid=cwp(1, -38, 175), DAYS=2,
            SETS_UNA=1, SKJ_C_UNA=10),
    ]


def test_purse_seine_school_melt_masks_and_catch_without_sets(tmp_path):
    lib = library(tmp_path, "wcpfc/1005", ps_rows(), PS)
    result = wcpfc.adapt(lib, "wcpfc_ps")
    s, e = by_id(result.strata), by_id(result.effort)
    assert list(s.index) == ["wcpfc_ps|2|UNA", "wcpfc_ps|2|DFAD", "wcpfc_ps|2|OTH", "wcpfc_ps|3", "wcpfc_ps|4",
                             "wcpfc_ps|5|OTH", "wcpfc_ps|6|UNA"]
    # Masked rows stay whole (no school split) and keep a reported flag.
    assert s.loc[["wcpfc_ps|3", "wcpfc_ps|4"], "stratum_status"].eq("masked").all()
    assert s.loc[["wcpfc_ps|3", "wcpfc_ps|4"], "school_type"].tolist() == ["", ""]
    assert s.loc[["wcpfc_ps|3", "wcpfc_ps|4"], "fleet"].tolist() == ["KR", ""]
    assert s.loc["wcpfc_ps|3", "reporting_group"] == "KR|S"
    masked_effort = e.loc["wcpfc_ps|3"]
    assert pd.isna(masked_effort.effort) and masked_effort.effort_status == "masked"
    assert masked_effort.effort_unit == "sets" and masked_effort.effort_scope == "stratum"
    masked_catch = result.catch[result.catch.stratum_id.eq("wcpfc_ps|3")]
    assert sorted(masked_catch.species_code) == ["BET", "SKJ", "YFT"] and masked_catch["catch"].isna().all()
    assert set(masked_catch.catch_status) == {"masked"}
    # Quarters span three months and are never spread into months.
    assert s.loc["wcpfc_ps|2|UNA", ["month_start", "month_end", "time_resolution"]].tolist() == [4, 6, "quarter"]
    assert s.loc["wcpfc_ps|3", ["month_start", "month_end"]].tolist() == [7, 9]
    assert s.loc["wcpfc_ps|3", ["west", "lon", "grid_width"]].tolist() == [-180, -179.5, 1]
    # Set-type effort is sets (fractional kept), never DAYS.
    assert set(result.effort.effort_unit) == {"sets"}
    assert e.loc["wcpfc_ps|2|UNA", ["effort", "effort_status", "effort_field", "effort_scope"]].tolist() == [
        2.5, "reported", "SETS_UNA", "school"]
    assert catch_of(result, "wcpfc_ps|2|UNA", "BET", "tonnes")[["catch", "catch_status"]].tolist() == [0, "zero"]
    assert catch_of(result, "wcpfc_ps|2|DFAD", "SKJ", "tonnes")["catch_field"] == "SKJ_C_DFAD"
    # Catch without sets: kept, effort missing (NA), flagged, catch-only stratum.
    oth = e.loc["wcpfc_ps|2|OTH"]
    assert pd.isna(oth.effort) and oth.effort_status == "missing"
    assert s.loc["wcpfc_ps|2|OTH", ["stratum_status", "notes"]].tolist() == ["catch_only", "catch_without_sets"]
    assert catch_of(result, "wcpfc_ps|2|OTH", "SKJ", "tonnes")["catch"] == 4
    # Sets with zero tuna catch remain a reported stratum with explicit zeros.
    assert s.loc["wcpfc_ps|5|OTH", ["stratum_status", "notes"]].tolist() == ["reported", "temperate_ps_area"]
    assert result.catch[result.catch.stratum_id.eq("wcpfc_ps|5|OTH")].catch_status.eq("zero").all()
    assert s.loc["wcpfc_ps|6|UNA", "notes"] == "temperate_ps_area_south"
    checks = qc(result)
    assert checks["masked_rows"] == 2 and checks["masked_rows_flag_kept"] == 1
    assert checks["catch_without_sets"] == 1 and checks["school_slots_other_species_only"] == 1
    assert checks["school_slots_not_emitted"] == 3 * 5 - 5
    assert set(result.catch.species_code) == {"SKJ", "YFT", "BET"} and set(result.catch.catch_unit) == {"tonnes"}
    assert note_tokens(result) == {"catch_without_sets", "temperate_ps_area", "temperate_ps_area_south"}


def test_purse_seine_days_view_pools_catch_but_never_sums_days_over_schools(tmp_path):
    lib = library(tmp_path, "wcpfc/1005", ps_rows(), PS)
    days = wcpfc.adapt(lib, "wcpfc_ps_days")
    sets = wcpfc.adapt(lib, "wcpfc_ps")
    s, e = by_id(days.strata), by_id(days.effort)
    assert list(s.index) == [f"wcpfc_ps_days|{i}" for i in range(2, 7)]
    assert set(s.school_type) == {"all"} and set(s.view_group) == set(sets.strata.view_group) == {"wcpfc_ps"}
    assert s.stratum_status.tolist() == ["reported", "masked", "masked", "reported", "reported"]
    # DAYS is one stratum total: emitted once per source row, not split or repeated per set type.
    assert e.loc["wcpfc_ps_days|2", ["effort", "effort_unit", "effort_scope", "effort_field"]].tolist() == [
        10.5, "days_fishing_searching", "stratum", "DAYS"]
    assert days.effort.effort.sum() == 10.5 + 3 + 2
    assert e.loc["wcpfc_ps_days|3", "effort_status"] == "masked"
    # Catch in one unit (tonnes) is summed over set types; both views carry the same catch.
    skj = catch_of(days, "wcpfc_ps_days|2", "SKJ", "tonnes")
    assert skj["catch"] == 54 and skj["catch_field"] == "+".join(f"SKJ_C_{x}" for x in SCHOOLS)
    assert catch_of(days, "wcpfc_ps_days|2", "BET", "tonnes")["catch"] == 1
    for code in ["SKJ", "YFT", "BET"]:
        assert (days.catch.loc[days.catch.species_code.eq(code), "catch"].sum()
                == sets.catch.loc[sets.catch.species_code.eq(code), "catch"].sum())
    assert s.loc["wcpfc_ps_days|5", "notes"] == "temperate_ps_area"
    zeros = days.catch[days.catch.stratum_id.eq("wcpfc_ps_days|5")]
    assert zeros.catch_status.eq("zero").all()


def lp_rows():
    return [
        row(LP, YY=2023, qtr=3, flag_code="JP", LAT1="30N", LON1="140E", cwp_grid=cwp(1, 30, 140), DAYS=120,
            SKJ_C="50.5", YFT_C=0, BET_C="1.25", OTH_C=2),
        # Pole-and-line masks keep the flag; there are no blank flags in the real file.
        row(LP, YY=2024, qtr=1, flag_code="SB", LAT1="08S", LON1="160E", cwp_grid=cwp(1, -8, 160)),
        # cwp_grid read with the SW corner as its digits (a one-cell shift): flagged, geometry from LAT/LON.
        row(LP, YY=2024, qtr=2, flag_code="JP", LAT1="05S", LON1="150E", cwp_grid="52050015000", DAYS=150,
            SKJ_C=10, YFT_C=1, BET_C=0),
    ]


def test_pole_and_line_flagged_zero_rows_are_masked_and_low_disclosure(tmp_path):
    coverage = pd.DataFrame({"yy": [2023, 2024, 2025], "days_reported": ["120", "150", ""],
                             "days_all": ["500", "900", "40"], "Days_reported_perc": ["24", "16.7", ""],
                             "strata_reported": ["1", "1", ""], "strata_all": ["4", "9", "2"],
                             "strata_reported_perc": ["25", "11.1", ""]})
    lib = library(tmp_path, "wcpfc/830", lp_rows(), LP,
                  coverage=("WCPFC_P_PUBLIC_BY_YY_QTR_FLAG_1x1_coverage.csv", coverage))
    result = wcpfc.adapt(lib, "wcpfc_lp")
    s, e = by_id(result.strata), by_id(result.effort)
    assert s.stratum_status.tolist() == ["reported", "masked", "reported"]
    assert s.fleet.tolist() == ["JP", "SB", "JP"] and set(s.gear_group) == {"pole_and_line"}
    assert s.loc["wcpfc_lp|2", ["month_start", "month_end", "time_resolution"]].tolist() == [7, 9, "quarter"]
    assert s.loc["wcpfc_lp|3", ["month_start", "month_end"]].tolist() == [1, 3]
    assert e.loc["wcpfc_lp|2", ["effort", "effort_unit", "effort_status"]].tolist() == [
        120, "days_fishing_searching", "reported"]
    assert pd.isna(e.loc["wcpfc_lp|3", "effort"]) and e.loc["wcpfc_lp|3", "effort_status"] == "masked"
    assert catch_of(result, "wcpfc_lp|2", "YFT", "tonnes")[["catch", "catch_status"]].tolist() == [0, "zero"]
    assert catch_of(result, "wcpfc_lp|3", "SKJ", "tonnes")["catch_status"] == "masked"
    assert s.notes.str.split(";").map(lambda flags: "low_disclosure_product" in flags).all()
    assert s.loc["wcpfc_lp|4", "notes"] == "cwp_grid_mismatch;low_disclosure_product"
    assert s.loc["wcpfc_lp|4", ["west", "south", "lat"]].tolist() == [150, -5, -4.5]
    checks = qc(result)
    assert checks["masked_rows"] == checks["masked_rows_flag_kept"] == 1 and checks["masked_rows_blank_flag"] == 0
    assert checks["coverage_years_checked"] == 2 and checks["coverage_effort_mismatch_years"] == 0
    assert checks["coverage_strata_mismatch_years"] == 0 and checks["coverage_years_without_rows"] == 1


def test_years_filter_and_pinned_snapshot(tmp_path):
    first = library(tmp_path, "wcpfc/830", lp_rows(), LP)
    old = first.record("wcpfc/830")["sha256"]
    changed = lp_rows()
    changed[0]["DAYS"] = "999"
    changed[2]["DAYS"] = "777"
    lib = library(tmp_path, "wcpfc/830", changed, LP)
    assert lib.record("wcpfc/830")["sha256"] != old
    result = wcpfc.adapt(lib, "wcpfc_lp", version=old, years=[2024])
    assert result.record["sha256"] == old and set(result.strata.source_sha256) == {old}
    assert result.strata.year.tolist() == [2024, 2024] and result.strata.source_rows.tolist() == ["3", "4"]
    assert result.effort.set_index("stratum_id").loc["wcpfc_lp|4", "effort"] == 150
    assert qc(result)["rows_outside_years"] == 1
    assert wcpfc.adapt(lib, "wcpfc_lp").effort.set_index("stratum_id").loc["wcpfc_lp|4", "effort"] == 777
    empty = wcpfc.adapt(lib, "wcpfc_lp", years=[1990])
    assert len(empty.strata) == len(empty.effort) == len(empty.catch) == 0


def test_schema_changes_and_unknown_tokens_stop_the_adapter(tmp_path):
    rows = lp_rows()
    lib = library(tmp_path, "wcpfc/830", [{k: v for k, v in r.items() if k != "BET_C"} for r in rows],
                  [c for c in LP if c != "BET_C"])
    with pytest.raises(ValueError, match="schema changed"):
        wcpfc.adapt(lib, "wcpfc_lp")
    rows[0]["DAYS"] = "conf"
    lib = library(tmp_path, "wcpfc/830", rows, LP, name="other")
    with pytest.raises(ValueError, match="Unparseable"):
        wcpfc.adapt(lib, "wcpfc_lp")


def test_negative_values_and_zero_effort_with_catch_are_never_zeros(tmp_path):
    rows = lp_rows()
    rows[0].update(DAYS="-5", YFT_C="-1")
    rows[2]["DAYS"] = "0"  # catch reported, effort coded 0: not a mask, not a real zero
    rows.append(row(LP, YY=2024, qtr=2, flag_code="PG", LAT1="", LON1="", cwp_grid="", DAYS=130, SKJ_C=5))
    lib = library(tmp_path, "wcpfc/830", rows, LP)
    result = wcpfc.adapt(lib, "wcpfc_lp")
    s, e = by_id(result.strata), by_id(result.effort)
    # No usable coordinates: kept as an unlocated stratum, never placed at (0, 0).
    assert s.loc["wcpfc_lp|5", ["geometry_status", "stratum_status"]].tolist() == ["unlocated", "reported"]
    assert s.loc["wcpfc_lp|5", ["west", "south", "lon", "lat"]].isna().all()
    assert qc(result)["unlocated_rows"] == 1 and s.loc["wcpfc_lp|5", "notes"] == "low_disclosure_product"
    assert qc(result)["cwp_grid_mismatch"] == 1  # only the shifted code of line 4, not the unlocated row
    assert e.loc["wcpfc_lp|2", "effort_status"] == "invalid" and pd.isna(e.loc["wcpfc_lp|2", "effort"])
    assert e.loc["wcpfc_lp|4", "effort_status"] == "missing" and pd.isna(e.loc["wcpfc_lp|4", "effort"])
    assert s.loc["wcpfc_lp|4", "stratum_status"] == "catch_only"
    assert "catch_without_effort" in s.loc["wcpfc_lp|4", "notes"].split(";")
    assert {"invalid_effort", "negative_catch"} <= set(s.loc["wcpfc_lp|2", "notes"].split(";"))
    yft = catch_of(result, "wcpfc_lp|2", "YFT", "tonnes")
    assert yft.catch_status == "blank" and pd.isna(yft["catch"])
    assert not np.any(result.effort.effort_status.eq("zero"))
    schema.validate(result.strata, result.effort, result.catch, final=False)
