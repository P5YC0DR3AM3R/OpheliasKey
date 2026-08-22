"""Core behaviour tests. Each one guards a decision that would silently corrupt
the analysis if it regressed."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from opheliaskey.analysis.cost import totals
from opheliaskey.analysis.reconcile import reconcile
from opheliaskey.analysis.risk import line_item_coverage, unclassified_spend
from opheliaskey.classify.rules import classify_description
from opheliaskey.classify.taxonomy import seed_systems
from opheliaskey.db.database import Database, fmt_money, money, utcnow
from opheliaskey.parsing.email_parser import parse_email
from opheliaskey.parsing.vendors_util import normalize_descriptor, resolve_vendor


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    database.migrate()
    seed_systems(database)
    return database


# --- money ------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("$1,234.56", 123456), ("1234.56", 123456), ("(12.34)", -1234),
    ("-12.34", -1234), (5, 500), (0, 0), (12.5, 1250),
    ("", None), (None, None), ("free", None), ("2024-01-01", None), (True, None),
])
def test_money_parsing(raw, expected):
    assert money(raw) == expected


def test_negative_money_formats_sign_before_dollar():
    assert fmt_money(-30100) == "-$301.00"
    assert fmt_money(None) == "—"


# --- classification ---------------------------------------------------------

@pytest.mark.parametrize("text,system", [
    ("Rocna 33 lb Galvanized Anchor", "ground_tackle"),
    ("Victron SmartSolar MPPT 100/30 Charge Controller", "electrical"),
    ("Interlux Micron 66 Antifouling Bottom Paint", "paint_coatings"),
    ("Yanmar 3YM30 Raw Water Pump Impeller Kit", "propulsion"),
    ("Harken 46 Self-Tailing Winch", "deck_hardware"),
])
def test_classifier_places_marine_skus(text, system):
    assert classify_description(text).system_key == system


def test_classifier_declines_rather_than_guesses():
    """An unrecognized item must return None. Guessing would put real dollars in
    the wrong system with no signal that it happened."""
    result = classify_description("USB-C charging cable 6ft")
    assert result.system_key is None
    assert result.confidence == 0.0


# --- vendor identity --------------------------------------------------------

def test_store_variants_collapse_to_one_vendor(db):
    """Two card descriptors for different West Marine stores must resolve to a
    single vendor, or per-vendor totals fragment."""
    a = resolve_vendor(db, name="WESTMARINE #0231 WATSONVILLE CA", alias_kind="card_descriptor")
    b = resolve_vendor(db, name="WEST MARINE 0450 SEATTLE WA", alias_kind="card_descriptor")
    c = resolve_vendor(db, domain="westmarine.com")
    assert a == b == c
    assert len(db.query("SELECT id FROM vendors")) == 1


def test_descriptor_drops_store_and_reference_noise():
    assert normalize_descriptor("THE HOME DEPOT #1234 SEATTLE WA 04/12") == "home depot"
    assert "2k4tr9" not in normalize_descriptor("AMZN Mktp US*2K4TR9 AMZN.COM/BILL WA")


# --- raw store --------------------------------------------------------------

def test_raw_store_dedupes_identical_and_versions_changed(db):
    first, new1 = db.store_raw("gmail", "msg-1", b"original")
    second, new2 = db.store_raw("gmail", "msg-1", b"original")
    third, new3 = db.store_raw("gmail", "msg-1", b"changed")

    assert new1 is True and new2 is False and new3 is True
    assert first == second != third
    assert db.load_raw(third) == b"changed"
    # The original version survives; nothing is overwritten.
    assert db.load_raw(first) == b"original"


# --- email parsing ----------------------------------------------------------

JSONLD_EMAIL = b"""From: auto-confirm@amazon.com
Subject: Your Amazon.com order
Date: Mon, 3 Mar 2025 10:15:00 +0000
Content-Type: text/html; charset=utf-8

<html><body>
<script type="application/ld+json">
{"@context":"http://schema.org","@type":"Order",
 "merchant":{"@type":"Organization","name":"Amazon.com"},
 "orderNumber":"114-7788990-1122334","priceCurrency":"USD","price":"289.98",
 "acceptedOffer":[
  {"@type":"Offer","price":"129.99",
   "itemOffered":{"@type":"Product","name":"Victron SmartSolar MPPT 100/30"},
   "eligibleQuantity":{"@type":"QuantitativeValue","value":2}},
  {"@type":"Offer","price":"30.00",
   "itemOffered":{"@type":"Product","name":"Ancor Marine Tinned Wire 10 AWG"},
   "eligibleQuantity":{"@type":"QuantitativeValue","value":1}}]}
</script>
<p>Thanks for your order.</p></body></html>
"""


def test_jsonld_order_extracted_with_line_items():
    order = parse_email(JSONLD_EMAIL)
    assert order is not None
    assert order.method == "jsonld"
    assert order.external_order_id == "114-7788990-1122334"
    assert order.vendor_domain == "amazon.com"
    assert order.total_cents == 28998
    assert len(order.items) == 2
    # Quantity must multiply into the line total.
    assert order.items[0].quantity == 2
    assert order.items[0].total_cents == 25998


HEURISTIC_EMAIL = b"""From: orders@defender.com
Subject: Order Confirmation ABC123456
Date: Tue, 4 Mar 2025 09:00:00 +0000
Content-Type: text/plain; charset=utf-8

Thank you. Order Number: ABC123456
Order Total: $1,745.00
"""


def test_heuristic_fallback_when_no_markup():
    order = parse_email(HEURISTIC_EMAIL)
    assert order is not None
    assert order.method == "heuristic"
    assert order.total_cents == 174500
    assert order.items == []


def test_non_order_email_returns_none():
    msg = b"From: news@example.com\nSubject: Newsletter\n\nBoats are nice.\n"
    assert parse_email(msg) is None


# --- risk -------------------------------------------------------------------

def _make_order(db, total, items, tax=0, shipping=0, ordered_at="2025-03-01T00:00:00Z"):
    cur = db.execute(
        """INSERT INTO orders (source, external_order_id, ordered_at, status,
             tax_cents, shipping_cents, total_cents, created_at, updated_at)
           VALUES ('test', ?, ?, 'delivered', ?, ?, ?, ?, ?)""",
        (f"o{db.one('SELECT COUNT(*) c FROM orders')['c']}", ordered_at,
         tax, shipping, total, utcnow(), utcnow()),
    )
    order_id = int(cur.lastrowid)
    for i, amount in enumerate(items):
        db.execute(
            "INSERT INTO line_items (order_id, line_no, description, total_cents) "
            "VALUES (?,?,?,?)", (order_id, i, f"item {i}", amount))
    return order_id


def test_coverage_gap_detected_when_items_dont_reach_total(db):
    _make_order(db, total=100000, items=[40000, 20000])  # $400 unaccounted for
    findings = line_item_coverage(db)
    assert len(findings) == 1
    assert findings[0]["code"] == "coverage_gap"
    assert findings[0]["amount_cents"] == 40000


def test_no_coverage_gap_when_tax_and_shipping_explain_it(db):
    _make_order(db, total=100000, items=[85000], tax=10000, shipping=5000)
    assert line_item_coverage(db) == []


def test_unclassified_spend_reported(db):
    _make_order(db, total=50000, items=[50000])
    findings = unclassified_spend(db)
    assert findings[0]["amount_cents"] == 50000


# --- reconciliation ---------------------------------------------------------

def _make_txn(db, amount, posted_at, txn_id):
    db.execute(
        "INSERT INTO transactions (plaid_transaction_id, posted_at, amount_cents, pending) "
        "VALUES (?,?,?,0)", (txn_id, posted_at, amount))


def test_reconcile_matches_on_amount_and_date(db):
    _make_order(db, total=50000, items=[50000], ordered_at="2025-03-01T00:00:00Z")
    _make_txn(db, 50000, "2025-03-03", "t1")
    stats = reconcile(db)
    assert stats["matched"] == 1
    assert len(db.query("SELECT id FROM reconciliations")) == 1


def test_reconcile_refuses_ambiguous_matches(db):
    """Two identical candidate charges must produce no link at all. A wrong link
    corrupts both unreconciled-order and spend-without-receipt signals."""
    _make_order(db, total=50000, items=[50000], ordered_at="2025-03-01T00:00:00Z")
    _make_txn(db, 50000, "2025-03-02", "t1")
    _make_txn(db, 50000, "2025-03-03", "t2")
    stats = reconcile(db)
    assert stats["matched"] == 0
    assert stats["ambiguous"] == 1


def test_reconcile_ignores_far_away_transactions(db):
    _make_order(db, total=50000, items=[50000], ordered_at="2025-03-01T00:00:00Z")
    _make_txn(db, 50000, "2025-06-01", "t1")
    assert reconcile(db)["matched"] == 0


# --- totals -----------------------------------------------------------------

def test_net_spend_excludes_cancelled_and_subtracts_refunds(db):
    order_id = _make_order(db, total=100000, items=[100000])
    db.execute(
        "INSERT INTO orders (source, external_order_id, status, total_cents, created_at, "
        "updated_at) VALUES ('test','cancelled-1','cancelled',999900,?,?)", (utcnow(), utcnow()))
    db.execute(
        "INSERT INTO refunds (order_id, kind, amount_cents, status) "
        "VALUES (?, 'refund', 25000, 'completed')", (order_id,))
    result = totals(db)
    assert result["gross_cents"] == 100000       # cancelled order excluded
    assert result["refunded_cents"] == 25000
    assert result["net_cents"] == 75000


# --- plural handling (regression) -------------------------------------------

@pytest.mark.parametrize("text,system", [
    ("Marine Grade Hose Clamps Stainless 20pc", "plumbing"),
    ("Rocna 33 lb Galvanized Anchors", "ground_tackle"),
    ("Harken 46 Self-Tailing Winches", "deck_hardware"),
    ("Bronze Ball Valve Seacocks 1.5in", "plumbing"),
])
def test_classifier_matches_plural_product_titles(text, system):
    """Product titles pluralize freely. A plain \\b anchor silently fails on
    every one of them, which is how 'Hose Clamps' went unclassified."""
    assert classify_description(text).system_key == system


def test_ambiguous_cross_system_item_is_refused():
    """A windlass circuit breaker is genuinely both electrical and ground
    tackle. It must land below the confidence floor, not pick a side."""
    assert classify_description("Windlass Circuit Breaker 90A").confidence < 0.6
