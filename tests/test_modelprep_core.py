"""Synthetic end-to-end tests of the model-prep core (no library data)."""
import json
import types

import pandas as pd
import pytest

from tuna_data import Library
from tuna_data.modelprep import adapters as registry
from tuna_data.modelprep import assign_stock, build_modelprep, confirm_zeros, cpue_table, read_modelprep, sdm_table
from tuna_data.modelprep.schema import AdapterResult, ProductSpec, finalize


def stratum(product, key, *, source, year=2020, month=1, west=-140.0, south=0.0, size=5.0, fleet="JPN",
            gear="longline", gear_raw="LL", school="", status="reported", notes="", geometry="located"):
    return dict(stratum_id=f"{product}|{key}", source=source, product=product, view_group=product,
                dataset_id=f"{source}/fake_{product}", source_sha256="a" * 64, source_member="m.csv",
                source_rows=str(key), year=year, month_start=month, month_end=month, time_resolution="month",
                grid_raw="", west=west, south=south, grid_width=size, grid_height=size, lon=west + size / 2,
                lat=south + size / 2, geometry_status=geometry, region_raw=source.upper(), fleet=fleet,
                gear_raw=gear_raw, gear_group=gear, school_type=school, reporting_group=f"{fleet}|{gear_raw}",
                stratum_status=status, notes=notes)


def effort(sid, value, unit="hooks", status="reported", year=2020, scope="stratum"):
    return dict(stratum_id=sid, year=year, effort_unit=unit, effort=value, effort_status=status,
                effort_field="E", effort_scope=scope)


def catch(sid, species, value, unit="fish", status=None, year=2020):
    if status is None:
        status = "positive" if value and value > 0 else "zero"
    return dict(stratum_id=sid, year=year, species_code=species, catch_unit=unit, catch=value,
                catch_status=status, catch_field=species, catch_basis="test")


def frames():
    s, e, c = {}, {}, {}
    # IATTC longline (EPO).
    s["iattc_ll"] = [stratum("iattc_ll", 1, source="iattc"),
                     stratum("iattc_ll", 2, source="iattc", fleet="ESP"),            # X01 excluded
                     stratum("iattc_ll", 3, source="iattc", fleet="TWN"),            # TWN never reports BET -> unconfirmed zero
                     stratum("iattc_ll", 4, source="iattc", status="masked"),
                     stratum("iattc_ll", 5, source="iattc", size=10.0),              # coarser than 5 degrees
                     stratum("iattc_ll", 6, source="iattc", month=2),
                     stratum("iattc_ll", 7, source="iattc", west=-130.0, south=15.0)]    # Hawaii corner (X15a)
    e["iattc_ll"] = [effort("iattc_ll|1", 2000.0), effort("iattc_ll|2", 1000.0), effort("iattc_ll|3", 1000.0),
                     effort("iattc_ll|4", None, status="masked"), effort("iattc_ll|5", 1000.0),
                     effort("iattc_ll|6", None, status="missing"), effort("iattc_ll|7", 1000.0)]
    c["iattc_ll"] = [catch("iattc_ll|1", "BET", 10.0), catch("iattc_ll|1", "BET", 0.4, unit="tonnes"),
                     catch("iattc_ll|2", "BET", 0.0), catch("iattc_ll|3", "BET", 0.0), catch("iattc_ll|3", "YFT", 3.0),
                     catch("iattc_ll|4", "BET", None, status="masked"), catch("iattc_ll|5", "BET", 2.0),
                     catch("iattc_ll|6", "BET", 1.0), catch("iattc_ll|7", "BET", 3.0),
                     catch("iattc_ll|1", "SKJ", 0.0), catch("iattc_ll|3", "SKJ", 2.0)]
    # WCPFC longline: one cell west of 150W (WCPO) and one east of it (IATTC authoritative).
    s["wcpfc_ll"] = [stratum("wcpfc_ll", 1, source="wcpfc", west=160.0), stratum("wcpfc_ll", 2, source="wcpfc", west=-145.0),
                     stratum("wcpfc_ll", 3, source="wcpfc", west=165.0),
                     stratum("wcpfc_ll", 4, source="wcpfc", west=170.0, year=2021),   # 0 fish next to positive tonnes
                     stratum("wcpfc_ll", 5, source="wcpfc", west=175.0, year=2021)]   # zero in a year without BET catch
    e["wcpfc_ll"] = [effort("wcpfc_ll|1", 1000.0), effort("wcpfc_ll|2", 1000.0), effort("wcpfc_ll|3", 500.0),
                     effort("wcpfc_ll|4", 800.0, year=2021), effort("wcpfc_ll|5", 800.0, year=2021)]
    c["wcpfc_ll"] = [catch("wcpfc_ll|1", "BET", 4.0), catch("wcpfc_ll|2", "BET", 9.0), catch("wcpfc_ll|3", "BET", 0.0),
                     catch("wcpfc_ll|4", "BET", 0.0, year=2021), catch("wcpfc_ll|4", "BET", 0.5, unit="tonnes", year=2021),
                     catch("wcpfc_ll|5", "BET", 0.0, year=2021)]
    # IOTC (positives only): stratum 2 has effort but no BET row -> inferred_zero_candidate.
    s["iotc_ce"] = [stratum("iotc_ce", 1, source="iotc", west=60.0, south=-10.0),
                    stratum("iotc_ce", 2, source="iotc", west=65.0, south=-10.0, status="effort_only")]
    e["iotc_ce"] = [effort("iotc_ce|1", 3000.0), effort("iotc_ce|2", 3000.0)]
    c["iotc_ce"] = [catch("iotc_ce|1", "BET", 6.0)]
    # Purse seine 1x1 strata nesting into one 5x5 cell.
    s["iattc_ps_settype"] = [stratum("iattc_ps_settype", k, source="iattc", west=w, south=-3.0, size=1.0, fleet="",
                                     gear="purse_seine", gear_raw="PS", school="OBJ") for k, w in [(1, -120.0), (2, -119.0)]]
    e["iattc_ps_settype"] = [effort("iattc_ps_settype|1", 2.0, unit="sets", scope="school"),
                             effort("iattc_ps_settype|2", 3.0, unit="sets", scope="school")]
    c["iattc_ps_settype"] = [catch("iattc_ps_settype|1", "SKJ", 50.0, unit="tonnes"),
                             catch("iattc_ps_settype|2", "SKJ", 0.0, unit="tonnes")]
    s["iattc_ps_flag"] = [stratum("iattc_ps_flag", 1, source="iattc", west=-120.0, size=1.0, gear="purse_seine", gear_raw="PS")]
    e["iattc_ps_flag"] = [effort("iattc_ps_flag|1", 5.0, unit="sets")]
    c["iattc_ps_flag"] = [catch("iattc_ps_flag|1", "SKJ", 50.0, unit="tonnes")]
    return s, e, c


@pytest.fixture
def run(tmp_path, monkeypatch):
    s, e, c = frames()
    specs = {}
    for name in s:
        source = name.split("_")[0]
        group = "iattc_ps" if name.startswith("iattc_ps") else name
        specs[name] = (source, ProductSpec(name, source, f"{source}/fake_{name}", "m.csv", group,
                                           zeros_explicit=(name != "iotc_ce"), catch_basis="test", description=name))

    def adapt(lib, product, *, version=None, years=None, progress=None):
        group = specs[product][1].view_group
        st = pd.DataFrame(s[product]).assign(view_group=group)
        out = finalize(st, pd.DataFrame(e[product]), pd.DataFrame(c[product]))
        return AdapterResult(*out, record={"sha256": "b" * 64, "filename": "f"}, qc=[{"check": "rows", "count": len(st), "note": ""}])

    fake = types.SimpleNamespace(adapt=adapt)
    monkeypatch.setattr(registry, "products", lambda: specs)
    monkeypatch.setattr(registry, "module", lambda source: fake)
    lib = Library(tmp_path / "lib")
    result = build_modelprep(library=lib, progress=None)
    return lib, result


def test_build_is_immutable_verified_and_confirms_zeros(run):
    lib, result = run
    latest = json.loads((lib.root / "processed" / "modelprep" / "latest.json").read_text())
    assert result["run_dir"].endswith(latest["run_id"])
    c = read_modelprep("catch", library=lib, species="BET")
    status = dict(zip(c.stratum_id + ":" + c.catch_unit, c.catch_status))
    assert status["iattc_ll|2:fish"] == "zero_unconfirmed"      # ESP never reports BET
    assert status["iattc_ll|3:fish"] == "zero_unconfirmed"      # TWN never reports BET
    assert status["wcpfc_ll|3:fish"] == "zero_reported"         # JPN reports BET that year
    assert status["iotc_ce|2:fish"] == "inferred_zero_candidate" and pd.isna(c.set_index("stratum_id").loc["iotc_ce|2", "catch"])
    path = next((lib.root / "processed" / "modelprep" / latest["run_id"] / "catch").glob("*.parquet"))
    path.write_bytes(path.read_bytes()[:-8] + b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        read_modelprep("catch", library=lib)


def test_confirm_zeros_blank_rules():
    st = pd.DataFrame([dict(stratum_id=f"x|{i}", reporting_group=g, year=2020, stratum_status="reported")
                       for i, g in [(1, "A"), (2, "A"), (3, "B"), (4, "B")]])
    c = pd.DataFrame([catch("x|1", "SBF", 5.0), catch("x|2", "SBF", None, status="blank"),
                      catch("x|3", "SBF", 0.0), catch("x|4", "SBF", None, status="blank")])
    out = dict(zip(c.stratum_id, confirm_zeros(st, c, zeros_explicit=True, catch_basis="t").catch_status))
    assert out["x|2"] == "inferred_zero_candidate"   # group A writes no zeros but reports SBF
    assert out["x|3"] == "zero_unconfirmed" and out["x|4"] == "not_reported"   # group B writes zeros, no positives


def test_cpue_table_rules_and_log(run):
    lib, _ = run
    with pytest.warns(UserWarning, match="positive catches only"):
        bet = cpue_table("BET", "longline", library=lib)
    ids = set(bet.stratum_id)
    assert ids == {"iattc_ll|1", "wcpfc_ll|1", "wcpfc_ll|3", "wcpfc_ll|5", "iotc_ce|1"}   # iattc_ll|7: X15a
    row = bet.set_index("stratum_id").loc["iattc_ll|1"]
    assert row.stock_id == "BET-EPO" and row.cpue == pytest.approx(5.0) and row.catch_unit == "fish"
    assert bet.set_index("stratum_id").loc["wcpfc_ll|1", "stock_id"] == "BET-WCPO"
    log = bet.attrs["filter_log"]
    assert {"R03", "X01", "X15a", "R06", "R06s", "R08", "R09", "W01"} <= set(log.rule_id)
    assert (log.rows_before - log.rows_after == log.removed).all()
    wide = cpue_table("BET", "longline", library=lib, zeros="with_candidates", stock="BET-IO")
    assert set(wide.stratum_id) == {"iotc_ce|1", "iotc_ce|2"} and wide.set_index("stratum_id").loc["iotc_ce|2", "catch"] == 0
    assert "inferred_zero" in wide.set_index("stratum_id").loc["iotc_ce|2", "qc_flags"]
    with pytest.raises(ValueError, match="one product per view_group"):
        cpue_table("SKJ", "purse_seine", library=lib, products=["iattc_ps_settype", "iattc_ps_flag"])
    with pytest.raises(ValueError, match="not recommended"):
        cpue_table("PBF", "longline", library=lib)


def test_sdm_presence_absence_and_grid(run):
    lib, _ = run
    skj = sdm_table("SKJ", "purse_seine", library=lib, grid=5, period="month")
    assert len(skj) == 1
    row = skj.iloc[0]
    assert row.presence == 1 and row.effort == 5.0 and row.catch == 50.0 and row.n_strata == 2
    assert (row.map_west, row.map_south, row.map_size) == (-120.0, -5.0, 5.0)


def test_stock_boundaries():
    t = pd.DataFrame([stratum("p", i, source=s, west=w, south=y, size=5.0) for i, (s, w, y) in enumerate([
        ("wcpfc", 170.0, 0.0), ("wcpfc", 170.0, -5.0), ("wcpfc", 170.0, 55.0),        # ALB N, S, beyond 55N
        ("iccat", -50.0, 40.0), ("iccat", -40.0, 40.0), ("iccat", -30.0, 2.5),         # BFT W, E (>=10N), E (0-5N: boundary 30W)
        ("iccat", 5.0, 40.0), ("iotc", 60.0, -10.0), ("ccsbt", 60.0, -40.0)])])
    alb, _ = assign_stock(t, "ALB")
    assert list(alb[:3]) == ["ALB-NPO", "ALB-SPO", ""]
    bft, flag = assign_stock(t, "BFT")
    assert list(bft[3:7]) == ["BFT-W", "BFT-E", "BFT-E", "BFT-E"] and "MED" in flag.iloc[6]
    sbf, flag = assign_stock(t, "SBF")
    assert list(sbf) == [""] * 8 + ["SBF"] and flag.iloc[7] == "SBF_CCSBT_authority"
    skj, _ = assign_stock(t, "SKJ")
    assert skj.iloc[4] == "SKJ-ATW" and skj.iloc[5] == "SKJ-ATE"


def test_zero_rules_cross_unit_and_whole_series(run):
    lib, _ = run
    c = read_modelprep("catch", library=lib, species="BET")
    status = dict(zip(c.stratum_id + ":" + c.catch_unit, c.catch_status))
    assert status["wcpfc_ll|4:fish"] == "unit_incomplete"       # 0 fish while tonnes > 0: a missing unit
    assert status["wcpfc_ll|5:fish"] == "zero_reported"         # JPN reports BET in the series (2020)


def test_sdm_gear_policies_and_argument_checks(run):
    lib, _ = run
    skj = sdm_table("SKJ", library=lib)                          # longline is not recommended: skipped
    assert set(skj.gear_group) == {"purse_seine"}
    assert "skip" in set(skj.attrs["filter_log"].rule_id)
    ll = sdm_table("SKJ", "longline", library=lib, allow_unrecommended=True)
    assert len(ll) and (ll.presence == 1).all()                  # longline zeros are not SKJ absences
    with pytest.raises(ValueError, match="not recommended"):
        sdm_table("SKJ", "longline", library=lib)
    with pytest.raises(ValueError, match="gfw=True needs grid"):
        sdm_table("SKJ", "purse_seine", library=lib, gfw=True)
    with pytest.raises(ValueError, match="Unknown BET stock"):
        cpue_table("BET", library=lib, stock="BET-WCPFC")
