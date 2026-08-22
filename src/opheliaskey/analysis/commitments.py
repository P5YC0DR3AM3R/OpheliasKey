"""Committed work — authorized or scheduled, not yet invoiced.

Spend answers "what has this cost". It cannot answer "what will it cost", and
on a live refit that is the more urgent question. Committed work is tracked
separately from orders so it can never leak into spend totals, and an unknown
estimate stays NULL rather than becoming zero — a commitment with no price is
unpriced, not free.
"""

from __future__ import annotations

from ..db.database import Database


def open_commitments(db: Database, vessel: str | None = None) -> list[dict]:
    params: list = []
    clause = ""
    if vessel:
        clause = " AND (c.vessel IS NULL OR c.vessel = ?)"
        params.append(vessel)
    rows = db.query(
        f"""SELECT c.id, c.description, c.estimate_cents, c.scheduled_for, c.reference,
                   c.note, v.canonical_name AS vendor, bs.name AS system_name,
                   bs.key AS system_key
            FROM commitments c
            LEFT JOIN vendors v ON v.id = c.vendor_id
            LEFT JOIN boat_systems bs ON bs.id = c.system_id
            WHERE c.status = 'open'{clause}
            ORDER BY COALESCE(c.scheduled_for, '9999'), c.estimate_cents DESC""",
        params,
    )
    return [dict(r) for r in rows]


def commitment_summary(db: Database, vessel: str | None = None) -> dict:
    items = open_commitments(db, vessel)
    priced = [i for i in items if i["estimate_cents"] is not None]
    unpriced = [i for i in items if i["estimate_cents"] is None]
    return {
        "items": items,
        "count": len(items),
        "estimated_cents": sum(i["estimate_cents"] for i in priced),
        "priced_count": len(priced),
        # The honest headline: how much committed work has no number on it yet.
        "unpriced_count": len(unpriced),
        "next_scheduled": next((i["scheduled_for"] for i in items if i["scheduled_for"]), None),
    }
