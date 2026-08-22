"""Synthetic data for exercising the pipeline without credentials.

Explicitly labelled: every generated order uses the source 'demo', so
`okey demo --clear` removes all of it and nothing can be confused with real
purchase data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..db.database import Database, utcnow
from ..parsing.vendors_util import resolve_vendor

DEMO_SOURCE = "demo"
TAX_RATE = 0.089

# (days_ago, vendor_domain, shipping_cents, [(description, qty, unit_price)])
# Order totals are computed: sum(line items) + shipping + tax.
DEMO_ORDERS = [
    (240, "defender.com", 4500, [
        ("Rocna 33 lb Galvanized Anchor", 1, 79900),
        ("ACCO G4 High Test Chain 5/16in x 200ft", 1, 189900),
        ("Mantus Chain Hook with Bridle", 1, 19650)]),
    (232, "amazon.com", 0, [
        ("Victron SmartSolar MPPT 100/30 Charge Controller", 1, 28999),
        ("Ancor Marine Grade Tinned Wire 10 AWG 100ft", 1, 8499),
        ("Heat Shrink Butt Connectors Marine 120pc", 1, 3799)]),
    (210, "westmarine.com", 2400, [
        ("Interlux Micron 66 Antifouling Bottom Paint Gallon", 2, 42900),
        ("Interlux Fiberglass Bottomkote Primer Quart", 1, 8900),
        ("3M Sandpaper Assortment 80-220 Grit", 4, 1925)]),
    (196, "amazon.com", 0, [
        ("Garmin GPSMAP 923xsv Chartplotter with Transducer", 1, 219900)]),
    (180, "fisheriessupply.com", 1850, [
        ("Yanmar 3YM30 Raw Water Pump Impeller Kit", 2, 8940),
        ("Racor 500FG Turbine Fuel Filter Water Separator", 1, 44900),
        ("Yanmar Fuel Filter Element 41650-502320", 3, 1200),
        ("Marine Exhaust Elbow Gasket Set", 1, 2760)]),
    (165, "amazon.com", 0, [
        ("3M 5200 Fast Cure Marine Sealant White 10oz", 2, 2399),
        ("Nitrile Gloves Box of 100", 1, 1899),
        ("3M Blue Painters Tape 1.88in 3-pack", 1, 2049)]),
    (150, "westmarine.com", 0, [
        ("Harken 46 Self-Tailing Two-Speed Winch", 2, 189900),
        ("Harken Winch Service Kit", 1, 7900),
        ("Genoa Track Car with Ball Bearings", 4, 17800)]),
    (140, "homedepot.com", 0, [
        ("DEWALT 20V Max Random Orbital Sander", 1, 14900),
        ("Respirator Half Mask with P100 Filters", 1, 4499),
        ("Shop Rags 50-pack", 1, 2477)]),
    (120, "defender.com", 3900, [
        ("Lewmar V700 Vertical Windlass 12V", 1, 149900),
        ("Windlass Circuit Breaker 90A", 1, 12400),
        ("Anchor Snubber Line 5/8in Nylon 30ft", 1, 12200)]),
    (95, "jamestowndistributors.com", 2200, [
        ("West System 105 Epoxy Resin Gallon", 1, 32900),
        ("West System 205 Fast Hardener Quart", 1, 18900),
        ("Biaxial Fiberglass Cloth 1708 50in x 10yd", 1, 16620)]),
    (78, "amazon.com", 0, [
        ("Balmar 100A Alternator Kit with Regulator", 1, 129900)]),
    (60, "westmarine.com", 1500, [
        ("Standing Rigging Wire 1x19 5/16in 100ft", 1, 52900),
        ("Sta-Lok Swageless Terminal 5/16in", 4, 8687)]),
    (45, "amazon.com", 0, [
        ("Blue Sea Systems 5026 ST Blade Fuse Block", 1, 8999),
        ("Victron BMV-712 Battery Monitor with Shunt", 1, 25986)]),
    (30, "fisheriessupply.com", 0, [
        ("Haul-out, pressure wash and blocking, 38ft sailboat", 1, 145000),
        ("Yard storage, monthly", 1, 65000)]),
    (12, "amazon.com", 0, [
        ("Marine Grade Hose Clamps Stainless 20pc", 1, 3299),
        ("Sanitation Hose 1.5in OdorSafe 10ft", 1, 8995),
        ("Bronze Ball Valve Seacock 1.5in", 1, 3500)]),
]

DEMO_BUDGET = {
    "ground_tackle": 400000, "electrical": 500000, "paint_coatings": 150000,
    "electronics": 250000, "propulsion": 300000, "deck_hardware": 400000,
    "rigging": 350000, "hull_structure": 200000, "consumables": 100000,
    "tools": 75000, "yard_services": 250000, "plumbing": 120000,
}


def seed_demo(db: Database) -> dict:
    """Insert demo orders, line items and a budget. Idempotent."""
    now = datetime.now(timezone.utc)
    created = 0

    with db.tx():
        for index, (days_ago, domain, shipping, items) in enumerate(DEMO_ORDERS):
            ordered = (now - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
            external = f"DEMO-{index+1:04d}"
            vendor_id = resolve_vendor(db, domain=domain)

            subtotal = sum(unit * qty for _, qty, unit in items)
            tax = round(subtotal * TAX_RATE)
            total = subtotal + tax + shipping

            existing = db.one(
                "SELECT id FROM orders WHERE source=? AND external_order_id=?",
                (DEMO_SOURCE, external),
            )
            if existing:
                continue

            cur = db.execute(
                """INSERT INTO orders (source, external_order_id, vendor_id, ordered_at,
                     status, subtotal_cents, tax_cents, shipping_cents, total_cents,
                     currency, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (DEMO_SOURCE, external, vendor_id, ordered, "delivered", subtotal,
                 tax, shipping, total, "USD", utcnow(), utcnow()),
            )
            order_id = int(cur.lastrowid)
            created += 1

            for line_no, (desc, qty, unit) in enumerate(items):
                db.execute(
                    """INSERT INTO line_items (order_id, line_no, description, quantity,
                         unit_price_cents, total_cents)
                       VALUES (?,?,?,?,?,?)""",
                    (order_id, line_no, desc, qty, unit, unit * qty),
                )

        for key, planned in DEMO_BUDGET.items():
            row = db.one("SELECT id FROM boat_systems WHERE key=?", (key,))
            if row:
                db.execute(
                    """INSERT INTO budget_lines (system_id, planned_cents, phase)
                       VALUES (?,?,'refit')
                       ON CONFLICT(system_id, phase) DO UPDATE SET
                         planned_cents=excluded.planned_cents""",
                    (row["id"], planned),
                )

    return {"orders_created": created}


def clear_demo(db: Database) -> int:
    with db.tx():
        rows = db.query("SELECT id FROM orders WHERE source=?", (DEMO_SOURCE,))
        db.execute("DELETE FROM orders WHERE source=?", (DEMO_SOURCE,))
    return len(rows)
