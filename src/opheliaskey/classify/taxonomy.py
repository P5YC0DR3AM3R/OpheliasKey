"""Boat-system taxonomy.

The account is dedicated to the project, so classification is not about
separating boat spend from personal spend — it is about attributing every
dollar to a *system*, which is what makes cost, risk and reward analysis
possible. `is_capital` distinguishes spend that becomes part of the vessel from
spend that is consumed (tape, sandpaper, yard time).
"""

from __future__ import annotations

from ..db.database import Database

# (key, name, sort_order, is_capital, description)
BOAT_SYSTEMS: list[tuple[str, str, int, int, str]] = [
    ("hull_structure",  "Hull & Structure",        10,  1, "Hull, keel, deck, coring, glass and structural repair"),
    ("paint_coatings",  "Paint & Coatings",        20,  1, "Bottom paint, barrier coat, topsides, varnish, primers"),
    ("deck_hardware",   "Deck Hardware",           30,  1, "Winches, clutches, tracks, cleats, blocks, hatches"),
    ("rigging",         "Rigging & Spars",         40,  1, "Standing and running rigging, mast, boom, furlers"),
    ("sails",           "Sails & Canvas",          50,  1, "Sails, dodger, bimini, covers, sail repair"),
    ("propulsion",      "Propulsion & Drivetrain", 60,  1, "Engine, transmission, shaft, prop, fuel system, exhaust"),
    ("steering",        "Steering & Control",      70,  1, "Wheel, quadrant, cables, rudder, emergency tiller"),
    ("electrical",      "Electrical & Power",      80,  1, "Batteries, charging, alternator, solar, wiring, panels"),
    ("electronics",     "Electronics & Nav",       90,  1, "Chartplotter, radar, AIS, VHF, autopilot, instruments"),
    ("plumbing",        "Plumbing & Tankage",     100,  1, "Freshwater, waste, holding, bilge pumps, seacocks"),
    ("ground_tackle",   "Ground Tackle",          110,  1, "Anchors, chain, rode, windlass, snubbers"),
    ("safety",          "Safety & Compliance",    120,  1, "Liferaft, PFDs, EPIRB, flares, fire suppression, jacklines"),
    ("interior",        "Interior & Joinery",     130,  1, "Cabinetry, upholstery, galley, berths, sole, insulation"),
    ("comfort",         "Comfort & Systems",      140,  1, "Heating, refrigeration, ventilation, watermaker"),
    ("tender",          "Tender & Davits",        150,  1, "Dinghy, outboard, davits, chocks"),
    ("tools",           "Tools",                  160,  1, "Tools purchased for the project (retain residual value)"),
    ("consumables",     "Consumables & Shop",     170,  0, "Abrasives, tape, fasteners, epoxy, solvents, PPE, rags"),
    ("yard_services",   "Yard & Labor",           180,  0, "Haul-out, storage, blocking, contracted labor, launch"),
    ("fees_admin",      "Fees & Admin",           190,  0, "Survey, documentation, registration, insurance, moorage"),
    ("uncategorized",   "Uncategorized",          900,  0, "Awaiting classification"),
]


def seed_systems(db: Database) -> int:
    """Insert any missing systems. Idempotent; safe on every startup."""
    inserted = 0
    for key, name, order, capital, desc in BOAT_SYSTEMS:
        cur = db.execute(
            """INSERT INTO boat_systems (key, name, sort_order, is_capital, description)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                 name=excluded.name, sort_order=excluded.sort_order,
                 is_capital=excluded.is_capital, description=excluded.description""",
            (key, name, order, capital, desc),
        )
        inserted += cur.rowcount or 0
    return inserted


def system_id_by_key(db: Database, key: str) -> int | None:
    row = db.one("SELECT id FROM boat_systems WHERE key=?", (key,))
    return row["id"] if row else None
