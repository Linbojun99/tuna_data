"""Readable storage names; dataset identifiers and original filenames stay stable."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import re
import unicodedata


IATTC = {
    "catchbyflaggear": "Catch_by_year_flag_gear_species",
    "publicllshark": "Longline_shark_catch_effort_5deg",
    "publiclltunabillfish": "Longline_tuna_billfish_catch_effort_5deg",
    "publiclptuna": "Pole_and_line_tuna_catch_effort_1deg",
    "publiclptunaflag": "Pole_and_line_tuna_catch_effort_by_flag_1deg",
    "publicpsbillfish": "Purse_seine_billfish_catch_effort_1deg",
    "publicpsshark": "Purse_seine_shark_catch_effort_1deg",
    "publicpstuna": "Purse_seine_tuna_catch_effort_1deg",
    "publicsizepsbillfish": "Purse_seine_billfish_size_observations",
}
CCSBT = {
    "annex2_subset": "Annex_2_subset", "annex2a_subset": "Annex_2a_subset",
    "catch_at_size": "Catch_at_size", "catchbyoyf": "Catch_by_ocean_year_flag",
    "catchbyymgolola": "Catch_by_year_month_gear_ocean_5deg",
    "cds_report": "Catch_documentation_scheme_report",
    "cedata_longline": "Longline_catch_effort", "cedata_surface": "Surface_catch_effort",
    "erswg_data": "ERSWG_data", "globalcatch_flag_gear": "Global_catch_by_flag_gear",
}


def safe_name(value, limit=110):
    value = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return re.sub(r"_+", "_", value).strip("._-")[:limit].rstrip("._-") or "dataset"


def readable_stem(dataset, record):
    source, key = dataset.split("/", 1)
    original = Path(record["filename"]).stem
    title = record.get("title") or original
    request = record.get("provenance", {}).get("request", {})
    label = original
    if source == "gfw" and request.get("date-range"):
        start, end = map(date.fromisoformat, request["date-range"].split(","))
        full_year = start == date(start.year, 1, 1) and end == date(start.year + 1, 1, 1)
        period = str(start.year) if full_year else f"{start}_to_{end - timedelta(days=1)}"
        resolution = {"LOW": "0.1deg"}.get(request.get("spatial-resolution"),
                                                    "resolution_" + request.get("spatial-resolution", "unknown"))
        version = request.get("datasets[0]", "dataset").rsplit(":", 1)[-1]
        label = f"{request.get('region-id', 'RFMO')}_Fishing_hours_{period}_{request.get('temporal-resolution', '')}_{resolution}_{version}"
    elif source == "gfw" and key == "merged_2012_2024":
        label = "Fishing_hours_merged_2012-2024_existing_snapshot"
    elif source == "iattc":
        label = IATTC.get(key, original)
    elif source == "ccsbt":
        label = CCSBT.get(key, original)
    elif source == "wcpfc":
        annual = re.fullmatch(r"(\d{4})([A-Za-z]{2})([A-Za-z]+)", original)
        if annual:
            # Use the publisher's country/fleet title; retain unexpanded gear codes.
            label = f"{annual[1]}_{title}_gear_{annual[2].upper()}_{annual[3].upper()}"
        elif key.isdigit():
            label = title.replace("°", "deg").replace("WCPFC Convention Area.", "WCPFC_area")
            label = label.replace("Pacific Ocean south of the equator.", "South_Pacific")
            label = label.split("Inactive since")[0].strip()
            # The publisher's media ID disambiguates any shortened title.
            label = safe_name(label, 90) + "_media_" + key
    elif source == "iccat":
        substitutions = [
            (r"^t1nc", "Task1_nominal_catch"),
            (r"^t2ce", "Task2_catch_effort"),
            (r"^t2cs", "Task2_catch_at_size"),
            (r"^t2sz", "Task2_size_samples"),
            (r"^_?tag", "Tagging_"),
            (r"^EFFDIS_LL", "Longline_effort_distribution_"),
            (r"^cdis", "Catch_distribution_CATDIS_"),
            (r"^ElecTags", "Electronic_tagging"),
        ]
        for pattern, replacement in substitutions:
            if re.search(pattern, original, re.I):
                label = re.sub(pattern, replacement, original, flags=re.I)
                break
        if key == "iccat":
            label = "Task1_FishStat"
    elif source == "iotc":
        label = re.sub(r"^IOTC[-_]", "", original, flags=re.I)
        label = re.sub(r"DATASETS[-_]", "", label, flags=re.I)
        label = label.replace("CELongline", "Longline_catch_effort").replace("CESurface", "Surface_catch_effort")
        label = re.sub(r"(^|-)CE(?=-|_)", r"\1Catch_effort", label)
        label = re.sub(r"(^|-)SF(?=-|_)", r"\1Size_frequency", label)
        label = re.sub(r"(^|-)RC(?=-|_)", r"\1Catch_RC", label)
    prefix = source.upper() + "_"
    return safe_name(label if label.upper().startswith(prefix) else prefix + label)


def readable_path(dataset, record):
    """A human-readable folder with a short checksum version suffix."""
    digest = record["sha256"]
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid SHA-256 for storage name")
    stem = readable_stem(dataset, record)
    extension = Path(record["filename"]).suffix.lower()
    return Path("raw") / dataset.split("/")[0] / f"{stem}__{digest[:12]}" / f"{stem}{extension}"
