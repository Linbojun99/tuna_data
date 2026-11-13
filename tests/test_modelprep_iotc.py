"""Synthetic tests for the IOTC CE modelprep adapter (no real library access)."""
import zipfile

import pandas as pd
import pytest

from tuna_data.library import Library
from tuna_data.modelprep import schema
from tuna_data.modelprep.adapters import iotc

CA_HEADER = ["YEAR", "QUARTER", "MONTH_START", "MONTH_END", "FISHING_GROUND_CODE", "FISHING_GROUND", "FLEET_CODE",
             "FLEET", "FISHERY_TYPE_CODE", "FISHERY_TYPE", "FISHERY_GROUP_CODE", "FISHERY_GROUP", "FISHERY_CODE",
             "FISHERY", "GEAR_CODE", "GEAR", "CATCH_SCHOOL_TYPE_CODE", "SPECIES_CATEGORY_CODE", "SPECIES_CATEGORY",
             "SPECIES_CODE", "SPECIES", "SPECIES_SCIENTIFIC", "CATCH_UNIT_CODE", "CATCH"]
EF_HEADER = CA_HEADER[:16] + ["SCHOOL_TYPE_CODE", "EFFORT_UNIT_CODE", "EFFORT"]
PS = {"PS", "PSS", "RIN", "RNOF"}
RELEASE = "IOTC-DATASETS-2099-01-01-CE-1952-2099"


def _base(year, months, grid, fleet, gear, group):
    start, end = months if isinstance(months, tuple) else (months, months)
    group = group or ("PS" if gear in PS else "LL")
    return dict(YEAR=year, QUARTER="Q1", MONTH_START=start, MONTH_END=end, FISHING_GROUND_CODE=grid,
                FISHING_GROUND="NA", FLEET_CODE=fleet, FLEET=fleet + " label", FISHERY_TYPE_CODE="IND",
                FISHERY_TYPE="Industrial", FISHERY_GROUP_CODE=group, FISHERY_GROUP=group, FISHERY_CODE=group + "X",
                FISHERY="label", GEAR_CODE=gear, GEAR=gear + " label")


def ca(year, months, grid, fleet, gear, school, species, unit, value, group=None):
    return dict(_base(year, months, grid, fleet, gear, group), CATCH_SCHOOL_TYPE_CODE=school,
                SPECIES_CATEGORY_CODE="TROPICAL", SPECIES_CATEGORY="Tropical", SPECIES_CODE=species,
                SPECIES=species, SPECIES_SCIENTIFIC="NA", CATCH_UNIT_CODE=unit, CATCH=value)


def ef(year, months, grid, fleet, gear, school, unit, value, group=None):
    return dict(_base(year, months, grid, fleet, gear, group), SCHOOL_TYPE_CODE=school,
                EFFORT_UNIT_CODE=unit, EFFORT=value)


# Non-purse-seine strata (iotc_ce)
CA_ROWS = [
    ca(2020, 1, "6210115", "TWN", "LL", "UNCL", "YFT", "MT", "5.5"),        # A
    ca(2020, 1, "6210115", "TWN", "LL", "UNCL", "YFT", "NO", "300"),        # A: same catch in numbers
    ca(2020, 1, "6210115", "TWN", "LL", "UNCL", "BET", "NO", "20"),         # A: BET in numbers only
    ca(2020, 1, "6210115", "TWN", "LL", "UNCL", "MLS", "NO", "3"),          # A: not a schema species
    ca(2020, 1, "6210115", "TWN", "LL", "UNCL", "AG34", "MT", "1"),         # A: mixed code, never split
    ca(2020, 2, "6210115", "TWN", "LL", "UNCL", "BET", "MT", "2.0"),        # B (effort coded 0)
    ca(2020, 4, "5100073", "MDV", "BB", "FS", "SKJ", "MT", "50"),           # D
    ca(2020, 4, "5100073", "MDV", "BB", "LA", "SKJ", "MT", "7"),            # E catch only
    ca(1998, (1, 12), "6200045", "KOR", "LL", "UNCL", "ALB", "NO", "100"),  # F annual
    ca(2001, (4, 5), "9000080", "THA", "GILL", "UNCL", "YFT", "MT", "1"),   # G months 4-5, unlocated
    ca(2020, 5, "3200050", "SYC", "HAND", "UNCL", "YFT", "MT", "3"),        # H
    ca(2024, 1, "6120090", "BGD", "NA", "UNCL", "SKJ", "MT", "2", "OT"),    # J literal NA gear
    ca(2020, 1, "2120060", "OMN", "GILL", "UNCL", "YFT", "MT", "4"),        # L surrogate OMAN
    ca(2020, 8, "6105095", "TWN", "LL", "UNCL", "YFT", "NO", "1"),          # M regular for TWN
    ca(2007, 3, "6105095", "THA", "LL", "UNCL", "YFT", "MT", "1"),          # N exempt gear
    ca(2002, 6, "6200050", "NEICE", "FLL", "UNCL", "BET", "MT", "1"),       # P
    ca(2020, 9, "6210115", "TWN", "LL", "UNCL", "MLS", "NO", "4"),          # Z other species only
    # Purse-seine family
    ca(2015, 1, "5100050", "EUESP", "PS", "FS", "YFT", "MT", "10"),         # R
    ca(2015, 1, "5100050", "EUESP", "PS", "LS", "YFT", "MT", "20"),
    ca(2015, 1, "5100050", "EUESP", "PS", "LS", "SKJ", "MT", "30"),
    ca(2019, 1, "5100051", "EUESP", "PS", "FS", "YFT", "MT", "5"),          # S
    ca(2019, 1, "5100051", "EUESP", "PS", "LS", "SKJ", "MT", "8"),
    ca(2022, 3, "5100052", "EUFRA", "PS", "LS", "YFT", "MT", "4"),          # T
    ca(2020, 1, "5106080", "LKA", "RNOF", "UNCL", "SKJ", "MT", "9"),        # X ring net
    ca(2020, 2, "5100055", "ZZZ", "PS", "FS", "YFT", "MT", "3"),            # W
    ca(2020, 2, "5100055", "ZZZ", "PS", "LS", "YFT", "NO", "10"),
    ca(2007, 3, "6105095", "THA", "PSS", "UNCL", "YFT", "MT", "2"),         # O surrogate ANDAM
    ca(2020, 3, "5100056", "IDN", "PSX", "UNCL", "SKJ", "MT", "6", "PS"),   # Y gear in family by group
]
EF_ROWS = [
    ef(2020, 1, "6210115", "TWN", "LL", "UNCL", "HOOKS", "100000"),         # A
    ef(2020, 1, "6210115", "TWN", "LL", "UNCL", "SETS", "40"),
    ef(2020, 2, "6210115", "TWN", "LL", "UNCL", "HOOKS", "0"),              # B coded zero
    ef(2020, 3, "6210115", "TWN", "LL", "UNCL", "HOOKS", "5000"),           # C effort only
    ef(2020, 4, "5100073", "MDV", "BB", "FS", "FDAYS", "10"),               # D
    ef(1998, (1, 12), "6200045", "KOR", "LL", "UNCL", "HOOKS", "12000000"),  # F (annual, under the cap)
    ef(2001, (4, 5), "9000080", "THA", "GILL", "UNCL", "TRIPS", "10"),      # G
    ef(2020, 5, "3200050", "SYC", "HAND", "UNCL", "FHOURS", "800"),         # H implausible
    ef(2020, (6, 7), "3200050", "SYC", "HAND", "UNCL", "FHOURS", "1000"),   # I two months: plausible
    ef(2024, 1, "6120090", "BGD", "NA", "UNCL", "BOATS", "3", "OT"),        # J
    ef(2023, 7, "6215040", "MOZ", "GILL", "UNCL", "NG", "5"),               # K
    ef(2023, 7, "6215040", "MOZ", "GILL", "UNCL", "DAYS", "3"),
    ef(2025, 3, "6215045", "MDG", "LLCO", "UNCL", "HOOKS", "145189364"),    # implausible hooks
    ef(2020, 1, "2120060", "OMN", "GILL", "UNCL", "FDAYS", "100"),          # L
    ef(2020, 8, "6105095", "TWN", "LL", "UNCL", "HOOKS", "1000"),           # M
    ef(2007, 3, "6105095", "THA", "LL", "UNCL", "HOOKS", "1000"),           # N
    ef(2002, 6, "6200050", "NEICE", "FLL", "UNCL", "HOOKS", "1000"),        # P
    *[ef(year, m, "5103040", "KEN", "GILL", "UNCL", "TRIPS", str(m)) for year in (2018, 2019) for m in range(1, 7)],
    *[ef(2021, m, "5103040", "KEN", "GILL", "UNCL", "TRIPS", str(m + 1)) for m in range(1, 7)],   # Q
    # Purse-seine family
    ef(2015, 1, "5100050", "EUESP", "PS", "UNCL", "FHOURS", "100"),         # R pooled effort
    ef(2015, 1, "5100050", "EUESP", "PS", "UNCL", "HOURS", "150"),
    ef(2019, 1, "5100051", "EUESP", "PS", "FS", "SETS", "2"),               # S school effort only
    ef(2019, 1, "5100051", "EUESP", "PS", "LS", "SETS", "3"),
    ef(2019, 1, "5100051", "EUESP", "PS", "FS", "FHOURS", "20"),
    ef(2019, 1, "5100051", "EUESP", "PS", "LS", "FHOURS", "30"),
    ef(2022, 3, "5100052", "EUFRA", "PS", "UNCL", "FHOURS", "50"),          # T mixed: UNCL remainder
    ef(2022, 3, "5100052", "EUFRA", "PS", "LS", "FHOURS", "20"),
    ef(2020, 5, "5100053", "SYC", "PS", "UNCL", "SETS", "4"),               # U pooled effort only
    ef(2020, 6, "5100054", "KOR", "PS", "FS", "SETS", "1"),                 # V school effort only, no catch
    ef(2020, 1, "5106080", "LKA", "RNOF", "UNCL", "TRIPS", "12"),           # X
    ef(2007, 3, "6105095", "THA", "PSS", "UNCL", "SETS", "5"),              # O
]
LONGLINE_2025 = [
    dict(Fleet="OMN    ", Gear="LL", Year="2005", iGrid="OMAN      ", Grid="2120060"),
    dict(Fleet="OMN    ", Gear="LL", Year="2008", iGrid="2120060   ", Grid="2120060"),
    dict(Fleet="THA    ", Gear="LL", Year="2007", iGrid="6105095   ", Grid="6105095"),
    dict(Fleet="TWN    ", Gear="LL", Year="2007", iGrid="6105095   ", Grid="6105095"),
]
SURFACE_2025 = [
    dict(Fleet="THA    ", Gear="PSS", Year="2007", iGrid="ANDAM     ", Grid="6105095"),
    dict(Fleet="THA    ", Gear="PSS", Year="2007", iGrid="INOCE     ", Grid="6105095"),
    dict(Fleet="MDV    ", Gear="BB", Year="1975", iGrid="GAVILI    ", Grid="5100073"),
    dict(Fleet="MDV    ", Gear="BB", Year="1975", iGrid="GDHTHI    ", Grid="5100073"),
    dict(Fleet="MDV    ", Gear="BBOF", Year="2020", iGrid="5100073   ", Grid="5100073"),
    dict(Fleet="EUESP  ", Gear="PS", Year="2020", iGrid="5100073   ", Grid="5100073"),
]


def _write_zip(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, frame in members.items():
            archive.writestr(name, frame.to_csv(index=False, lineterminator="\r\n"))
    return path


def _wide(rows):
    frame = pd.DataFrame(rows)
    for column, value in [("MonthStart", "1"), ("MonthEnd", "1"), ("Effort", "1.00"), ("EffortUnits", "HOOKS"),
                          ("QualityCode", "3"), ("Source", "LO"), ("YFT-MT", "")]:
        frame[column] = value
    return frame


def make_library(tmp_path, ca_rows=CA_ROWS, ef_rows=EF_ROWS, surrogates=True):
    lib = Library(tmp_path / "library")
    source = tmp_path / "source"
    source.mkdir(parents=True)
    ce = _write_zip(source / f"{RELEASE}.zip", {
        f"{RELEASE}/IOTC-DATASETS-2099-01-01-CA-1952-2099.csv": pd.DataFrame(ca_rows, columns=CA_HEADER),
        f"{RELEASE}/IOTC-DATASETS-2099-01-01-EF-1952-2099.csv": pd.DataFrame(ef_rows, columns=EF_HEADER)})
    lib.import_file(ce, "iotc/iotc_datasets_ce")
    if surrogates:
        ll = _write_zip(source / "IOTC-DATASETS-2025-10-13-CELongline.zip",
                        {"IOTC-DATASETS-2025-10-13-CELongline.csv": _wide(LONGLINE_2025)})
        sf = _write_zip(source / "IOTC-DATASETS-2025-10-13-CESurface.zip",
                        {"IOTC-DATASETS-2025-10-13-CESurface.csv": _wide(SURFACE_2025)})
        lib.import_file(ll, "iotc/iotc_datasets_celongline")
        lib.import_file(sf, "iotc/iotc_datasets_cesurface")
    return lib


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    lib = make_library(tmp_path_factory.mktemp("iotc"))
    return lib, {product: iotc.adapt(lib, product) for product in iotc.PRODUCTS}


def stratum(result, key):
    rows = result.strata[result.strata.stratum_id.eq(key)]
    assert len(rows) == 1, key
    return rows.iloc[0]


def effort_of(result, key):
    return result.effort[result.effort.stratum_id.eq(key)].set_index("effort_unit")


def catch_of(result, key):
    return result.catch[result.catch.stratum_id.eq(key)].set_index(["species_code", "catch_unit"])


def qc(result, check):
    found = [q["count"] for q in result.qc if q["check"] == check]
    assert len(found) == 1, check
    return found[0]


def test_products_follow_the_contract():
    assert set(iotc.PRODUCTS) == {"iotc_ce", "iotc_ce_ps_school", "iotc_ce_ps_pooled"}
    for name, spec in iotc.PRODUCTS.items():
        assert spec.product == name and spec.source == "iotc" and spec.dataset_id == "iotc/iotc_datasets_ce"
        assert spec.zeros_explicit is False and spec.catch_basis == "iotc_ce_reported"
    assert iotc.PRODUCTS["iotc_ce"].view_group == "iotc_ce"
    assert iotc.PRODUCTS["iotc_ce_ps_school"].view_group == iotc.PRODUCTS["iotc_ce_ps_pooled"].view_group == "iotc_ps"


def test_s7_full_outer_join_gives_reported_effort_only_and_catch_only(results):
    _, out = results
    r = out["iotc_ce"]
    schema.validate(r.strata, r.effort, r.catch, final=False)
    a = stratum(r, "iotc_ce|2020|1|1|6210115|TWN|LL|UNCL")
    assert a.stratum_status == "reported" and a.school_type == "UNCL" and a.reporting_group == "TWN|LL"
    assert a.gear_group == "longline" and a.region_raw == "IOTC" and a.view_group == "iotc_ce"
    c = stratum(r, "iotc_ce|2020|3|3|6210115|TWN|LL|UNCL")
    assert c.stratum_status == "effort_only" and catch_of(r, c.stratum_id).empty
    # Same K6 as D but another school type: a separate S7 stratum, catch only.
    d = stratum(r, "iotc_ce|2020|4|4|5100073|MDV|BB|FS")
    e = stratum(r, "iotc_ce|2020|4|4|5100073|MDV|BB|LA")
    assert d.stratum_status == "reported" and e.stratum_status == "catch_only"
    assert effort_of(r, e.stratum_id).empty
    assert effort_of(r, d.stratum_id).loc["fishing_days", "effort_scope"] == "school"
    assert effort_of(r, a.stratum_id).loc["hooks", "effort_scope"] == "stratum"
    # Catch of other species only: catch information, no schema-species rows.
    z = stratum(r, "iotc_ce|2020|9|9|6210115|TWN|LL|UNCL")
    assert z.stratum_status == "catch_only" and catch_of(r, z.stratum_id).empty
    # Purse-seine family gears never enter iotc_ce.
    assert not r.strata.gear_raw.isin(["PS", "PSS", "RNOF", "PSX"]).any()
    assert r.strata.stratum_id.is_unique


def test_effort_units_are_separate_mapped_and_coded_zero_is_missing(results):
    _, out = results
    r = out["iotc_ce"]
    a = effort_of(r, "iotc_ce|2020|1|1|6210115|TWN|LL|UNCL")
    assert a.loc["hooks", "effort"] == 100000 and a.loc["sets", "effort"] == 40
    assert set(a.effort_status) == {"reported"} and set(a.effort_field) == {"EFFORT"}
    k = effort_of(r, "iotc_ce|2023|7|7|6215040|MOZ|GILL|UNCL")
    assert set(k.index) == {"iotc:NG", "days_at_sea"}
    assert effort_of(r, "iotc_ce|2024|1|1|6120090|BGD|NA|UNCL").index.tolist() == ["iotc:BOATS"]
    b_id = "iotc_ce|2020|2|2|6210115|TWN|LL|UNCL"
    b = effort_of(r, b_id).loc["hooks"]
    assert b.effort_status == "missing" and pd.isna(b.effort)
    assert "effort_coded_zero" in stratum(r, b_id).notes.split(";")
    assert stratum(r, b_id).stratum_status == "reported"
    assert qc(r, "effort_coded_zero_rows") == 1


def test_mt_and_no_stay_separate_and_unit_incomplete_blocks_false_zeros(results):
    _, out = results
    r = out["iotc_ce"]
    a = catch_of(r, "iotc_ce|2020|1|1|6210115|TWN|LL|UNCL")
    assert a.loc[("YFT", "tonnes"), "catch"] == 5.5 and a.loc[("YFT", "fish"), "catch"] == 300
    assert a.loc[("YFT", "tonnes"), "catch_status"] == a.loc[("YFT", "fish"), "catch_status"] == "positive"
    assert set(a.index.get_level_values(0)) == {"YFT", "BET"}       # MLS and AG34 are not split or kept
    # TWN|LL reports BET in tonnes in 2020 (stratum B); here BET was listed in numbers only.
    assert a.loc[("BET", "tonnes"), "catch_status"] == "unit_incomplete" and pd.isna(a.loc[("BET", "tonnes"), "catch"])
    b = catch_of(r, "iotc_ce|2020|2|2|6210115|TWN|LL|UNCL")
    assert b.loc[("BET", "tonnes"), "catch"] == 2.0 and b.loc[("BET", "fish"), "catch_status"] == "unit_incomplete"
    # KOR|LL 1998 reports ALB in numbers only: no tonnes row is invented.
    assert catch_of(r, "iotc_ce|1998|1|12|6200045|KOR|LL|UNCL").index.tolist() == [("ALB", "fish")]
    assert set(r.catch.catch_field) == {"CATCH"} and set(r.catch.catch_basis) == {"iotc_ce_reported"}
    assert set(r.catch.catch_status) <= {"positive", "unit_incomplete"}


def test_purse_seine_school_view(results):
    _, out = results
    r = out["iotc_ce_ps_school"]
    schema.validate(r.strata, r.effort, r.catch, final=False)
    assert not r.strata.school_type.eq("UNCL").any() and set(r.strata.view_group) == {"iotc_ps"}
    fs = stratum(r, "iotc_ce_ps_school|2015|1|1|5100050|EUESP|PS|FS")
    assert fs.stratum_status == "catch_only" and "pooled_effort_only" in fs.notes.split(";")
    s_fs = "iotc_ce_ps_school|2019|1|1|5100051|EUESP|PS|FS"
    assert stratum(r, s_fs).stratum_status == "reported"
    e = effort_of(r, s_fs)
    assert e.loc["sets", "effort"] == 2 and e.loc["fishing_hours", "effort"] == 20 and set(e.effort_scope) == {"school"}
    assert catch_of(r, s_fs).loc[("YFT", "tonnes"), "catch"] == 5
    assert effort_of(r, "iotc_ce_ps_school|2022|3|3|5100052|EUFRA|PS|LS").loc["fishing_hours", "effort"] == 20
    v = stratum(r, "iotc_ce_ps_school|2020|6|6|5100054|KOR|PS|FS")
    assert v.stratum_status == "effort_only"
    # Ring nets report UNCL only: they appear in the pooled view, not here.
    assert not r.strata.gear_raw.eq("RNOF").any()
    assert qc(r, "ca_uncl_rows_left_to_pooled_view") == 3      # RNOF, PSS and PSX catch rows
    # Mixed units across school types inside one group-year.
    w_fs = catch_of(r, "iotc_ce_ps_school|2020|2|2|5100055|ZZZ|PS|FS")
    assert w_fs.loc[("YFT", "tonnes"), "catch"] == 3 and w_fs.loc[("YFT", "fish"), "catch_status"] == "unit_incomplete"


def test_purse_seine_pooled_view_uses_uncl_effort_only(results):
    _, out = results
    r = out["iotc_ce_ps_pooled"]
    schema.validate(r.strata, r.effort, r.catch, final=False)
    assert set(r.strata.school_type) == {"all"} and set(r.effort.effort_scope) == {"stratum"}
    k = "iotc_ce_ps_pooled|2015|1|1|5100050|EUESP|PS|all"
    assert stratum(r, k).stratum_status == "reported"
    assert catch_of(r, k).loc[("YFT", "tonnes"), "catch"] == 30          # FS 10 + LS 20, same unit
    assert catch_of(r, k).loc[("SKJ", "tonnes"), "catch"] == 30
    e = effort_of(r, k)
    assert e.loc["fishing_hours", "effort"] == 100 and e.loc["hours_at_sea", "effort"] == 150
    # Effort reported by school type only is not pooled: catch only, flagged.
    s = stratum(r, "iotc_ce_ps_pooled|2019|1|1|5100051|EUESP|PS|all")
    assert s.stratum_status == "catch_only" and "school_effort_not_pooled" in s.notes.split(";")
    assert effort_of(r, s.stratum_id).empty
    # UNCL next to school-specific effort in the same unit is only a remainder: withheld.
    t_id = "iotc_ce_ps_pooled|2022|3|3|5100052|EUFRA|PS|all"
    t = effort_of(r, t_id).loc["fishing_hours"]
    assert t.effort_status == "missing" and pd.isna(t.effort)
    assert "pooled_effort_partial" in stratum(r, t_id).notes.split(";")
    u = stratum(r, "iotc_ce_ps_pooled|2020|5|5|5100053|SYC|PS|all")
    assert u.stratum_status == "effort_only" and effort_of(r, u.stratum_id).loc["sets", "effort"] == 4
    # School-specific effort without catch or UNCL effort has no pooled stratum.
    assert not r.strata.stratum_id.str.contains("5100054").any()
    assert qc(r, "k6_school_effort_only_not_stratum") == 1
    x = stratum(r, "iotc_ce_ps_pooled|2020|1|1|5106080|LKA|RNOF|all")
    assert x.stratum_status == "reported" and catch_of(r, x.stratum_id).loc[("SKJ", "tonnes"), "catch"] == 9
    # FS reports YFT in tonnes, LS in numbers: neither pooled unit is complete.
    w = catch_of(r, "iotc_ce_ps_pooled|2020|2|2|5100055|ZZZ|PS|all")
    assert set(w.catch_status) == {"unit_incomplete"} and w["catch"].isna().all()
    # A gear inside the family through FISHERY_GROUP_CODE 'PS'.
    assert stratum(r, "iotc_ce_ps_pooled|2020|3|3|5100056|IDN|PSX|all").gear_group == "unmapped:iotc:PSX"


def test_time_resolution_geometry_and_implausible_effort(results):
    _, out = results
    r = out["iotc_ce"]
    f = stratum(r, "iotc_ce|1998|1|12|6200045|KOR|LL|UNCL")
    assert f.time_resolution == "year" and "implausible_effort" not in f.notes
    g = stratum(r, "iotc_ce|2001|4|5|9000080|THA|GILL|UNCL")
    assert g.time_resolution == "multi_month" and g.geometry_status == "unlocated"
    assert pd.isna(g.lon) and pd.isna(g.lat) and pd.isna(g.west)
    h = stratum(r, "iotc_ce|2020|5|5|3200050|SYC|HAND|UNCL")
    assert h.time_resolution == "month" and "implausible_effort" in h.notes.split(";")
    assert (h.west, h.south, h.grid_width, h.grid_height) == (50, -10, 10, 10)
    i = stratum(r, "iotc_ce|2020|6|7|3200050|SYC|HAND|UNCL")
    assert i.time_resolution == "multi_month" and "implausible_effort" not in i.notes
    assert "implausible_effort" in stratum(r, "iotc_ce|2025|3|3|6215045|MDG|LLCO|UNCL").notes
    a = stratum(r, "iotc_ce|2020|1|1|6210115|TWN|LL|UNCL")
    assert (a.west, a.south, a.lon, a.lat, a.geometry_status) == (115, -15, 117.5, -12.5, "located")
    assert qc(r, "implausible_effort_FHOURS") == 1 and qc(r, "implausible_effort_HOOKS") == 1


def test_surrogate_areas_are_fleet_specific_and_keep_rectangles(results):
    _, out = results
    r = out["iotc_ce"]
    l = stratum(r, "iotc_ce|2020|1|1|2120060|OMN|GILL|UNCL")
    assert l.geometry_status == "surrogate_area" and "surrogate:OMAN" in l.notes.split(";")
    assert (l.west, l.south, l.grid_width, l.grid_height) == (60, 20, 20, 10)
    assert stratum(r, "iotc_ce|2020|8|8|6105095|TWN|LL|UNCL").geometry_status == "located"
    # THA named 6105095 with surface gears, but THA LL reported it only as a grid cell.
    assert stratum(r, "iotc_ce|2007|3|3|6105095|THA|LL|UNCL").geometry_status == "located"
    d = stratum(r, "iotc_ce|2020|4|4|5100073|MDV|BB|FS")
    assert d.geometry_status == "surrogate_area" and "surrogate:GAVILI+GDHTHI" in d.notes.split(";")
    o = stratum(out["iotc_ce_ps_pooled"], "iotc_ce_ps_pooled|2007|3|3|6105095|THA|PSS|all")
    assert o.geometry_status == "surrogate_area" and "surrogate:ANDAM+INOCE" in o.notes.split(";")
    sources = {q["check"]: q["note"] for q in r.qc if q["check"].startswith("surrogate_source_")}
    assert set(sources) == {"surrogate_source_celongline", "surrogate_source_cesurface"}
    assert all(note.startswith("rows read: iotc/") for note in sources.values())


def test_surrogate_lookup_helper():
    table = pd.DataFrame(SURFACE_2025 + LONGLINE_2025)
    names, exempt, _ = iotc.surrogate_lookup(table)
    assert names == {("THA", "6105095"): "ANDAM+INOCE", ("MDV", "5100073"): "GAVILI+GDHTHI",
                     ("OMN", "2120060"): "OMAN"}
    # Numeric-only gears of named pairs are exempt; other fleets are never named.
    assert exempt == {("THA", "LL", "6105095"), ("MDV", "BBOF", "5100073")}


def test_static_fallback_when_2025_releases_are_missing(tmp_path):
    lib = make_library(tmp_path, surrogates=False)
    r = iotc.adapt(lib, "iotc_ce", years=[2020])
    assert all(q["note"].startswith("static fallback rows") for q in r.qc if q["check"].startswith("surrogate_source_"))
    assert stratum(r, "iotc_ce|2020|1|1|2120060|OMN|GILL|UNCL").geometry_status == "surrogate_area"
    # A release without the iGrid column also falls back, with the reason in qc.
    changed = _write_zip(tmp_path / "IOTC-DATASETS-2026-01-01-CELongline.zip",
                         {"IOTC-DATASETS-2026-01-01-CELongline.csv": _wide(LONGLINE_2025).drop(columns="iGrid")})
    lib.import_file(changed, "iotc/iotc_datasets_celongline")
    r = iotc.adapt(lib, "iotc_ce", years=[2020])
    note = next(q["note"] for q in r.qc if q["check"] == "surrogate_source_celongline")
    assert note.startswith("static fallback rows: iotc/iotc_datasets_celongline unavailable (ValueError")
    assert stratum(r, "iotc_ce|2020|1|1|2120060|OMN|GILL|UNCL").geometry_status == "surrogate_area"
    names, exempt, _ = iotc.surrogate_lookup(pd.DataFrame(
        [row for rows in iotc.STATIC_SURROGATE_ROWS.values() for row in rows], columns=["Fleet", "Gear", "iGrid", "Grid"]))
    assert len(names) == 22 and names[("IRN", "6125055")] == "IRN17_OLD+IRN181"
    assert ("IDN", "FLL", "6205110") in exempt and ("MDV", "BBOF", "5200073") in exempt


def test_years_filter_and_provenance(results):
    lib, out = results
    record = lib.record("iotc/iotc_datasets_ce")
    r = iotc.adapt(lib, "iotc_ce", years=[2020], version=record["sha256"])
    assert set(r.strata.year) == {2020} and set(r.effort.year) == {2020} and set(r.catch.year) == {2020}
    assert r.record["sha256"] == record["sha256"] and set(r.strata.source_sha256) == {record["sha256"]}
    full = out["iotc_ce"].strata.set_index("stratum_id")
    part = r.strata.set_index("stratum_id")
    pd.testing.assert_frame_equal(part, full.loc[part.index])     # the filter does not change 2020 rows
    a = stratum(r, "iotc_ce|2020|1|1|6210115|TWN|LL|UNCL")
    ca_lines = [i + 2 for i in range(5)]                          # CSV lines of the five CA rows of A
    assert a.source_rows == ";".join([f"CA:{n}" for n in ca_lines] + ["EF:2", "EF:3"])
    assert a.source_member == (f"{RELEASE}/IOTC-DATASETS-2099-01-01-CA-1952-2099.csv;"
                               f"{RELEASE}/IOTC-DATASETS-2099-01-01-EF-1952-2099.csv")
    c = stratum(r, "iotc_ce|2020|3|3|6210115|TWN|LL|UNCL")
    assert c.source_rows == "EF:5" and c.source_member.endswith("-EF-1952-2099.csv")
    assert qc(r, "ca_rows_outside_years") > 0 and qc(r, "duplicates_aggregated") == 0


def test_repeated_effort_tables_and_secretariat_estimates(results):
    _, out = results
    r = out["iotc_ce"]
    flagged = r.strata[r.strata.notes.str.contains("repeated_effort_table")]
    assert set(flagged.year) == {2018, 2019} and set(flagged.fleet) == {"KEN"} and len(flagged) == 12
    assert "secretariat_estimate" in stratum(r, "iotc_ce|2002|6|6|6200050|NEICE|FLL|UNCL").notes.split(";")


def test_literal_na_gear_code_is_kept(results):
    _, out = results
    j = stratum(out["iotc_ce"], "iotc_ce|2024|1|1|6120090|BGD|NA|UNCL")
    assert j.gear_raw == "NA" and j.gear_group == "unmapped:iotc:NA" and j.reporting_group == "BGD|NA"


def test_ambiguous_gear_family_and_duplicate_keys_stop_the_adapter(tmp_path):
    mixed = CA_ROWS[:1] + [ca(2020, 1, "6210115", "TWN", "LL", "UNCL", "BET", "MT", "1", "PS")]
    with pytest.raises(ValueError, match="purse-seine family"):
        iotc.adapt(make_library(tmp_path / "a", ca_rows=mixed, ef_rows=EF_ROWS[:1], surrogates=False), "iotc_ce")
    with pytest.raises(ValueError, match="duplicate rows"):
        iotc.adapt(make_library(tmp_path / "b", ca_rows=CA_ROWS[:1], ef_rows=EF_ROWS[:1] * 2, surrogates=False),
                   "iotc_ce")


def test_empty_year_selection_returns_valid_empty_tables(results):
    lib, _ = results
    r = iotc.adapt(lib, "iotc_ce_ps_pooled", years=[1900])
    assert r.strata.empty and r.effort.empty and r.catch.empty
    assert list(r.strata.columns) == schema.STRATA_COLUMNS


def test_surrogate_years_with_numeric_grids_are_located():
    table = pd.DataFrame({"Fleet": ["MDV", "MDV", "OMN"], "Gear": ["BB", "BB", "LL"],
                          "Year": ["2010", "2015", "2005"], "iGrid": ["KMALE", "5104073", "OMAN"],
                          "Grid": ["5104073", "5104073", "2120060"]})
    names, exempt, exempt_years = iotc.surrogate_lookup(table)
    assert names[("MDV", "5104073")] == "KMALE" and names[("OMN", "2120060")] == "OMAN"
    assert ("MDV", "BB", "5104073", "2015") in exempt_years       # reported as a real 1x1 cell that year
    assert ("MDV", "BB", "5104073", "2010") not in exempt_years   # atoll name that year: surrogate
    assert not exempt                                              # the gear named the cell in some year
