"""Authenticated GFW 4Wings RFMO reports (apparent fishing hours, not catch)."""
from __future__ import annotations

from datetime import date
import hashlib
import json
import os
import time
from pathlib import Path
from tempfile import TemporaryDirectory

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
    with http.get(API + "/4wings/report", params=params, headers={"Authorization": f"Bearer {token}"},
                  stream=True, timeout=(20, 180)) as response:
        if response.status_code in {429, 524}:
            raise RuntimeError(f"GFW HTTP {response.status_code}: another report may be running. Wait or recover it via /4wings/last-report; no duplicate report was submitted.")
        response.raise_for_status()
        with TemporaryDirectory() as temp:
            path = Path(temp) / f"{identifier.split('/')[1]}.zip"
            with path.open("wb") as f:
                for chunk in response.iter_content(1024 * 1024):
                    f.write(chunk)
            validate_download(path, response.headers.get("Content-Type", ""))
            return lib.import_file(path, identifier, source_url=API + "/4wings/report",
                                   title=f"GFW apparent fishing hours in {rfmo}",
                                   provenance={"request": params, "measurement": "apparent_fishing_hours"})


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
