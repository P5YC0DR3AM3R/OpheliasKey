"""Boat-system taxonomy for Ophelia's Key.

Built around the actual vessel: a liveaboard pleasure craft with a large
off-grid electrical system. Power is split into six systems rather than one
"electrical" bucket, because generation, storage, conversion and the two
separate distribution voltages are independently budgeted and independently
capable of overrunning. Sailing systems (rigging, spars, sails) are absent by
design — this is not a sailboat.

`is_capital` separates spend that becomes part of the vessel from spend that is
consumed (abrasives, yard time, fees).
"""

from __future__ import annotations

from ..db.database import Database, utcnow

# (key, name, sort_order, is_capital, description)
BOAT_SYSTEMS: list[tuple[str, str, int, int, str]] = [
    # --- power: generation, storage, conversion, distribution ---
    ("solar_generation", "Solar Array",            10, 1,
     "Flexible panels, mounts, combiners, string wiring, MC4 connectors"),
    ("energy_storage",   "Battery Banks",          20, 1,
     "LiFePO4 banks, BMS, interconnects, lugs, busbars, battery monitoring"),
    ("power_conversion", "Inverter & Charging",    30, 1,
     "Hybrid inverter, MPPT controller, transfer switch, shore charging"),
    ("generator",        "Generator",              40, 1,
     "Portable inverter generator, fuel handling, generator exhaust"),
    ("ac_distribution",  "120V AC Distribution",   50, 1,
     "AC panel, breakers, outlets, shore power inlet and cordage"),
    ("dc_distribution",  "12V DC Systems",         60, 1,
     "Native 12V loads: nav lights, cabin lighting, pumps, DC panel, fuses"),

    # --- propulsion and running gear ---
    ("propulsion",       "Propulsion & Drivetrain", 70, 1,
     "Engine, impeller, exhaust, fuel system, prop, shaft, cooling"),
    ("steering",         "Steering & Control",      80, 1,
     "Helm, cables, hydraulics, rudder, controls, trim"),

    # --- electronics ---
    ("electronics_nav",  "Navigation Electronics",  90, 1,
     "Chartplotter, radar, transducer, autopilot, NMEA 2000 backbone, sensors"),
    ("connectivity",     "Connectivity & Comms",   100, 1,
     "Starlink, VHF, cellular, routers, switches, network cabling"),
    ("av_security",      "A/V & Security",         110, 1,
     "Sound system, amplifiers, speakers, cameras, NVR, displays"),

    # --- habitability ---
    ("climate",          "Climate Control",        120, 1,
     "Air conditioning, heat, ventilation, insulation, refrigeration"),
    ("plumbing",         "Plumbing & Sanitation",  130, 1,
     "Freshwater, black water, holding tank, hoses, pumps, bilge, seacocks"),
    ("interior",         "Interior & Berths",      140, 1,
     "Berths, seating, captain chairs, upholstery, joinery, coolers, galley"),

    # --- hull and deck ---
    ("hull_structure",   "Hull & Structure",       150, 1,
     "Hull, transom, deck, stringers, glasswork, structural repair"),
    ("paint_coatings",   "Paint & Coatings",       160, 1,
     "Bottom paint, barrier coat, topsides, gelcoat, primers"),
    ("deck_hardware",    "Deck Hardware",          170, 1,
     "Cleats, rails, hatches, hardtop, ladders, fittings, canvas"),
    ("ground_tackle",    "Ground Tackle",          180, 1,
     "Anchors, chain, rode, windlass, snubbers, bow roller"),

    # --- use and compliance ---
    ("dive",             "Dive & Watersports",     190, 1,
     "Dive flags, tanks, compressor, dive ladder, watersports gear"),
    ("safety",           "Safety & Compliance",    200, 1,
     "PFDs, fire suppression, flares, EPIRB, first aid, bilge alarms"),
    ("tender",           "Tender & Davits",        210, 1,
     "Dinghy, outboard, davits, chocks"),
    ("trailer",          "Trailer & Transport",    220, 1,
     "Trailer, bunks, winch, bearings, tires, haul transport"),

    # --- non-capital ---
    ("tools",            "Tools",                  230, 1,
     "Tools bought for the project; retain residual value"),
    ("consumables",      "Consumables & Shop",     240, 0,
     "Abrasives, tape, fasteners, sealant, solvents, PPE, wire, connectors"),
    ("yard_services",    "Yard & Labor",           250, 0,
     "Haul-out, storage, blocking, contracted labor, launch, hauling"),
    ("fees_admin",       "Fees & Admin",           260, 0,
     "Registration, decals, documentation, survey, insurance, moorage"),

    ("uncategorized",    "Uncategorized",          900, 0,
     "Awaiting classification"),
]

# Facts about the vessel, seeded into project_meta and given to the LLM
# classifier as context. A PoE switch is an ambiguous purchase in the abstract;
# against a boat with six 4K cameras it is obviously part of the camera system.
VESSEL_META: dict[str, str] = {
    "vessel_name": "Ophelia's Key",
    "vessel_type": "Pleasure craft with berths (liveaboard)",
    "vessel_make_model": "1988 Cruisers Yachts Esprit 3370, white, express cruiser",
    "vessel_condition": "Good (Georgia DOR T-22B inspection, July 2026)",
    "acquisition": "Bill of sale, no prior title",
    "registration": "State of Montana permanent registration, Granite County",
    "registration_mark": "MT9740CA",
    "hin": "CRS7251BA888",
    "decal_number": "A06585888",
    "mmsi": "unassigned",
    "callsign": "unassigned",
    "solar_array": "8 x flexible panels marketed at 500W each (4000W nominal), 4S2P",
    "generator": "Genkins 8000W portable quiet inverter generator, gas, electric start, 120V/240V 30A",
    "inverter": "Lonsge 4000W pure sine wave hybrid solar inverter, 24V DC in, 120V AC out at 33A continuous",
    "mppt": "Built-in 140A MPPT, 55-350V DC operating window",
    "bank_24v": "2 x Dumfume 12.8V 600Ah LiFePO4 in series: 25.6V, 600Ah, 15.36 kWh, 200A BMS per unit",
    "bank_12v": "1 x 12V 320Ah, isolated house bank for native 12V loads",
    "primary_ac_load": "Air conditioner, 120V AC, 16.2A (~1944W running)",
    "recent_additions": (
        "Simrad GO9, radar, in-hull transducer, solar, Starlink, updated sound system, "
        "6 x 4K cameras, NMEA 2000 across all systems, Orca Core 2, new black water hoses, "
        "new exhaust, new impeller, exterior coolers, extra captain chair"
    ),
}


def seed_systems(db: Database) -> int:
    """Insert or update systems. Idempotent; safe on every startup."""
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


def seed_vessel_meta(db: Database) -> None:
    for key, value in VESSEL_META.items():
        db.execute(
            """INSERT INTO project_meta (key, value, updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                 updated_at=excluded.updated_at""",
            (key, value, utcnow()),
        )


def vessel_context(db: Database) -> str:
    """Render the vessel spec as text for the LLM classifier's system prompt."""
    rows = db.query("SELECT key, value FROM project_meta ORDER BY key")
    if not rows:
        source = VESSEL_META.items()
    else:
        source = [(r["key"], r["value"]) for r in rows]
    return "\n".join(f"- {k.replace('_', ' ')}: {v}" for k, v in source)


def system_id_by_key(db: Database, key: str) -> int | None:
    row = db.one("SELECT id FROM boat_systems WHERE key=?", (key,))
    return row["id"] if row else None


def system_catalog(db: Database) -> str:
    """Render the taxonomy for the LLM classifier's system prompt."""
    rows = db.query(
        "SELECT key, name, description FROM boat_systems WHERE key != 'uncategorized' "
        "ORDER BY sort_order"
    )
    return "\n".join(f"- {r['key']} ({r['name']}): {r['description']}" for r in rows)
