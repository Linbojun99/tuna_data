"""Annual catch adapters. Preserve native dimensions; never infer absent catch."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

DATASETS = {
    "iattc": "iattc/catchbyflaggear",
    "wcpfc": "wcpfc/952",
    "iotc": "iotc/iotc_datasets_rc_sci",
    "iccat": "iccat/t1nc",
    "ccsbt": "ccsbt/globalcatch_flag_gear",
}
SPECIES = {
    "ALB": "Albacore", "BET": "Bigeye tuna", "SKJ": "Skipjack tuna",
    "YFT": "Yellowfin tuna", "PBF": "Pacific bluefin tuna",
    "BFT": "Atlantic bluefin tuna", "SBF": "Southern bluefin tuna",
}
# Conservative, source-specific crosswalk. Unverified codes remain explicit.
GEARS = {
    "iattc": {
        "GN": "gillnet", "HAR": "harpoon", "LHP": "handline", "LL": "longline",
        "LP": "pole_and_line", "LTL": "troll", "OTR": "other", "PS": "purse_seine",
        "RG": "recreational", "TX": "trawl", "UNK": "unknown",
    },
    "wcpfc": {
        "G": "gillnet", "L": "longline", "O": "other", "P": "pole_and_line",
        "R": "ringnet", "S": "purse_seine", "T": "troll",
    },
    "iotc": {
        **dict.fromkeys(["LLCO", "FLL", "LL", "ELL", "LLEX", "SLL", "LLF"], "longline"),
        **dict.fromkeys(["BB", "BBN", "BBM", "BBOF"], "pole_and_line"),
        **dict.fromkeys(["TROL", "TROLM", "TROLN"], "troll"),
        **dict.fromkeys(["HAND", "HLOF", "DL"], "handline"),
        **dict.fromkeys(["GILL", "GIOF"], "gillnet"),
        **dict.fromkeys(["PS", "PSS"], "purse_seine"),
        **dict.fromkeys(["RIN", "RNOF"], "ringnet"),
        "BS": "beach_seine", "SPOR": "recreational", "DSEI": "danish_seine",
        "LIFT": "lift_net", "TRAW": "trawl", "RR": "rod_and_reel", "CN": "cast_net",
        "TRAP": "trap", "LG": "mixed_longline_gillnet", "GL": "mixed_gillnet_longline",
        "FN": "fish_net", "HARP": "harpoon", "TROT": "trotline", "SBG": "set_bag_net",
    },
    "iccat": {
        "BB": "pole_and_line", "GN": "gillnet", "HL": "handline", "HP": "harpoon",
        "LL": "longline", "PS": "purse_seine", "RR": "rod_and_reel",
        "TP": "trap", "TR": "troll", "TW": "trawl",
    },
    "ccsbt": {
        "Longline": "longline", "Purse Seine": "purse_seine", "Pole and Line": "pole_and_line",
        "Troll": "troll", "Handline": "handline", "Gill Net": "gillnet", "Other": "other",
    },
}
CANONICAL = [
    "observation_id", "source", "dataset_id", "source_sha256", "source_member",
    "source_sheet", "source_row", "source_column", "native_record_id", "year",
    "species_code", "species_name", "region", "subregion_raw", "stock_raw",
    "gear_code_raw", "gear_label_raw", "gear_mapping_code", "gear_group", "gear_mapping_status",
    "flag_raw", "flag_name_raw", "fleet_raw", "flag_key", "flag_resolution",
    "catch_type_raw", "catch_basis", "catch_tonnes", "unit", "value_status", "source_note",
]


def measurement(values):
    """Only known missing tokens become NA. Unknown tokens stop the build."""
    values = values.astype("string").str.strip()
    numbers = pd.to_numeric(values.mask(values.isin(["", "NA", "N/A", "NaN", "…", "..."])), errors="raise")
    if ((numbers < 0) | (numbers == float("inf"))).any():
        raise ValueError("Negative or infinite catch is not supported by this annual template")
    return numbers.astype("Float64")


def _require(raw, columns):
    missing = set(columns) - set(raw.columns)
    if missing:
        raise ValueError(f"Source schema changed; missing columns: {sorted(missing)}")


def standardize(raw, source, *, member, sha256, region=None, sheet="", overrides=None):
    """Normalize one documented annual table; keep every row and all native fields.

    CCSBT input is the gear sheet read with header=7, dtype=str, keep_default_na=False.
    CSV source_row includes its header; Excel source_row is the worksheet row number.
    """
    raw = raw.reset_index(drop=True).copy()
    if source == "ccsbt":
        raw.columns = [re.sub(r"\s+", " ", str(c)).strip() for c in raw.columns]
        _require(raw, ["Calendar Year", *GEARS["ccsbt"]])
        if set(raw.columns) != {"Calendar Year", *GEARS["ccsbt"]}:
            raise ValueError("CCSBT gear columns changed; review new columns before melting")
        raw["_worksheet_row"] = range(9, len(raw) + 9)
        raw = raw.melt(id_vars=["Calendar Year", "_worksheet_row"],
                       value_vars=list(GEARS["ccsbt"]), var_name="gear", value_name="tonnes")
    result = pd.DataFrame(index=raw.index, columns=CANONICAL)
    # Save native strings separately in each standardized source file.
    for col in raw.columns:
        result[f"raw_{col}"] = raw[col].astype("string")
    result["source"] = source
    result["dataset_id"] = DATASETS[source]
    result["source_sha256"] = sha256
    result["source_member"] = member
    result["source_sheet"] = sheet
    result["source_row"] = range(2, len(raw) + 2)
    result["source_column"] = ""
    result["flag_resolution"] = "reported_flag_or_fleet"
    result["unit"] = "tonnes_whole_weight"

    def assign(mapping):
        _require(raw, mapping.values())
        for target, native in mapping.items():
            result[target] = raw[native].astype("string")

    if source == "iattc":
        assign({"year": "AnoYear", "flag_raw": "BanderaFlag", "gear_code_raw": "ArteGear",
                "species_code": "EspeciesSpecies", "catch_tonnes": "t"})
        result["region"] = "EPO"
        result["catch_basis"] = "retained"
        result["source_note"] = "Reported zero means less than 0.5 t; historical combined gears remain as reported."
    elif source == "wcpfc":
        assign({"year": "YY", "flag_raw": "FLAG_CODE", "fleet_raw": "FLEET_CODE",
                "gear_code_raw": "GEAR_CODE", "species_code": "SP_CODE",
                "species_name": "SP_NAME", "catch_tonnes": "SP_MT"})
        if region not in {"WCPO", "EPO", "WCPFC", "NorthPacific", "SouthPacific", "NorthWCPFC", "SouthWCPFC"}:
            raise ValueError(f"Unknown WCPFC geographic view: {region}")
        result["region"] = region
        # Do not assume that the edition's 'since 2017' note uniquely identifies
        # the discard content of each historical record or each component fleet.
        result["catch_basis"] = "wcpfc_yearbook_reported"
        result["source_note"] = "Yearbook includes discard-weight estimates since 2017; adjusted purse-seine species; charter attribution; some recent estimates provisional."
    elif source == "iotc":
        assign({"year": "YEAR", "flag_raw": "FLEET_CODE", "flag_name_raw": "FLEET",
                "fleet_raw": "FLEET_CODE", "gear_code_raw": "GEAR_CODE", "gear_label_raw": "GEAR",
                "species_code": "SPECIES_CODE", "species_name": "SPECIES", "catch_tonnes": "CATCH",
                "subregion_raw": "FISHING_GROUND_CODE", "catch_type_raw": "FATE_CODE"})
        _require(raw, ["FATE_TYPE_CODE"])
        if not raw.FATE_TYPE_CODE.eq("RETAINED").all():
            raise ValueError("IOTC retained-catch source now contains another fate; review adapter")
        if not raw.FISHING_GROUND_CODE.isin(["IRWESIO", "IREASIO"]).all():
            raise ValueError("Unexpected IOTC fishing ground; review regional scope")
        result["region"] = "Indian"
        result["catch_basis"] = "retained"
        result["source_note"] = "Retained catch, live-weight equivalent; fishery and fleet details retained in raw fields."
    elif source == "iccat":
        assign({"year": "YearC", "flag_raw": "FlagName", "flag_name_raw": "FlagName",
                "fleet_raw": "FleetCode", "gear_code_raw": "GearCode", "gear_mapping_code": "GearGrp",
                "species_code": "Species", "catch_tonnes": "Qty_t", "native_record_id": "RecID",
                "subregion_raw": "SampAreaCode", "stock_raw": "Stock", "catch_type_raw": "CatchTypeCode"})
        if result.native_record_id.isna().any() or result.native_record_id.eq("").any() or result.native_record_id.duplicated().any():
            raise ValueError("ICCAT RecID must be present and unique; no automatic deduplication")
        types = {"L": "landings", "DD": "dead_discards", "DM": "post_release_mortality",
                 "DL": "live_discards", "C": "iccat_reported_total", "FA": "iccat_FA", "LF": "iccat_LF"}
        unknown = set(raw.CatchTypeCode) - set(types)
        if unknown:
            raise ValueError(f"New ICCAT catch types need review: {sorted(unknown)}")
        result["region"] = "Atlantic"
        result["catch_basis"] = raw.CatchTypeCode.map(types)
        result["source_note"] = "Task 1 nominal catches; catch types separate; Qty_t already tonnes, CnvFactor not reapplied."
    elif source == "ccsbt":
        assign({"year": "Calendar Year", "gear_code_raw": "gear", "catch_tonnes": "tonnes"})
        result["source_row"] = raw["_worksheet_row"]
        result["source_column"] = raw["gear"]
        result["species_code"] = "SBF"
        result["region"] = "Global_SBF"
        result["flag_resolution"] = "all_flags"
        result["catch_basis"] = "ccsbt_reported_catch"
        result["source_note"] = "Gear-only marginal table, all flags/oceans; no joint flag or ocean breakdown; blanks remain missing."
    else:
        raise ValueError(source)

    years = measurement(result.year)
    if years.isna().any() or (years % 1 != 0).any():
        raise ValueError("Missing or non-integral year")
    result["year"] = years.astype("int64")
    result["catch_tonnes"] = measurement(result.catch_tonnes)
    result["species_name"] = result.species_code.map(SPECIES).fillna(result.species_name)
    result["gear_mapping_code"] = result.gear_mapping_code.fillna(result.gear_code_raw)
    mapping = GEARS[source].copy()
    mapping.update((overrides or {}).get(source, {}))
    result["gear_group"] = result.gear_mapping_code.map(mapping)
    result["gear_mapping_status"] = result.gear_group.notna().map({True: "mapped", False: "unmapped"})
    result["gear_group"] = result.gear_group.fillna("unmapped:" + source + ":" + result.gear_mapping_code.fillna("MISSING"))
    result["flag_key"] = (source + "::" + result.flag_raw).where(result.flag_raw.notna())
    result["value_status"] = "reported"
    result.loc[result.catch_tonnes.isna(), "value_status"] = "missing"
    if source == "iattc":
        result.loc[result.catch_tonnes.eq(0).fillna(False), "value_status"] = "reported_zero_below_0.5_t"
    result["observation_id"] = (DATASETS[source] + "|" + sha256 + "|" + member + "|" + sheet + "|"
                                + result.source_row.astype(str) + "|" + result.source_column)
    if result.observation_id.duplicated().any():
        raise ValueError("Duplicate observation identity")
    # Keep strings as strings in R/Parquet, including NA as a native country code.
    for col in result.columns:
        if col not in {"year", "source_row", "catch_tonnes"}:
            result[col] = result[col].astype("string")
    return result


def read_source(library, source, version=None, overrides=None):
    """Read only the canonical annual tables, not their Excel/CSV duplicates."""
    dataset = DATASETS[source]
    record = library.record(dataset, version)
    sha = record["sha256"]  # pin before listing/reading, including concurrent updates
    members = library.members(dataset, version=sha)
    if source == "wcpfc":
        candidates = [m for m in members if Path(m).name.startswith("XLS_") and m.lower().endswith(".csv")]
    elif source in {"iccat", "ccsbt"}:
        candidates = [m for m in members if m.lower().endswith(".xlsx")]
    else:
        candidates = [m for m in members if m.lower().endswith(".csv")]
    if len(candidates) != (7 if source == "wcpfc" else 1):
        raise ValueError(f"Annual source layout changed for {dataset}: {candidates}")
    if source == "wcpfc" and {Path(m).name.split("_")[1] for m in candidates} != {
        "WCPO", "EPO", "WCPFC", "NorthPacific", "SouthPacific", "NorthWCPFC", "SouthWCPFC"
    }:
        raise ValueError("WCPFC needs exactly one CSV per geographic view")
    frames = []
    for member in sorted(candidates):
        sheet = {"iccat": "Data", "ccsbt": "Catch by Gear"}.get(source, "")
        kwargs = dict(dtype=str, keep_default_na=False)
        if source == "ccsbt":
            kwargs["header"] = 7
        raw = library.read_table(dataset, member=member, version=sha, sheet_name=sheet or 0, **kwargs)
        region = Path(member).name.split("_")[1] if source == "wcpfc" else None
        # A standalone workbook has no archive member; use its provider filename
        # so a readable on-disk rename cannot change observation identities.
        provenance_member = record["filename"] if source == "ccsbt" else member
        frames.append(standardize(raw, source, member=provenance_member, sha256=sha, region=region, sheet=sheet, overrides=overrides))
    return pd.concat(frames, ignore_index=True), record
