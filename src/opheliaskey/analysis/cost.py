"""Cost analysis.

Two rules govern every figure here:

  * **Project spend is line-item based and relevance-gated.** The account is
    mixed, so summing order totals would count sandals and takeout as refit
    spend. Only line items marked `relevance='boat'` count toward the project.
  * **Everything is net of refunds.** Gross spend on a project with returns is a
    number that flatters and misleads.

Spend that has not been reviewed yet is never silently folded into either side.
It is reported as its own figure so the project total always carries its own
error bar.
"""

from __future__ import annotations

from ..db.database import Database

BOAT = "relevance = 'boat'"
PERSONAL = "relevance = 'personal'"
UNREVIEWED = "(relevance IS NULL OR relevance = 'ambiguous')"


def _scalar(db: Database, sql: str, params=()) -> int:
    row = db.one(sql, params)
    return int(row[0] or 0) if row else 0


def totals(db: Database) -> dict:
    boat = _scalar(db, f"SELECT SUM(total_cents) FROM line_items WHERE {BOAT}")
    personal = _scalar(db, f"SELECT SUM(total_cents) FROM line_items WHERE {PERSONAL}")
    unreviewed = _scalar(db, f"SELECT SUM(total_cents) FROM line_items WHERE {UNREVIEWED}")

    refunded = _scalar(
        db,
        """SELECT SUM(r.amount_cents) FROM refunds r
           WHERE r.status='completed' AND (
             r.line_item_id IN (SELECT id FROM line_items WHERE relevance='boat')
             OR (r.line_item_id IS NULL AND r.order_id IN (
                   SELECT DISTINCT order_id FROM line_items WHERE relevance='boat')))""",
    )
    capital = _scalar(
        db,
        f"""SELECT SUM(li.total_cents) FROM line_items li
            JOIN boat_systems bs ON bs.id = li.system_id
            WHERE bs.is_capital = 1 AND li.{BOAT}""",
    )
    consumable = _scalar(
        db,
        f"""SELECT SUM(li.total_cents) FROM line_items li
            JOIN boat_systems bs ON bs.id = li.system_id
            WHERE bs.is_capital = 0 AND li.{BOAT}""",
    )
    unattributed = _scalar(
        db, f"SELECT SUM(total_cents) FROM line_items WHERE system_id IS NULL AND {BOAT}"
    )
    return {
        "project_gross_cents": boat,
        "refunded_cents": refunded,
        "net_cents": boat - refunded,
        "personal_cents": personal,
        "unreviewed_cents": unreviewed,
        "capital_cents": capital,
        "consumable_cents": consumable,
        "unattributed_cents": unattributed,
        "order_count": _scalar(db, "SELECT COUNT(*) FROM orders"),
        "item_count": _scalar(db, "SELECT COUNT(*) FROM line_items"),
        "boat_item_count": _scalar(db, f"SELECT COUNT(*) FROM line_items WHERE {BOAT}"),
        "unreviewed_count": _scalar(db, f"SELECT COUNT(*) FROM line_items WHERE {UNREVIEWED}"),
    }


def by_system(db: Database) -> list[dict]:
    rows = db.query(
        f"""SELECT bs.key, bs.name, bs.is_capital,
                   COUNT(li.id)                     AS items,
                   COALESCE(SUM(li.total_cents), 0) AS spend_cents,
                   (SELECT planned_cents FROM budget_lines b WHERE b.system_id = bs.id
                    LIMIT 1)                        AS planned_cents
            FROM boat_systems bs
            LEFT JOIN line_items li ON li.system_id = bs.id AND li.{BOAT}
            GROUP BY bs.id
            HAVING items > 0 OR planned_cents IS NOT NULL
            ORDER BY spend_cents DESC"""
    )
    out = []
    for r in rows:
        planned, spend = r["planned_cents"], r["spend_cents"]
        out.append({
            "key": r["key"], "name": r["name"], "is_capital": bool(r["is_capital"]),
            "items": r["items"], "spend_cents": spend, "planned_cents": planned,
            "variance_cents": (spend - planned) if planned is not None else None,
            "pct_of_plan": round(spend / planned * 100, 1) if planned else None,
        })
    return out


def by_vendor(db: Database, limit: int = 25) -> list[dict]:
    rows = db.query(
        f"""SELECT COALESCE(v.canonical_name, '(unattributed)') AS vendor,
                   COUNT(DISTINCT o.id)             AS orders,
                   COALESCE(SUM(li.total_cents), 0) AS spend_cents
            FROM line_items li
            JOIN orders o ON o.id = li.order_id
            LEFT JOIN vendors v ON v.id = o.vendor_id
            WHERE li.{BOAT} AND o.status != 'cancelled'
            GROUP BY v.id ORDER BY spend_cents DESC LIMIT ?""",
        (limit,),
    )
    return [dict(r) for r in rows]


def by_month(db: Database) -> list[dict]:
    rows = db.query(
        f"""SELECT substr(o.ordered_at, 1, 7)        AS month,
                   COUNT(DISTINCT o.id)             AS orders,
                   COALESCE(SUM(li.total_cents), 0) AS spend_cents
            FROM line_items li
            JOIN orders o ON o.id = li.order_id
            WHERE li.{BOAT} AND o.ordered_at IS NOT NULL AND o.status != 'cancelled'
            GROUP BY month ORDER BY month"""
    )
    return [dict(r) for r in rows]


def cost_report(db: Database) -> dict:
    months = by_month(db)
    # Trailing three months is the number that actually predicts when the money
    # runs out; a lifetime average understates a project that is accelerating.
    recent = months[-3:]
    burn = sum(m["spend_cents"] for m in recent) // max(len(recent), 1) if recent else 0
    return {
        "totals": totals(db),
        "by_system": by_system(db),
        "by_vendor": by_vendor(db),
        "by_month": months,
        "monthly_burn_cents": burn,
    }
