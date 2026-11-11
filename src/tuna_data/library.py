from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import NamedTemporaryFile
from urllib.parse import urlparse
import zipfile

from filelock import FileLock
import pandas as pd

from .config import data_home
from .sources import Product, discover, session, stable_id, open_download
from .storage import atomic_json, checksum, contained, utc_now


class Library:
    """A portable, content-addressed data directory, independent of source code.

    Roots can be regular folders, Drive for desktop folders, or mounted NAS
    shares. Writes preserve old snapshots. Only one machine should update a
    Drive-synced library at a time (Drive does not offer distributed file locks).
    """

    def __init__(self, root=None):
        self.root = data_home(root)

    def _manifest(self):
        path = self.root / "manifest.json"
        if not path.exists():
            return {"schema_version": 1, "datasets": {}}
        result = json.loads(path.read_text(encoding="utf-8"))
        if result.get("schema_version") != 1:
            raise ValueError("Unsupported library schema")
        return result

    def catalog(self) -> pd.DataFrame:
        entries = self._manifest()["datasets"]
        return pd.DataFrame([dict(id=key, **value["current"]) for key, value in entries.items()])

    def record(self, dataset: str, version=None):
        entries = self._manifest()["datasets"]
        if dataset not in entries:
            raise KeyError(f"Dataset not in this library: {dataset}; run catalog()")
        entry = entries[dataset]
        if version is None:
            return entry["current"]
        for record in [entry["current"], *entry["history"]]:
            if record["sha256"] == version:
                return record
        raise KeyError(f"Snapshot {version} not found for {dataset}")

    def path(self, dataset: str, version=None, verify=True) -> Path:
        record = self.record(dataset, version)
        path = contained(self.root, record["path"])
        if not path.is_file():
            raise FileNotFoundError(f"Missing data: {path}. Sync/download this library first.")
        if verify and checksum(path) != record["sha256"]:
            raise ValueError(f"Checksum mismatch for {dataset}; restore the snapshot")
        return path

    def import_file(self, path, dataset: str, *, source_url=None, title=None, provenance=None):
        """Copy a file into the library without changing the original file."""
        source = Path(path).expanduser().resolve()
        if not source.is_file() or source.name.startswith("._"):
            raise ValueError(f"Not a data file: {source}")
        if not re.fullmatch(r"[a-z0-9_-]+/[a-zA-Z0-9_.-]+", dataset):
            raise ValueError("Dataset ID must be source/name (letters, digits, _, - and .)")
        digest = checksum(source)
        # Keep the basename in the hash directory; it carries format information.
        relative = Path("raw") / dataset.split("/")[0] / digest / source.name
        target = contained(self.root, str(relative))
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists() or checksum(target) != digest:
            with NamedTemporaryFile(dir=target.parent, delete=False) as stream:
                temporary = Path(stream.name)
            try:
                shutil.copyfile(source, temporary)
                if checksum(temporary) != digest:
                    raise ValueError("Source changed during copy; retry import")
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        now = utc_now()
        record = {
            "source": dataset.split("/")[0], "path": relative.as_posix(),
            "filename": source.name, "bytes": target.stat().st_size,
            "sha256": digest, "retrieved_at": now, "checked_at": now,
            "source_url": source_url, "title": title or source.name,
            "provenance": provenance or {},
        }
        with FileLock(str(self.root / ".manifest.lock"), timeout=120):
            manifest = self._manifest()
            previous = manifest["datasets"].get(dataset)
            history = list(previous["history"]) if previous else []
            if previous:
                if previous["current"]["sha256"] == digest:
                    record["retrieved_at"] = previous["current"]["retrieved_at"]
                else:
                    history.append(previous["current"])
            manifest["datasets"][dataset] = {"current": record, "history": history}
            manifest["updated_at"] = now
            atomic_json(self.root / "manifest.json", manifest)
        return target

    def fetch(self, product: Product, *, update=True, http=None) -> Path:
        http = http or session()
        headers = {}
        previous = self._manifest()["datasets"].get(product.id)
        if previous:
            # A corrupt or incomplete local snapshot must not satisfy an HTTP 304.
            try:
                cached = self.path(product.id)
            except (FileNotFoundError, ValueError):
                cached = None
            if cached and not update:
                return cached
            if cached and previous["current"]["source_url"] == product.url:
                meta = previous["current"].get("provenance", {})
                if meta.get("etag"):
                    headers["If-None-Match"] = meta["etag"]
                if meta.get("last_modified"):
                    headers["If-Modified-Since"] = meta["last_modified"]
        self.root.mkdir(parents=True, exist_ok=True)
        # A unique staging directory avoids collisions when reading the same file.
        from tempfile import TemporaryDirectory
        with open_download(product, http, headers) as response:
            if response.status_code == 304:
                return self.path(product.id)
            response.raise_for_status()
            with TemporaryDirectory(dir=self.root) as staging:
                target = Path(staging) / Path(product.filename).name
                with target.open("wb") as stream:
                    for chunk in response.iter_content(1024 * 1024):
                        stream.write(chunk)
                validate_download(target, response.headers.get("Content-Type", ""))
                return self.import_file(target, product.id, source_url=product.url, title=product.title,
                    provenance={"landing_page": product.landing_page, "category": product.category,
                                "final_url": response.url, "etag": response.headers.get("ETag"),
                                "last_modified": response.headers.get("Last-Modified")})

    def update(self, sources=None, categories=None):
        """Discover then fetch; return all successes and explicit failures."""
        from .sources import LANDINGS
        results = {"updated": [], "errors": []}
        http = session()
        for source in sources or LANDINGS:
            try:
                products = discover(source, http=http)
            except Exception as exc:
                results["errors"].append({"source": source, "error": str(exc)})
                continue
            for product in products:
                if categories and product.category not in categories:
                    continue
                try:
                    self.fetch(product, http=http)
                    results["updated"].append(product.id)
                except Exception as exc:
                    results["errors"].append({"dataset": product.id, "error": str(exc)})
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_json(self.root / "last_update.json", dict(checked_at=utc_now(), **results))
        return results

    def members(self, dataset, version=None):
        path = self.path(dataset, version)
        if zipfile.is_zipfile(path) and path.suffix.lower() != ".xlsx":
            with zipfile.ZipFile(path) as z:
                return [i.filename for i in z.infolist() if not i.is_dir() and not Path(i.filename).name.startswith(".")]
        return [path.name]

    def read_table(self, dataset, *, member=None, version=None, sheet_name=0, table=None, **kwargs):
        """Read one table; multiple tables require an explicit member.

        CSV defaults preserve source identifiers and missing-value codes as text.
        Pass dtype/na_values explicitly for numerical analysis. Does not merge
        fisheries, add effort over species, decode unknown grids or fill zeros.
        """
        path = self.path(dataset, version)
        if zipfile.is_zipfile(path) and path.suffix.lower() != ".xlsx":
            with zipfile.ZipFile(path) as archive:
                candidates = [n for n in archive.namelist() if Path(n).suffix.lower() in {".csv", ".tsv", ".txt", ".xlsx", ".xls", ".parquet", ".mdb", ".accdb"} and not Path(n).name.startswith(".")]
                if member is None:
                    if len(candidates) != 1:
                        raise ValueError(f"Choose member= from {candidates}; use members() for all files")
                    member = candidates[0]
                if member not in archive.namelist():
                    raise KeyError(member)
                with archive.open(member) as stream:
                    if Path(member).suffix.lower() in {".mdb", ".accdb"}:
                        return self._read_mdb(stream, dataset, member, version, table, **kwargs)
                    return read_stream(stream, member, sheet_name=sheet_name, **kwargs)
        if path.suffix.lower() in {".mdb", ".accdb"}:
            with path.open("rb") as stream:
                return self._read_mdb(stream, dataset, path.name, version, table, **kwargs)
        return read_stream(path, path.name, sheet_name=sheet_name, **kwargs)

    def _read_mdb(self, stream, dataset, member, version, table, **kwargs):
        if not shutil.which("mdb-tables") or not shutil.which("mdb-export"):
            raise RuntimeError("ICCAT Access tables require mdbtools: brew install mdbtools / apt-get install mdbtools")
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            database = Path(directory) / Path(member).name
            with database.open("wb") as output:
                shutil.copyfileobj(stream, output)
            tables = subprocess.run(["mdb-tables", "-1", str(database)], check=True, capture_output=True, text=True).stdout.splitlines()
            if table not in tables:
                raise ValueError(f"Choose table= from {tables}")
            csv_path = Path(directory) / "table.csv"
            with csv_path.open("wb") as output:
                subprocess.run(["mdb-export", str(database), table], stdout=output, stderr=subprocess.PIPE, check=True)
            return read_stream(csv_path, "table.csv", **kwargs)

    def export_table(self, dataset, destination, **kwargs):
        table = self.read_table(dataset, **kwargs)
        output = Path(destination).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.suffix.lower() == ".parquet":
            table.to_parquet(output, index=False)
        else:
            table.to_csv(output, index=False)
        return output

    def mirror(self, destination):
        """Merge immutable snapshots into a mounted disk/Drive/NAS; no deletion.

        Existing destination datasets and older versions are retained. The source
        library's current version becomes current for datasets being mirrored.
        """
        other = Library(destination)
        if other.root.resolve() == self.root.resolve():
            return other
        for dataset, entry in self._manifest()["datasets"].items():
            for record in [*entry["history"], entry["current"]]:
                source = contained(self.root, record["path"])
                if checksum(source) != record["sha256"]:
                    raise ValueError(f"Checksum mismatch before mirror: {dataset}")
                other.import_file(source, dataset, source_url=record["source_url"], title=record["title"],
                                  provenance={**record.get("provenance", {}), "original_retrieved_at": record["retrieved_at"]})
        return other


def validate_download(path, content_type=""):
    if path.stat().st_size == 0:
        raise ValueError("Empty download")
    with path.open("rb") as stream:
        prefix = stream.read(256).lstrip().lower()
    if "text/html" in content_type.lower() or prefix.startswith((b"<!doctype html", b"<html")):
        raise ValueError("Expected data, received an HTML page")
    if path.suffix.lower() == ".zip" and not zipfile.is_zipfile(path):
        raise ValueError("Download is not a valid ZIP")
    # Detect actual workbook bytes rather than assuming an extension is accurate.
    if path.suffix.lower() == ".xlsx" and not zipfile.is_zipfile(path) and not prefix.startswith(bytes.fromhex("d0cf11e0a1b11ae1")):
        raise ValueError("Download is not a valid Excel workbook")
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError(f"Corrupt ZIP member: {bad}")


def read_stream(stream, name, *, sheet_name=0, **kwargs):
    suffix = Path(name).suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        return pd.read_excel(stream, sheet_name=sheet_name, **kwargs)
    if suffix == ".parquet":
        return pd.read_parquet(stream, **kwargs)
    if suffix not in {".csv", ".tsv", ".txt"}:
        raise ValueError(f"No table reader for {suffix}. MDB/ACCDB require mdbtools; use path() for the original.")
    if "chunksize" in kwargs or kwargs.get("iterator"):
        raise ValueError("Use pandas.read_csv on an explicitly opened file for chunked reads; this reader owns its stream")
    kwargs.setdefault("dtype", "string")
    kwargs.setdefault("keep_default_na", False)
    kwargs.setdefault("encoding", "utf-8-sig")
    if "sep" not in kwargs and "delimiter" not in kwargs:
        if isinstance(stream, Path):
            with stream.open("rb") as f:
                first = f.readline().decode(kwargs["encoding"])
        else:
            position = stream.tell()
            first = stream.readline().decode(kwargs["encoding"])
            stream.seek(position)
        try:
            kwargs["sep"] = csv.Sniffer().sniff(first, delimiters=",;\t|").delimiter
        except csv.Error:
            kwargs["sep"] = "\t" if suffix == ".tsv" else ","
    return pd.read_csv(stream, **kwargs)
