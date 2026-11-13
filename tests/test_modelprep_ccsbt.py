"""Synthetic tests for the CCSBT catch-effort adapter (never touches the real library)."""
import math

import openpyxl
from openpyxl.styles import PatternFill
import pandas as pd
import pytest

from tuna_data import Library
from tuna_data.modelprep.adapters import ccsbt
from tuna_data.modelprep.build import confirm_zeros
from tuna_data.modelprep.schema import STRATA_COLUMNS, validate

LL_HEADER = ["YEAR", "MONTH", "COUNTRY\nCODE", "TARGET\nSPECIES", "CCSBT\nSTATISTICAL\nAREA", "LATITUDE",
             "LONGITUDE", "NUMBER\nOF\nHOOKS", "NUMBER\nOF SBT\nRETAINED"]
SF_HEADER = ["YEAR", "MONTH", "COUNTRY\nCODE", "GEAR\nCODE", "CCSBT\nSTATISTICAL\nAREA", "LATITUDE",
             "LONGITUDE", "NUMBER\nOF HOURS\nSEARCHED", "WEIGHT (Kg)\nOF SBT\nRETAINED"]
# Worksheet row numbers in the comments (row 1 is the header).
LL_ROWS = [
    (1965, 1, "JP", None, 1, -15, 100, 2083, 4),             # 2 positive; NW corner of a 5x5 cell
    (1965, 1, "JP", None, 1, -15, 110, 9647, None),          # 3 blank SBF
    (2005, 3, "ZA", None, 9, -35, 15, 9523, 0),              # 4 explicit zero
    (1995, 12, "NZ", "BET", 5, -35, 175, 26700, 1.445087),   # 5 duplicate stratum, part 1
    (1995, 12, "NZ", "BET", 5, -35, 175, 186870, 7),         # 6 duplicate stratum, part 2
    (1990, 5, "NZ", "BET", 5, None, None, 15180, 1.574803),  # 7 unlocated, fractional estimate
    (1995, 5, "JP", None, 11, 0, 0, None, None),             # 8 (0, 0) in area 11; no values
    (1991, 6, "AU", None, 7, -35, 135, None, 259),           # 9 SBF without hooks
    (1985, 4, "TW", None, "NH", 10, 120, 500000, None),      # 10 area NH; TW before 1996
    (2020, 7, "KR", "SBF", 9, -40, 20, 30000, 12),           # 11 target label
    (1991, 7, "AU", None, 7, -35, 135, 5000, 3),             # 12 AU reports hooks elsewhere in the file
]
SF_ROWS = [
    (1975, 12, "AU", "PS", 4, -37, 150, 17, 59800),          # 2 positive kg; NW corner of a 1x1 cell
    (1976, 8, "AU", "BB", 4, -36, 150, 8, None),             # 3 blank weight
    None,                                                    # 4 empty row inside the data range
    (1993, 1, "AU", "BB", 4, -34, 150, 0, None),             # 5 zero hours searched
    (1987, 12, "AU", "BB", 3, -33, 129, None, None),         # 6 blank hours and weight
    (2016, 12, "ZA", "BB", None, 0, None, None, 0),          # 7 lat 0 placeholder, blank lon
    (2025, 7, "ZA", "BB", 15, -32, 16, None, 126.8),         # 8 ZA baitboat: never any effort
]


def write_workbook(path, sheet, header, rows, *, formatted_empty_rows=0):
    """A CCSBT-like workbook: a code sheet first, then the data sheet."""
    book = openpyxl.Workbook()
    book.active.title = sheet.replace("CEData", "CECodes")
    book.active.append(["Code Type", "Codes", "Description/Name"])
    data = book.create_sheet(sheet)
    data.append(header)
    for row in rows:
        data.append([] if row is None else list(row))
    first_empty = data.max_row + 1
    for r in range(first_empty, first_empty + formatted_empty_rows):
        for c in range(1, len(header) + 1):
            data.cell(row=r, column=c).fill = PatternFill("solid", fgColor="FFFF00")
    book.save(path)
    return path


@pytest.fixture
def lib(tmp_path):
    library = Library(tmp_path / "lib")
    ll = write_workbook(tmp_path / "CEData_Longline.xlsx", "CEData_Longline", LL_HEADER, LL_ROWS)
    sf = write_workbook(tmp_path / "CEData_Surface.xlsx", "CEData_Surface", SF_HEADER, SF_ROWS,
                        formatted_empty_rows=40)
    library.import_file(ll, "ccsbt/cedata_longline")
    library.import_file(sf, "ccsbt/cedata_surface")
    return library


def by_row(result):
    """Strata joined to their effort and SBF catch rows, indexed by source_rows."""
    effort = result.effort.set_index("stratum_id")
    catch = result.catch.set_index("stratum_id")
    s = result.strata.set_index("stratum_id")
    s = s.join(effort[["effort", "effort_status", "effort_unit", "effort_field", "effort_scope"]])
    s = s.join(catch[["species_code", "catch", "catch_status", "catch_unit", "catch_field", "catch_basis"]])
    return s.reset_index().set_index("source_rows")


def notes(row):
    return row.notes.split(";") if row.notes else []


def qc(result):
    return {q["check"]: q["count"] for q in result.qc}


class FakeLibrary:
    """Minimal record/read_table stand-in that logs how the adapter reads."""

    def __init__(self, sheets, sha="a" * 64, filename="CEData_Longline.xlsx"):
        self.sheets, self.sha, self.filename, self.calls = sheets, sha, filename, []

    def record(self, dataset, version=None):
        self.calls.append(("record", dataset, version))
        return {"sha256": self.sha, "filename": self.filename, "path": "raw/x.xlsx"}

    def read_table(self, dataset, *, member=None, version=None, sheet_name=0, **kwargs):
        self.calls.append(("read_table", dataset, version, sheet_name, tuple(sorted(kwargs))))
        return self.sheets[sheet_name].copy()


def text_frame(header, rows):
    """What read_table(dtype=str, keep_default_na=False) returns: text, blanks as ''."""
    return pd.DataFrame([["" if v is None else str(v) for v in row] for row in rows], columns=header)


def test_product_specs():
    assert set(ccsbt.PRODUCTS) == {"ccsbt_ll", "ccsbt_surface"}
    ll, sf = ccsbt.PRODUCTS["ccsbt_ll"], ccsbt.PRODUCTS["ccsbt_surface"]
    assert (ll.dataset_id, ll.member, ll.view_group) == ("ccsbt/cedata_longline", "CEData_Longline", "ccsbt_ll")
    assert (sf.dataset_id, sf.member, sf.view_group) == ("ccsbt/cedata_surface", "CEData_Surface", "ccsbt_surface")
    for spec in (ll, sf):
        assert spec.source == "ccsbt" and spec.zeros_explicit and spec.catch_basis == "ccsbt_ce_retained"
    with pytest.raises(ValueError, match="Unknown CCSBT product"):
        ccsbt.adapt(FakeLibrary({}), "ccsbt_grid")


def test_longline_cells_statuses_and_duplicate_stratum(lib):
    result = ccsbt.adapt(lib, "ccsbt_ll")
    record = lib.record("ccsbt/cedata_longline")
    assert result.record["sha256"] == record["sha256"]
    assert len(result.strata) == len(result.effort) == len(result.catch) == 10
    s = by_row(result)

    # 5x5 cell whose LATITUDE/LONGITUDE is the north-west corner.
    r = s.loc["2"]
    assert (r.west, r.south, r.grid_width, r.grid_height, r.lon, r.lat) == (100, -20, 5, 5, 102.5, -17.5)
    assert (r.geometry_status, r.grid_raw, r.region_raw, r.time_resolution) == ("located", "-15,100", "1", "month")
    assert (r.month_start, r.month_end, r.year) == (1, 1, 1965)
    assert (r.fleet, r.gear_raw, r.gear_group, r.school_type, r.reporting_group) == ("JP", "LL", "longline", "", "JP|LL")
    assert (r.source, r["product"], r.view_group, r.dataset_id) == ("ccsbt", "ccsbt_ll", "ccsbt_ll",
                                                                    "ccsbt/cedata_longline")
    assert r.source_sha256 == record["sha256"]
    assert r.source_member == "CEData_Longline"                  # worksheet; the sha pins the workbook
    assert r.stratum_id == "ccsbt_ll|1965|1|JP||1|-15|100"
    # Hooks are a count (not hundreds); SBF retained in numbers of fish.
    assert (r.effort, r.effort_status, r.effort_unit, r.effort_scope) == (2083, "reported", "hooks", "stratum")
    assert (r.catch, r.catch_status, r.catch_unit, r.species_code) == (4, "positive", "fish", "SBF")
    assert (r.effort_field, r.catch_field, r.catch_basis) == ("NUMBER OF HOOKS", "NUMBER OF SBT RETAINED",
                                                              "ccsbt_ce_retained")

    # Blank SBF is 'blank' (NA), never zero; an explicit 0 is a zero.
    assert s.loc["3", "catch_status"] == "blank" and pd.isna(s.loc["3", "catch"])
    assert s.loc["4", "catch_status"] == "zero" and s.loc["4", "catch"] == 0

    # The repeated natural key is one stratum: hooks and SBF summed, both worksheet rows kept.
    d = s.loc["5;6"]
    assert d.stratum_id == "ccsbt_ll|1995|12|NZ|BET|5|-35|175"
    assert d.effort == 26700 + 186870 and math.isclose(d.catch, 8.445087)
    assert d.catch_status == "positive" and d.school_type == ""
    assert {"target:BET", "duplicate_rows_aggregated"} <= set(notes(d))
    assert qc(result)["duplicate_strata_aggregated"] == 1 and qc(result)["duplicate_rows_merged"] == 1

    # Blank coordinates stay unlocated: no rectangle, never (0, 0).
    u = s.loc["7"]
    assert u.geometry_status == "unlocated" and u.grid_raw == ""
    assert all(pd.isna(u[c]) for c in ["west", "south", "grid_width", "grid_height", "lon", "lat"])
    assert u.catch_status == "positive" and math.isclose(u.catch, 1.574803)
    assert qc(result)["unlocated_strata"] == 1

    # (0, 0) in area 11 keeps its cell, flagged for review; blank hooks are missing effort.
    z = s.loc["8"]
    assert (z.geometry_status, z.west, z.south) == ("located", 0, -5)
    assert {"null_island_review", "jp_rtmp_era", "no_values"} <= set(notes(z))
    assert z.effort_status == "missing" and pd.isna(z.effort) and z.catch_status == "blank"
    assert z.stratum_status == "reported"

    c = s.loc["9"]
    assert (c.stratum_status, c.effort_status, c.catch, c.catch_status) == ("catch_only", "missing", 259, "positive")
    assert "catch_without_effort" in notes(c) and "no_effort_field" not in notes(c)

    nh = s.loc["10"]
    assert nh.region_raw == "NH" and {"area_NH", "tw_pre1996_estimated"} <= set(notes(nh))
    assert (nh.west, nh.south, nh.geometry_status) == (120, 5, "located")
    assert notes(s.loc["11"]) == ["target:SBF"]
    assert "jp_rtmp_era" not in notes(s.loc["2"])           # JP before 1995
    assert set(result.strata.stratum_status) == {"reported", "catch_only"}


def test_surface_one_degree_cells_effort_and_empty_rows(lib):
    sheet = openpyxl.load_workbook(lib.path("ccsbt/cedata_surface"), read_only=True)["CEData_Surface"]
    assert sheet.max_row > 40                                  # formatted empty rows are present
    result = ccsbt.adapt(lib, "ccsbt_surface")
    assert len(result.strata) == len(result.effort) == len(result.catch) == 6
    counts = qc(result)
    assert counts["rows_read"] == 7 and counts["blank_rows_skipped"] == 1 and counts["rows_used"] == 6
    s = by_row(result)

    # 1x1 cell from the north-west corner; PS -> purse_seine; kg of SBF, visual estimates.
    r = s.loc["2"]
    assert (r.west, r.south, r.grid_width, r.grid_height, r.lon, r.lat) == (150, -38, 1, 1, 150.5, -37.5)
    assert (r.gear_raw, r.gear_group, r.reporting_group) == ("PS", "purse_seine", "AU|PS")
    assert (r.effort, r.effort_unit, r.effort_status) == (17, "hours_searched", "reported")
    assert (r.catch, r.catch_unit, r.catch_status) == (59800, "kg", "positive")
    assert (r.effort_field, r.catch_field) == ("NUMBER OF HOURS SEARCHED", "WEIGHT (Kg) OF SBT RETAINED")
    assert notes(r) == ["visual_estimate"] and r.source_member == "CEData_Surface"

    b = s.loc["3"]
    assert (b.gear_group, b.catch_status) == ("pole_and_line", "blank") and pd.isna(b.catch) and notes(b) == []

    # Row numbers survive the empty row 4. Zero hours searched are not a real zero.
    h = s.loc["5"]
    assert h.effort_status == "missing" and pd.isna(h.effort)
    assert {"hours_coded_zero", "no_values"} <= set(notes(h)) and h.stratum_status == "reported"
    assert counts["coded_zero_effort"] == 1
    m = s.loc["6"]
    assert (m.effort_status, m.catch_status) == ("missing", "blank") and "no_values" in notes(m)

    # LATITUDE 0 with blank LONGITUDE is a placeholder: unlocated, not the equator.
    p = s.loc["7"]
    assert p.geometry_status == "unlocated" and pd.isna(p.west) and p.grid_raw == "0,"
    assert {"incomplete_coordinates", "area_missing", "no_effort_field"} <= set(notes(p))
    assert (p.catch, p.catch_status, p.stratum_status) == (0, "zero", "catch_only")

    za = s.loc["8"]
    assert (za.stratum_status, za.effort_status, za.catch_status) == ("catch_only", "missing", "positive")
    assert "no_effort_field" in notes(za) and "catch_without_effort" not in notes(za)
    assert counts["no_effort_field"] == 2


def test_headers_matched_ignoring_newlines_case_and_spaces():
    header = ["Year", " MONTH ", "Country\r\nCode", "TARGET  SPECIES", "CCSBT\nStatistical\nArea", "LATITUDE",
              "LONGITUDE", "Number\nof\nHooks", "NUMBER\nOF SBT\nRETAINED", "Unnamed: 9"]
    rows = [(2001, 2, "NZ", "SBF", 6, -45, 170, 4000, 3, None)]
    fake = FakeLibrary({"CEData_Longline": text_frame(header, rows)})
    result = ccsbt.adapt(fake, "ccsbt_ll")
    assert result.effort.effort_field.tolist() == ["Number of Hooks"]
    assert result.catch.catch_field.tolist() == ["NUMBER OF SBT RETAINED"]
    assert qc(result)["empty_columns_ignored"] == 1
    assert result.strata.stratum_id.tolist() == ["ccsbt_ll|2001|2|NZ|SBF|6|-45|170"]

    missing = FakeLibrary({"CEData_Longline": text_frame(header[:-2], [r[:-2] for r in rows])})
    with pytest.raises(ValueError, match="schema changed"):
        ccsbt.adapt(missing, "ccsbt_ll")
    extra = FakeLibrary({"CEData_Longline": text_frame(header[:-1] + ["NUMBER OF VESSELS"],
                                                       [(*rows[0][:-1], 5)])})
    with pytest.raises(ValueError, match="unreviewed column"):
        ccsbt.adapt(extra, "ccsbt_ll")


def test_pins_the_record_before_reading():
    rows = [(2001, 2, "NZ", "", 6, -45, 170, 4000, 3)]
    fake = FakeLibrary({"CEData_Longline": text_frame(LL_HEADER, rows)}, sha="b" * 64)
    result = ccsbt.adapt(fake, "ccsbt_ll", version="b" * 64)
    assert fake.calls[0] == ("record", "ccsbt/cedata_longline", "b" * 64)
    assert fake.calls[1] == ("read_table", "ccsbt/cedata_longline", "b" * 64, "CEData_Longline",
                             ("dtype", "keep_default_na"))
    assert result.record["sha256"] == "b" * 64 and set(result.strata.source_sha256) == {"b" * 64}


def test_years_filter_and_empty_selection(lib):
    result = ccsbt.adapt(lib, "ccsbt_ll", years=range(1995, 1996))
    assert set(result.strata.year) == {1995} and len(result.strata) == 2
    assert set(result.strata.source_rows) == {"5;6", "8"}
    assert qc(result)["rows_outside_years"] == 8
    empty = ccsbt.adapt(lib, "ccsbt_surface", years=[1900])
    assert len(empty.strata) == len(empty.effort) == len(empty.catch) == 0
    assert list(empty.strata.columns) == STRATA_COLUMNS


def test_suspect_effort_from_whole_file_and_invalid_values():
    rows = [(y, m, "TW", "", 9, -35, 20, 100000 + m, 5) for y in range(1996, 2001) for m in (1, 2, 3)]
    rows += [(1986, m, "TW", "", 9, -35, 20, 5000000, None) for m in (1, 2)]
    rows += [(2002, 1, "TW", "", 9, -35, 25, -5, 0)]
    fake = FakeLibrary({"CEData_Longline": text_frame(LL_HEADER, rows)})
    result = ccsbt.adapt(fake, "ccsbt_ll")
    flagged = result.strata[result.strata.notes.str.contains("suspect_effort")]
    assert set(flagged.year) == {1986} and len(flagged) == 2
    assert {"tw_pre1996_estimated"} <= set(flagged.notes.iloc[0].split(";"))
    only_1986 = ccsbt.adapt(fake, "ccsbt_ll", years=[1986])     # same flags under a years filter
    assert only_1986.strata.notes.str.contains("suspect_effort").all()
    bad = result.effort.merge(result.strata, on="stratum_id")
    bad = bad[bad.year_x.eq(2002)]
    assert bad.effort_status.tolist() == ["invalid"] and bad.effort.isna().all()
    assert "invalid_effort" in bad.notes.iloc[0]
    negative = FakeLibrary({"CEData_Longline": text_frame(LL_HEADER, [(2001, 2, "NZ", "", 6, -45, 170, 4000, -1)])})
    with pytest.raises(ValueError, match="Negative values"):
        ccsbt.adapt(negative, "ccsbt_ll")


def test_output_passes_zero_confirmation(lib):
    for product, spec in ccsbt.PRODUCTS.items():
        result = ccsbt.adapt(lib, product)
        final = confirm_zeros(result.strata, result.catch, zeros_explicit=spec.zeros_explicit,
                              catch_basis=spec.catch_basis)
        validate(result.strata, result.effort, final, final=True)
        blank = result.catch.catch_status.eq("blank")
        assert final.loc[blank.to_numpy(), "catch"].isna().all()   # blanks never become zeros
