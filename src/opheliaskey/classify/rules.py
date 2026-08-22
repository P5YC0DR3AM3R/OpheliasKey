"""Deterministic first-pass classifiers.

Two independent questions, answered separately:

  1. **Relevance** — is this line part of the project at all? The account is
     mixed, so this gate decides whether a dollar counts.
  2. **System** — which boat system does it belong to?

Rules run before the LLM because they are free, instant and auditable. They
answer only what they can answer with high precision; everything else is left
NULL for the LLM pass, and anything the LLM is unsure of goes to human review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..db.database import Database, utcnow

# --- system keywords --------------------------------------------------------

RULES: dict[str, list[str]] = {
    "solar_generation": [
        "solar panel", "flexible solar", "monocrystalline", "photovoltaic", "pv panel",
        "mc4", "solar connector", "panel mount", "solar cable", "combiner box",
        "tilt mount", "bimini solar",
    ],
    "energy_storage": [
        "lifepo4", "lithium iron phosphate", "battery bank", "deep cycle", "agm battery",
        "bms", "battery management", "battery cable", "battery lug", "busbar", "bus bar",
        "battery box", "battery terminal", "battery monitor", "shunt", "victron bmv",
        "smartshunt", "dumfume", "battery switch", "battery isolator",
    ],
    "power_conversion": [
        "inverter", "pure sine", "hybrid inverter", "mppt", "charge controller",
        "solar controller", "transfer switch", "shore power", "converter charger",
        "smartsolar", "lonsge", "battery charger", "dc dc charger", "orion",
    ],
    "generator": [
        "generator", "genset", "inverter generator", "genkins", "honda eu", "predator",
        "generator cover", "generator wheel", "extension cord 30a",
    ],
    "ac_distribution": [
        "ac panel", "breaker panel", "gfci", "outlet", "receptacle", "romex",
        "shore power inlet", "shore power cord", "30 amp", "50 amp", "power inlet",
        "load center", "ac breaker", "surge protector",
    ],
    "dc_distribution": [
        "fuse block", "blade fuse", "anl fuse", "mrbf", "12v socket", "cigarette lighter",
        "led navigation light", "nav light", "anchor light", "courtesy light",
        "cabin light", "dc panel", "rocker switch", "toggle switch", "12v led",
        "marine wire", "tinned copper", "ancor", "heat shrink", "ring terminal",
        "wire loom", "12 volt",
    ],
    "propulsion": [
        "impeller", "raw water pump", "heat exchanger", "exhaust manifold",
        "exhaust elbow", "exhaust hose", "muffler", "water lift", "outboard",
        "lower unit", "gear lube", "spark plug", "fuel filter", "racor", "fuel pump",
        "fuel line", "fuel tank", "propeller", "prop shaft", "cutless bearing",
        "cutlass bearing", "stuffing box", "dripless", "engine mount", "thermostat",
        "sterndrive", "outdrive", "bellows", "trim pump",
    ],
    "steering": [
        "steering cable", "helm pump", "hydraulic steering", "steering wheel",
        "rudder", "tiller", "trim tab", "steering hose",
    ],
    "electronics_nav": [
        "chartplotter", "multifunction display", "radar", "transducer", "depth finder",
        "fishfinder", "sonar", "autopilot", "nmea", "nmea 2000", "n2k", "backbone cable",
        "drop cable", "t-connector", "simrad", "lowrance", "garmin", "raymarine",
        "furuno", "b&g", "gps antenna", "heading sensor", "ais", "orca core",
        "navigation display", "go9", "instrument",
    ],
    "connectivity": [
        "starlink", "vhf", "vhf radio", "antenna", "cellular", "router", "modem",
        "poe switch", "network switch", "ethernet", "cat6", "cat5e", "wifi",
        "access point", "sim card", "signal booster", "mikrotik", "ubiquiti",
        "tp-link", "peplink", "handheld radio",
    ],
    "av_security": [
        "amplifier", "subwoofer", "speaker", "marine speaker", "head unit", "stereo",
        "soundbar", "tweeter", "rockville", "wet sounds", "jl audio", "kicker",
        "security camera", "ip camera", "4k camera", "nvr", "dvr", "poe camera",
        "surveillance", "monitor", "display screen", "tv mount",
    ],
    "climate": [
        "air conditioner", "air conditioning", "marine ac", "btu", "mini split",
        "heater", "diesel heater", "webasto", "espar", "dehumidifier", "fan",
        "ventilation", "vent", "blower", "refrigerator", "fridge", "freezer",
        "icemaker", "insulation", "reflectix", "thermostat control",
    ],
    "plumbing": [
        "bilge pump", "float switch", "seacock", "thru-hull", "through hull",
        "ball valve", "holding tank", "macerator", "black water", "sanitation hose",
        "odorsafe", "waste hose", "water tank", "fresh water pump", "accumulator",
        "water heater", "shower sump", "faucet", "sink", "hose clamp", "y-valve",
        "pump out", "toilet", "marine head", "raritan", "jabsco", "shurflo", "whale",
    ],
    "interior": [
        "captain chair", "helm seat", "boat seat", "seat pedestal", "pedestal",
        "upholstery", "cushion", "vinyl fabric", "marine vinyl", "foam",
        "berth", "mattress", "bedding", "cabinet", "drawer slide", "table pedestal",
        "cooler", "yeti", "galley", "cabin sole", "flooring", "seadek", "eva foam",
    ],
    "hull_structure": [
        "fiberglass", "gelcoat", "gel coat", "chopped strand", "biaxial", "roving",
        "epoxy resin", "polyester resin", "west system", "coosa", "transom",
        "stringer", "bulkhead", "fairing", "bondo", "structural", "keel",
    ],
    "paint_coatings": [
        "bottom paint", "antifoul", "ablative", "barrier coat", "interlux", "petit",
        "awlgrip", "topside paint", "primer", "gelcoat repair", "buffing compound",
        "wax", "polish", "ceramic coating",
    ],
    "deck_hardware": [
        "cleat", "rail", "stanchion", "hatch", "portlight", "hardtop", "t-top",
        "rod holder", "boarding ladder", "swim platform", "grab handle", "hinge",
        "latch", "deck fill", "bimini", "canvas", "snap", "fender", "dock line",
        "boat cover", "windshield",
    ],
    "ground_tackle": [
        "anchor", "anchor chain", "chain", "anchor rode", "windlass", "snubber",
        "chain hook", "swivel shackle", "bow roller", "danforth", "rocna", "mantus",
        "anchor line",
    ],
    "dive": [
        "dive flag", "diver down", "scuba", "dive tank", "regulator", "bcd",
        "dive ladder", "spear", "snorkel", "wetsuit", "dive compressor", "dive light",
    ],
    "safety": [
        "life jacket", "pfd", "life vest", "fire extinguisher", "fire suppression",
        "flare", "epirb", "plb", "first aid", "throw ring", "horseshoe buoy",
        "carbon monoxide", "co detector", "smoke detector", "bilge alarm",
        "emergency", "signal mirror", "air horn", "whistle",
    ],
    "tender": ["dinghy", "inflatable boat", "tender", "davit", "oarlock", "kayak", "paddle board"],
    "trailer": [
        "trailer", "trailer bunk", "trailer winch", "bearing buddy", "trailer tire",
        "trailer light", "coupler", "hitch", "trailer jack", "wheel bearing",
    ],
    "tools": [
        "multimeter", "crimper", "crimping tool", "angle grinder", "orbital sander",
        "heat gun", "drill", "impact driver", "torque wrench", "socket set",
        "oscillating tool", "hole saw", "rivet gun", "dremel", "clamp set",
        "wire stripper", "hydraulic crimper",
    ],
    "consumables": [
        "sandpaper", "abrasive", "sanding disc", "masking tape", "painters tape",
        "acetone", "denatured alcohol", "mineral spirits", "respirator", "tyvek",
        "nitrile glove", "shop rag", "mixing cup", "chip brush", "roller cover",
        "5200", "4200", "sikaflex", "butyl tape", "silicone sealant", "loctite",
        "screw", "bolt", "washer", "cotter pin", "zip tie", "cable tie",
        "sealant", "grease", "penetrating oil", "wd-40", "anti-seize", "starboard",
        "adhesive", "caulk", "self amalgamating",
    ],
    "professional_install": [
        "installation", "install labor", "labor", "service call", "commissioning",
        "repair order", "shop rate", "technician", "rigging labor", "mobile service",
        "diagnostic", "sea trial", "workmanship",
    ],
    "yard_services": [
        "haul out", "haul-out", "boatyard", "boat yard", "blocking", "travel lift",
        "travelift", "launch", "pressure wash", "shipwright", "yard bill",
        "bottom wash", "transport", "hauling", "delivery of boat", "shipping listing",
        "dry storage", "storage fee", "yard storage",
    ],
    "moorage": [
        "slip", "slip fee", "slip rent", "moorage", "marina", "dockage", "dock fee",
        "pump out", "pump-out", "live aboard fee", "liveaboard fee", "dock electric",
        "shore power fee", "wet slip", "monthly statement",
    ],
    "vessel_acquisition": [
        "purchase price", "vessel purchase", "boat purchase", "hull purchase",
        "acquisition", "purchase of vessel",
    ],
    "fees_admin": [
        "registration", "decal", "documentation", "title", "retitle", "survey",
        "insurance", "premium", "permit", "boat decal", "registration number",
        "notary", "bill of sale", "temp tag", "tag fee",
    ],
}


def _compile(keyword: str) -> re.Pattern[str]:
    """Plural-tolerant, whitespace-flexible keyword pattern.

    Product titles pluralize freely ('Hose Clamps', 'Anchors', 'Winches'); a
    plain \\b anchor silently fails on every one of them.
    """
    body = re.escape(keyword).replace(r"\ ", r"\s+")
    return re.compile(r"\b" + body + r"(?:e?s)?\b", re.I)


_COMPILED: dict[str, list[tuple[re.Pattern[str], int]]] = {
    system: [(_compile(kw), 2 if " " in kw else 1) for kw in keywords]
    for system, keywords in RULES.items()
}

MIN_SCORE = 1
CONFIDENT_MARGIN = 1


# --- relevance keywords -----------------------------------------------------
#
# High precision only. These decide whether a dollar counts at all, so a false
# positive here is worse than a miss; anything not clearly one or the other is
# left for the LLM, which gets the vessel spec as context.

PERSONAL_PATTERNS: list[str] = [
    r"doordash", r"uber\s*eats", r"grubhub", r"instacart", r"starbucks",
    r"netflix", r"spotify", r"hulu", r"disney\s*\+", r"audible", r"kindle unlimited",
    r"google play", r"app store", r"playstation", r"xbox", r"nintendo",
    r"grocer", r"pharmacy", r"prescription", r"vitamin", r"supplement",
    r"\bsandal", r"\bsneaker", r"\bshoe", r"olukai", r"t-?shirt", r"\bjeans\b",
    r"\bhoodie", r"\bsocks\b", r"underwear", r"perfume", r"cologne", r"makeup",
    r"firestone", r"jiffy lube", r"oil change", r"brake pad", r"windshield wiper",
    r"\btire rotation", r"car wash", r"auto care", r"dog food", r"cat food",
    r"litter box", r"\btoy\b", r"greeting card", r"gift card",
]

BOAT_PATTERNS: list[str] = [
    r"\bmarine\b", r"\bboat\b", r"\bnautical\b", r"\bbilge\b", r"\bhull\b",
    r"\btransom\b", r"thru-?hull", r"through hull", r"\bseacock", r"\bgunwale",
    r"\bstarboard\b", r"\bportlight", r"\bcleat\b", r"\bhelm\b", r"\bberth\b",
    r"\bgalley\b", r"\bnmea\b", r"\bvhf\b", r"chartplotter", r"transducer",
    r"trolling", r"outboard", r"sterndrive", r"outdrive", r"anchor chain",
    r"diver down", r"dive flag", r"\bdinghy\b", r"livewell", r"\bcuddy\b",
    r"bimini", r"\bstarlink\b", r"lifepo4", r"\bmppt\b", r"shore power",
    r"bottom paint", r"antifoul", r"holding tank", r"black water", r"windlass",
]

_PERSONAL = [re.compile(p, re.I) for p in PERSONAL_PATTERNS]
_BOAT = [re.compile(p, re.I) for p in BOAT_PATTERNS]


@dataclass(frozen=True)
class Classification:
    system_key: str | None
    confidence: float
    matched: tuple[str, ...]


def classify_description(text: str) -> Classification:
    """Score a product description against every system's keyword set."""
    if not text or not text.strip():
        return Classification(None, 0.0, ())

    scores: dict[str, int] = {}
    hits: dict[str, list[str]] = {}
    for system, patterns in _COMPILED.items():
        total = 0
        matched: list[str] = []
        for pattern, weight in patterns:
            found = pattern.search(text)
            if found:
                total += weight
                matched.append(found.group(0).lower())
        if total:
            scores[system] = total
            hits[system] = matched

    if not scores:
        return Classification(None, 0.0, ())

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_system, top_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if top_score < MIN_SCORE:
        return Classification(None, 0.0, ())

    margin = top_score - runner_up
    confidence = 0.45 if margin < CONFIDENT_MARGIN else min(0.95, 0.6 + 0.1 * margin)
    return Classification(top_system, round(confidence, 2), tuple(hits[top_system]))


def classify_relevance(text: str) -> tuple[str | None, float]:
    """Decide boat vs personal. Returns (relevance, confidence).

    Returns (None, 0.0) when neither side is clearly indicated, deferring to
    the LLM rather than defaulting the item into the project.
    """
    if not text or not text.strip():
        return None, 0.0
    boat_hits = sum(1 for p in _BOAT if p.search(text))
    personal_hits = sum(1 for p in _PERSONAL if p.search(text))

    if boat_hits and not personal_hits:
        return "boat", min(0.95, 0.75 + 0.05 * boat_hits)
    if personal_hits and not boat_hits:
        return "personal", min(0.95, 0.75 + 0.05 * personal_hits)
    # Both or neither: genuinely unclear from keywords alone.
    return None, 0.0


def apply_rules(db: Database, *, min_confidence: float = 0.6, reclassify: bool = False) -> dict:
    """Run both rule passes over line items. Returns a summary dict."""
    # A human verdict is final. --reclassify re-runs the rules over everything
    # else, but must never silently overwrite a decision someone made by hand.
    manual_guard = (
        "COALESCE(li.relevance_by,'') != 'manual' "
        "OR COALESCE(li.classified_by,'') != 'manual'"
    )
    where = (
        f"WHERE ({manual_guard})"
        if reclassify
        else f"WHERE (li.system_id IS NULL OR li.relevance IS NULL) AND ({manual_guard})"
    )
    rows = db.query(
        f"""SELECT li.id, li.description, li.system_id, li.relevance,
                   li.relevance_by, li.classified_by, v.canonical_name AS vendor
            FROM line_items li
            JOIN orders o ON o.id = li.order_id
            LEFT JOIN vendors v ON v.id = o.vendor_id
            {where}"""
    )

    stats = {
        "examined": len(rows), "system_set": 0, "system_ambiguous": 0,
        "system_unmatched": 0, "relevance_boat": 0, "relevance_personal": 0,
        "relevance_deferred": 0,
    }
    with db.tx():
        for row in rows:
            # Vendor name is weak but real evidence.
            haystack = f"{row['description']} {row['vendor'] or ''}"

            if row["relevance_by"] == "manual":
                pass  # human decision stands
            elif reclassify or row["relevance"] is None:
                relevance, rconf = classify_relevance(haystack)
                if relevance is None:
                    stats["relevance_deferred"] += 1
                else:
                    db.execute(
                        """UPDATE line_items SET relevance=?, relevance_by='rule',
                             relevance_conf=? WHERE id=?""",
                        (relevance, rconf, row["id"]),
                    )
                    stats[f"relevance_{relevance}"] += 1

            if row["classified_by"] == "manual":
                continue  # human decision stands
            if reclassify or row["system_id"] is None:
                result = classify_description(haystack)
                if result.system_key is None:
                    stats["system_unmatched"] += 1
                elif result.confidence < min_confidence:
                    stats["system_ambiguous"] += 1
                else:
                    sys_row = db.one(
                        "SELECT id FROM boat_systems WHERE key=?", (result.system_key,)
                    )
                    if sys_row:
                        db.execute(
                            """UPDATE line_items SET system_id=?, classified_by='rule',
                                 classify_conf=?, classified_at=? WHERE id=?""",
                            (sys_row["id"], result.confidence, utcnow(), row["id"]),
                        )
                        stats["system_set"] += 1
    return stats
