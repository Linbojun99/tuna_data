from pathlib import Path
import json

import pytest

from tuna_data import Library
from tuna_data.naming import readable_stem


def test_readable_import_keeps_provider_name_and_snapshot_versions(tmp_path):
    source = tmp_path / "820.zip"
    source.write_bytes(b"first original payload")
    lib = Library(tmp_path / "library")
    path = lib.import_file(source, "wcpfc/820", title="5 degree longline monthly 1950 to 2024")
    original = lib.record("wcpfc/820")
    assert "longline" in path.name and path.suffix == ".zip"
    assert original["filename"] == "820.zip"
    assert lib.import_file(source, "wcpfc/820", title="Changed title") == path
    source.write_bytes(b"second original payload")
    new = lib.import_file(source, "wcpfc/820", title="5 degree longline monthly 1950 to 2025")
    assert new != path and path.read_bytes() == b"first original payload"
    assert lib.path("wcpfc/820", version=original["sha256"]) == path


def test_gfw_names_distinguish_full_and_partial_years():
    record = {"filename": "opaque.zip", "provenance": {"request": {
        "date-range": "2024-01-01,2025-01-01", "region-id": "IOTC",
        "temporal-resolution": "MONTHLY", "spatial-resolution": "LOW",
        "datasets[0]": "public-global-fishing-effort:v5.0"}}}
    assert readable_stem("gfw/test", record) == "GFW_IOTC_Fishing_hours_2024_MONTHLY_0.1deg_v5.0"
    record["provenance"]["request"]["date-range"] = "2026-01-01,2026-11-08"
    assert "2026-01-01_to_2026-11-07" in readable_stem("gfw/test", record)


def test_path_aliases_allow_out_of_order_sync_but_verify_bytes(tmp_path):
    src = tmp_path / "data.csv"
    src.write_text("year,value\n2024,10\n")
    lib = Library(tmp_path / "library")
    old = lib.import_file(src, "iotc/test")
    manifest = lib._manifest()
    record = manifest["datasets"]["iotc/test"]["current"]
    record["path_aliases"] = [record["path"]]
    record["path"] = "raw/iotc/renamed/data.csv"
    (lib.root / "manifest.json").write_text(json.dumps(manifest))
    assert lib.path("iotc/test") == old
    new = lib.root / record["path"]
    new.parent.mkdir(parents=True)
    old.rename(new)
    assert lib.read_table("iotc/test")["value"].tolist() == ["10"]
    new.write_text("damaged")
    with pytest.raises(ValueError, match="Checksum"):
        lib.path("iotc/test")


def test_aliases_cannot_escape_library(tmp_path):
    src = tmp_path / "data.csv"
    src.write_text("x\n1\n")
    lib = Library(tmp_path / "library")
    old = lib.import_file(src, "iotc/test")
    m = lib._manifest()
    m["datasets"]["iotc/test"]["current"]["path_aliases"] = ["../data.csv"]
    (lib.root / "manifest.json").write_text(json.dumps(m))
    old.unlink()
    with pytest.raises(ValueError, match="escapes"):
        lib.path("iotc/test")
