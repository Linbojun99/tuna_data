"""IATTC modelprep adapter on tiny synthetic ZIPs in a temporary library (no real data)."""
import zipfile

import pandas as pd
import pytest

from tuna_data.library import Library
from tuna_data.modelprep import schema
from tuna_data.modelprep.adapters import iattc

NUM, MT = iattc.NUM_VIEW, iattc.MT_VIEW
SPECIES = ["ALB", "BET", "PBF", "SKJ", "TUN", "YFT", "SWO"]
LL_COLUMNS = ["Year", "Month", "Flag", "LatC5", "LonC5", "Hooks"] + [s + "n" for s in SPECIES] + [s + "mt" for s in SPECIES]
PS_CATCH = ["ALB", "BET", "BKJ", "BZX", "FRZ", "PBF", "SKJ", "TUN", "YFT"]


def csv(columns, rows, default="0.00"):
    lines = [",".join(f'"{c}"' for c in columns)]
    lines += [",".join(str(row.get(c, default)) for c in columns) for row in rows]
    return "\r\n".join(lines) + "\r\n"


def library(tmp_path, dataset, members, name="snapshot.zip"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        for member, text in members.items():
            archive.writestr(member, text)
    lib = Library(tmp_path / "library")
    lib.import_file(path, dataset)
    return lib


def ll_row(year, flag, *, month=1, lat="-2.50", lon="-147.50", hooks="1000"):
    return {"Year": year, "Month": month, "Flag": f'"{flag}"', "LatC5": lat, "LonC5": lon, "Hooks": hooks}


def ll_lib(tmp_path, rows, mt_order=None):
    """rows: (key, Num-view catch, Mt-view catch); unlisted catch cells are 0.00."""
    num = [{**key, **n} for key, n, _ in rows]
    mt = [{**key, **m} for key, _, m in rows]
    if mt_order:
        mt = [mt[i] for i in mt_order]
    return library(tmp_path, "iattc/publiclltunabillfish",
                   {"LLTunaBillfish-AtunPicudo.pdf": "readme", NUM: csv(LL_COLUMNS, num), MT: csv(LL_COLUMNS, mt)})


def cell(result, line, species, unit):
    c = result.catch
    row = c[c.stratum_id.eq(f"iattc_ll|{line}") & c.species_code.eq(species) & c.catch_unit.eq(unit)]
    assert len(row) == 1
    return row.catch_status.iloc[0], row.catch.iloc[0]


def stratum(result, line, product="iattc_ll"):
    return result.strata.set_index("stratum_id").loc[f"{product}|{line}"]


def test_num_and_mt_views_pair_into_one_stratum_per_key(tmp_path):
    rows = [(ll_row(1954, "JPN", hooks="13485"), {"BETn": "163.00", "YFTn": "45.00"}, {"BETn": "163.00", "YFTn": "45.00"}),
            # Dual-reported catch: Num keeps the numbers, Mt keeps the weight.
            (ll_row(2010, "TWN", lat="-22.50", lon="-132.50", hooks="17414"), {"BETn": "22.00"}, {"BETmt": "0.63"})]
    result = iattc.adapt(ll_lib(tmp_path, rows), "iattc_ll")
    s, e, c = result.strata, result.effort, result.catch
    assert s.stratum_id.tolist() == ["iattc_ll|2", "iattc_ll|3"]
    assert s.source_rows.tolist() == ["2", "3"]
    assert (s.source_member == f"{NUM};{MT}").all() and (s.view_group == "iattc_ll").all()
    assert (s.source_sha256 == result.record["sha256"]).all()
    assert s.fleet.tolist() == ["JPN", "TWN"] and s.reporting_group.tolist() == ["JPN|LL", "TWN|LL"]
    assert (s.gear_raw == "LL").all() and (s.gear_group == "longline").all() and (s.school_type == "").all()
    assert (s.time_resolution == "month").all() and (s.region_raw == "EPO").all()
    # Hooks once per stratum, never summed over the two views.
    assert e.effort.tolist() == [13485, 17414] and (e.effort_unit == "hooks").all() and (e.effort_field == "Hooks").all()
    # Six SPECIES columns present (TUN is not SBF) x two units, nothing else.
    assert len(c) == 2 * 6 * 2 and set(c.species_code) == {"YFT", "BET", "SKJ", "ALB", "PBF", "SWO"}
    assert set(c.catch_unit) == {"fish", "tonnes"} and (c.catch_basis == "iattc_flag_reported_retained").all()
    assert cell(result, 3, "BET", "fish") == ("positive", 22) and cell(result, 3, "BET", "tonnes") == ("positive", 0.63)
    assert cell(result, 2, "BET", "fish") == ("positive", 163)
    assert cell(result, 2, "PBF", "fish") == ("zero", 0) and cell(result, 2, "PBF", "tonnes") == ("zero", 0)
    assert c[c.catch_unit.eq("fish")].catch_field.str.endswith("n").all()
    assert c[c.catch_unit.eq("tonnes")].catch_field.str.endswith("mt").all()


def test_reordered_views_pair_by_key_and_mismatched_views_fail(tmp_path):
    rows = [(ll_row(2020, "JPN"), {"BETn": "5.00"}, {"BETn": "5.00"}),
            (ll_row(2020, "KOR", month=2), {"BETn": "7.00"}, {"BETmt": "0.30"})]
    result = iattc.adapt(ll_lib(tmp_path / "a", rows, mt_order=[1, 0]), "iattc_ll")
    assert result.strata.source_rows.tolist() == ["2;3", "3;2"]
    assert cell(result, 3, "BET", "tonnes") == ("positive", 0.3)
    one = [rows[0]]
    lib = library(tmp_path / "b", "iattc/publiclltunabillfish",
                  {NUM: csv(LL_COLUMNS, [{**k, **n} for k, n, _ in rows]), MT: csv(LL_COLUMNS, [{**k, **m} for k, _, m in one])})
    with pytest.raises(ValueError, match="different strata"):
        iattc.adapt(lib, "iattc_ll")


def test_unit_incomplete_in_both_directions(tmp_path):
    rows = [
        # Part of the YFT catch was reported only in weight: Num YFTmt > 0, so fish are incomplete.
        (ll_row(1983, "MEX"), {"YFTn": "39.00", "YFTmt": "10.15", "SWOn": "19.00"}, {"YFTmt": "16.21", "SWOmt": "0.62"}),
        # Numbers only: Mt BETn > 0, so the tonnes (0.00) are incomplete, not zero.
        (ll_row(1995, "JPN"), {"BETn": "398.00"}, {"BETn": "398.00"}),
        # Weight only: Num BETmt > 0, so the fish (0) are incomplete.
        (ll_row(2005, "CHN"), {"BETmt": "68.61"}, {"BETmt": "68.61"}),
    ]
    result = iattc.adapt(ll_lib(tmp_path, rows), "iattc_ll")
    status, value = cell(result, 2, "YFT", "fish")
    assert status == "unit_incomplete" and pd.isna(value)
    assert cell(result, 2, "YFT", "tonnes") == ("positive", 16.21)
    assert cell(result, 2, "SWO", "fish") == ("positive", 19) and cell(result, 2, "SWO", "tonnes") == ("positive", 0.62)
    assert cell(result, 3, "BET", "fish") == ("positive", 398)
    status, value = cell(result, 3, "BET", "tonnes")
    assert status == "unit_incomplete" and pd.isna(value)
    status, value = cell(result, 4, "BET", "fish")
    assert status == "unit_incomplete" and pd.isna(value)
    assert cell(result, 4, "BET", "tonnes") == ("positive", 68.61)
    assert cell(result, 4, "SKJ", "fish") == ("zero", 0)
    qc = {q["check"]: q["count"] for q in result.qc}
    assert qc["unit_incomplete_fish"] == 2 and qc["unit_incomplete_tonnes"] == 1


def test_kor_1987_2001_same_fish_in_both_units(tmp_path):
    same = {"YFTn": "17.00", "YFTmt": "1.83", "SKJmt": "0.02", "ALBn": "5.00"}
    rows = [(ll_row(1992, "KOR"), same, same),
            (ll_row(1987, "KOR", month=2), same, same),
            (ll_row(2001, "KOR", month=3), same, same),
            (ll_row(2002, "KOR"), same, same),
            (ll_row(1986, "KOR"), same, same),
            (ll_row(1992, "JPN"), same, same)]
    result = iattc.adapt(ll_lib(tmp_path, rows), "iattc_ll")
    for line in (2, 3, 4):
        assert "kor_dual_unit_unsplit" in stratum(result, line).notes.split(";")
        # Both units carry the same fish: both are complete.
        assert cell(result, line, "YFT", "fish") == ("positive", 17)
        assert cell(result, line, "YFT", "tonnes") == ("positive", 1.83)
        # A species reported in one unit only is not a zero in the other unit.
        assert cell(result, line, "SKJ", "fish")[0] == "unit_incomplete"
        assert cell(result, line, "SKJ", "tonnes") == ("positive", 0.02)
        assert cell(result, line, "ALB", "fish") == ("positive", 5)
        assert cell(result, line, "ALB", "tonnes")[0] == "unit_incomplete"
        assert cell(result, line, "BET", "fish") == ("zero", 0) and cell(result, line, "BET", "tonnes") == ("zero", 0)
    for line in (5, 6, 7):  # outside KOR 1987-2001: n and mt are additive parts
        assert "kor_dual_unit_unsplit" not in stratum(result, line).notes
        assert cell(result, line, "YFT", "fish")[0] == "unit_incomplete"
        assert cell(result, line, "YFT", "tonnes")[0] == "unit_incomplete"
    qc = {q["check"]: q["count"] for q in result.qc}
    assert qc["kor_dual_unit_cells"] == 3 and qc["note:kor_dual_unit_unsplit"] == 3


def test_hooks_coded_zero_is_missing_effort_and_flags(tmp_path):
    rows = [(ll_row(2023, "CHN", hooks="0"), {"BETn": "55.00"}, {"BETmt": "2.30"}),
            (ll_row(2023, "KOR", hooks="2000"), {"BETn": "9.00"}, {"BETn": "9.00"}),
            (ll_row(2020, "ESP", hooks=""), {"SWOn": "3.00"}, {"SWOn": "3.00"}),
            (ll_row(2020, "USA", hooks="-5"), {}, {}),
            (ll_row(2020, "TWN", hooks="100"), {"BETn": "40.00"}, {"BETn": "40.00"})]
    result = iattc.adapt(ll_lib(tmp_path, rows), "iattc_ll")
    e = result.effort.set_index("stratum_id")
    assert e.loc["iattc_ll|2", "effort_status"] == "missing" and pd.isna(e.loc["iattc_ll|2", "effort"])
    zero = stratum(result, 2)
    assert zero.stratum_status == "catch_only"
    assert set(zero.notes.split(";")) == {"hooks_coded_zero", "suspect_effort", "catch_without_effort"}
    assert e.loc["iattc_ll|3", "effort_status"] == "reported" and stratum(result, 3).notes == "suspect_effort"
    assert e.loc["iattc_ll|4", "effort_status"] == "missing" and "hooks_coded_zero" not in stratum(result, 4).notes
    assert e.loc["iattc_ll|5", "effort_status"] == "invalid" and pd.isna(e.loc["iattc_ll|5", "effort"])
    assert stratum(result, 5).stratum_status == "catch_only" and "invalid_effort" in stratum(result, 5).notes
    assert stratum(result, 6).notes == "high_catch_per_hook"  # 0.4 fish per hook
    qc = {q["check"]: q["count"] for q in result.qc}
    assert qc["effort_coded_zero"] == 1 and qc["note:hooks_coded_zero"] == 1 and qc["rows_dropped_empty"] == 0


def ps_lib(tmp_path):
    settype = [dict(Year=2020, Month=3, SetType='"DEL"', LatC1="18.50", LonC1="-114.50", NumSets=8, YFT="26.30"),
               dict(Year=2020, Month=3, SetType='"OBJ"', LatC1="18.50", LonC1="-114.50", NumSets=2, BET="4.00")]
    flag = [dict(Year=2020, Month=3, Flag='"ECU"', LatC1="18.50", LonC1="-114.50", NumSets=7, YFT="20.00", BET="4.00"),
            dict(Year=2020, Month=3, Flag='"Other"', LatC1="18.50", LonC1="-114.50", NumSets=3, YFT="6.30")]
    return library(tmp_path, "iattc/publicpstuna", {
        "PSTuna-Atun.pdf": "readme",
        "PublicPSTunaSetType.csv": csv(["Year", "Month", "SetType", "LatC1", "LonC1", "NumSets", *PS_CATCH], settype),
        "PublicPSTunaFlag.csv": csv(["Year", "Month", "Flag", "LatC1", "LonC1", "NumSets", *PS_CATCH], flag)})


def test_ps_settype_and_flag_are_separate_products_in_one_view_group(tmp_path):
    a, b = iattc.PRODUCTS["iattc_ps_settype"], iattc.PRODUCTS["iattc_ps_flag"]
    assert a.view_group == b.view_group == "iattc_ps" and a.dataset_id == b.dataset_id
    assert a.member != b.member and a.zeros_explicit and b.zeros_explicit
    lib = ps_lib(tmp_path)
    by_set, by_flag = iattc.adapt(lib, "iattc_ps_settype"), iattc.adapt(lib, "iattc_ps_flag")
    s, f = by_set.strata, by_flag.strata
    assert s.stratum_id.str.startswith("iattc_ps_settype|").all() and f.stratum_id.str.startswith("iattc_ps_flag|").all()
    assert not set(s.stratum_id) & set(f.stratum_id)
    assert s.school_type.tolist() == ["DEL", "OBJ"] and (s.fleet == "").all()
    assert s.reporting_group.tolist() == ["settype:DEL|PS", "settype:OBJ|PS"]
    assert f.fleet.tolist() == ["ECU", "Other"] and (f.school_type == "").all()
    assert f.reporting_group.tolist() == ["ECU|PS", "Other|PS"]
    assert f.notes.tolist() == ["", "pooled_flag_other"] and (s.notes == "").all()
    assert (by_set.effort.effort_scope == "school").all() and (by_flag.effort.effort_scope == "stratum").all()
    assert by_set.effort.effort.sum() == by_flag.effort.effort.sum() == 10
    assert (by_set.effort.effort_unit == "sets").all() and (by_set.effort.effort_field == "NumSets").all()
    for result in (by_set, by_flag):
        c = result.catch
        assert set(c.species_code) == {"ALB", "BET", "PBF", "SKJ", "YFT"} and (c.catch_unit == "tonnes").all()
        assert (c.catch_field == c.species_code).all()
        assert set(c.catch_status) == {"positive", "zero"} and (c[c.catch_status.eq("zero")].catch == 0).all()
        assert (result.strata.gear_group == "purse_seine").all() and (result.strata.stratum_status == "reported").all()
    yft = by_set.catch[by_set.catch.species_code.eq("YFT")].set_index("stratum_id")
    assert yft.loc["iattc_ps_settype|2", "catch"] == 26.3 and yft.loc["iattc_ps_settype|3", "catch_status"] == "zero"


def test_geometry_uses_documented_cell_centres(tmp_path):
    rows = [(ll_row(2020, "JPN", lat="-2.50", lon="-147.50"), {}, {}),
            (ll_row(2020, "JPN", month=2, lat="47.50", lon="-72.50"), {}, {}),
            (ll_row(2020, "JPN", month=3, lat="", lon=""), {}, {}),
            (ll_row(2020, "JPN", month=4, lat="95.00", lon="-147.50"), {}, {}),
            (ll_row(2020, "JPN", month=5, lat="7.00", lon="-152.50"), {}, {})]
    s = iattc.adapt(ll_lib(tmp_path / "ll", rows), "iattc_ll").strata
    first = s.iloc[0]
    assert (first.west, first.south, first.grid_width, first.grid_height, first.lon, first.lat) == (-150, -5, 5, 5, -147.5, -2.5)
    assert first.grid_raw == "LatC5=-2.50,LonC5=-147.50" and first.geometry_status == "located" and first.notes == ""
    assert (s.iloc[1].west, s.iloc[1].south) == (-75, 45)
    for i in (2, 3):  # blank or impossible coordinates stay unlocated, never (0, 0)
        assert s.iloc[i].geometry_status == "unlocated" and pd.isna(s.iloc[i].west) and pd.isna(s.iloc[i].lat)
    assert s.iloc[3].notes == "invalid_coordinates"
    assert s.iloc[4].geometry_status == "located" and s.iloc[4].notes == "off_grid_centre;outside_convention_area"
    p = iattc.adapt(ps_lib(tmp_path / "ps"), "iattc_ps_flag").strata.iloc[0]
    assert (p.west, p.south, p.grid_width, p.grid_height, p.lon, p.lat) == (-115, 18, 1, 1, -114.5, 18.5)
    assert p.grid_raw == "LatC1=18.50,LonC1=-114.50"


def test_pole_and_line_reads_one_dataset_and_is_historical(tmp_path):
    assert iattc.PRODUCTS["iattc_lp"].dataset_id == "iattc/publiclptunaflag"
    assert all(spec.dataset_id != "iattc/publiclptuna" for spec in iattc.PRODUCTS.values())
    columns = ["Year", "Month", "Flag", "LatC1", "LonC1", "NumSets", "ALB", "BET", "BKJ", "BZX", "PBF", "SKJ", "TUN", "YFT"]
    rows = [dict(Year=1978, Month=1, Flag="USA", LatC1="3.5", LonC1="-79.5", NumSets=2, SKJ="6.057716", YFT="4.742284"),
            dict(Year=2015, Month=1, Flag="MEX", LatC1="23.5", LonC1="-112.5", NumSets=2, SKJ="", YFT="28.5")]
    lib = library(tmp_path, "iattc/publiclptunaflag", {"LPTuna-Atun.pdf": "readme", "PublicLPTunaFlag.csv": csv(columns, rows, "0")})
    result = iattc.adapt(lib, "iattc_lp")
    s, c = result.strata, result.catch.set_index(["stratum_id", "species_code"])
    assert (s.notes == "historical_only").all() and (s.view_group == "iattc_lp").all()
    assert (s.gear_group == "pole_and_line").all() and s.reporting_group.tolist() == ["USA|LP", "MEX|LP"]
    assert c.loc[("iattc_lp|2", "SKJ"), "catch"] == pytest.approx(6.057716)
    assert c.loc[("iattc_lp|3", "SKJ"), "catch_status"] == "blank" and pd.isna(c.loc[("iattc_lp|3", "SKJ"), "catch"])
    assert (result.effort.effort_unit == "sets").all() and (result.catch.catch_unit == "tonnes").all()


def test_years_filter_pinned_snapshot_and_contract(tmp_path):
    old = [(ll_row(2019, "JPN"), {"BETn": "1.00"}, {"BETn": "1.00"}), (ll_row(2020, "JPN"), {"BETn": "2.00"}, {"BETn": "2.00"})]
    lib = ll_lib(tmp_path / "old", old)
    old_sha = lib.record("iattc/publiclltunabillfish")["sha256"]
    new = [(ll_row(2021, "JPN"), {"BETn": "3.00"}, {"BETn": "3.00"})]
    new_members = {NUM: csv(LL_COLUMNS, [{**k, **n} for k, n, _ in new]), MT: csv(LL_COLUMNS, [{**k, **m} for k, _, m in new])}
    path = tmp_path / "new.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for member, text in new_members.items():
            archive.writestr(member, text)
    lib.import_file(path, "iattc/publiclltunabillfish")
    current = iattc.adapt(lib, "iattc_ll")
    assert current.strata.year.tolist() == [2021] and current.record["sha256"] != old_sha
    pinned = iattc.adapt(lib, "iattc_ll", version=old_sha, years=[2020], progress=lambda message: None)
    assert pinned.record["sha256"] == old_sha and (pinned.strata.source_sha256 == old_sha).all()
    assert pinned.strata.year.tolist() == [2020] and pinned.strata.source_rows.tolist() == ["3"]
    assert {q["check"]: q["count"] for q in pinned.qc}["rows_outside_years"] == 1
    empty = iattc.adapt(lib, "iattc_ll", version=old_sha, years=[])
    assert len(empty.strata) == len(empty.effort) == len(empty.catch) == 0
    assert list(empty.strata.columns) == schema.STRATA_COLUMNS
    schema.validate(pinned.strata, pinned.effort, pinned.catch, final=False)
    with pytest.raises(ValueError, match="Unknown IATTC product"):
        iattc.adapt(lib, "iattc_ps")


def test_products_are_registered_with_source_prefixes():
    assert set(iattc.PRODUCTS) == {"iattc_ll", "iattc_ps_settype", "iattc_ps_flag", "iattc_lp"}
    for name, spec in iattc.PRODUCTS.items():
        assert spec.product == name and spec.source == "iattc" and name.startswith("iattc_")
        assert spec.catch_basis == "iattc_flag_reported_retained" and spec.zeros_explicit
    assert iattc.PRODUCTS["iattc_ll"].extra["members"] == {"fish": NUM, "tonnes": MT}
