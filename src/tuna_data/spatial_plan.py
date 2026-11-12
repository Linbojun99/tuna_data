"""Select one nonoverlapping GFW report per RFMO/year, including partial years."""
from datetime import date


def report_info(record):
    p = record.get("provenance", {}).get("request", {})
    profile = {"group-by": "FLAGANDGEARTYPE", "temporal-resolution": "MONTHLY", "format": "CSV",
               "spatial-resolution": "LOW", "spatial-aggregation": "false", "region-dataset": "public-rfmo"}
    if any(p.get(k) != v for k, v in profile.items()) or any(k.startswith("filters") for k in p):
        return None
    try:
        start, end = [date.fromisoformat(x) for x in p["date-range"].split(",")]
    except (KeyError, ValueError):
        return None
    if start != date(start.year, 1, 1) or not start < end <= date(start.year + 1, 1, 1):
        return None
    return dict(year=start.year, rfmo=p.get("region-id"), start=start.isoformat(), end=end.isoformat(),
                period_status="full_calendar_year" if end == date(start.year + 1, 1, 1) else "partial_year")


def plan_reports(lib, options, versions, fixed):
    selected = {}
    ids = list(fixed.values()) if fixed else list(lib.catalog().id)
    for dataset in ids:
        if not dataset.startswith("gfw/"):
            continue
        record = lib.record(dataset, versions.get(dataset))
        info = report_info(record)
        if info is None or info["rfmo"] not in options["gfw_rfmos"]:
            if fixed:
                raise ValueError("Pinned GFW report does not match the selected profile")
            continue
        if not options["all_years"] and not options["start_year"] <= info["year"] <= options["end_year"]:
            continue
        if info["period_status"] == "partial_year" and not options["include_partial_gfw"]:
            continue
        key = f'{info["rfmo"]}:{info["year"]}'
        if fixed and fixed.get(key) != dataset:
            raise ValueError("GFW report lock key does not match report dates/region")
        item = dict(info, dataset=dataset, sha256=record["sha256"], retrieved_at=record.get("retrieved_at", ""))
        # Prefer the largest available year-to-date window, then the newest snapshot.
        if key not in selected or (item["end"], item["retrieved_at"]) > (selected[key]["end"], selected[key]["retrieved_at"]):
            selected[key] = item
    if options["gfw_rfmos"]:
        if options["all_years"]:
            if not selected:
                raise ValueError("No eligible GFW reports in the library; run gfw-sync first")
            years = [r["year"] for r in selected.values()]
            first, last = min(years), max(years)
        else:
            first, last = options["start_year"], options["end_year"]
        missing = [f"{r}:{y}" for r in options["gfw_rfmos"] for y in range(first, last + 1) if f"{r}:{y}" not in selected]
        if missing:
            raise ValueError("Missing GFW RFMO/year reports; run gfw-sync: " + ", ".join(missing))
    return sorted(selected.values(), key=lambda r: (r["rfmo"], r["year"]))
