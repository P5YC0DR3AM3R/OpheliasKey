"""Match orders to the bank transactions that paid for them.

Deliberately conservative: an ambiguous match (several transactions with the
same amount in the same week) is left unmatched rather than guessed, because a
wrong link corrupts both the 'unreconciled orders' and 'spend without receipt'
signals at once.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from ..db.database import Database, utcnow

DATE_WINDOW_DAYS = 7
AMOUNT_TOLERANCE_CENTS = 100  # tax/shipping rounding between confirmation and settlement


def _date(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value[: len(fmt) + 2].rstrip("Z")[:19], fmt.rstrip("Z"))
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", ""))
    except ValueError:
        return None


def reconcile(db: Database, *, window_days: int = DATE_WINDOW_DAYS) -> dict:
    orders = db.query(
        """SELECT o.id, o.total_cents, o.ordered_at, o.vendor_id FROM orders o
           LEFT JOIN reconciliations rc ON rc.order_id = o.id
           WHERE rc.id IS NULL AND o.status != 'cancelled' AND o.total_cents > 0"""
    )
    stats = {"examined": len(orders), "matched": 0, "ambiguous": 0, "unmatched": 0}

    with db.tx():
        for order in orders:
            order_date = _date(order["ordered_at"])
            if order_date is None:
                stats["unmatched"] += 1
                continue

            low = (order_date - timedelta(days=window_days)).strftime("%Y-%m-%d")
            high = (order_date + timedelta(days=window_days)).strftime("%Y-%m-%d")

            candidates = db.query(
                """SELECT t.id, t.amount_cents, t.posted_at, t.vendor_id FROM transactions t
                   LEFT JOIN reconciliations rc ON rc.transaction_id = t.id
                   WHERE rc.id IS NULL AND t.pending = 0
                     AND ABS(t.amount_cents - ?) <= ?
                     AND date(t.posted_at) BETWEEN ? AND ?""",
                (order["total_cents"], AMOUNT_TOLERANCE_CENTS, low, high),
            )
            if not candidates:
                stats["unmatched"] += 1
                continue

            # A vendor agreement promotes a candidate above amount-only matches.
            same_vendor = [
                c for c in candidates
                if order["vendor_id"] and c["vendor_id"] == order["vendor_id"]
            ]
            pool = same_vendor or candidates

            if len(pool) > 1:
                stats["ambiguous"] += 1
                continue

            match = pool[0]
            exact = match["amount_cents"] == order["total_cents"]
            if same_vendor and exact:
                confidence, method = 0.98, "exact"
            elif exact:
                confidence, method = 0.85, "amount_date"
            else:
                confidence, method = 0.7, "amount_date"

            db.execute(
                """INSERT OR IGNORE INTO reconciliations
                     (order_id, transaction_id, confidence, method, matched_at)
                   VALUES (?,?,?,?,?)""",
                (order["id"], match["id"], confidence, method, utcnow()),
            )
            stats["matched"] += 1
    return stats
