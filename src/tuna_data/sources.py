"""Discover downloadable public products from live RFMO index pages.

The result is a source catalogue, not a claim that every confidential or
assessment-specific dataset has been obtained. Failures are never hidden.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from urllib.parse import urljoin, urldefrag, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LANDINGS = {
    "iattc": "https://www.iattc.org/en-US/Data/Public-domain",
    "iccat": "https://www.iccat.int/en/accesingdb.html",
    "iotc": "https://iotc.org/data/datasets",
    "wcpfc": "https://www.wcpfc.int/sustainability/scientific-data/wcpfc-public-domain-aggregated-catcheffort-data-download-page",
    "ccsbt": "https://www.ccsbt.org/en/content/sbt-data",
}
EXTRA_LANDINGS = {
    "wcpfc": [
        "https://www.wcpfc.int/sustainability/scientific-data",
        "https://www.wcpfc.int/sustainability/scientific-data/wcpfc-annual-catch-and-effort-estimates-ace-tables-fleet",
        "https://www.wcpfc.int/sustainability/bycatch-mitigation/public-domain-bycatch-data",
    ]
}
SUFFIXES = (".zip", ".csv", ".xlsx", ".xls", ".7z", ".gz", ".mdb", ".accdb", ".txt", ".tsv")


def session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "tuna-data/0.1 (+https://github.com/Linbojun99/tuna_data)"
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET", "HEAD"], backoff_max=20)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


@dataclass(frozen=True)
class Product:
    id: str
    source: str
    url: str
    filename: str
    title: str
    landing_page: str
    category: str

    def to_dict(self):
        return asdict(self)


def category(name: str) -> str:
    name = name.lower()
    if "tag" in name:
        return "tagging"
    if any(x in name for x in ["size", "t2sz", "t2cs", "/sf/", "catch_at_size"]):
        return "size"
    if any(x in name for x in ["catalog", "reference", "ref_", "iccat.zip"]):
        return "reference"
    if any(x in name for x in ["craft", "vessel"]):
        return "fleet"
    if any(x in name for x in ["price", "fuel", "/sd/"]):
        return "economic"
    return "catch_effort"


def stable_id(source: str, name: str) -> str:
    stem = re.sub(r"\.(zip|csv|xlsx?|7z|gz|mdb|accdb|txt|tsv)$", "", name, flags=re.I)
    # Strip release dates but retain species, aggregation and product type.
    stem = re.sub(r"\d{4}-\d{2}-\d{2}|\d{8}|\d{4}-\d{4}", "", stem)
    return source + "/" + re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")


def discover(source: str, http=None) -> list[Product]:
    source = source.lower()
    if source not in LANDINGS:
        raise ValueError(f"Unknown RFMO: {source}; choose {', '.join(LANDINGS)}")
    http = http or session()
    pending = [LANDINGS[source], *EXTRA_LANDINGS.get(source, [])]
    visited, found = set(), {}
    while pending:
        page = pending.pop(0)
        if page in visited:
            continue
        visited.add(page)
        response = http.get(page, timeout=(15, 60))
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser")
        if source == "wcpfc" and soup.select_one("form#ace-year-form"):
            # Drupal exposes historical ACE releases through its year selector.
            # Use the live form/available options, not guessed filenames or years.
            form = soup.select_one("form#ace-year-form")
            options = form.select('select[name="ace_year"] option')
            selected = next((o["value"] for o in options if o.has_attr("selected")), None)
            for option in options:
                if option["value"] == selected:
                    continue
                fresh = http.get(page, timeout=(15, 60))
                fresh.raise_for_status()
                form = BeautifulSoup(fresh.content, "html.parser").select_one("form#ace-year-form")
                payload = {f["name"]: f.get("value", "") for f in form.select("input[name]")}
                payload.update(ace_year=option["value"], _triggering_element_name="ace_year", _drupal_ajax="1")
                ajax = http.post(page + "?ajax_form=1&_wrapper_format=drupal_ajax", data=payload, timeout=(15, 60))
                ajax.raise_for_status()
                fragments = [c.get("data", "") for c in ajax.json() if c.get("command") == "insert"]
                fragment = BeautifulSoup("".join(fragments), "html.parser")
                if not fragment.select("a.ace-file-link"):
                    raise ValueError(f"ACE year {option['value']} returned no files")
                soup.append(fragment)
        for anchor in soup.select("a[href]"):
            href = urljoin(response.url, anchor["href"])
            url, fragment = urldefrag(href)
            # Follow only IOTC's official product pages, never arbitrary crawls.
            if source == "iotc" and urlparse(url).hostname in {"iotc.org", "www.iotc.org"} and "/data/datasets/latest/" in url:
                if page == LANDINGS[source] and url not in visited and url not in pending:
                    pending.append(url)
                continue
            path = urlparse(url).path
            if not path.lower().endswith(SUFFIXES) and not (source == "wcpfc" and path.startswith("/media/") and fragment == ".zip"):
                continue
            title = anchor.get_text(" ", strip=True) or anchor.get("title", "")
            row = anchor.find_parent("tr")
            if row:
                title = row.get_text(" ", strip=True)
            filename = path.rsplit("/", 1)[-1]
            if source == "wcpfc" and path.startswith("/media/") and fragment == ".zip" and not filename.endswith(".zip"):
                filename += ".zip"
            product_id = stable_id(source, filename)
            product = Product(product_id, source, url, filename, title, page, category(filename + " " + page))
            if product_id in found and found[product_id].url != url:
                raise ValueError(f"Ambiguous products share ID {product_id}; review upstream catalogue")
            found[product_id] = product
    if not found:
        raise ValueError(f"No downloadable products on {LANDINGS[source]}; website may have changed")
    return sorted(found.values(), key=lambda p: p.id)


def open_download(product, http, headers=None):
    """CCSBT has an acknowledgement form in front of some public XLSX files.

    Follow the published form, retaining the source acknowledgement and caveats
    in provenance. A generic HTML response is never accepted as a data file.
    """
    response = http.get(product.url, headers=headers or {}, stream=True, timeout=(20, 180))
    if product.source != "ccsbt" or "text/html" not in response.headers.get("Content-Type", "").lower():
        return response
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    form = soup.find("form", id="download-conditions-form")
    if form is None:
        return response
    payload = {field["name"]: field.get("value", "") for field in form.select("input[name]")}
    target = urljoin(response.url, form.get("action", response.url))
    if urlparse(target).hostname != urlparse(response.url).hostname:
        raise ValueError("Unexpected CCSBT form destination")
    response.close()
    return http.post(target, data=payload, stream=True, timeout=(20, 180))
