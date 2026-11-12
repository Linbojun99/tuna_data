import json

import pandas as pd
import pytest

from tuna_data.spatial import _adapt_gfw
from tuna_data.spatial_plan import plan_reports
from tuna_data.spatial_store import YearWriter, read_table
from tuna_data.storage import atomic_json


def test_year_partitions_filter_before_conversion_and_verify_only_selected_files(tmp_path):
    data = pd.DataFrame(dict(year=[2023, 2024, 2024], species_code=["YFT", "YFT", "BET"],
                             gear_group=["longline", "longline", "purse_seine"], source=["iattc"] * 3,
                             value=[1., 2., 3.], flag_raw=["NA", "JP", "JP"]))
    with YearWriter(tmp_path, "rfmo_native") as writer:
        writer.write(data.iloc[:2])
        writer.write(data.iloc[2:])
        entry = writer.entry()
    atomic_json(tmp_path / "provenance.json", dict(schema_version=2, status="complete", outputs={"rfmo_native": entry}))
    selected = read_table(tmp_path, "rfmo_native", year=[2024.0], species="yft", gear="longline", source="IATTC", columns=["year", "value"])
    assert selected.to_dict("records") == [{"year": 2024, "value": 2.0}]
    assert read_table(tmp_path, "rfmo_native", year=1999).empty
    assert read_table(tmp_path, "rfmo_native", year=2023).flag_raw.iloc[0] == "NA"
    with (tmp_path / "rfmo_native/2023.parquet").open("ab") as f:
        f.write(b"modified")
    assert read_table(tmp_path, "rfmo_native", year=2024).value.sum() == 5
    with pytest.raises((ValueError, OSError)):
        read_table(tmp_path, "rfmo_native", year=2023)


def test_empty_selection_and_old_single_file_runs(tmp_path):
    from tuna_data.storage import checksum
    t = pd.DataFrame(dict(year=[2024], species_code=["YFT"], value=[4.]))
    t.to_parquet(tmp_path / "old.parquet", index=False)
    atomic_json(tmp_path / "provenance.json", dict(schema_version=1, status="complete", outputs={
        "global_catch_cells": dict(path="old.parquet", rows=1, sha256=checksum(tmp_path / "old.parquet"))}))
    assert read_table(tmp_path, "global_catch_cells", year=2023).empty
    assert read_table(tmp_path, "global_catch_cells", species="YFT").value.iloc[0] == 4
    with pytest.raises(ValueError, match="integer"):
        read_table(tmp_path, "global_catch_cells", year=2024.5)
    with pytest.raises(ValueError, match="no gear_group"):
        read_table(tmp_path, "global_catch_cells", gear="longline")


def record(rfmo, year, end, retrieved="2026-01-01"):
    return dict(sha256="a" * 64, retrieved_at=retrieved, provenance={"request": {
        "region-id": rfmo, "region-dataset": "public-rfmo", "date-range": f"{year}-01-01,{end}",
        "group-by": "FLAGANDGEARTYPE", "temporal-resolution": "MONTHLY", "format": "CSV",
        "spatial-resolution": "LOW", "spatial-aggregation": "false"}})


def test_plan_all_years_chooses_one_longest_window_and_pins_replays():
    class Library:
        records = {"gfw/old": record("IATTC", 2025, "2025-12-01"),
                   "gfw/full": record("IATTC", 2025, "2026-01-01"),
                   "gfw/short": record("IATTC", 2026, "2026-10-01"),
                   "gfw/latest": record("IATTC", 2026, "2026-11-09")}
        def catalog(self):
            return pd.DataFrame({"id": list(self.records)})
        def record(self, name, version=None):
            return self.records[name]
    options = dict(all_years=True, include_partial_gfw=True, gfw_rfmos=["IATTC"])
    plan = plan_reports(Library(), options, {}, {})
    assert [p["dataset"] for p in plan] == ["gfw/full", "gfw/latest"]
    assert [p["period_status"] for p in plan] == ["full_calendar_year", "partial_year"]
    pinned = plan_reports(Library(), options, {}, {"IATTC:2025": "gfw/full"})
    assert [p["year"] for p in pinned] == [2025]
    with pytest.raises(ValueError, match="Missing GFW"):
        plan_reports(Library(), dict(options, gfw_rfmos=["IATTC", "IOTC"]), {}, {})


def test_gfw_partial_year_is_explicit_and_species_filter_is_rejected(tmp_path):
    raw = pd.DataFrame({"Lat": ["1"], "Lon": ["120"], "Time Range": ["2026-11"],
                        "Flag": ["JP"], "Geartype": ["drifting_longlines"], "Apparent Fishing Hours": ["2"]})
    t = _adapt_gfw(raw, "gfw/example", "a" * 64, "data.csv", "WCPFC", 2026, 1, "2026-01-01", "2026-11-09")
    assert t.period_status.iloc[0] == "partial_year" and t.report_end_exclusive.iloc[0] == "2026-11-09"
    with YearWriter(tmp_path, "gfw_native") as writer:
        writer.write(t)
        entry = writer.entry()
    atomic_json(tmp_path / "provenance.json", dict(schema_version=2, status="complete", outputs={"gfw_native": entry}))
    assert len(read_table(tmp_path, "gfw_native", year=2026, rfmo="wcpfc")) == 1
    assert read_table(tmp_path, "gfw_native", include_partial=False).empty
    with pytest.raises(ValueError, match="do not identify tuna species"):
        read_table(tmp_path, "gfw_native", species="YFT")
