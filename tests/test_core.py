"""Core behaviour tests. Each one guards a decision that would silently corrupt
the analysis if it regressed."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from opheliaskey.analysis.cost import totals
from opheliaskey.analysis.reconcile import reconcile
from opheliaskey.analysis.risk import (
    line_item_coverage,
    unclassified_spend,
    unreviewed_spend,
)
from opheliaskey.classify.rules import classify_description, classify_relevance
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
    ("Lonsge 4000W Pure Sine Wave Hybrid Inverter", "power_conversion"),
    ("Dumfume 12.8V 600Ah LiFePO4 Battery", "energy_storage"),
    ("Flexible Solar Panel 500W Monocrystalline", "solar_generation"),
    ("Genkins 8000W Portable Inverter Generator", "generator"),
    ("Simrad GO9 XSE Chartplotter", "electronics_nav"),
    ("Starlink Mini Roam Kit", "connectivity"),
    ("Sanitation Hose OdorSafe Black Water", "plumbing"),
    ("Diver Down Flag with Pole", "dive"),
    ("Raw Water Pump Impeller Kit", "propulsion"),
])
def test_classifier_places_vessel_skus(text, system):
    """The taxonomy is built for this vessel: six power systems, no rigging."""
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
    orders = parse_email(JSONLD_EMAIL)
    assert len(orders) == 1
    order = orders[0]
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
    orders = parse_email(HEURISTIC_EMAIL)
    assert len(orders) == 1
    order = orders[0]
    assert order.method == "heuristic"
    assert order.total_cents == 174500
    assert order.items == []


def test_non_order_email_returns_nothing():
    msg = b"From: news@example.com\nSubject: Newsletter\n\nBoats are nice.\n"
    assert parse_email(msg) == []


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


def test_unclassified_spend_counts_only_boat_items(db):
    """A personal item with no boat system is correct, not a coverage gap."""
    _make_order(db, total=50000, items=[50000])
    db.execute("UPDATE line_items SET relevance='boat'")
    assert unclassified_spend(db)[0]["amount_cents"] == 50000

    db.execute("UPDATE line_items SET relevance='personal'")
    assert unclassified_spend(db) == []


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

def test_project_spend_excludes_personal_and_subtracts_refunds(db):
    """The headline number counts boat line items only. Personal spend in the
    same account must never reach it."""
    order_id = _make_order(db, total=150000, items=[100000, 50000])
    items = db.query("SELECT id FROM line_items ORDER BY line_no")
    db.execute("UPDATE line_items SET relevance='boat' WHERE id=?", (items[0]["id"],))
    db.execute("UPDATE line_items SET relevance='personal' WHERE id=?", (items[1]["id"],))
    db.execute(
        "INSERT INTO refunds (order_id, kind, amount_cents, status) "
        "VALUES (?, 'refund', 25000, 'completed')", (order_id,))

    result = totals(db)
    assert result["project_gross_cents"] == 100000   # the personal $500 is excluded
    assert result["personal_cents"] == 50000
    assert result["refunded_cents"] == 25000
    assert result["net_cents"] == 75000


def test_unreviewed_spend_is_reported_not_absorbed(db):
    """Undecided items must appear as their own figure rather than defaulting
    into or out of the project total."""
    _make_order(db, total=80000, items=[80000])   # relevance left NULL
    result = totals(db)
    assert result["project_gross_cents"] == 0
    assert result["personal_cents"] == 0
    assert result["unreviewed_cents"] == 80000

    finding = unreviewed_spend(db)[0]
    assert finding["code"] == "unreviewed_relevance"
    assert finding["amount_cents"] == 80000


# --- relevance --------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Sanitation Hose 1.5in OdorSafe Black Water", "boat"),
    ("Starlink Mini Roam Kit", "boat"),
    ("Diver Down Flag with 4 FT Pole", "boat"),
    ("OluKai Ulele Men's Beach Sandals", "personal"),
    ("DoorDash DashPass Annual Subscription", "personal"),
    ("Firestone Complete Auto Care Oil Change", "personal"),
])
def test_relevance_rules_decide_clear_cases(text, expected):
    relevance, confidence = classify_relevance(text)
    assert relevance == expected
    assert confidence >= 0.75


@pytest.mark.parametrize("text", [
    "TP-Link LS108GP 8 Port PoE+ Network Switch",
    "GMKtec G11 Mini PC Ryzen 7 16GB",
    "Rockville dB13 3000W Mono Amplifier",
])
def test_ambiguous_items_defer_to_the_llm(text):
    """Generic hardware genuinely could be boat or household. Keyword rules
    must not decide it — the LLM pass gets the vessel spec and can reason that
    a PoE switch fits a boat running six 4K cameras."""
    relevance, confidence = classify_relevance(text)
    assert relevance is None
    assert confidence == 0.0


# --- plural handling (regression) -------------------------------------------

@pytest.mark.parametrize("text,system", [
    ("Marine Grade Hose Clamps Stainless 20pc", "plumbing"),
    ("Rocna 33 lb Galvanized Anchors", "ground_tackle"),
    ("Bronze Ball Valve Seacocks 1.5in", "plumbing"),
    ("4K PoE Security Cameras Outdoor", "av_security"),
])
def test_classifier_matches_plural_product_titles(text, system):
    """Product titles pluralize freely. A plain \\b anchor silently fails on
    every one of them, which is how 'Hose Clamps' went unclassified."""
    assert classify_description(text).system_key == system


def test_unrecognized_item_yields_no_system():
    """Nothing in the catalog matches, so no system is assigned."""
    assert classify_description("Greeting card birthday").system_key is None


def test_manual_verdicts_survive_reclassify(db):
    """A human decision is final. --reclassify must not silently overwrite it,
    even when the keyword rules would reach the opposite conclusion."""
    from opheliaskey.classify.rules import apply_rules

    cur = db.execute(
        "INSERT INTO orders (source, external_order_id, status, total_cents, created_at, "
        "updated_at) VALUES ('test','o-manual','delivered',5000,?,?)", (utcnow(), utcnow()))
    order_id = int(cur.lastrowid)
    # The rules confidently call this 'personal'; the human said otherwise.
    db.execute(
        "INSERT INTO line_items (order_id, line_no, description, total_cents, relevance, "
        "relevance_by, relevance_conf) VALUES (?,0,'OluKai Ulele beach sandals',5000,"
        "'boat','manual',1.0)", (order_id,))

    apply_rules(db, reclassify=True)

    row = db.one("SELECT relevance, relevance_by FROM line_items WHERE order_id=?", (order_id,))
    assert row["relevance"] == "boat"
    assert row["relevance_by"] == "manual"


# --- specification risk -----------------------------------------------------

from opheliaskey.analysis.spec import (  # noqa: E402
    DEFAULT_SPEC,
    check_ac_startup_surge,
    check_bms_headroom,
    check_generator_leg_capacity,
    check_mppt_ceiling,
    load_spec,
    spec_report,
)


def _spec(**overrides) -> dict:
    s = dict(DEFAULT_SPEC)
    s.update(overrides)
    return s


def test_series_bank_ceiling_is_one_bms_not_the_sum():
    """Batteries in series share a current path, so the bank ceiling is a single
    unit's BMS. Treating it as the sum would double the apparent headroom and
    hide the finding entirely."""
    findings = check_bms_headroom(_spec())
    assert len(findings) == 1
    f = findings[0]
    assert f.code == "bms_headroom"
    assert f.severity == "high"
    assert "174A" in f.numbers["DC draw"]
    assert f.numbers["BMS ceiling"] == "200A"


def test_bms_check_silent_when_headroom_is_comfortable():
    """A check that finds nothing wrong must return nothing, not reassurance."""
    assert check_bms_headroom(_spec(bms_amps_per_unit=400)) == []


def test_surge_check_fires_on_the_pessimistic_estimate():
    """Startup surge spans a wide range. If the upper end exceeds the inverter
    the risk is real even when the lower end fits, and must not be suppressed
    by the favourable assumption."""
    findings = check_ac_startup_surge(_spec())
    assert len(findings) == 1
    assert findings[0].severity == "medium"     # low end fits, high end does not

    # A larger compressor fails on every estimate.
    worse = check_ac_startup_surge(_spec(ac_load_watts=3000))
    assert worse[0].severity == "high"

    # A small load fits under every estimate — no finding at all.
    assert check_ac_startup_surge(_spec(ac_load_watts=500)) == []


def test_mppt_check_distinguishes_binding_from_moot():
    """The controller caps below nameplate, but realistic panel output stays
    under that cap — so it is not the binding constraint and must not be
    reported as though it were."""
    findings = check_mppt_ceiling(_spec())
    assert findings[0].severity == "low"
    assert "unlikely to bind" in findings[0].detail

    # Panels that actually perform make the controller the real limit.
    binding = check_mppt_ceiling(_spec(solar_panel_watts_nameplate=1000))
    assert binding[0].severity == "medium"
    assert "binding constraint" in binding[0].detail


def test_generator_check_silent_when_leg_covers_rating():
    assert check_generator_leg_capacity(_spec(generator_circuit_amps=70)) == []


def test_spec_values_are_overridable_from_project_meta(db):
    """Every number must be correctable without editing code, or the findings
    become unfalsifiable."""
    assert load_spec(db)["bms_amps_per_unit"] == 200
    db.execute(
        "INSERT INTO project_meta (key, value, updated_at) VALUES ('spec.bms_amps_per_unit',"
        "'400', ?)", (utcnow(),))
    assert load_spec(db)["bms_amps_per_unit"] == 400
    # ...and the override actually changes the outcome.
    assert check_bms_headroom(load_spec(db)) == []


def test_bad_override_is_ignored_not_fatal(db):
    db.execute(
        "INSERT INTO project_meta (key, value, updated_at) VALUES ('spec.bank_kwh',"
        "'not a number', ?)", (utcnow(),))
    assert load_spec(db)["bank_kwh"] == DEFAULT_SPEC["bank_kwh"]


def test_every_finding_declares_its_assumptions_or_uses_none():
    """A finding derived from a judgement call must name it, or it cannot be
    audited or argued with."""
    report = spec_report()
    assert report["findings"], "expected findings for this vessel"
    for finding in report["findings"]:
        for line in finding["assumptions"]:
            assert "=" in line and "(" in line, f"unexplained assumption: {line}"


def test_spec_findings_stay_separate_from_purchase_findings(db):
    """Blending them would let an engineering constraint read as overspending."""
    from opheliaskey.analysis.risk import risk_report

    report = risk_report(db)
    assert "spec_findings" in report and "findings" in report
    spec_codes = {f["code"] for f in report["spec_findings"]}
    purchase_codes = {f["code"] for f in report["findings"]}
    assert not (spec_codes & purchase_codes)


# --- reward -----------------------------------------------------------------

from opheliaskey.analysis.reward import (  # noqa: E402
    RECOVERY_RATES,
    REWARD_ASSUMPTIONS,
    capability,
    labor_avoided,
    recovery,
    reward_report,
    use_value,
)


def _boat_order(db, system_key, cents):
    cur = db.execute(
        "INSERT INTO orders (source, external_order_id, status, total_cents, created_at, "
        "updated_at) VALUES ('test',?, 'delivered', ?, ?, ?)",
        (f"r{db.one('SELECT COUNT(*) c FROM orders')['c']}", cents, utcnow(), utcnow()))
    order_id = int(cur.lastrowid)
    sys_id = None
    if system_key:
        row = db.one("SELECT id FROM boat_systems WHERE key=?", (system_key,))
        sys_id = row["id"]
    db.execute(
        "INSERT INTO line_items (order_id, line_no, description, total_cents, system_id, "
        "relevance) VALUES (?,0,'item',?,?,'boat')", (order_id, cents, sys_id))
    return order_id


def test_recovery_never_claims_full_value(db):
    """No system recovers 100%. A reward model that let one would be lying."""
    assert all(rate < 1.0 for rate, _ in RECOVERY_RATES.values())
    _boat_order(db, "electronics_nav", 100000)
    r = recovery(db)
    assert r["recoverable_cents"] < r["vessel_spend_cents"]
    assert r["sunk_cents"] > 0


def test_consumed_categories_recover_nothing(db):
    for key in ("consumables", "yard_services", "fees_admin"):
        assert RECOVERY_RATES[key][0] == 0.0
    _boat_order(db, "yard_services", 50000)
    r = recovery(db)
    assert r["recoverable_cents"] == 0
    assert r["sunk_cents"] == 50000


def test_tools_are_excluded_from_vessel_value(db):
    """Tools retain value but do not convey with the boat, so they must not
    inflate what a buyer would pay."""
    _boat_order(db, "tools", 40000)
    r = recovery(db)
    assert r["vessel_spend_cents"] == 0
    assert r["recoverable_cents"] == 0
    assert r["tool_spend_cents"] == 40000
    assert r["tool_residual_cents"] == 20000


def test_unattributed_boat_spend_is_reported_not_dropped(db):
    """Boat spend with no system has no recovery rate. Silently omitting it
    would understate both the spend base and the sunk figure."""
    _boat_order(db, None, 13999)
    r = recovery(db)
    assert r["unattributed_cents"] == 13999
    assert r["unattributed_count"] == 1


def test_labor_is_recorded_not_estimated(db):
    """With no hours logged the value is zero. Estimating hours would
    manufacture return out of nothing."""
    empty = labor_avoided(db)
    assert empty["logged"] is False
    assert empty["value_cents"] == 0

    row = db.one("SELECT id FROM boat_systems WHERE key='energy_storage'")
    db.execute(
        "INSERT INTO labor_log (system_id, hours, logged_at) VALUES (?,?,?)",
        (row["id"], 10, utcnow()))
    logged = labor_avoided(db)
    assert logged["logged"] is True
    assert logged["hours"] == 10
    assert logged["value_cents"] == 10 * logged["rate_cents"]


def test_labor_rate_override_is_honoured(db):
    db.execute("INSERT INTO labor_log (hours, rate_cents, logged_at) VALUES (5, 5000, ?)",
               (utcnow(),))
    assert labor_avoided(db)["value_cents"] == 25000   # 5h x $50, not the default rate


def test_use_value_amortizes_sunk_cost_only(db):
    """Charging the recoverable portion against nights aboard would
    double-count: it is not consumed by using the boat."""
    db.execute("INSERT INTO usage_log (nights, logged_at) VALUES (10, ?)", (utcnow(),))
    result = use_value(db, sunk_cents=100000)
    assert result["nights"] == 10
    assert result["cost_per_night_cents"] == 10000

    # A larger total with the same sunk figure must not change cost per night.
    assert use_value(db, sunk_cents=100000)["cost_per_night_cents"] == 10000


def test_use_value_breakeven_and_remaining(db):
    alt = REWARD_ASSUMPTIONS["alternative_nightly_cost_dollars"][0] * 100
    sunk = int(alt * 40)
    db.execute("INSERT INTO usage_log (nights, logged_at) VALUES (15, ?)", (utcnow(),))
    result = use_value(db, sunk_cents=sunk)
    assert result["breakeven_nights"] == 40
    assert result["nights_remaining"] == 25

    db.execute("INSERT INTO usage_log (nights, logged_at) VALUES (30, ?)", (utcnow(),))
    assert use_value(db, sunk_cents=sunk)["nights_remaining"] == 0


def test_use_value_with_no_nights_logged_reports_no_rate(db):
    result = use_value(db, sunk_cents=100000)
    assert result["logged"] is False
    assert result["cost_per_night_cents"] is None
    assert result["breakeven_nights"] > 0


def test_capability_autonomy_bounds_are_ordered(db):
    """min must be the worse case. These were inverted on first write."""
    caps = capability(db)
    assert caps["days_with_ac_min"] <= caps["days_with_ac_max"]


def test_capability_reports_none_when_solar_covers_the_load(db):
    """`None` means autonomy is not battery-limited, which is a different
    statement from 'zero days' and must not be rendered as a number."""
    caps = capability(db)
    assert caps["days_without_ac"] is None


def test_reward_lenses_are_not_summed(db):
    """The report must not present a single combined 'total return' figure —
    the lenses measure different things and adding them would double-count."""
    report = reward_report(db)
    assert set(report) >= {"recovery", "labor", "capability", "use_value"}
    assert not any("total_return" in k or "combined" in k for k in report)


# --- review UI endpoints ----------------------------------------------------

@pytest.fixture
def client(db, monkeypatch):
    """TestClient wired to the temp database.

    Builds a fresh connection per call rather than sharing the fixture's,
    mirroring production: the server handles each request on a worker thread
    and SQLite connections cannot cross threads.
    """
    from fastapi.testclient import TestClient

    from opheliaskey.db.database import Database
    from opheliaskey.web import app as web

    def per_request():
        conn = Database(db.path)
        conn.migrate()
        return conn

    monkeypatch.setattr(web, "_db", per_request)
    return TestClient(web.app)


def _queued_item(db, description="Mystery bracket", cents=12345, system_key=None,
                 order_items=1):
    cur = db.execute(
        "INSERT INTO orders (source, external_order_id, status, total_cents, ordered_at, "
        "created_at, updated_at) VALUES ('test',?,'delivered',?, '2026-05-01T00:00:00Z',?,?)",
        (f"rv{db.one('SELECT COUNT(*) c FROM orders')['c']}", cents, utcnow(), utcnow()))
    order_id = int(cur.lastrowid)
    sys_id = None
    if system_key:
        sys_id = db.one("SELECT id FROM boat_systems WHERE key=?", (system_key,))["id"]
    ids = []
    for n in range(order_items):
        c = db.execute(
            "INSERT INTO line_items (order_id, line_no, description, quantity, total_cents, "
            "system_id) VALUES (?,?,?,1,?,?)",
            (order_id, n, f"{description} {n}" if order_items > 1 else description,
             cents // order_items, sys_id))
        ids.append(int(c.lastrowid))
    return order_id, ids


def test_review_queue_lists_undecided_items(client, db):
    _queued_item(db)
    data = client.get("/api/review/queue").json()
    assert data["remaining"] == 1
    assert data["items"][0]["description"] == "Mystery bracket"


def test_decide_marks_manual_and_clears_from_queue(client, db):
    _, ids = _queued_item(db)
    res = client.post("/api/review/decide", json={
        "item_id": ids[0], "relevance": "boat", "system_key": "deck_hardware"})
    assert res.status_code == 200
    assert res.json()["remaining"] == 0

    row = db.one("SELECT relevance, relevance_by, classified_by FROM line_items WHERE id=?",
                 (ids[0],))
    assert row["relevance"] == "boat"
    assert row["relevance_by"] == "manual"
    assert row["classified_by"] == "manual"


def test_boat_without_a_system_stays_in_the_queue(client, db):
    """Relevance alone does not finish the job — the item still needs a system,
    and the queue must keep surfacing it until it has one."""
    _, ids = _queued_item(db)
    res = client.post("/api/review/decide", json={"item_id": ids[0], "relevance": "boat"})
    assert res.json()["remaining"] == 1


def test_personal_needs_no_system(client, db):
    _, ids = _queued_item(db)
    assert client.post("/api/review/decide",
                       json={"item_id": ids[0], "relevance": "personal"}).json()["remaining"] == 0


def test_undo_restores_the_exact_prior_state(client, db):
    """Restoring only the value would leave a spurious 'manual' marker and
    freeze the item against future re-classification."""
    _, ids = _queued_item(db, system_key="deck_hardware")
    db.execute("UPDATE line_items SET classified_by='rule', classify_conf=0.8 WHERE id=?",
               (ids[0],))

    previous = client.post("/api/review/decide", json={
        "item_id": ids[0], "relevance": "boat", "system_key": "interior"}).json()["previous"]
    client.post("/api/review/restore", json=previous)

    row = db.one("""SELECT relevance, relevance_by, system_id, classified_by, classify_conf
                    FROM line_items WHERE id=?""", (ids[0],))
    assert row["relevance"] is None
    assert row["relevance_by"] is None
    assert row["classified_by"] == "rule"       # not left as 'manual'
    assert row["classify_conf"] == 0.8
    assert row["system_id"] == db.one(
        "SELECT id FROM boat_systems WHERE key='deck_hardware'")["id"]


def test_apply_to_order_touches_only_unresolved_siblings(client, db):
    """A sibling already decided by hand must not be swept up by a bulk call."""
    _, ids = _queued_item(db, cents=30000, order_items=3)
    db.execute("UPDATE line_items SET relevance='personal', relevance_by='manual' WHERE id=?",
               (ids[2],))

    res = client.post("/api/review/decide", json={
        "item_id": ids[0], "relevance": "boat", "system_key": "interior",
        "apply_to_order": True}).json()
    assert res["count"] == 2                     # the third was already resolved

    untouched = db.one("SELECT relevance FROM line_items WHERE id=?", (ids[2],))
    assert untouched["relevance"] == "personal"


def test_decide_rejects_bad_input(client, db):
    _, ids = _queued_item(db)
    assert client.post("/api/review/decide",
                       json={"item_id": ids[0], "relevance": "maybe"}).status_code == 400
    assert client.post("/api/review/decide",
                       json={"item_id": ids[0], "relevance": "boat",
                             "system_key": "nonexistent"}).status_code == 400
    assert client.post("/api/review/decide",
                       json={"item_id": 999999, "relevance": "boat"}).status_code == 404


def test_mutating_endpoints_reject_cross_site_requests(client, db):
    """The dashboard binds to localhost, but any page in the browser can POST
    there. These endpoints mutate the ledger."""
    _, ids = _queued_item(db)
    body = {"item_id": ids[0], "relevance": "boat"}
    assert client.post("/api/review/decide", json=body,
                       headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/review/decide", json=body,
                       headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    # Same-origin still works.
    assert client.post("/api/review/decide", json=body,
                       headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_review_page_renders_with_an_empty_queue(client, db):
    res = client.get("/review")
    assert res.status_code == 200
    assert client.get("/api/review/queue").json()["remaining"] == 0


# --- Amazon order emails ----------------------------------------------------
#
# Structure taken from real confirmation mail; contents are neutral so no
# personal purchase history is committed.

from opheliaskey.parsing.vendors.amazon_email import parse_amazon_text  # noqa: E402

AMAZON_MULTI = """Your Orders

Thanks for your order!

Order #
111-1111111-1111111

View or edit order
https://www.amazon.com/your-orders/order-details?orderID=111-1111111-1111111

* Widget Alpha, Extended Description That Runs On At Some Length
  Quantity: 2
  154.95 USD

* Widget Beta
  Quantity: 1
  49.99 USD

Grand Total:
386.98 USD

Email delivery

Order #
222-2222222-2222222

* Service Plan Gamma
  Quantity: 3
  10.00 USD

Grand Total:
30.00 USD

Arriving Monday

Order #
333-3333333-3333333

* Widget Delta
  Quantity: 1
  49.99 USD

Grand Total:
53.49 USD

(c)2026 Amazon.com, Inc.
"""


def test_amazon_email_yields_every_order_it_contains():
    """A single confirmation routinely carries several order numbers. Returning
    only the first silently drops the rest — real mail had three in one message
    and $119.46 would have vanished."""
    orders = parse_amazon_text(AMAZON_MULTI)
    assert len(orders) == 3
    assert [o.external_order_id for o in orders] == [
        "111-1111111-1111111", "222-2222222-2222222", "333-3333333-3333333"]
    assert sum(o.total_cents for o in orders) == 38698 + 3000 + 5349


def test_amazon_line_price_is_unit_price_times_quantity():
    """Amazon prints the unit price beside the quantity. Reading it as the line
    total understates the order by the quantity multiple."""
    first = parse_amazon_text(AMAZON_MULTI)[0]
    assert first.items[0].quantity == 2
    assert first.items[0].unit_price_cents == 15495
    assert first.items[0].total_cents == 30990

    third = parse_amazon_text(AMAZON_MULTI)[1]
    assert third.items[0].total_cents == 3000    # 3 x $10.00


def test_amazon_tax_is_derived_from_the_grand_total_gap():
    """Tax never appears as a line. Deriving it keeps the order internally
    consistent instead of leaving an unexplained coverage gap."""
    orders = parse_amazon_text(AMAZON_MULTI)
    assert orders[0].subtotal_cents == 30990 + 4999
    assert orders[0].tax_cents == 38698 - (30990 + 4999)
    # A order whose lines already equal the total has no tax to derive.
    assert orders[1].tax_cents is None


def test_amazon_wrapped_item_names_are_collapsed():
    wrapped = """Order #
444-4444444-4444444

* Widget With A Name
  That Wrapped Across Lines
  Quantity: 1
  20.00 USD

Grand Total:
20.00 USD
"""
    item = parse_amazon_text(wrapped)[0].items[0]
    assert item.description == "Widget With A Name That Wrapped Across Lines"


def test_amazon_route_used_for_amazon_senders():
    """The Amazon parser must win over the generic paths for Amazon mail, since
    only it handles multiple orders per message."""
    raw = (
        b"From: auto-confirm@amazon.com\nSubject: Ordered\n"
        b"Date: Fri, 21 Aug 2026 02:04:27 +0000\n"
        b"Content-Type: text/plain; charset=utf-8\n\n"
        + AMAZON_MULTI.encode()
    )
    orders = parse_email(raw)
    assert len(orders) == 3
    assert all(o.method == "amazon_email" for o in orders)
    assert all(o.vendor_domain == "amazon.com" for o in orders)


def test_registry_persists_every_order_from_one_email(db):
    """The parse stage must write all of them, not just the first."""
    from opheliaskey.parsing.registry import parse_pending

    raw = (
        b"From: auto-confirm@amazon.com\nSubject: Ordered\n"
        b"Date: Fri, 21 Aug 2026 02:04:27 +0000\n"
        b"Content-Type: text/plain; charset=utf-8\n\n"
        + AMAZON_MULTI.encode()
    )
    db.store_raw("gmail", "msg-multi", raw, content_type="message/rfc822")
    stats = parse_pending(db)
    assert stats["parsed"] == 1

    orders = db.query("SELECT external_order_id, total_cents FROM orders ORDER BY 1")
    assert len(orders) == 3
    assert sum(o["total_cents"] for o in orders) == 38698 + 3000 + 5349
    assert len(db.query("SELECT id FROM line_items")) == 4


# --- quoted threads and attachment-only invoices ----------------------------

from opheliaskey.analysis.risk import unpriced_invoices  # noqa: E402
from opheliaskey.parsing.email_parser import strip_quoted, unparsed_reason  # noqa: E402


def test_quoted_reply_history_is_not_scanned_for_totals():
    """Vendor threads carry superseded numbers — an estimate that was later
    revised. Scanning the quoted history picks the wrong one."""
    raw = (
        b"From: sales@example.com\nSubject: Invoice\n"
        b"Date: Tue, 11 Aug 2026 19:40:19 +0000\n"
        b"Content-Type: text/plain; charset=utf-8\n\n"
        b"Order Number: NEW12345\nOrder Total: $285.00\n\n"
        b"On Wed, Aug 5, 2026 at 3:19 PM Cindy wrote:\n"
        b"Order Number: OLD99999\nOrder Total: $570.00\n"
    )
    orders = parse_email(raw)
    assert len(orders) == 1
    assert orders[0].external_order_id == "NEW12345"
    assert orders[0].total_cents == 28500      # not the superseded $570


@pytest.mark.parametrize("body", [
    "Current total $10.00\n> quoted total $99.00",
    "Current total $10.00\nOn Mon, Aug 10, 2026 at 9:33 AM Cindy wrote:\nold $99.00",
    "Current total $10.00\n-----Original Message-----\nold $99.00",
    "Current total $10.00\nFrom: someone@x.com\nSent: Monday\nTo: me@y.com\nold $99.00",
])
def test_strip_quoted_handles_common_reply_markers(body):
    assert "99.00" not in strip_quoted(body)
    assert "10.00" in strip_quoted(body)


def _with_pdf(subject: str) -> bytes:
    return (
        f"From: sales@signsbytomorrow.com\nSubject: {subject}\n"
        "Date: Tue, 11 Aug 2026 19:40:19 +0000\n"
        'Content-Type: multipart/mixed; boundary="B"\n\n'
        "--B\nContent-Type: text/plain; charset=utf-8\n\n"
        "Attached is the invoice for your recent order.\n\n"
        "--B\nContent-Type: application/pdf\n"
        'Content-Disposition: attachment; filename="Invoice__SBT_.pdf"\n\n'
        "%PDF-1.4 fake\n--B--\n"
    ).encode()


def test_attachment_only_invoice_is_named_not_dismissed():
    """An invoice priced only in a PDF is real spend the parser cannot read.
    Calling it 'not an order email' would file it under noise."""
    raw = _with_pdf("Invoice/Production: Boat Decal")
    assert parse_email(raw) == []
    reason = unparsed_reason(raw)
    assert "attachment" in reason
    assert "Invoice__SBT_.pdf" in reason


def test_ordinary_non_order_mail_still_reads_as_noise():
    msg = b"From: news@example.com\nSubject: Newsletter\n\nBoats are nice.\n"
    assert unparsed_reason(msg) == "not an order email"


def test_unpriced_invoices_surface_as_a_risk_finding(db):
    from opheliaskey.parsing.registry import parse_pending

    db.store_raw("gmail", "sbt-1", _with_pdf("Invoice/Production: Boat Decal"),
                 content_type="message/rfc822")
    parse_pending(db)

    row = db.one("SELECT parse_error FROM raw_documents WHERE external_id='sbt-1'")
    assert "Invoice__SBT_.pdf" in row["parse_error"]

    findings = unpriced_invoices(db)
    assert len(findings) == 1
    assert findings[0]["code"] == "unpriced_invoice"


@pytest.mark.parametrize("text,expected", [
    ("Order Number: NEW12345", "NEW12345"),
    ("Order Confirmation ABC123456", "ABC123456"),
    ("Order #112-4886390-4634637", "112-4886390-4634637"),
    ("Invoice 4163842538", "4163842538"),
    ("Confirmation Number: XY9081726", "XY9081726"),
])
def test_order_number_regex_extracts_the_id_not_the_label(text, expected):
    """Under re.I the character class matched letters, so 'Order Number: X'
    captured the literal word 'Number'. Requiring a digit in the token and
    matching longer labels first fixes it."""
    from opheliaskey.parsing.email_parser import ORDER_NO_RE

    assert ORDER_NO_RE.search(text).group(1) == expected


def test_order_number_regex_declines_when_there_is_no_id():
    from opheliaskey.parsing.email_parser import ORDER_NO_RE

    assert ORDER_NO_RE.search("Your order Number is pending") is None
