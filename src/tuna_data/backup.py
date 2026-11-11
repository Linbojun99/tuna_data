"""Portable private snapshots for Drive, external disks and NAS."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
import shutil
import zipfile

from .library import Library, METADATA_FILES
from .storage import checksum, contained


def backup(library: Library, destination):
    """Store original compressed files without recompressing; include all versions."""
    destination = Path(destination).expanduser().absolute()
    if destination.exists():
        raise FileExistsError(f"Snapshot already exists; use a new version name: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = library._manifest()
    paths = {}
    for entry in manifest["datasets"].values():
        for record in [entry["current"], *entry["history"]]:
            paths[record["path"]] = record["sha256"]
    with NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
        temp = Path(temporary.name)
    try:
        with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
            for relative, expected in paths.items():
                source = contained(library.root, relative)
                if checksum(source) != expected:
                    raise ValueError(f"Checksum mismatch: {relative}")
                archive.write(source, relative)
            for name in METADATA_FILES:
                path = library.root / name
                if path.is_file():
                    archive.writestr(name, json.dumps(json.loads(path.read_text(encoding="utf-8"))))
        os.replace(temp, destination)
    finally:
        temp.unlink(missing_ok=True)
    return destination


def restore(snapshot, destination):
    """Validate all bytes before merging; never extract arbitrary archive paths."""
    target = Library(destination)
    with TemporaryDirectory() as temp:
        staging = Path(temp)
        with zipfile.ZipFile(snapshot) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("schema_version") != 1:
                raise ValueError("Unsupported snapshot schema")
            expected = {}
            for entry in manifest["datasets"].values():
                for record in [*entry["history"], entry["current"]]:
                    expected[record["path"]] = record["sha256"]
            for relative, digest in expected.items():
                path = contained(staging, relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(relative) as src, path.open("wb") as out:
                    shutil.copyfileobj(src, out)
                if checksum(path) != digest:
                    raise ValueError(f"Snapshot checksum mismatch: {relative}")
            (staging / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            for name in METADATA_FILES:
                if name in archive.namelist():
                    (staging / name).write_text(json.dumps(json.loads(archive.read(name))), encoding="utf-8")
        Library(staging).mirror(target.root)
    return target
