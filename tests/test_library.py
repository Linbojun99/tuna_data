import json
from pathlib import Path
import zipfile

import pandas as pd
import pytest

from tuna_data import Library
from tuna_data.backup import backup, restore
from tuna_data.cleaning import gfw_hours, numeric_columns
from tuna_data.config import data_home
from tuna_data.sources import discover, stable_id
from tuna_data.storage import contained


def test_portable_snapshots_and_tamper_detection(tmp_path):
    src = tmp_path / "source.csv"
    src.write_text("flag,value\n001,NA\n002,0\n")
    lib = Library(tmp_path / "disk")
    lib.import_file(src, "iotc/test")
    old = lib.record("iotc/test")["sha256"]
    src.write_text("flag,value\n003,12\n")
    lib.import_file(src, "iotc/test")
    assert lib.read_table("iotc/test", version=old).iloc[0].tolist() == ["001", "NA"]
    snapshot = backup(lib, tmp_path / "private.zip")
    moved = restore(snapshot, tmp_path / "NAS")
    assert moved.read_table("iotc/test").iloc[0].tolist() == ["003", "12"]
    assert moved.read_table("iotc/test", version=old).iloc[1].tolist() == ["002", "0"]
    moved.path("iotc/test").write_text("corrupt")
    with pytest.raises(ValueError, match="Checksum"):
        moved.read_table("iotc/test")


def test_explicit_member_and_semicolon_reader(tmp_path):
    path = tmp_path / "archive.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("data.csv", "id;catch\n001;0\n002;NA\n")
        z.writestr("coverage.csv", "year,value\n2020,2\n")
    lib = Library(tmp_path / "lib")
    lib.import_file(path, "wcpfc/test")
    with pytest.raises(ValueError, match="Choose member"):
        lib.read_table("wcpfc/test")
    data = lib.read_table("wcpfc/test", member="data.csv")
    assert data["id"].tolist() == ["001", "002"]
    assert data["catch"].tolist() == ["0", "NA"]


def test_traversal_and_bad_restore(tmp_path):
    with pytest.raises(ValueError):
        contained(tmp_path, "../outside.csv")
    src = tmp_path / "a.csv"
    src.write_text("x\n1\n")
    lib = Library(tmp_path / "lib")
    with pytest.raises(ValueError):
        lib.import_file(src, "../../evil")
    manifest = {"schema_version": 1, "datasets": {"a/b": {"current": {"path": "../outside", "sha256": "abc"}, "history": []}}}
    with zipfile.ZipFile(tmp_path / "bad.zip", "w") as z:
        z.writestr("manifest.json", json.dumps(manifest))
        z.writestr("../outside", "bad")
    with pytest.raises(ValueError):
        restore(tmp_path / "bad.zip", tmp_path / "destination")
    assert not (tmp_path / "destination/manifest.json").exists()


def test_missing_codes_not_zero():
    x = pd.DataFrame({"Lat": ["0"], "Lon": ["12"], "Apparent Fishing Hours": ["0"], "flag": ["NA"]})
    result = gfw_hours(x)
    assert result["flag"][0] == "NA"
    assert result["fishing_hours"][0] == 0
    result = numeric_columns(pd.DataFrame({"catch": ["0", "NA"]}), ["catch"])
    assert result["catch"][0] == 0 and pd.isna(result["catch"][1])
    with pytest.raises(ValueError):
        numeric_columns(pd.DataFrame({"catch": ["confidential"]}), ["catch"])


def test_root_priority(tmp_path, monkeypatch):
    config = tmp_path / "config.toml"
    config.write_text('[storage]\nroot="/test/config"\n')
    monkeypatch.setenv("TUNA_DATA_CONFIG", str(config))
    monkeypatch.delenv("TUNA_DATA_HOME", raising=False)
    assert str(data_home()) == "/test/config"
    monkeypatch.setenv("TUNA_DATA_HOME", "/test/env")
    assert str(data_home()) == "/test/env"
    assert str(data_home("/test/argument")) == "/test/argument"


class FakeResponse:
    def __init__(self, url, body):
        self.url = url
        self.content = body.encode()
    def raise_for_status(self):
        pass


def test_iotc_subpages_and_wcpfc_media():
    class HTTP:
        def get(self, url, **kwargs):
            if url.endswith("datasets"):
                return FakeResponse(url, '<a href="/data/datasets/latest/NC/SCI">Data</a>')
            if url.endswith("SCI"):
                return FakeResponse(url, '<a href="/data/IOTC-2026-08-13-RC-SCI-1950-2025.zip">data</a>')
            return FakeResponse(url, '<table><tr><td>5 degree longline monthly</td><td><a href="/media/820#.zip">LL</a></td></tr></table><a href="/data/2023LLAU.xls">Excel</a>')
    assert len(discover("iotc", HTTP())) == 1
    products = discover("wcpfc", HTTP())
    result = next(p for p in products if p.id == "wcpfc/820")
    assert result.url.endswith("/media/820") and result.filename == "820.zip"
    assert next(p for p in products if p.id == "wcpfc/2023llau").filename == "2023LLAU.xls"
    assert "5 degree" in result.title
    assert stable_id("iccat", "t2ce_20260130.zip") == stable_id("iccat", "t2ce_20270130.zip")


def test_mirror_preserves_other_datasets(tmp_path):
    source = tmp_path / "a.csv"
    source.write_text("a\n1\n")
    a, b = Library(tmp_path / "a"), Library(tmp_path / "b")
    a.import_file(source, "iotc/new")
    b.import_file(source, "iccat/existing")
    a.mirror(b.root)
    assert set(b.catalog()["id"]) == {"iotc/new", "iccat/existing"}


def test_gfw_without_token_does_not_download(tmp_path, monkeypatch):
    from tuna_data.gfw import report
    monkeypatch.delenv("GFW_TOKEN", raising=False)
    monkeypatch.setenv("GFW_TOKEN_FILE", str(tmp_path / "missing-token"))
    with pytest.raises(ValueError, match="GFW_TOKEN"):
        report("WCPFC", "2024-01-01", "2024-02-01", library=Library(tmp_path))
