"""Species, stock and gear rules applied before CPUE standardization or SDMs.

Stock rules are cell-based: a stratum belongs to a stock only when its whole
rectangle lies inside the stock area and its source is authoritative there.
Straddling cells are flagged, never split. Boundaries follow RFMO convention
areas and assessment practice; approximations are labelled as such (for example
the cell-based Mediterranean rule, which is not ICCAT's polygon).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

SPECIES_NAMES = {
    "YFT": "Yellowfin tuna", "BET": "Bigeye tuna", "SKJ": "Skipjack tuna", "ALB": "Albacore",
    "PBF": "Pacific bluefin tuna", "BFT": "Atlantic bluefin tuna", "SBF": "Southern bluefin tuna",
}


@dataclass(frozen=True)
class Stock:
    stock_id: str
    species: str
    rfmo: str
    sources: tuple
    rule: str
    citation: str


@dataclass(frozen=True)
class GearRule:
    """Default unit choices for one species x gear analysis."""
    gear_group: str
    catch_unit: str
    effort_unit: str
    cpue_scale: float           # CPUE = catch / effort * cpue_scale
    products: tuple             # preferred product per source (one view per source)
    by_school: bool = False     # purse seine: CPUE within one set type
    recommended: bool = True
    note: str = ""
    fleets: tuple = ()          # restrict to these fleets by default (e.g. BFT JPN longline)
    product_units: tuple = ()   # ((product, effort_unit), ...) where a product uses another unit
    product_catch_units: tuple = ()   # ((product, catch_unit), ...); units are never converted
    absences: bool = True       # False: zeros of this gear are not absences for the species (presence records only)

    def unit_for(self, product):
        return dict(self.product_units).get(product, self.effort_unit)

    def catch_unit_for(self, product):
        return dict(self.product_catch_units).get(product, self.catch_unit)


@dataclass(frozen=True)
class Exclusion:
    """A default filter: rows matching are dropped (or only flagged when drop is False)."""
    rule_id: str
    description: str
    species: tuple              # () = all species
    gears: tuple                # () = all gears
    product: str | tuple = ""   # one product or a tuple of products
    fleets: tuple = ()
    gear_raw: tuple = ()
    years: tuple = ()           # (first, last) inclusive; () = all years
    min_south: float | None = None   # matches cells with south >= min_south
    max_north: float | None = None   # matches cells with south + grid_height <= max_north
    max_east: float | None = None    # matches cells with west + grid_width <= max_east
    notes_contains: tuple = ()  # matches strata whose notes contain any of these tokens
    drop: bool = True
    dedupe: tuple = ()          # drop all but one matched stratum per key (ICCAT duplicate data sets)


LL_PRODUCTS = ("iattc_ll", "wcpfc_ll", "iotc_ce", "iccat_t2ce", "ccsbt_ll")
PS_PRODUCTS = ("iattc_ps_settype", "wcpfc_ps", "iotc_ce_ps_school", "iccat_t2ce")
LP_PRODUCTS = ("iattc_lp", "wcpfc_lp", "iotc_ce", "iccat_t2ce")

_LL = GearRule("longline", "fish", "hooks", 1000.0, LL_PRODUCTS,
               note="Numbers per 1000 hooks; never pool kg/tonnes with numbers.")
_KG = (("iccat_t2ce", "kg"),)   # ICCAT T2CE weights are kg and are not converted to tonnes
_PS = GearRule("purse_seine", "tonnes", "sets", 1.0, PS_PRODUCTS, by_school=True, product_catch_units=_KG,
               note="Catch per set within one set type (tonnes; ICCAT kg); search time cannot be split by set type.")
_LP = GearRule("pole_and_line", "tonnes", "days_fishing_searching", 1.0, LP_PRODUCTS, recommended=False,
               note="Exploratory: public pole-and-line data are sparse (WCPFC 830 is 98% masked; IATTC LP ends 2015).",
               product_units=(("iattc_lp", "sets"), ("iotc_ce", "fishing_days"), ("iccat_t2ce", "fishing_days")),
               product_catch_units=_KG)

GEAR_RULES = {
    "YFT": {"longline": _LL, "purse_seine": _PS, "pole_and_line": _LP},
    "BET": {"longline": _LL,
            "purse_seine": GearRule("purse_seine", "tonnes", "sets", 1.0, PS_PRODUCTS, by_school=True, recommended=False,
                                    product_catch_units=_KG,
                                    note="Assessments do not use PS BET indices: species composition of public PS data is uncertain.")},
    "SKJ": {"purse_seine": _PS, "pole_and_line": _LP,
            "longline": GearRule("longline", "fish", "hooks", 1000.0, LL_PRODUCTS, recommended=False, absences=False,
                                 note="Not an SKJ index: longline SKJ catch is incidental and inconsistently reported; longline zeros are not SKJ absences.")},
    "ALB": {"longline": _LL},
    "PBF": {"longline": GearRule("longline", "fish", "hooks", 1000.0, ("iattc_ll",), recommended=False, absences=False,
                                 note="No public PBF index: WCPFC has no PBF field; IATTC LL PBF is rare bycatch; presence records only."),
            "purse_seine": GearRule("purse_seine", "tonnes", "sets", 1.0, ("iattc_ps_settype",), by_school=True,
                                    recommended=False, absences=False, note="Quota-driven farming catch; presence records only.")},
    "BFT": {"longline": GearRule("longline", "fish", "hooks", 1000.0, ("iccat_t2ce",), fleets=("JPN",),
                                 note="Only Japanese longline approximates an assessment index (ICCAT T2CE, numbers).")},
    "SBF": {"longline": GearRule("longline", "fish", "hooks", 1000.0, ("ccsbt_ll",),
                                 note="CCSBT is the sole SBF authority; JP areas 4-9, Apr-Sep approximates the MP CPUE domain."),
            "purse_seine": GearRule("purse_seine", "kg", "hours_searched", 1.0, ("ccsbt_surface",), recommended=False,
                                    note="Surface catch is visual weight estimates; not an index.")},
}

_C_IATTC = "IATTC Antigua Convention Art. III; IATTC SAC-15/16 assessments"
_C_WCPFC = "WCPFC Convention Area (16 USC 6901); SPC assessments use the WCPO west of 150W"
_C_IOTC = "IOTC area of competence (FAO 51 and 57; western limit 20E since 1999)"
_C_ICCAT = "ICCAT Convention Area; CATDIS Stock labels (local check)"

STOCKS = {
    "YFT": [Stock("YFT-EPO", "YFT", "IATTC", ("iattc",), "EPO cells east of 150W", _C_IATTC),
            Stock("YFT-WCPO", "YFT", "WCPFC", ("wcpfc",), "WCPFC cells wholly west of 150W", _C_WCPFC),
            Stock("YFT-IO", "YFT", "IOTC", ("iotc",), "IOTC cells east of 20E", _C_IOTC),
            Stock("YFT-AT", "YFT", "ICCAT", ("iccat",), "Atlantic west of 20E, Mediterranean excluded", _C_ICCAT)],
    "BET": [Stock("BET-EPO", "BET", "IATTC", ("iattc",), "EPO cells east of 150W; index domain excludes the Hawaii corner (south>=10N, east edge<=105W) and cells south of 25S", "IATTC SAC-15-02 (2024 BET benchmark)"),
            Stock("BET-WCPO", "BET", "WCPFC", ("wcpfc",), "WCPFC cells wholly west of 150W", _C_WCPFC),
            Stock("BET-IO", "BET", "IOTC", ("iotc",), "IOTC cells east of 20E", _C_IOTC),
            Stock("BET-AT", "BET", "ICCAT", ("iccat",), "Atlantic west of 20E, Mediterranean excluded", _C_ICCAT)],
    "SKJ": [Stock("SKJ-EPO", "SKJ", "IATTC", ("iattc",), "EPO cells east of 150W", _C_IATTC),
            Stock("SKJ-WCPO", "SKJ", "WCPFC", ("wcpfc",), "WCPFC cells wholly west of 150W", _C_WCPFC),
            Stock("SKJ-IO", "SKJ", "IOTC", ("iotc",), "IOTC cells east of 20E", _C_IOTC),
            Stock("SKJ-ATE", "SKJ", "ICCAT", ("iccat",), "East Atlantic: north of 5S east of 35W; south of 5S east of 20W (CATDIS-derived staircase)", "ICCAT 2022 SKJ assessment; CATDIS SJ01-SJ06"),
            Stock("SKJ-ATW", "SKJ", "ICCAT", ("iccat",), "West Atlantic: complement of SKJ-ATE", "ICCAT 2022 SKJ assessment; CATDIS SJ07-SJ40")],
    "ALB": [Stock("ALB-NPO", "ALB", "ISC/WCPFC/IATTC", ("iattc", "wcpfc"), "Pacific north of the equator to 55N (one source per cell, split at 150W)", "ISC ALBWG 2026 benchmark"),
            Stock("ALB-SPO", "ALB", "WCPFC/IATTC", ("iattc", "wcpfc"), "Pacific south of the equator (one source per cell, split at 150W)", "SPC 2024 South Pacific albacore assessment"),
            Stock("ALB-IO", "ALB", "IOTC", ("iotc",), "IOTC cells east of 20E", _C_IOTC),
            Stock("ALB-ATN", "ALB", "ICCAT", ("iccat",), "North Atlantic north of 5N, Mediterranean excluded", "ICCAT ALB stock boundaries (5N)"),
            Stock("ALB-ATS", "ALB", "ICCAT", ("iccat",), "South Atlantic south of 5N, west of 20E", "ICCAT ALB stock boundaries (5N)"),
            Stock("ALB-MED", "ALB", "ICCAT", ("iccat",), "Mediterranean (approximate cell rule)", "ICCAT Mediterranean albacore")],
    "PBF": [Stock("PBF-PO", "PBF", "ISC/WCPFC/IATTC", ("iattc", "wcpfc"), "Pacific-wide single stock (one source per cell, split at 150W)", "ISC PBFWG 2024 benchmark")],
    "BFT": [Stock("BFT-W", "BFT", "ICCAT", ("iccat",), "West management area: staircase 45W (>=10N), 35W (5-10N), 30W (0-5N), 25W (<0)", "ICCAT ABFT MSE trial specifications; Rec. 22-09"),
            Stock("BFT-E", "BFT", "ICCAT", ("iccat",), "East management area: Atlantic complement of BFT-W plus the Mediterranean", "ICCAT ABFT MSE trial specifications; Rec. 22-09")],
    "SBF": [Stock("SBF", "SBF", "CCSBT", ("ccsbt",), "Single global stock; CCSBT data only", "CCSBT")],
}

EXCLUSIONS = [
    Exclusion("X01", "IATTC longline ESP reports only swordfish: its tuna zeros are unreported", (), ("longline",), product="iattc_ll", fleets=("ESP",)),
    Exclusion("X02", "IATTC longline KOR and CHN 2023: suspect effort", (), ("longline",), product="iattc_ll", fleets=("KOR", "CHN"), years=(2023, 2023)),
    Exclusion("X03", "IOTC swordfish-targeting (ELL) and coastal (LLCO) longline are other fisheries", ("YFT", "BET", "ALB", "SKJ", "SBF"), (), product="iotc_ce", gear_raw=("ELL", "LLCO")),
    Exclusion("X03b", "IOTC small purse seine (PSS) is not the industrial purse-seine fishery", (), ("purse_seine",), product=("iotc_ce_ps_school", "iotc_ce_ps_pooled"), gear_raw=("PSS",)),
    Exclusion("X04", "ICCAT swordfish/shark longline gears (LLSWO, LL-Shrk, LLHB) target other species", ("YFT", "BET", "ALB", "BFT", "SBF"), ("longline",), product="iccat_t2ce", gear_raw=("LLSWO", "LL-Shrk", "LLHB")),
    Exclusion("X05", "WCPFC purse seine north of 25N is temperate Japanese purse seine", ("YFT", "BET", "SKJ"), ("purse_seine",), product="wcpfc_ps", min_south=25.0),
    Exclusion("X05b", "WCPFC purse seine wholly south of 25S is temperate (NZ/AU) purse seine", ("YFT", "BET", "SKJ"), ("purse_seine",), product="wcpfc_ps", max_north=-25.0),
    Exclusion("X10", "ICCAT working-group estimates (WG-EST) are not reported catch-effort", (), (), product="iccat_t2ce", notes_contains=("working_group_estimate",)),
    Exclusion("X11", "ICCAT strata whose kg and number records report different effort", (), (), product="iccat_t2ce", notes_contains=("effort_differs_between_units", "unit_records_differ")),
    Exclusion("X12", "ICCAT cells decoding outside the Atlantic/Mediterranean (suspect quadrant)", (), (), product="iccat_t2ce", notes_contains=("suspect_quadrant",)),
    Exclusion("X13", "ICCAT stratum also reported by another data set: keep one (official report first, then lowest DSetID)", (), (), product="iccat_t2ce", notes_contains=("stratum_in_other_dataset",),
              dedupe=("fleet", "gear_raw", "year", "month_start", "month_end", "west", "south", "grid_width", "grid_height", "school_type")),
    Exclusion("X14", "ICCAT data set is not an official report (port sampling, data recovery, other file types)", (), (), product="iccat_t2ce", notes_contains=("not_official_report",), drop=False),
    Exclusion("X15a", "BET-EPO longline index domain excludes the Hawaii corner (north of 10N, west of 105W; IATTC SAC-15-02)", ("BET",), ("longline",), product="iattc_ll", min_south=10.0, max_east=-105.0),
    Exclusion("X15b", "BET-EPO longline index domain excludes cells south of 25S (IATTC SAC-15-02)", ("BET",), ("longline",), product="iattc_ll", max_north=-25.0),
    Exclusion("X16", "IOTC catches estimated by the Secretariat (NEICE/NEIFR) are not reported catch-effort", (), (), product=("iotc_ce", "iotc_ce_ps_school", "iotc_ce_ps_pooled"), notes_contains=("secretariat_estimate",)),
    Exclusion("X06", "CCSBT Taiwan SBF before 1996 was estimated from mixed bluefin data", ("SBF",), ("longline",), product="ccsbt_ll", fleets=("TW",), years=(0, 1995)),
    Exclusion("X07", "IOTC Taiwan longline before 2005 has known data-quality issues (joint CPUE practice)", ("YFT", "BET", "ALB"), ("longline",), product="iotc_ce", fleets=("TWN",), years=(0, 2004), drop=False),
    Exclusion("X08", "Japanese EPO longline hooks-between-floats shift 1993-1995 (catchability break)", ("BET", "YFT"), ("longline",), product="iattc_ll", fleets=("JPN",), years=(1993, 1995), drop=False),
    Exclusion("X09", "Japanese CCSBT data from 1995 come from the Real Time Monitoring Program (series break: model an era term)", ("SBF",), ("longline",), product="ccsbt_ll", fleets=("JP",), years=(1995, 9999), drop=False),
]

# CPUE values above these catch-per-effort-unit ceilings are flagged (not dropped).
CPUE_CEILINGS = {("BET", "longline"): 0.1, ("YFT", "longline"): 0.2, ("ALB", "longline"): 0.2}


def stocks(species):
    species = str(species).upper()
    if species not in STOCKS:
        raise ValueError(f"No stock rules for {species}; choose {', '.join(STOCKS)}")
    return STOCKS[species]


def gear_rule(species, gear):
    species = str(species).upper()
    rules = GEAR_RULES.get(species, {})
    if gear not in rules:
        raise ValueError(f"No {gear} rule for {species}; available: {', '.join(rules) or 'none'}")
    return rules[gear]


def rules_table(species=None):
    """One row per species x stock x gear: the rules a user should read first."""
    rows = []
    if species:
        stocks(species)
    for code in ([str(species).upper()] if species else list(STOCKS)):
        gears = GEAR_RULES.get(code, {})
        for stock in STOCKS[code]:
            for gear, rule in gears.items():
                rows.append(dict(species_code=code, species_name=SPECIES_NAMES[code], stock_id=stock.stock_id,
                                 rfmo=stock.rfmo, authoritative_sources=",".join(stock.sources), stock_rule=stock.rule,
                                 gear_group=gear, catch_unit=rule.catch_unit, effort_unit=rule.effort_unit,
                                 cpue_scale=rule.cpue_scale, by_school=rule.by_school, recommended=rule.recommended,
                                 absences=rule.absences, default_fleets=",".join(rule.fleets),
                                 products=",".join(p for p in rule.products if any(src in p for src in stock.sources)),
                                 product_units=";".join(f"{p}={rule.unit_for(p)}/{rule.catch_unit_for(p)}"
                                                        for p in rule.products if any(src in p for src in stock.sources)),
                                 note=rule.note, citation=stock.citation))
    return pd.DataFrame(rows)


def _cells(strata):
    west = strata["west"].astype("Float64")
    south = strata["south"].astype("Float64")
    east = west + strata["grid_width"].astype("Float64")
    north = south + strata["grid_height"].astype("Float64")
    lat_c = south + strata["grid_height"].astype("Float64") / 2
    lon_c = west + strata["grid_width"].astype("Float64") / 2
    located = strata["geometry_status"].eq("located")
    return west, south, east, north, lat_c, lon_c, located


def mediterranean(strata):
    """Approximate cell rule (not ICCAT's polygon): centre east of 5.6W, 30-47.5N,
    west of 42E, excluding the Bay of Biscay. Returns (inside, straddles_gibraltar)."""
    west, south, east, north, lat_c, lon_c, located = _cells(strata)
    inside = located & (lon_c > -5.6) & (lat_c > 30) & (lat_c < 47.5) & (lon_c < 42) & ~((lat_c > 43) & (lon_c < 0))
    straddle = located & (west < -5.6) & (east > -5.6) & (south < 42) & (north > 30)
    return inside.fillna(False), straddle.fillna(False)


def assign_stock(strata, species):
    """Return (stock_id, stock_flag) Series for each stratum and species.

    stock_id is '' when the stratum belongs to no stock of this species (other
    ocean, non-authoritative source, outside partition, straddling, unlocated
    split stock). stock_flag explains why, or records a sub-area (ATE/MED).
    """
    species = str(species).upper()
    stocks(species)
    west, south, east, north, lat_c, lon_c, located = _cells(strata)
    src = strata["source"].astype("string")
    idx = strata.index
    stock = pd.Series("", index=idx, dtype="string")
    flag = pd.Series("", index=idx, dtype="string")

    def set_(mask, value, why=""):
        mask = pd.Series(mask, index=idx).fillna(False).astype(bool)
        stock[mask] = value
        if why:
            flag[mask] = why

    def note(mask, why):
        mask = pd.Series(mask, index=idx).fillna(False).astype(bool) & flag.eq("")
        flag[mask] = why

    # IATTC Convention Area: east of 150W, 50S-50N (eastern limit is the coast).
    epo = src.eq("iattc") & located & (west >= -150) & (east <= 0) & (south >= -50) & (north <= 50)
    wcpo = src.eq("wcpfc") & located & (((west >= 0) & (east <= 180)) | ((west >= -180) & (east <= -150)))
    note(src.eq("iattc") & located & ~epo.fillna(False), "outside_EPO_partition")
    note(src.eq("wcpfc") & located & ~wcpo.fillna(False), "outside_WCPO_partition_IATTC_authoritative")
    io = src.eq("iotc") & ((~located) | (west >= 20))
    note(src.eq("iotc") & located & (west < 20), "outside_IOTC_area")
    # IOTC and WCPFC convention areas overlap south of Australia (WCPFC line at 141E).
    note(src.eq("iotc") & located & (east > 141) & (north <= -30), "IOTC_WCPFC_overlap_area")
    note(src.eq("wcpfc") & located & (west < 150) & (west >= 100) & (north <= -30), "IOTC_WCPFC_overlap_area")
    med, gib = mediterranean(strata)
    atl = src.eq("iccat") & located & (east <= 20) & ~med & ~gib
    note(src.eq("iccat") & located & (east > 20) & ~med, "outside_Atlantic_east_of_20E")
    note(src.eq("iccat") & gib, "straddles_Gibraltar")
    note(src.eq("iccat") & ~located, "unlocated")

    if species in ("YFT", "BET", "SKJ"):
        set_(epo, f"{species}-EPO")
        set_(wcpo, f"{species}-WCPO")
        set_(io, f"{species}-IO")
        if species != "SKJ":
            set_(atl, f"{species}-AT")
        else:
            north_of_5s = lat_c >= -5
            east_side = (north_of_5s & (west >= -35)) | (~north_of_5s & (west >= -20))
            west_side = (north_of_5s & (east <= -35)) | (~north_of_5s & (east <= -20))
            set_(atl & east_side, "SKJ-ATE")
            set_(atl & west_side, "SKJ-ATW")
            note(atl & ~east_side & ~west_side, "straddles_SKJ_ATE_ATW")
        note(src.eq("iccat") & med, "Mediterranean_not_a_stock_area")
        if species == "BET":
            hawaii = epo & (south >= 10) & (east <= -105)
            note(hawaii, "BET_EPO_Hawaii_corner_outside_index_domain")
            note(epo & (north <= -25), "BET_EPO_south_of_25S_outside_index_domain")
    elif species == "ALB":
        pacific = epo | wcpo
        set_(pacific & (south >= 0) & (north <= 55), "ALB-NPO")
        set_(pacific & (north <= 0), "ALB-SPO")
        note(pacific & (south < 0) & (north > 0), "straddles_equator")
        set_(io, "ALB-IO")
        set_(atl & (south >= 5), "ALB-ATN")
        set_(atl & (north <= 5), "ALB-ATS")
        note(atl & (south < 5) & (north > 5), "straddles_5N")
        set_(src.eq("iccat") & med, "ALB-MED", "approximate_Mediterranean_rule")
    elif species == "PBF":
        set_(epo | wcpo, "PBF-PO")
    elif species == "BFT":
        east_edge = east
        w = atl & (((lat_c >= 10) & (east_edge <= -45)) | ((lat_c >= 5) & (lat_c < 10) & (east_edge <= -35))
                   | ((lat_c >= 0) & (lat_c < 5) & (east_edge <= -30)) | ((lat_c < 0) & (east_edge <= -25)))
        bands = [(lat_c >= b).fillna(False).to_numpy(dtype=bool) for b in (10, 5, 0)]
        boundary = np.select(bands, [-45.0, -35.0, -30.0], -25.0)
        e = atl & (west >= pd.Series(boundary, index=idx))
        set_(w, "BFT-W", "management_area_not_stock_of_origin")
        set_(e, "BFT-E", "ATE")
        set_(src.eq("iccat") & med, "BFT-E", "MED;approximate_Mediterranean_rule")
        note(atl & ~w.fillna(False) & ~e.fillna(False), "straddles_BFT_management_boundary")
    elif species == "SBF":
        set_(src.eq("ccsbt"), "SBF")
        note(~src.eq("ccsbt"), "SBF_CCSBT_authority")
    # Unlocated strata of split stocks cannot be assigned by area.
    split = species in ("ALB", "BFT") or (species == "SKJ")
    if split:
        note(~located & src.isin(["iccat", "iattc", "wcpfc"]), "unlocated_split_stock")
    return stock, flag


def exclusions_for(species, gear):
    species = str(species).upper()
    return [x for x in EXCLUSIONS if (not x.species or species in x.species) and (not x.gears or gear in x.gears)]


def exclusion_mask(frame, rule):
    """Rows of a strata-like frame matched by an Exclusion."""
    mask = pd.Series(True, index=frame.index)
    if rule.product:
        products = (rule.product,) if isinstance(rule.product, str) else tuple(rule.product)
        mask &= frame["product"].isin(products)
    if rule.notes_contains:
        tokens = frame["notes"].astype("string").fillna("").str.split(";")
        wanted = set(rule.notes_contains)
        mask &= tokens.map(lambda t: bool(wanted.intersection(t)))
    if rule.max_north is not None:
        mask &= ((frame["south"] + frame["grid_height"]) <= rule.max_north).fillna(False)
    if rule.max_east is not None:
        mask &= ((frame["west"] + frame["grid_width"]) <= rule.max_east).fillna(False)
    if rule.fleets:
        mask &= frame["fleet"].str.strip().isin(rule.fleets)
    if rule.gear_raw:
        mask &= frame["gear_raw"].str.strip().isin(rule.gear_raw)
    if rule.years:
        mask &= frame["year"].between(rule.years[0], rule.years[1])
    if rule.min_south is not None:
        mask &= (frame["south"] >= rule.min_south).fillna(False)
    mask = mask.fillna(False).astype(bool)
    if rule.dedupe and mask.any():
        cand = frame.loc[mask, [*rule.dedupe, "stratum_id", "notes"]].copy()
        cand["_official"] = ~cand.notes.astype("string").str.contains("not_official_report", regex=False)
        cand["_dset"] = pd.to_numeric(cand.stratum_id.str.split("|").str[1], errors="coerce")
        cand = cand.sort_values(["_official", "_dset", "stratum_id"], ascending=[False, True, True])
        kept = set(cand.drop_duplicates(list(rule.dedupe)).stratum_id)
        mask &= ~frame["stratum_id"].isin(kept)
    return mask
