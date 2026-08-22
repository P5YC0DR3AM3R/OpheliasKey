"""Cost analysis.

Everything here reports *net* spend — gross purchases less refunds — because
gross spend on a project with returns is a number that flatters and misleads.
"""

from __future__ import annotations

from ..db.database import Database


def _scalar(db: Database, sql: str, params=()) -> int:
    row = db.one(sql, params)
    return int(row[0] or 0) if row else 0


def totals(db: Database) -> dict:
    gross = _scalar(db, "SELECT SUM(total_cents) FROM orders WHERE status != 'cancelled'")
    refunded = _scalar(db, "SELECT SUM(amount_cents) FROM refunds WHERE status='completed'")
    capital = _scalar(
        db,
        """SELECT SUM(li.total_cents) FROM line_items li
           JOIN boat_systems bs ON bs.id = li.system_id WHERE bs.is_capital = 1""",
    )
    consumable = _scalar(
        db,
        """SELECT SUM(li.total_cents) FROM line_items li
           JOIN boat_systems bs ON bs.id = li.system_id WHERE bs.is_capital = 0""",
    )
    unattributed = _scalar(
        db, "SELECT SUM(total_cents) FROM line_items WHERE system_id IS NULL"
    )
    return {
        "gross_cents": gross,
        "unattributed_cents": unattributed,
        "refunded_cents": refunded,
        "net_cents": gross - refunded,
        "capital_cents": capital,
        "consumable_cents": consumable,
        "order_count": _scalar(db, "SELECT COUNT(*) FROM orders"),
        "item_count": _scalar(db, "SELECT COUNT(*) FROM line_items"),
    }


def by_system(db: Database) -> list[dict]:
    rows = db.query(
        """SELECT bs.key, bs.name, bs.is_capital,
                  COUNT(li.id)                       AS items,
                  COALESCE(SUM(li.total_cents), 0)   AS spend_cents,
                  (SELECT planned_cents FROM budget_lines b WHERE b.system_id = bs.id
                   LIMIT 1)                          AS planned_cents
           FROM boat_systems bs
           LEFT JOIN line_items li ON li.system_id = bs.id
           GROUP BY bs.id
           HAVING items > 0 OR planned_cents IS NOT NULL
           ORDER BY spend_cents DESC"""
    )
    out = []
    for r in rows:
        planned = r["planned_cents"]
        spend = r["spend_cents"]
        out.append(
            {
                "key": r["key"],
                "name": r["name"],
                "is_capital": bool(r["is_capital"]),
                "items": r["items"],
                "spend_cents": spend,
                "planned_cents": planned,
                "variance_cents": (spend - planned) if planned is not None else None,
                "pct_of_plan": round(spend / planned * 100, 1) if planned else None,
            }
        )
    return out


def by_vendor(db: Database, limit: int = 25) -> list[dict]:
    rows = db.query(
        """SELECT COALESCE(v.canonical_name, '(unattributed)') AS vendor,
                  COUNT(o.id)                     AS orders,
                  COALESCE(SUM(o.total_cents), 0) AS spend_cents
           FROM orders o LEFT JOIN vendors v ON v.id = o.vendor_id
           WHERE o.status != 'cancelled'
           GROUP BY v.id ORDER BY spend_cents DESC LIMIT ?""",
        (limit,),
    )
    return [dict(r) for r in rows]


def by_month(db: Database) -> list[dict]:
    rows = db.query(
        """SELECT substr(ordered_at, 1, 7) AS month,
                  COUNT(*)                 AS orders,
                  SUM(total_cents)         AS spend_cents
           FROM orders
           WHERE ordered_at IS NOT NULL AND status != 'cancelled'
           GROUP BY month ORDER BY month"""
    )
    return [dict(r) for r in rows]


def cost_report(db: Database) -> dict:
    months = by_month(db)
    # Burn rate over the trailing three months is the number that actually
    # predicts when the money runs out.
    recent = months[-3:]
    burn = sum(m["spend_cents"] for m in recent) // max(len(recent), 1) if recent else 0
    return {
        "totals": totals(db),
        "by_system": by_system(db),
        "by_vendor": by_vendor(db),
        "by_month": months,
        "monthly_burn_cents": burn,
    }
