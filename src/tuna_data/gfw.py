"""Authenticated GFW 4Wings RFMO reports (apparent fishing hours, not catch)."""
from __future__ import annotations

from datetime import date
import hashlib
import json
import os
import time
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlparse

import requests
from filelock import FileLock

from .library import Library, validate_download
from .sources import session
from .storage import atomic_json, utc_now

API = "https://gateway.api.globalfishingwatch.org/v3"


def get_token(token=None):
    value = token or os.environ.get("GFW_TOKEN")
    path = Path(os.environ.get("GFW_TOKEN_FILE", "~/.config/tuna-data/gfw-token")).expanduser()
    if not value and path.is_file():
        value = path.read_text().strip()
    if not value:
        raise ValueError("Set GFW_TOKEN or ~/.config/tuna-data/gfw-token (never in Git)")
    return value


def report(rfmo, start, end, *, library=None, token=None, dataset="public-global-fishing-effort:latest",
           group_by="FLAGANDGEARTYPE", temporal_resolution="MONTHLY", spatial_resolution="LOW",
           spatial_aggregation=False, filters=None, update=False):
    """Download one report. Submit sequentially; split large periods by month.

    Date endpoints are forwarded unchanged to GFW. Request parameters and raw
    ZIP (including caveats/geometry) are preserved. GFW can revise latest data.
    """
    if date.fromisoformat(start) >= date.fromisoformat(end):
        raise ValueError("start must precede end (YYYY-MM-DD)")
    rfmo = rfmo.upper()
    if rfmo not in {"IATTC", "ICCAT", "IOTC", "WCPFC", "CCSBT"}:
        raise ValueError("Choose IATTC, ICCAT, IOTC, WCPFC or CCSBT")
    params = {"datasets[0]": dataset, "format": "CSV", "group-by": group_by,
              "temporal-resolution": temporal_resolution, "spatial-resolution": spatial_resolution,
              "spatial-aggregation": str(spatial_aggregation).lower(), "date-range": f"{start},{end}",
              "region-id": rfmo, "region-dataset": "public-rfmo"}
    if filters:
        params["filters[0]"] = filters
    key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    identifier = f"gfw/{rfmo.lower()}_{start}_{end}_{key}"
    lib = library or Library()
    if not update:
        try:
            return lib.path(identifier)
        except (KeyError, FileNotFoundError, ValueError):
            pass
    token = get_token(token)
    http = session()
    # No retry for report creation: a timeout can mean the server is still working.
    from requests.adapters import HTTPAdapter
    http.mount("https://", HTTPAdapter(max_retries=0))
    lib.root.mkdir(parents=True, exist_ok=True)
    pending = lib.root / "gfw_pending_report.json"
    with FileLock(str(lib.root / ".gfw-report.lock"), timeout=1):
        if pending.exists():
            state = json.loads(pending.read_text())
            if state["request"] != params:
                raise RuntimeError("A different GFW query is pending; finish it before submitting another")
            response = recover(http, token, params, pending)
        else:
            atomic_json(pending, {"request": params, "dataset_id": identifier, "submitted_at": utc_now()})
            try:
                response = http.get(API + "/4wings/report", params=params, headers={"Authorization": f"Bearer {token}"},
                                    stream=True, timeout=(20, 180))
            except requests.Timeout:
                response = recover(http, token, params, pending)
            if response.status_code == 524:
                response.close()
                response = recover(http, token, params, pending)
            elif response.status_code == 429:
                response.close()
                pending.unlink(missing_ok=True)
                raise RuntimeError("GFW is processing another report for this token; no replacement query was submitted")
        with response:
            if response.status_code >= 400:
                pending.unlink(missing_ok=True)
            response.raise_for_status()
            with TemporaryDirectory() as temp:
                path = Path(temp) / f"{identifier.split('/')[1]}.zip"
                with path.open("wb") as f:
                    for chunk in response.iter_content(1024 * 1024):
                        f.write(chunk)
                validate_download(path, response.headers.get("Content-Type", ""))
                validate_report(path, params)
                saved = lib.import_file(path, identifier, source_url=API + "/4wings/report",
                                        title=f"GFW apparent fishing hours in {rfmo}",
                                        provenance={"request": params, "measurement": "apparent_fishing_hours"})
                pending.unlink(missing_ok=True)
                return saved


def validate_report(path, params):
    """Confirm the returned ZIP belongs to this query, including on recovery."""
    with zipfile.ZipFile(path) as archive:
        notes = "\n".join(archive.read(n).decode("utf-8") for n in archive.namelist() if n.endswith(".md"))
        geometries = [json.loads(archive.read(n)) for n in archive.namelist() if n.endswith(".geojson")]
    start, end = params["date-range"].split(",")
    expected = [
        f"timestamp >= parseDateTimeBestEffort('{start}')",
        f"timestamp < parseDateTimeBestEffort('{end}')",
        f"Group by: {params['group-by']}",
        f"Temporal resolution: {params['temporal-resolution']}",
        f"Spatial aggregation: {params['spatial-aggregation']}",
        f"Resolution: {params['spatial-resolution']}",
    ]
    if not params["datasets[0]"].endswith(":latest"):
        expected.append("API Dataset versions: " + params["datasets[0]"])
    if any(text.lower() not in notes.lower() for text in expected):
        raise ValueError("GFW response metadata does not match the requested dates/version/resolution")
    regions = {str(feature.get("id")) for geometry in geometries for feature in geometry.get("features", [])}
    if regions != {params["region-id"]}:
        raise ValueError("GFW response geometry does not match the requested RFMO")


def recover(http, token, params, pending, *, wait_seconds=30, max_wait=1800):
    """Recover our timed-out report instead of submitting another job."""
    deadline = time.monotonic() + max_wait
    while time.monotonic() < deadline:
        response = http.get(API + "/4wings/last-report", headers={"Authorization": f"Bearer {token}"},
                            stream=True, timeout=(20, 180))
        if response.status_code == 404:
            response.close()
            pending.unlink(missing_ok=True)
            raise RuntimeError("GFW recovery window expired; rerun the same command to submit this missing partition")
        response.raise_for_status()
        if "json" not in response.headers.get("Content-Type", "").lower():
            return response
        status = response.json()
        response.close()
        if status.get("status") != "running":
            pending.unlink(missing_ok=True)
            raise RuntimeError(f"GFW report failed: {status}")
        active = {key: values[0] for key, values in parse_qs(urlparse(status["uri"]).query).items()}
        if any(active.get(key) != value for key, value in params.items()):
            raise RuntimeError("GFW last-report belongs to a different query; refusing to mislabel its data")
        atomic_json(pending, {"request": params, "status": "running", "checked_at": utc_now()})
        time.sleep(wait_seconds)
    raise TimeoutError("GFW report is still running; progress is saved. Resume the same command.")


def sync(*, library=None, rfmos=("IATTC", "ICCAT", "IOTC", "WCPFC", "CCSBT"), start_year=2012,
         refresh=False, progress=print):
    """All available years at 0.1 degree / monthly / flag-and-gear resolution.

    Resume completed jobs with verified checksums. Dataset version is resolved
    before the run, so a version change cannot silently mix query definitions.
    Use refresh=True to re-fetch historical revisions within the same version.
    """
    lib = library or Library()
    token = get_token()
    http = session()
    response = http.get(API + "/datasets/public-global-fishing-effort:latest",
                        headers={"Authorization": f"Bearer {token}"}, timeout=(20, 60))
    response.raise_for_status()
    metadata = response.json()
    dataset = metadata["id"]
    end = date.fromisoformat(metadata["endDate"][:10])
    from datetime import timedelta
    stop = end + timedelta(days=1)
    start_year = max(start_year, int(metadata["startDate"][:4]))
    result = {"dataset": dataset, "available_through": end.isoformat(), "started_at": utc_now(),
              "resolution": "0.1 degree, MONTHLY, FLAGANDGEARTYPE", "completed": [], "errors": []}
    lib.root.mkdir(parents=True, exist_ok=True)
    atomic_json(lib.root / "gfw_dataset.json", metadata)
    pending = lib.root / "gfw_pending_report.json"
    if pending.exists():
        # Finish the saved job first, even if the available end date or selected
        # RFMO list changed since the interruption.
        request = json.loads(pending.read_text())["request"]
        begin, finish = request["date-range"].split(",")
        progress(f"Recovering pending GFW {request['region-id']} {begin} to {finish}")
        report(request["region-id"], begin, finish, library=lib, token=token,
               dataset=request["datasets[0]"], group_by=request["group-by"],
               temporal_resolution=request["temporal-resolution"],
               spatial_resolution=request["spatial-resolution"],
               spatial_aggregation=request["spatial-aggregation"] == "true",
               filters=request.get("filters[0]"), update=True)
    for rfmo in rfmos:
        for year in range(start_year, stop.year + 1):
            begin, finish = date(year, 1, 1), min(date(year + 1, 1, 1), stop)
            if begin >= finish:
                continue
            job = {"rfmo": rfmo, "start": begin.isoformat(), "end": finish.isoformat()}
            try:
                path = report(rfmo, job["start"], job["end"], library=lib, token=token, dataset=dataset, update=refresh)
                result["completed"].append(job)
                progress(f"GFW OK {rfmo} {year} ({path.stat().st_size:,} bytes)")
            except Exception as exc:
                result["errors"].append(dict(**job, error=str(exc)))
                progress(f"GFW ERROR {rfmo} {year}: {exc}")
                # Don't submit a different query while a server-side report runs.
                atomic_json(lib.root / "gfw_sync_status.json", result)
                raise
            atomic_json(lib.root / "gfw_sync_status.json", result)
    result["finished_at"] = utc_now()
    atomic_json(lib.root / "gfw_sync_status.json", result)
    return result
