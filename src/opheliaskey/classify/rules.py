"""Deterministic first-pass classifier.

Rules run before any LLM pass because they are free, instant, reproducible and
auditable. Anything they cannot place with confidence is left NULL for the LLM
or manual review — guessing would poison the analysis silently.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..db.database import Database, utcnow

# Keyword patterns per system. Matched case-insensitively on word boundaries.
# Order does not matter; scoring decides. Multi-word phrases score higher.
RULES: dict[str, list[str]] = {
    "hull_structure": [
        "fiberglass", "gelcoat", "gel coat", "chopped strand", "biaxial", "roving",
        "core", "balsa", "coosa", "bulkhead", "stringer", "keel bolt", "blister",
        "fairing compound", "west system", "epoxy resin", "polyester resin",
    ],
    "paint_coatings": [
        "bottom paint", "antifoul", "ablative", "barrier coat", "interlux", "petit",
        "awlgrip", "topside paint", "varnish", "cetol", "primer", "brightwork",
        "tuff stuff", "vc17", "micron",
    ],
    "deck_hardware": [
        "winch", "self-tailing", "clutch", "cam cleat", "cleat", "genoa track",
        "traveler", "turning block", "padeye", "stanchion", "pulpit", "pushpit",
        "hatch", "portlight", "deck fill", "harken", "ronstan", "garhauer",
    ],
    "rigging": [
        "standing rigging", "running rigging", "shroud", "forestay", "backstay",
        "turnbuckle", "swage", "norseman", "sta-lok", "hayn", "toggle", "clevis",
        "mast", "boom", "spreader", "furler", "halyard", "sheet line", "dyneema",
        "wire rope", "rigging wire", "spinnaker pole", "vang", "profurl", "schaefer",
    ],
    "sails": [
        "mainsail", "genoa", "jib", "spinnaker", "storm sail", "trysail", "sail cover",
        "lazy jack", "stack pack", "dodger", "bimini", "sunbrella", "sail repair",
        "batten", "sail slide", "boltrope", "north sails", "quantum sails",
    ],
    "propulsion": [
        "engine", "diesel", "yanmar", "volvo penta", "beta marine", "westerbeke",
        "impeller", "raw water pump", "heat exchanger", "injector", "glow plug",
        "transmission", "gearbox", "coupling", "cutless bearing", "cutlass bearing",
        "stuffing box", "dripless", "propeller", "prop shaft", "flexible coupling",
        "fuel filter", "racor", "fuel tank", "exhaust elbow", "water lift", "muffler",
        "motor mount", "outboard",
    ],
    "steering": [
        "steering cable", "quadrant", "rudder", "tiller", "wheel steering", "edson",
        "whitlock", "steering pedestal", "rudder bearing", "autopilot ram",
    ],
    "electrical": [
        "battery", "agm", "lifepo4", "lithium iron", "battery monitor", "victron",
        "balmar", "alternator", "shore power", "inverter", "charger", "solar panel",
        "mppt", "charge controller", "busbar", "bus bar", "ancor", "marine wire",
        "tinned copper", "breaker", "fuse block", "blue sea", "shunt", "windlass breaker",
        "battery switch", "cable lug", "heat shrink",
    ],
    "electronics": [
        "chartplotter", "multifunction display", "radar", "ais", "vhf", "ssb",
        "autopilot", "raymarine", "garmin marine", "b&g", "simrad", "furuno",
        "transducer", "depth sounder", "wind instrument", "nmea", "seatalk",
        "masthead antenna", "gps antenna", "epirb"  ,
    ],
    "plumbing": [
        "seacock", "thru-hull", "through hull", "ball valve", "bilge pump", "whale",
        "jabsco", "shurflo", "holding tank", "macerator", "head pump", "raritan",
        "sanitation hose", "water tank", "accumulator", "galley faucet", "sink drain",
        "hose clamp", "y-valve",
    ],
    "ground_tackle": [
        "anchor", "rocna", "mantus", "delta anchor", "cqr", "danforth", "anchor chain",
        "g4 chain", "high test chain", "chain", "windlass", "anchor rode", "snubber",
        "chain hook", "swivel shackle", "bow roller", "anchor swivel",
        "bow roller",
    ],
    "safety": [
        "life raft", "liferaft", "pfd", "life jacket", "epirb", "plb", "flare",
        "fire extinguisher", "fire suppression", "jackline", "tether", "harness",
        "man overboard", "mob", "horseshoe buoy", "bilge alarm", "co detector",
        "first aid", "emergency tiller",
    ],
    "interior": [
        "upholstery", "cushion", "foam", "headliner", "cabin sole", "teak and holly",
        "joinery", "drawer slide", "cabinet latch", "berth", "lee cloth",
        "insulation", "settee",
    ],
    "comfort": [
        "refrigeration", "isotherm", "engel", "dometic", "diesel heater", "webasto",
        "espar", "dickinson", "watermaker", "reverse osmosis", "fan", "dorade",
        "ventilation", "solar vent", "air conditioner",
    ],
    "tender": [
        "dinghy", "inflatable boat", "tender", "davit", "oarlock", "dinghy chaps",
    ],
    "tools": [
        "multimeter", "crimper", "crimping tool", "angle grinder", "orbital sander",
        "heat gun", "drill", "impact driver", "torque wrench", "socket set",
        "oscillating tool", "clamp set", "hole saw", "rivet gun", "dremel",
    ],
    "consumables": [
        "sandpaper", "abrasive", "sanding disc", "masking tape", "painters tape",
        "acetone", "denatured alcohol", "mineral spirits", "respirator", "tyvek",
        "nitrile glove", "shop rag", "mixing cup", "chip brush", "roller cover",
        "5200", "4200", "sikaflex", "butyl tape", "silicone sealant", "loctite",
        "screw", "bolt", "washer", "nut", "cotter pin", "zip tie", "cable tie",
        "sealant", "grease", "penetrating oil", "wd-40", "anti-seize",
    ],
    "yard_services": [
        "haul out", "haul-out", "boatyard", "boat yard", "storage fee", "blocking",
        "travel lift", "travelift", "launch fee", "pressure wash", "labor",
        "shipwright", "yard bill", "yard storage", "storage", "bottom wash", "launch",
    ],
    "fees_admin": [
        "survey", "marine survey", "documentation", "uscg", "registration",
        "insurance", "moorage", "slip fee", "marina", "mooring ball", "permit",
    ],
}

# Compiled once at import.
def _compile(keyword: str) -> re.Pattern[str]:
    """Compile a keyword to a plural-tolerant, whitespace-flexible pattern.

    Product titles pluralize freely ('Hose Clamps', 'Anchors', 'Winches'), and a
    plain \\b anchor silently fails on every one of them.
    """
    body = re.escape(keyword).replace(r"\ ", r"\s+")
    return re.compile(r"\b" + body + r"(?:e?s)?\b", re.I)


_COMPILED: dict[str, list[tuple[re.Pattern[str], int]]] = {
    system: [
        # Multi-word phrases are stronger evidence than single tokens.
        (_compile(kw), 2 if " " in kw else 1)
        for kw in keywords
    ]
    for system, keywords in RULES.items()
}

MIN_SCORE = 1
CONFIDENT_MARGIN = 1  # winner must beat runner-up by this much to be trusted


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
    if margin < CONFIDENT_MARGIN:
        # Genuinely ambiguous (e.g. "engine wiring harness"). Leave it for
        # review rather than silently picking one.
        confidence = 0.45
    else:
        confidence = min(0.95, 0.6 + 0.1 * margin)

    return Classification(top_system, round(confidence, 2), tuple(hits[top_system]))


def apply_rules(db: Database, *, min_confidence: float = 0.6, reclassify: bool = False) -> dict:
    """Classify line items in the database. Returns a summary dict."""
    where = "" if reclassify else "WHERE li.system_id IS NULL"
    rows = db.query(
        f"""SELECT li.id, li.description, v.canonical_name AS vendor
            FROM line_items li
            JOIN orders o ON o.id = li.order_id
            LEFT JOIN vendors v ON v.id = o.vendor_id
            {where}"""
    )

    stats = {"examined": len(rows), "classified": 0, "ambiguous": 0, "unmatched": 0}
    with db.tx():
        for row in rows:
            # Vendor name is weak but real evidence: a line from Defender is
            # more likely marine hardware than the same words from Home Depot.
            haystack = f"{row['description']} {row['vendor'] or ''}"
            result = classify_description(haystack)
            if result.system_key is None:
                stats["unmatched"] += 1
                continue
            if result.confidence < min_confidence:
                stats["ambiguous"] += 1
                continue
            sys_row = db.one("SELECT id FROM boat_systems WHERE key=?", (result.system_key,))
            if sys_row is None:
                continue
            db.execute(
                """UPDATE line_items
                   SET system_id=?, classified_by='rule', classify_conf=?, classified_at=?
                   WHERE id=?""",
                (sys_row["id"], result.confidence, utcnow(), row["id"]),
            )
            stats["classified"] += 1
    return stats
