"""ICCAT T2CE adapter: synthetic records only (no library, no mdbtools)."""
import math

import pandas as pd
import pytest

from tuna_data.modelprep.adapters import iccat
from tuna_data.modelprep.schema import finalize, validate

SHA = "a" * 64
MEMBER = "t2ce_20990101web.mdb"
NULL_SPECIES = {code: "" for code in iccat.SPECIES_COLUMNS}
FLAGS = pd.DataFrame({"FleetID": ["012JP00", "021ES06", "029VE04"],
                      "FleetCode": ["JPN", "EU.ESP-ES-ETRO", "VEN-VE-INDUSTRIAL"],
                      "FlagName": ["Japan", "EU-España", "Venezuela"]})
PERIODS = pd.DataFrame({"TimeStrataID": [str(i) for i in range(1, 18)],
                        "TimeStrata": ["mm"] * 12 + ["qq"] * 4 + ["yy"]})


def rec(sid, **values):
    """One t2ce record as mdb-export text (NULL = ''); a Japanese longline 5x5 NW cell by default."""
    row = dict(StrataID=str(sid), DSetID="10", FleetID="012JP00", GearGrpCode="LL", GearCode="LL",
               FileTypeCode="OF-REP", YearC="2000", TimePeriodID="5", SquareTypeCode="5x5", QuadID="4",
               Lat="10", Lon="40", SchoolTypeCode="n/a", Eff1="1000", Eff1Type="NO.HOOKS", Eff2="", Eff2Type="",
               DSetTypeID=".w", CatchUnit="kg", **{code: "0" for code in iccat.SPECIES_COLUMNS})
    row.update(values)
    return row


def run(records, *, flags=FLAGS, periods=PERIODS, years=None):
    strata, effort, catch, qc = iccat.build_tables(pd.DataFrame(records), flags, periods, sha256=SHA,
                                                   member=MEMBER, years=years)
    strata, effort, catch = finalize(strata, effort, catch)
    return strata.set_index("stratum_id", drop=False), effort, catch, {q["check"]: q["count"] for q in qc}


def sid(*key, effort_only=False):
    return "iccat_t2ce|" + "|".join(key) + ("|.." if effort_only else "")


BASE = ("10", "012JP00", "LL", "2000", "5", "5x5", "4", "10", "40", "n/a")


def effort_of(effort, stratum_id):
    rows = effort[effort.stratum_id.eq(stratum_id)]
    return {r.effort_unit: (None if pd.isna(r.effort) else float(r.effort), r.effort_status, r.effort_field,
                            r.effort_scope) for r in rows.itertuples()}


def catch_of(catch, stratum_id):
    rows = catch[catch.stratum_id.eq(stratum_id)]
    return {(r.species_code, r.catch_unit): (None if pd.isna(r.catch) else float(r.catch), r.catch_status)
            for r in rows.itertuples()}


def notes(strata, stratum_id):
    return set(filter(None, strata.loc[stratum_id, "notes"].split(";")))


def test_product_spec():
    spec = iccat.PRODUCTS["iccat_t2ce"]
    assert (spec.dataset_id, spec.view_group, spec.zeros_explicit, spec.catch_basis) == (
        "iccat/t2ce", "iccat_t2ce", True, "iccat_t2ce_landings_dead_discards")
    assert set(iccat.SPECIES_COLUMNS) == {"YFT", "BET", "SKJ", "ALB", "BFT", "SBF", "SWO"}   # no PBF column


def test_nw_kg_and_nr_records_merge_without_doubling_effort():
    strata, effort, catch, qc = run([
        rec(12, DSetTypeID="nw", CatchUnit="nr", YFT="3", BET="1"),
        rec(11, DSetTypeID="nw", CatchUnit="kg", YFT="120.5", BET="40"),
    ])
    assert list(strata.index) == [sid(*BASE)]
    s = strata.loc[sid(*BASE)]
    assert s.source_rows == "11;12" and s.stratum_status == "reported"
    assert effort_of(effort, sid(*BASE)) == {"hooks": (1000.0, "reported", "Eff1", "stratum")}
    c = catch_of(catch, sid(*BASE))
    assert len(c) == 2 * len(iccat.SPECIES_COLUMNS)
    assert c[("YFT", "kg")] == (120.5, "positive") and c[("YFT", "fish")] == (3.0, "positive")
    assert c[("BET", "fish")] == (1.0, "positive") and c[("SWO", "kg")] == (0.0, "zero")
    assert "effort_differs_between_units" not in notes(strata, sid(*BASE))
    assert qc["strata_kg_and_nr"] == 1 and qc["effort_differs_between_units"] == 0


def test_kg_and_nr_records_with_different_effort_keep_kg_effort():
    strata, effort, _, qc = run([rec(1, CatchUnit="kg"), rec(2, CatchUnit="nr", DSetTypeID="n.", Eff1="1657")])
    assert effort_of(effort, sid(*BASE))["hooks"][:2] == (1000.0, "reported")
    assert "effort_differs_between_units" in notes(strata, sid(*BASE))
    assert qc["effort_differs_between_units"] == 1


def test_records_sharing_a_key_are_summed_per_unit():
    strata, effort, catch, qc = run([
        rec(7, Eff1="200", YFT="5", BET="1"),
        rec(5, Eff1="300", YFT="10", BET="0"),
        rec(6, Eff1="500", YFT="0", BET="2.5", Eff2="1", Eff2Type="NO.TRIPS"),
    ])
    assert len(strata) == 1
    s = strata.loc[sid(*BASE)]
    assert s.source_rows == "5;6;7"
    e = effort_of(effort, sid(*BASE))
    assert e["hooks"][:2] == (1000.0, "reported")
    # Only one of three records reports trips: never a partial total.
    assert e["trips"][:2] == (None, "missing") and "effort_partial" in notes(strata, sid(*BASE))
    c = catch_of(catch, sid(*BASE))
    assert c[("YFT", "kg")] == (15.0, "positive") and c[("BET", "kg")] == (3.5, "positive")
    assert qc["strata_multi_record"] == 1 and qc["records_in_multi_record_strata"] == 3


def test_effort_only_records_form_their_own_strata():
    strata, effort, catch, qc = run([
        rec(1, Eff1="100"),
        rec(2, CatchUnit="..", Eff1="40", **NULL_SPECIES),
        rec(3, CatchUnit="..", Eff1="60", **NULL_SPECIES),
        rec(4, DSetID="11", CatchUnit="..", Eff1="0", Eff1Type="-none-", **NULL_SPECIES),
    ])
    catch_id, eo_id = sid(*BASE), sid(*BASE, effort_only=True)
    assert strata.loc[eo_id, "stratum_status"] == "effort_only" and strata.loc[eo_id, "source_rows"] == "2;3"
    assert effort_of(effort, catch_id)["hooks"][0] == 100.0        # never merged with the '..' effort
    assert effort_of(effort, eo_id)["hooks"][0] == 100.0           # 40 + 60 from the effort-only records
    assert not catch.stratum_id.isin([eo_id]).any()
    assert {"effort_only_sibling"} <= notes(strata, catch_id) and {"effort_only_sibling"} <= notes(strata, eo_id)
    empty_id = sid("11", *BASE[1:], effort_only=True)
    assert strata.loc[empty_id, "stratum_status"] == "effort_only"
    assert "no_effort_reported" in notes(strata, empty_id) and not effort.stratum_id.eq(empty_id).any()
    assert qc["strata_effort_only"] == 2 and qc["records_effort_only"] == 3


def test_two_effort_types_stay_separate_rows():
    strata, effort, _, _ = run([
        rec(1, GearGrpCode="PS", GearCode="PS", Eff1="24.5", Eff1Type="FISH.HOUR", Eff2="3", Eff2Type="NO.SETS"),
        rec(2, DSetID="11", Eff1="7", Eff1Type="D.FISH.G", Eff2="2", Eff2Type="TRAP D"),
    ])
    ps = sid("10", "012JP00", "PS", *BASE[3:])
    assert effort_of(effort, ps) == {"fishing_hours": (24.5, "reported", "Eff1", "stratum"),
                                     "sets": (3.0, "reported", "Eff2", "stratum")}
    other = effort_of(effort, sid("11", *BASE[1:]))
    assert set(other) == {"iccat:D.FISH.G", "iccat:TRAP D"}
    assert strata.loc[ps, "gear_group"] == "purse_seine"


def test_effort_type_mapping():
    codes = ["NO.HOOKS", "NO.SETS", "SUC.SETS", "D.FISH", "D.AT SEA", "FISH.HOUR", "HOURS.SEA", "NO.TRIPS"]
    _, effort, _, _ = run([rec(i, DSetID=str(i), Eff1="5", Eff1Type=code) for i, code in enumerate(codes, 1)])
    assert sorted(effort.effort_unit) == sorted(["hooks", "sets", "successful_sets", "fishing_days", "days_at_sea",
                                                 "fishing_hours", "hours_at_sea", "trips"])


def test_untyped_zero_effort_gives_no_effort_row():
    strata, effort, _, qc = run([rec(1, Eff1="0", Eff1Type="-none-"), rec(2, DSetID="11", Eff1="0", Eff1Type="")])
    assert effort.empty
    assert set(strata.stratum_status) == {"catch_only"}
    assert all("catch_without_effort" in notes(strata, i) for i in strata.index)
    assert qc["eff1_untyped_zero"] == 2


def test_typed_zero_effort_is_missing_not_zero():
    strata, effort, _, qc = run([rec(1, Eff1="0")])
    assert effort_of(effort, sid(*BASE))["hooks"][:2] == (None, "missing")
    assert {"coded_zero_effort", "catch_without_effort"} <= notes(strata, sid(*BASE))
    assert strata.loc[sid(*BASE), "stratum_status"] == "catch_only" and qc["effort_coded_zero"] == 1


def test_negative_effort_is_invalid():
    strata, effort, _, _ = run([rec(1, Eff1="-30.2", Eff1Type="D.AT SEA", Eff2="500", Eff2Type="NO.HOOKS")])
    e = effort_of(effort, sid(*BASE))
    assert e["days_at_sea"][:2] == (None, "invalid") and e["hooks"][:2] == (500.0, "reported")
    assert "invalid_effort" in notes(strata, sid(*BASE)) and strata.loc[sid(*BASE), "stratum_status"] == "reported"


def test_null_is_blank_and_zero_is_zero():
    strata, _, catch, _ = run([rec(1, YFT="", BET="0", SKJ="12.5"), rec(2, CatchUnit="..", **NULL_SPECIES)])
    c = catch_of(catch, sid(*BASE))
    assert c[("YFT", "kg")] == (None, "blank")
    assert c[("BET", "kg")] == (0.0, "zero") and c[("SKJ", "kg")] == (12.5, "positive")
    assert not catch.stratum_id.eq(sid(*BASE, effort_only=True)).any()
    assert set(catch.catch_unit) == {"kg"} and "PBF" not in set(catch.species_code)


@pytest.mark.parametrize("period, months, resolution, note", [
    ("5", (5, 5), "month", None), ("13", (1, 3), "quarter", "quarter_order_assumed"),
    ("14", (4, 6), "quarter", "quarter_order_assumed"), ("16", (10, 12), "quarter", "quarter_order_assumed"),
    ("17", (1, 12), "year", None),
])
def test_time_periods(period, months, resolution, note):
    strata, _, _, _ = run([rec(1, TimePeriodID=period)])
    s = strata.iloc[0]
    assert (s.month_start, s.month_end, s.time_resolution) == (*months, resolution)
    assert (note in notes(strata, s.stratum_id)) if note else ("quarter_order_assumed" not in s.notes)


def test_unknown_time_period_is_not_spread_or_guessed():
    strata, _, _, qc = run([rec(1, TimePeriodID="18")])
    s = strata.iloc[0]
    assert pd.isna(s.month_start) and s.time_resolution == "multi_month" and "unknown_time_period" in s.notes
    assert qc["unknown_time_period"] == 1


def test_changed_time_strata_table_stops():
    with pytest.raises(ValueError, match="TimeStrata"):
        run([rec(1, TimePeriodID="13")], periods=PERIODS.assign(TimeStrata=["mm"] * 17))


@pytest.mark.parametrize("square, quad, lat, lon, rect", [
    ("5x5", "1", "10", "5", (5, 10, 5, 5)),           # NE: corner nearest (0,0) is SW
    ("5x5", "2", "10", "5", (5, -15, 5, 5)),          # SE: corner is NW
    ("5x5", "3", "10", "5", (-10, -15, 5, 5)),        # SW: corner is NE
    ("5x5", "4", "10", "5", (-10, 10, 5, 5)),         # NW: corner is SE
    ("1x1", "2", "0", "0", (0, -1, 1, 1)),            # Lat 0 in a southern quadrant lies just south of 0
    ("5x10", "3", "0", "30", (-40, -5, 10, 5)),       # 5 deg lat x 10 deg lon
    ("10x20", "4", "20", "40", (-60, 20, 20, 10)),    # 10 deg lat x 20 deg lon
    ("10x10", "1", "30", "0", (0, 30, 10, 10)),
])
def test_quadrant_geometry(square, quad, lat, lon, rect):
    strata, _, _, _ = run([rec(1, SquareTypeCode=square, QuadID=quad, Lat=lat, Lon=lon)])
    s = strata.iloc[0]
    west, south, width, height = rect
    assert (s.west, s.south, s.grid_width, s.grid_height) == (west, south, width, height)
    assert math.isclose(s.lon, west + width / 2) and math.isclose(s.lat, south + height / 2)
    assert s.geometry_status == "located" and s.grid_raw == f"{square}:{quad}:{lat}:{lon}"
    assert s.notes == ""


@pytest.mark.parametrize("square, quad, lat, lon, note", [
    ("none", "0", "0", "0", ""),                       # no square reported: never (0, 0)
    ("5x5", "0", "10", "20", ""),
    ("1x1", "2", "-13", "12", "invalid_coordinates"),  # sign in an unsigned field
    ("5x5", "5", "10", "20", "invalid_coordinates"),
    ("10x10", "1", "85", "20", "invalid_coordinates"),  # north edge beyond 90N
    ("20x20", "1", "20", "20", "unknown_square_type"),
])
def test_unlocated_cases(square, quad, lat, lon, note):
    strata, _, _, qc = run([rec(1, SquareTypeCode=square, QuadID=quad, Lat=lat, Lon=lon)])
    s = strata.iloc[0]
    assert s.geometry_status == "unlocated"
    assert all(pd.isna(s[c]) for c in ["west", "south", "grid_width", "grid_height", "lon", "lat"])
    assert s.notes == note
    if note:
        assert qc[note] == 1
    else:
        assert qc["unlocated_no_square"] == 1


def test_suspect_quadrant_is_flagged_not_moved():
    strata, _, _, qc = run([rec(1, FleetID="029VE04", QuadID="1", Lat="10", Lon="60"),
                            rec(2, FleetID="029VE04", QuadID="4", Lat="10", Lon="60")])
    ne = strata.loc[sid("10", "029VE04", "LL", "2000", "5", "5x5", "1", "10", "60", "n/a")]
    nw = strata.loc[sid("10", "029VE04", "LL", "2000", "5", "5x5", "4", "10", "60", "n/a")]
    assert ne.geometry_status == "located" and ne.west == 60 and ne.notes == "suspect_quadrant"
    assert nw.west == -65 and nw.notes == "" and qc["suspect_quadrant"] == 1


def test_provenance_fleet_gear_and_reporting_group():
    strata, _, _, qc = run([
        rec(3),
        rec(4, DSetID="12", FleetID="999XX00", GearGrpCode="UN", GearCode="UNCL", FileTypeCode="WG-EST"),
    ])
    s = strata.loc[sid(*BASE)]
    columns = ["source", "product", "view_group", "dataset_id", "source_sha256", "source_member", "region_raw",
               "fleet", "gear_raw", "gear_group", "school_type", "reporting_group"]
    assert [s[c] for c in columns] == ["iccat", "iccat_t2ce", "iccat_t2ce", "iccat/t2ce", SHA, MEMBER, "ICCAT",
                                       "JPN", "LL", "longline", "", "dset:10"]
    o = strata.loc[sid("12", "999XX00", "UNCL", *BASE[3:])]
    assert (o.fleet, o.gear_group) == ("999XX00", "unmapped:iccat:UN")
    assert {"not_official_report", "working_group_estimate", "fleet_code_unresolved"} <= notes(strata, o.stratum_id)
    assert qc["fleet_code_unresolved"] == 1 and qc["strata_working_group_estimate"] == 1


def test_school_specific_and_repeated_effort_scope():
    records, i = [], 0
    for dset, fleet, repeated in [("20", "021ES06", True), ("21", "012JP00", False)]:
        for cell in ["1", "2", "3"]:
            for school, sets, hours_offset in [("FAD", "4", 0.0), ("FSC", "2", 1.5)]:
                i += 1
                hours = 30.0 + int(cell) + (0 if repeated else hours_offset)
                records.append(rec(i, DSetID=dset, FleetID=fleet, GearGrpCode="PS", GearCode="PS", SquareTypeCode="1x1",
                                   QuadID="4", Lat=cell, Lon="20", SchoolTypeCode=school, Eff1=str(hours),
                                   Eff1Type="FISH.HOUR", Eff2=sets, Eff2Type="NO.SETS"))
    strata, effort, _, qc = run(records)
    e = effort.merge(strata[["stratum_id", "fleet", "school_type", "notes"]].reset_index(drop=True), on="stratum_id")
    esp, jpn = e[e.fleet.eq("EU.ESP-ES-ETRO")], e[e.fleet.eq("JPN")]
    assert set(esp.school_type) == {"FAD", "FSC"}
    assert set(esp[esp.effort_unit.eq("fishing_hours")].effort_scope) == {"stratum"}
    assert set(esp[esp.effort_unit.eq("sets")].effort_scope) == {"school"}
    assert esp.notes.str.contains("effort_repeated_across_school_types").all()
    assert set(jpn.effort_scope) == {"school"} and not jpn.notes.str.contains("repeated").any()
    assert qc["effort_repeated_across_school_types"] == 6


def test_same_stratum_in_another_dataset_is_flagged():
    strata, _, _, qc = run([rec(1), rec(2, DSetID="11"), rec(3, DSetID="12", Lat="15")])
    assert "stratum_in_other_dataset" in notes(strata, sid(*BASE))
    assert "stratum_in_other_dataset" in notes(strata, sid("11", *BASE[1:]))
    assert strata.loc[sid("12", *BASE[1:7], "15", "40", "n/a"), "notes"] == ""
    assert qc["stratum_in_other_dataset"] == 2


def test_years_are_filtered_before_processing():
    strata, _, _, qc = run([rec(1, YearC="1999", DSetID="9"), rec(2), rec(3, YearC="2001", DSetID="11")],
                           years=range(2000, 2001))
    assert list(strata.year) == [2000] and qc["rows_outside_years"] == 2 and qc["rows_read"] == 3
    empty, effort, catch, _ = run([rec(1)], years=[1990])
    assert empty.empty and effort.empty and catch.empty


@pytest.mark.parametrize("change, message", [
    ({"CatchUnit": "t"}, "CatchUnit"),
    ({"Eff1Type": "NO.HOOKS#"}, "effort type"),
    ({"YFT": "abc"}, "Unparseable"),
])
def test_unreviewed_values_stop(change, message):
    with pytest.raises(ValueError, match=message):
        run([rec(1, **change)])


def test_duplicate_strata_ids_stop():
    with pytest.raises(ValueError, match="unique"):
        run([rec(1), rec(1, DSetID="11")])


class FakeLibrary:
    """record/members/read_table like tuna_data.library.Library, serving synthetic MDB tables."""

    def __init__(self, tables):
        self.tables, self.calls = tables, []

    def record(self, dataset, version=None):
        assert dataset == "iccat/t2ce"
        if version not in (None, SHA):
            raise KeyError(version)
        return {"sha256": SHA, "filename": "t2ce_20990101.zip"}

    def members(self, dataset, version=None):
        self.calls.append(("members", version))
        return ["readme.txt", MEMBER]

    def read_table(self, dataset, *, member=None, version=None, table=None, **kwargs):
        self.calls.append(("read_table", member, version, table, kwargs.get("dtype"), kwargs.get("keep_default_na")))
        frame = self.tables[table].copy()
        return frame[kwargs["usecols"]] if "usecols" in kwargs else frame


def test_adapt_reads_only_the_pinned_snapshot():
    raw = pd.DataFrame([rec(1, DSetTypeID="nw"), rec(2, DSetTypeID="nw", CatchUnit="nr"),
                        rec(3, CatchUnit="..", **NULL_SPECIES)]).assign(oTun="0", BUM="0")
    lib = FakeLibrary({"t2ce": raw, "Flags": FLAGS, "TimeStrata": PERIODS})
    result = iccat.adapt(lib, "iccat_t2ce", version=SHA)
    assert result.record["sha256"] == SHA
    reads = [c for c in lib.calls if c[0] == "read_table"]
    assert {c[3] for c in reads} == {"t2ce", "Flags", "TimeStrata"}
    assert all(c[1] == MEMBER and c[2] == SHA and c[4] is str and c[5] is False for c in reads)
    assert lib.calls[0] == ("members", SHA)
    validate(result.strata, result.effort, result.catch, final=False)
    assert str(result.strata.year.dtype) == "Int64" and str(result.catch["catch"].dtype) == "Float64"
    assert result.strata.stratum_id.is_unique and len(result.strata) == 2
    assert {q["check"] for q in result.qc} >= {"rows_read", "effort_coded_zero", "unlocated_no_square", "rows_dropped"}
    with pytest.raises(ValueError, match="Unknown ICCAT product"):
        iccat.adapt(lib, "iccat_catdis")
