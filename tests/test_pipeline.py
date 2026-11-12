import io
import json
from pathlib import Path
import shutil
import zipfile

import pandas as pd
import pytest

from tuna_data import Library
from tuna_data.harmonize import DATASETS, GEARS, measurement, read_source, standardize
from tuna_data.pipeline import aggregate_catch, build_annual, read_processed, select_catch


def iattc(values=("0", ""), gear="LL"):
    return standardize(pd.DataFrame({"AnoYear": ["2020"] * len(values),
        "BanderaFlag": ["NA"] * len(values), "ArteGear": [gear] * len(values),
        "EspeciesSpecies": ["YFT"] * len(values), "t": list(values)}),
        "iattc", member="catch.csv", sha256="a" * 64)


def test_missing_zero_unknown_gear_and_native_codes():
    t = iattc(gear="NEW")
    assert t.flag_raw.tolist() == ["NA", "NA"]
    assert t.flag_key.tolist() == ["iattc::NA", "iattc::NA"]
    assert t.gear_group.tolist() == ["unmapped:iattc:NEW"] * 2
    assert t.gear_code_raw.tolist() == ["NEW", "NEW"]
    assert t.value_status.tolist() == ["reported_zero_below_0.5_t", "missing"]
    assert t.raw_t.tolist() == ["0", ""]
    total = aggregate_catch(t).iloc[0]
    assert total.observed_sum_tonnes == 0
    assert pd.isna(total.catch_tonnes) and total.n_missing == 1
    with pytest.raises(ValueError):
        measurement(pd.Series(["confidential"]))
    with pytest.raises(ValueError):
        measurement(pd.Series(["-1"]))
    with pytest.raises(ValueError):
        aggregate_catch(t, by=["year", "species_code", "gear_group"])


def test_catch_bases_are_not_added_together():
    t = pd.concat([iattc(("10",)), iattc(("20",))], ignore_index=True)
    t.loc[1, "catch_basis"] = "wcpfc_yearbook_reported"
    total = aggregate_catch(t)
    assert len(total) == 2
    assert set(total.catch_tonnes) == {10, 20}


def test_pacific_authority_views_prevent_double_counting():
    records = []
    for species in ["YFT", "ALB", "PBF", "SBF"]:
        for source, region in [("iattc", "EPO"), ("wcpfc", "WCPO"), ("wcpfc", "WCPFC"),
                               ("wcpfc", "EPO"), ("wcpfc", "NorthPacific"),
                               ("wcpfc", "SouthPacific"), ("ccsbt", "Global_SBF")]:
            row = iattc(("1",)).iloc[0].copy()
            row["source"], row["region"], row["species_code"] = source, region, species
            row["observation_id"] = ":".join([source, region, species])
            records.append(row)
    table = pd.DataFrame(records)
    selected = select_catch(table, species=["YFT", "ALB", "PBF", "SBF"], scope="pacific")
    assert len(selected) == 6
    assert set(selected.loc[selected.species_code.eq("YFT"), "region"]) == {"EPO", "WCPO"}
    assert set(selected.loc[selected.species_code.eq("ALB"), "region"]) == {"NorthPacific", "SouthPacific"}
    assert "SBF" not in set(selected.species_code)
    with pytest.raises(ValueError, match="No reviewed"):
        select_catch(table, species=["SWO"], scope="global")


def make_library(tmp_path):
    lib = Library(tmp_path / "library")

    def zipped(source, files):
        path = tmp_path / (source + ".zip")
        with zipfile.ZipFile(path, "w") as z:
            for name, body in files.items():
                z.writestr(name, body)
        lib.import_file(path, DATASETS[source])

    zipped("iattc", {"catch.csv": "AnoYear,BanderaFlag,ArteGear,EspeciesSpecies,t\n2020,001,LL,YFT,10\n"})
    zipped("wcpfc", {f"XLS_{area}_2026-01-01.csv":
        "YY,GEAR_CODE,FLAG_CODE,FLEET_CODE,SP_CODE,SP_NAME,SP_MT\n"
        "2020,L,NA,NA,YFT,Yellowfin,20\n2020,S,JP,JP,ALB,Albacore,30\n"
        "2020,S,JP,JP,PBF,Pacific bluefin,40\n"
        for area in ["WCPO", "EPO", "WCPFC", "NorthPacific", "SouthPacific", "NorthWCPFC", "SouthWCPFC"]})
    zipped("iotc", {"retained.csv":
        "YEAR,FLEET_CODE,FLEET,GEAR_CODE,GEAR,SPECIES_CODE,SPECIES,CATCH,FISHING_GROUND_CODE,FATE_TYPE_CODE,FATE_CODE\n"
        "2020,01,Example,LL,Longline,YFT,Yellowfin,30,IRWESIO,RETAINED,RFL\n"})
    iccat = pd.DataFrame({"YearC": ["2020", "2020"], "FlagName": ["Example"] * 2,
        "FleetCode": ["001"] * 2, "GearCode": ["LL"] * 2, "GearGrp": ["LL"] * 2,
        "Species": ["YFT"] * 2, "Qty_t": ["40", "5"], "RecID": ["1", "2"],
        "SampAreaCode": ["AT"] * 2, "Stock": ["YFT"] * 2, "CatchTypeCode": ["L", "DD"],
        "CnvFactor": ["2", "2"]})
    workbook = io.BytesIO()
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        iccat.to_excel(writer, sheet_name="Data", index=False)
        pd.DataFrame({"wrong_total": [999]}).to_excel(writer, sheet_name="Pivot", index=False)
    zipped("iccat", {"nominal.xlsx": workbook.getvalue()})
    ccsbt = pd.DataFrame({"Calendar Year": ["2020"], **{g: ["50" if g == "Longline" else ""] for g in GEARS["ccsbt"]}})
    path = tmp_path / "ccsbt.xlsx"
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        ccsbt.to_excel(writer, sheet_name="Catch by Gear", index=False, startrow=7)
        pd.DataFrame({"catch": [50]}).to_excel(writer, sheet_name="Catch by Flag", index=False)
    lib.import_file(path, DATASETS["ccsbt"])
    return lib


def test_build_freeze_missing_coverage_and_portability(tmp_path):
    lib = make_library(tmp_path)
    recipe = tmp_path / "recipe.toml"
    recipe.write_text('[pipeline]\nstart_year=2020\nend_year=2021\n')
    built = build_annual(recipe, library=lib)
    run = Path(built["run_dir"])
    selected = read_processed("global_catch", run=run)
    yft = selected.loc[selected.species_code.eq("YFT")]
    assert yft.catch_tonnes.sum() == 105  # source components; not a harmonized retained total
    assert len(yft) == 5  # four regions, two ICCAT types; no workbook Pivot duplicates
    assert selected.loc[selected.source.eq("ccsbt"), "catch_tonnes"].isna().sum() == 6
    assert selected.loc[selected.source.eq("ccsbt"), "flag_raw"].isna().all()
    assert selected.loc[selected.source.eq("ccsbt"), "flag_resolution"].eq("all_flags").all()
    cov = read_processed("global_coverage", run=run)
    assert not cov.loc[cov.year.eq(2021), "source_view_present"].any()
    assert (read_processed("global_by_gear", run=run).query("species_code == 'YFT'")
            .all_source_views_present.all())
    # Freeze means old source versions survive a new import.
    replacement = tmp_path / "new.csv"
    replacement.write_text("AnoYear,BanderaFlag,ArteGear,EspeciesSpecies,t\n2020,001,LL,YFT,999\n")
    lib.import_file(replacement, DATASETS["iattc"])
    repeated = build_annual(run / "recipe.lock.toml", library=lib)
    pd.testing.assert_frame_equal(selected, read_processed("global_catch", run=repeated["run_dir"]))
    # The completed run is portable independently of the original library path.
    moved = tmp_path / "NAS with spaces" / "run"
    shutil.copytree(run, moved)
    pd.testing.assert_frame_equal(selected, read_processed("global_catch", run=moved))
    (moved / "global_catch.parquet").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        read_processed("global_catch", run=moved)
    # Invalid configuration cannot replace the last completed run pointer.
    pointer = lib.root / "processed/annual/latest.json"
    latest = json.loads(pointer.read_text())
    recipe.write_text('[pipeline]\nscopes=["pacfic"]\n')
    with pytest.raises(ValueError):
        build_annual(recipe, library=lib)
    assert json.loads(pointer.read_text()) == latest


def test_iccat_id_and_fate_validation():
    raw = pd.DataFrame({"YearC": ["2020"] * 2, "FlagName": ["NA"] * 2,
        "FleetCode": ["X"] * 2, "GearCode": ["LL"] * 2, "GearGrp": ["LL"] * 2,
        "Species": ["YFT"] * 2, "Qty_t": ["1"] * 2, "RecID": ["same"] * 2,
        "SampAreaCode": ["A"] * 2, "Stock": ["A"] * 2, "CatchTypeCode": ["L"] * 2})
    with pytest.raises(ValueError, match="unique"):
        standardize(raw, "iccat", member="a.xlsx", sha256="a" * 64)
    raw.loc[1, "RecID"] = "different"
    raw.loc[1, "CatchTypeCode"] = "NEW"
    with pytest.raises(ValueError, match="catch types"):
        standardize(raw, "iccat", member="a.xlsx", sha256="a" * 64)


def test_override_evidence_and_replay(tmp_path):
    lib = make_library(tmp_path)
    crosswalk = tmp_path / "mapping.csv"
    crosswalk.write_text("source,gear_code,gear_group,evidence\niattc,LL,reviewed_longline,synthetic review\n")
    cfg = tmp_path / "pipeline.toml"
    cfg.write_text('[pipeline]\nstart_year=2020\nspecies=["YFT"]\nscopes=["pacific"]\ngear_overrides="mapping.csv"\n')
    built = build_annual(cfg, library=lib)
    run = Path(built["run_dir"])
    t = read_processed("pacific_catch", run=run)
    assert t.loc[t.source.eq("iattc"), "gear_group"].tolist() == ["reviewed_longline"]
    replay = build_annual(run / "recipe.lock.toml", library=lib)
    pd.testing.assert_frame_equal(t, read_processed("pacific_catch", run=replay["run_dir"]))


def test_source_schema_drift_cannot_silently_drop_or_duplicate_data(tmp_path):
    raw = pd.DataFrame({"Calendar Year": ["2020"], **{g: ["0"] for g in GEARS["ccsbt"]},
                        "New gear": ["123"]})
    with pytest.raises(ValueError, match="columns changed"):
        standardize(raw, "ccsbt", member="x.xlsx", sha256="a" * 64)
    lib = make_library(tmp_path)
    path = tmp_path / "duplicate_views.zip"
    with zipfile.ZipFile(path, "w") as z:
        for i in range(7):
            z.writestr(f"XLS_WCPO_version{i}.csv", "YY\n2020\n")
    lib.import_file(path, DATASETS["wcpfc"])
    with pytest.raises(ValueError, match="one CSV per"):
        read_source(lib, "wcpfc")
