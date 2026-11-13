import pandas as pd
import pytest

from tuna_data.spatial_adapters import adapt, PRODUCTS
from tuna_data.spatial_geometry import cwp_iotc, wcpfc_cell, ccsbt_cell, centered_cell, grid_status, pacific_selection
from tuna_data.spatial import _partial, _combine, _adapt_gfw, _catdis_id, GFW_KEYS


def convert(rows, product, species=("YFT", "BET")):
    return adapt(pd.DataFrame(rows), product, dataset=PRODUCTS[product][0], sha="a" * 64,
                 member="test.csv", species=species)


def test_source_corner_conventions_and_dateline():
    assert wcpfc_cell("05S", "175E", 5)["lat"] == -2.5
    assert ccsbt_cell(-5, 175)["lat"] == -7.5
    assert centered_cell(-2.5, -177.5, 5)["west"] == -180
    assert wcpfc_cell("00N", "180E", 5)["lon"] == -177.5
    assert cwp_iotc("6305175")["lon"] == -177.5
    assert cwp_iotc("6405010")["lon"] == -12.5
    assert cwp_iotc("6405010")["lat"] == 7.5
    assert cwp_iotc("IRWESIO")["geometry_status"] == "unlocated"
    assert cwp_iotc("2200040")["grid_width"] == 20


def test_catdis_latest_release_and_pinned_replay():
    class FakeLibrary:
        def catalog(self):
            return pd.DataFrame({"id": ["iccat/cdis5024_all", "iccat/cdis5025_all", "iccat/cdis5025_byspecies"]})
    assert _catdis_id(FakeLibrary(), {}) == "iccat/cdis5025_all"
    assert _catdis_id(FakeLibrary(), {"iccat/cdis5024_all": "a" * 64}) == "iccat/cdis5024_all"
    with pytest.raises(ValueError, match="Multiple"):
        _catdis_id(FakeLibrary(), {"iccat/cdis5024_all": "a" * 64, "iccat/cdis5025_all": "b" * 64})


def test_coarse_grids_and_pacific_overlap_are_retained_but_not_split_or_added():
    t = pd.DataFrame([cwp_iotc("2200040"), centered_cell(2.5, -147.5, 5),
                      centered_cell(2.5, -152.5, 5), cwp_iotc("IRWESIO")])
    t["source"], t["species_code"], t["region_raw"] = ["iotc", "wcpfc", "wcpfc", "iotc"], "YFT", ""
    out = grid_status(pacific_selection(t))
    assert out.grid_status.tolist() == ["coarser_or_misaligned", "eligible", "eligible", "unlocated"]
    assert out.region_selection.tolist() == ["selected", "outside_WCPO_partition", "selected", "unlocated"]
    assert pd.isna(out.iloc[0].map_lon)


def test_iotc_grid_sizes_follow_iotc_cwp_library_not_the_sf_notes_table():
    # iotc-lib-core-gis-cwp: 1=30x30, 2=10x20, 3=10x10, 4=20x20, 5=1x1, 6=5x5 (lat x lon).
    # The SF readme bundled with the CE reference says 5x10 = 1; CE codes do not use it.
    expected = {"1": (30, 30), "2": (10, 20), "3": (10, 10), "4": (20, 20), "5": (1, 1), "6": (5, 5)}
    for size, (height, width) in expected.items():
        cell = cwp_iotc(size + "120040")
        assert (cell["grid_height"], cell["grid_width"]) == (height, width)
        assert (cell["south"], cell["west"]) == (20, 40)
    # A 30x30 cell grows away from (0, 0) in every quadrant.
    corners = {"1110030": (10, 30), "1210030": (-40, 30), "1310030": (-40, -60), "1410030": (10, -60)}
    for code, (south, west) in corners.items():
        cell = cwp_iotc(code)
        assert (cell["south"], cell["west"], cell["grid_height"], cell["grid_width"]) == (south, west, 30, 30)
    # Iran's CE code 1100030 = IOTC's 30x30 cell 0-30N, 30-60E (DATA06 twin: 9100030).
    cell = cwp_iotc("1100030")
    assert (cell["south"], cell["west"], cell["lat"], cell["lon"]) == (0, 30, 15, 45)
    assert grid_status(pd.DataFrame([cell])).grid_status.iloc[0] == "coarser_or_misaligned"


def test_wcpfc_masking_quarters_and_effort_not_repeated_per_species():
    row = dict(YY="2024", MM="1", flag_code="JP", LAT5="05S", LON5="175E", cwp_grid="test",
               HHOOKS="2", YFT_C="0", YFT_N="0", BET_C="4", BET_N="8")
    t = convert([row, dict(row, flag_code="", HHOOKS="0", BET_C="0", BET_N="0")], "wcpfc_ll")
    effort = t[t.measure.eq("effort")]
    assert len(effort) == 2 and effort.value.iloc[0] == 200 and pd.isna(effort.value.iloc[1])
    assert t[(t.source_row == 2) & t.species_code.eq("YFT") & t.unit.eq("tonnes")].value.iloc[0] == 0
    assert t[t.source_row == 3].value.isna().all()
    ps = dict(YY="2024", qtr="2", flag_code="JP", LAT1="01S", LON1="150E", cwp_grid="test", DAYS="10")
    for school in ["UNA", "LOG", "DFAD", "AFAD", "OTH"]:
        ps.update({"SETS_" + school: "1", "YFT_C_" + school: "2", "BET_C_" + school: "3"})
    p = convert([ps], "wcpfc_ps")
    assert len(p) == 16 and (p.month_start == 4).all() and (p.month_end == 6).all()
    assert p[p.unit.eq("fishing_days")].value.sum() == 10
    assert p[p.unit.eq("sets")].value.sum() == 5


def test_iattc_preferred_weight_view_and_number_residual_do_not_convert_units():
    t = convert([dict(Year="2024", Month="1", Flag="JP", LatC5="-2.5", LonC5="-147.5",
                      Hooks="1000", YFTmt="0", YFTn="30", BETmt="5", BETn="0")], "iattc_ll")
    assert t[t.species_code.eq("YFT") & t.unit.eq("tonnes")].value.isna().all()
    assert t[t.species_code.eq("YFT") & t.unit.eq("fish")].value.iloc[0] == 30
    assert t[t.species_code.eq("BET") & t.unit.eq("tonnes")].value.iloc[0] == 5
    assert t[t.species_code.eq("BET") & t.unit.eq("fish")].value.isna().all()
    assert len(t[t.measure.eq("effort")]) == 1


def test_partial_values_never_claim_complete_and_gfw_regions_never_union():
    t = pd.DataFrame({"source": ["a", "a", "b", "c"], "value": [3, None, 7, None]})
    out = _combine([_partial(t, ["source"])], ["source"]).set_index("source")
    assert out.loc["a", "observed_value"] == 3 and pd.isna(out.loc["a", "value"])
    assert pd.isna(out.loc["c", "observed_value"]) and out.loc["a", "n_missing"] == 1
    raw = pd.DataFrame({"Lat": ["-0.1", "-0.2"], "Lon": ["179.9", "179.8"], "Time Range": ["2024-01"] * 2,
                        "Flag": ["NA", "JP"], "Geartype": ["longline"] * 2,
                        "Vessel IDs": ["2", "3"], "Apparent Fishing Hours": ["1.5", "2.5"]})
    parts = [_partial(_adapt_gfw(raw, "gfw/test", "b" * 64, "test.csv", region, 2024, 1), GFW_KEYS)
             for region in ["IATTC", "WCPFC"]]
    out = _combine(parts, GFW_KEYS)
    assert len(out) == 2 and set(out.region_raw) == {"IATTC", "WCPFC"}
    assert out.value.tolist() == [4, 4] and out.map_south.tolist() == [-1, -1]
    raw.loc[0, "Apparent Fishing Hours"] = ""
    with pytest.raises(ValueError, match="Missing"):
        _adapt_gfw(raw, "gfw/test", "b" * 64, "test.csv", "IATTC", 2024, 1)
