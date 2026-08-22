"""Risk analysis.

Each finding is a concrete, checkable condition with a dollar figure attached.
Vague warnings are useless; 'a $412 return window closes in 6 days' is not.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..db.database import Database
from .cost import by_system, totals
from .spec import spec_report

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _finding(severity: str, code: str, title: str, detail: str, amount_cents: int | None = None):
    return {
        "severity": severity,
        "code": code,
        "title": title,
        "detail": detail,
        "amount_cents": amount_cents,
    }


def budget_overruns(db: Database) -> list[dict]:
    out = []
    for row in by_system(db):
        if row["planned_cents"] and row["variance_cents"] and row["variance_cents"] > 0:
            over = row["variance_cents"]
            pct = row["pct_of_plan"]
            severity = "high" if pct and pct >= 150 else "medium"
            out.append(
                _finding(
                    severity,
                    "budget_overrun",
                    f"{row['name']} is over budget",
                    f"Spent {pct}% of the ${row['planned_cents']/100:,.0f} plan.",
                    over,
                )
            )
    return out


def expiring_windows(db: Database, days: int = 30) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = db.query(
        """SELECT iw.window_kind, iw.expires_at, li.description, li.total_cents
           FROM item_windows iw JOIN line_items li ON li.id = iw.line_item_id
           WHERE iw.expires_at <= ? AND iw.expires_at >= ?
           ORDER BY iw.expires_at""",
        (cutoff, today),
    )
    return [
        _finding(
            "high" if r["total_cents"] and r["total_cents"] > 20000 else "medium",
            "window_expiring",
            f"{r['window_kind'].title()} window closing: {r['description'][:60]}",
            f"Expires {r['expires_at']}.",
            r["total_cents"],
        )
        for r in rows
    ]


def unresolved_refunds(db: Database) -> list[dict]:
    rows = db.query(
        """SELECT r.amount_cents, r.occurred_at, r.status, o.external_order_id
           FROM refunds r LEFT JOIN orders o ON o.id = r.order_id
           WHERE r.status IS NOT NULL AND r.status != 'completed'"""
    )
    return [
        _finding(
            "medium",
            "refund_outstanding",
            f"Refund not yet received on order {r['external_order_id'] or '?'}",
            f"Status '{r['status']}' since {r['occurred_at'] or 'unknown date'}.",
            r["amount_cents"],
        )
        for r in rows
    ]


def unreconciled_spend(db: Database) -> list[dict]:
    """Orders with no matching bank transaction, and charges with no order.

    Both directions matter: the first can mean a charge that never posted, the
    second is usually yard labor or a cash purchase with no email trail — real
    project spend that would otherwise be invisible."""
    orphan_orders = db.one(
        """SELECT COUNT(*) AS n, COALESCE(SUM(o.total_cents),0) AS amt FROM orders o
           LEFT JOIN reconciliations rc ON rc.order_id = o.id
           WHERE rc.id IS NULL AND o.status != 'cancelled'"""
    )
    orphan_txns = db.one(
        """SELECT COUNT(*) AS n, COALESCE(SUM(t.amount_cents),0) AS amt FROM transactions t
           LEFT JOIN reconciliations rc ON rc.transaction_id = t.id
           WHERE rc.id IS NULL AND t.pending = 0 AND t.amount_cents > 0"""
    )
    out = []
    if orphan_orders and orphan_orders["n"]:
        out.append(
            _finding(
                "low",
                "orders_unreconciled",
                f"{orphan_orders['n']} orders not matched to a bank transaction",
                "Either the charge has not posted, or Plaid has not been connected yet.",
                orphan_orders["amt"],
            )
        )
    if orphan_txns and orphan_txns["n"]:
        out.append(
            _finding(
                "medium",
                "spend_without_receipt",
                f"{orphan_txns['n']} charges have no matching order",
                "Likely yard labor, cash purchases, or vendors that do not email receipts. "
                "This is real project spend with no itemization behind it.",
                orphan_txns["amt"],
            )
        )
    return out


def vendor_concentration(db: Database, threshold: float = 0.5) -> list[dict]:
    from .cost import by_vendor

    vendors = by_vendor(db, limit=100)
    total = sum(v["spend_cents"] for v in vendors)
    if not total:
        return []
    top = vendors[0]
    share = top["spend_cents"] / total
    if share < threshold:
        return []
    return [
        _finding(
            "low",
            "vendor_concentration",
            f"{round(share*100)}% of spend is with {top['vendor']}",
            "Single-supplier dependence on lead times, pricing and returns policy.",
            top["spend_cents"],
        )
    ]


def unclassified_spend(db: Database) -> list[dict]:
    row = db.one(
        """SELECT COUNT(*) AS n, COALESCE(SUM(total_cents),0) AS amt
           FROM line_items WHERE system_id IS NULL AND relevance = 'boat'"""
    )
    if not row or not row["n"]:
        return []
    return [
        _finding(
            "medium",
            "unclassified",
            f"{row['n']} line item{'s are' if row['n'] != 1 else ' is'} unclassified",
            "Every system-level total below understates reality until these are attributed.",
            row["amt"],
        )
    ]


def line_item_coverage(db: Database, tolerance_cents: int = 100) -> list[dict]:
    """Orders whose line items do not account for the order total.

    Real order emails often carry a total with no itemization behind it, or
    itemization that omits tax and shipping. Wherever that happens, every
    per-system figure silently understates true spend — so it is surfaced as a
    finding rather than absorbed quietly."""
    row = db.one(
        """SELECT COUNT(*) AS n, COALESCE(SUM(gap), 0) AS amt FROM (
             SELECT o.total_cents
                    - COALESCE((SELECT SUM(li.total_cents) FROM line_items li
                                WHERE li.order_id = o.id), 0)
                    - COALESCE(o.tax_cents, 0)
                    - COALESCE(o.shipping_cents, 0)
                    + COALESCE(o.discount_cents, 0) AS gap
             FROM orders o WHERE o.status != 'cancelled'
           ) WHERE ABS(gap) > ?""",
        (tolerance_cents,),
    )
    if not row or not row["n"]:
        return []
    return [
        _finding(
            "medium",
            "coverage_gap",
            f"{row['n']} order{'s are' if row['n'] != 1 else ' is'} not fully itemized",
            "Order totals exceed the sum of their line items, tax and shipping. "
            "System-level spend is understated by this amount.",
            row["amt"],
        )
    ]


def unreviewed_spend(db: Database) -> list[dict]:
    """Line items whose project relevance is still undecided.

    This is the project total's error bar. Until it is cleared, every figure
    below could move by this much in either direction."""
    row = db.one(
        """SELECT COUNT(*) AS n, COALESCE(SUM(total_cents), 0) AS amt
           FROM line_items WHERE relevance IS NULL OR relevance = 'ambiguous'"""
    )
    if not row or not row["n"]:
        return []
    return [
        _finding(
            "high" if row["amt"] > 100000 else "medium",
            "unreviewed_relevance",
            f"{row['n']} line item{'s' if row['n'] != 1 else ''} not yet confirmed boat or personal",
            "Project spend could move by this amount in either direction. "
            "Clear the queue with `okey review`.",
            row["amt"],
        )
    ]


def unpriced_invoices(db: Database) -> list[dict]:
    """Invoice emails whose amount lives only in an attachment.

    These are real project spend the parser cannot read. Counting them as
    'not an order email' would file genuine expenditure under noise, so they
    are surfaced for manual entry instead."""
    rows = db.query(
        """SELECT COUNT(*) AS n FROM raw_documents
           WHERE parse_error LIKE '%attachment%'"""
    )
    count = int(rows[0]["n"]) if rows else 0
    if not count:
        return []
    return [
        _finding(
            "medium",
            "unpriced_invoice",
            f"{count} invoice email{'s' if count != 1 else ''} priced only in an attachment",
            "The amount is inside a PDF, so the pipeline cannot read it. This is real "
            "spend missing from every total until it is entered by hand. "
            "List them with `okey report unpriced`.",
            None,
        )
    ]


def refit_against_hull_value(db: Database) -> list[dict]:
    """Improvement spend measured against what the boat cost.

    The classic way a boat project goes wrong is not any single overrun — it is
    the total quietly passing the hull's value while each invoice still looks
    reasonable on its own. Stating the ratio makes that visible early."""
    row = db.one(
        """SELECT COALESCE(SUM(CASE WHEN bs.key = 'vessel_acquisition'
                                    THEN li.total_cents ELSE 0 END), 0) AS hull,
                  COALESCE(SUM(CASE WHEN bs.key != 'vessel_acquisition'
                                    THEN li.total_cents ELSE 0 END), 0) AS refit
           FROM line_items li
           JOIN orders o ON o.id = li.order_id
           LEFT JOIN boat_systems bs ON bs.id = li.system_id
           WHERE li.relevance = 'boat' AND o.status != 'cancelled'"""
    )
    hull = int(row["hull"]) if row else 0
    refit = int(row["refit"]) if row else 0
    if not hull or not refit:
        return []

    ratio = refit / hull
    if ratio < 0.5:
        return []
    if ratio >= 1.0:
        severity, verb = "high", "exceeds"
    elif ratio >= 0.75:
        severity, verb = "medium", "approaches"
    else:
        severity, verb = "low", "is a large fraction of"

    return [
        _finding(
            severity,
            "refit_vs_hull_value",
            f"Improvement spend {verb} the purchase price",
            f"{ratio*100:.0f}% of the {hull/100:,.0f} dollar hull value has been spent on "
            f"improvements. Combined outlay is ${(hull + refit)/100:,.0f}. Refit spend does "
            f"not return dollar-for-dollar at resale, so the boat is very unlikely to be "
            f"worth the combined figure — see `okey report reward` for the recoverable "
            f"estimate.",
            refit,
        )
    ]


def risk_report(db: Database) -> dict:
    findings = (
        budget_overruns(db)
        + expiring_windows(db)
        + unresolved_refunds(db)
        + unreconciled_spend(db)
        + vendor_concentration(db)
        + unclassified_spend(db)
        + line_item_coverage(db)
        + unreviewed_spend(db)
        + unpriced_invoices(db)
        + refit_against_hull_value(db)
    )
    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), -(f["amount_cents"] or 0)))

    t = totals(db)
    planned = db.one("SELECT COALESCE(SUM(planned_cents),0) AS p FROM budget_lines")
    planned_total = int(planned["p"]) if planned else 0
    # Spec findings are kept in their own list rather than merged. They answer
    # a different question ("will this work") from a different data source (the
    # installed specification, not receipts), and blending them would let an
    # engineering constraint read as a spending problem.
    spec = spec_report(db)

    return {
        "findings": findings,
        "spec_findings": spec["findings"],
        "spec_counts": spec["counts"],
        "counts": {
            s: sum(1 for f in findings if f["severity"] == s) for s in ("high", "medium", "low")
        },
        "budget_total_cents": planned_total,
        "net_spend_cents": t["net_cents"],
        "remaining_cents": planned_total - t["net_cents"] if planned_total else None,
    }
