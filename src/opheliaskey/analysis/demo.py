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
    # Power system — the dominant spend on this vessel.
    (250, "amazon.com", 0, [
        ("Dumfume 12.8V 600Ah LiFePO4 Battery 200A BMS", 2, 129900)]),
    (245, "amazon.com", 0, [
        ("Lonsge 4000W Pure Sine Wave Hybrid Solar Inverter 24V 140A MPPT", 1, 89900),
        ("4/0 AWG Battery Cable Lugs Tinned Copper 20pc", 1, 4299),
        ("Class T Fuse 400A with Block", 1, 6850)]),
    (238, "amazon.com", 1200, [
        ("Flexible Solar Panel 500W Monocrystalline", 8, 32900),
        ("MC4 Solar Connectors Waterproof 12 Pair", 1, 1899),
        ("10 AWG Solar Extension Cable 30ft Pair", 2, 4299)]),
    (220, "amazon.com", 0, [
        ("Genkins 8000W Portable Quiet Inverter Generator Electric Start", 1, 139900)]),
    # Personal purchases in the same account and window.
    (216, "amazon.com", 0, [
        ("OluKai Ulele Men's Beach Sandals Size 11", 1, 11000)]),
    (210, "amazon.com", 0, [
        ("Victron SmartShunt 500A Battery Monitor", 1, 12999),
        ("Blue Sea Systems 5026 ST Blade Fuse Block", 2, 8999),
        ("Ancor Marine Grade Tinned Wire 10 AWG 100ft", 1, 8499)]),
    # Genuinely ambiguous without vessel context: these are the camera and
    # nav-computer support hardware, not household networking.
    (198, "amazon.com", 0, [
        ("TP-Link LS108GP 8 Port PoE+ Network Switch", 1, 5999),
        ("Cat6 Outdoor Ethernet Cable 100ft Direct Burial", 2, 3499),
        ("GMKtec G11 Mini PC Ryzen 7 16GB 512GB", 1, 29900)]),
    (190, "amazon.com", 0, [
        ("4K PoE Security Camera Outdoor IP67", 6, 8999),
        ("8 Channel NVR 4K 2TB", 1, 21999)]),
    (176, "amazon.com", 0, [
        ("Simrad GO9 XSE Chartplotter with Active Imaging", 1, 149900),
        ("Airmar P79 In-Hull Depth Transducer", 1, 22900),
        ("NMEA 2000 Starter Kit Backbone", 1, 15900),
        ("NMEA 2000 Drop Cable 2m", 4, 2899)]),
    (160, "amazon.com", 0, [
        ("Halo20+ Radar Dome", 1, 219900)]),
    (150, "amazon.com", 0, [
        ("Starlink Mini Roam Kit", 1, 59900)]),
    (140, "amazon.com", 0, [
        ("Rockville dB13 3000W Mono Amplifier", 2, 12999),
        ("Marine 6.5in Coaxial Speakers Waterproof Pair", 3, 6499),
        ("Marine Head Unit Bluetooth Receiver", 1, 13999)]),
    # Personal again.
    (132, "amazon.com", 0, [
        ("DoorDash DashPass Annual Subscription", 1, 9600)]),
    (120, "amazon.com", 0, [
        ("Air Conditioner 12000 BTU 120V Marine", 1, 119900),
        ("Insulated Ducting 4in 25ft", 1, 6900)]),
    (105, "amazon.com", 0, [
        ("Sanitation Hose 1.5in OdorSafe Black Water 25ft", 1, 18995),
        ("Bronze Ball Valve Seacock 1.5in", 3, 3500),
        ("Marine Grade Hose Clamps Stainless 20pc", 2, 3299)]),
    (92, "amazon.com", 0, [
        ("Marine Exhaust Hose 4in Wet Wrapped 10ft", 1, 27900),
        ("Raw Water Pump Impeller Kit", 2, 4890)]),
    (78, "amazon.com", 0, [
        ("Hotkesa Boat Seat Pedestal Adjustable", 1, 12999),
        ("Captain Helm Chair with Bolster Marine Vinyl", 1, 48900)]),
    (64, "amazon.com", 0, [
        ("Diver Down Flag with 4 FT Pole and Base", 1, 3499),
        ("12x18in Diver Down Scuba Flag", 2, 1299),
        ("Type II Life Jackets 4 Pack USCG Approved", 1, 5999),
        ("Fire Extinguisher Marine B-1 Rated", 2, 3299)]),
    # Personal: unrelated auto maintenance.
    (55, "amazon.com", 0, [
        ("Firestone Complete Auto Care Oil Change and Tire Rotation", 1, 8947)]),
    (40, "amazon.com", 0, [
        ("Exterior Cooler 65qt Marine White", 2, 24900),
        ("Stainless Cooler Mounting Brackets", 1, 4599)]),
    (22, "signsbytomorrow.com", 0, [
        ("UV Laminated Digitally Printed Boat Decal Cut Contour", 1, 28500),
        ("Registration Number Vinyl MT9740CA", 1, 6500)]),
    (10, "amazon.com", 0, [
        ("3M 5200 Fast Cure Marine Sealant White 10oz", 3, 2399),
        ("Marine Grade Heat Shrink Butt Connectors 120pc", 1, 3799),
        ("Stainless Steel Screws Assortment 18-8", 1, 2899)]),
]

DEMO_BUDGET = {
    "energy_storage": 300000, "power_conversion": 120000, "solar_generation": 300000,
    "generator": 150000, "dc_distribution": 60000, "ac_distribution": 40000,
    "electronics_nav": 450000, "connectivity": 80000, "av_security": 200000,
    "climate": 150000, "plumbing": 80000, "interior": 100000,
    "propulsion": 100000, "safety": 30000, "dive": 15000,
    "consumables": 40000, "fees_admin": 40000,
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

    usage = seed_demo_usage(db)
    return {"orders_created": created, "usage": usage}


# Labor and nights cannot be inferred from receipts, so the demo seeds a
# plausible record of both to exercise the reward analysis end to end.
DEMO_LABOR = [
    ("electronics_nav", 14, "GO9, radar, in-hull transducer, NMEA 2000 backbone"),
    ("energy_storage", 22, "24V bank build, busbars, Class T fuse, interconnects"),
    ("solar_generation", 18, "8-panel array, 4S2P string wiring, MC4 terminations"),
    ("av_security", 9, "Six camera runs, NVR mount, PoE switch"),
    ("plumbing", 11, "Black water hose replacement, seacock service"),
    ("propulsion", 6, "Exhaust replacement, impeller"),
]

DEMO_NIGHTS = [(21, "2026-06-15", "Summer aboard"), (9, "2026-07-20", "Lake week")]


def seed_demo_usage(db: Database) -> dict:
    """Seed labor and nights. Skipped entirely if either is already recorded,
    so a real log is never mixed with sample data."""
    existing = db.one("SELECT COUNT(*) n FROM labor_log")
    if existing and existing["n"]:
        return {"skipped": True}
    with db.tx():
        for key, hours, note in DEMO_LABOR:
            row = db.one("SELECT id FROM boat_systems WHERE key=?", (key,))
            db.execute(
                """INSERT INTO labor_log (system_id, hours, description, performed_at,
                     logged_at) VALUES (?,?,?,?,?)""",
                (row["id"] if row else None, hours, note, None, utcnow()),
            )
        for nights, start, note in DEMO_NIGHTS:
            db.execute(
                """INSERT INTO usage_log (nights, start_date, note, logged_at)
                   VALUES (?,?,?,?)""",
                (nights, start, note, utcnow()),
            )
    return {"labor_entries": len(DEMO_LABOR), "nights": sum(n for n, _, _ in DEMO_NIGHTS)}


def clear_demo(db: Database) -> int:
    with db.tx():
        rows = db.query("SELECT id FROM orders WHERE source=?", (DEMO_SOURCE,))
        db.execute("DELETE FROM orders WHERE source=?", (DEMO_SOURCE,))
    return len(rows)
