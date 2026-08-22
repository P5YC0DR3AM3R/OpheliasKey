"""Insurance schedule.

An insurer wants the value that was *added to the vessel*: equipment fitted to
it, and the professional labor that fitted it. It does not want the cost of
keeping the boat somewhere, insuring it, or registering it — those are
operating costs that add no insurable property.

So this report is deliberately narrower than the cost report. Everything it
excludes is excluded by a named rule, listed in the output, so the schedule can
be defended line by line rather than merely asserted.
"""

from __future__ import annotations

from ..db.database import Database

# Excluded from the schedule, each for a stated reason the insurer can check.
EXCLUDED_SYSTEMS: dict[str, str] = {
    "moorage": "Slip rent and marina dues — operating cost, adds no property to the vessel",
    "fees_admin": "Registration, title, documentation and insurance premiums — not property",
    "yard_services": "Haul-out, storage and transport — services consumed, not fitted",
    "consumables": "Shop consumables — used up rather than installed as property",
    "tools": "Tools — retained by the owner, do not convey with the vessel",
    "uncategorized": "Not yet attributed to a system",
}


def schedule(db: Database, vessel: str | None = None) -> dict:
    """Build the insurance schedule.

    Only boat-relevant line items count, and only for the named vessel — a
    previous boat's invoices must never appear on this boat's schedule.
    """
    params: list = []
    vessel_clause = ""
    if vessel:
        # NULL vessel means 'not stated', which for manually entered rows
        # defaults to the current boat; parsed rows have no vessel at all.
        vessel_clause = " AND (o.vessel IS NULL OR o.vessel = ?)"
        params.append(vessel)

    excluded_keys = ",".join("?" for _ in EXCLUDED_SYSTEMS)
    params_full = params + list(EXCLUDED_SYSTEMS)

    rows = db.query(
        f"""SELECT li.id, li.description, li.quantity, li.total_cents,
                   bs.key AS system_key, bs.name AS system_name, bs.is_capital,
                   v.canonical_name AS vendor, o.ordered_at, o.reference, o.vessel
            FROM line_items li
            JOIN orders o ON o.id = li.order_id
            LEFT JOIN vendors v ON v.id = o.vendor_id
            LEFT JOIN boat_systems bs ON bs.id = li.system_id
            WHERE li.relevance = 'boat'
              AND o.status != 'cancelled'
              {vessel_clause}
              AND (
                    li.insurable = 1
                 OR (li.insurable IS NULL AND bs.key IS NOT NULL
                     AND bs.key NOT IN ({excluded_keys}))
              )
            ORDER BY bs.sort_order, o.ordered_at""",
        params_full,
    )

    equipment: dict[str, dict] = {}
    installation: dict[str, dict] = {}
    for row in rows:
        bucket = installation if row["system_key"] == "professional_install" else equipment
        group = bucket.setdefault(
            row["system_key"], {"name": row["system_name"], "items": [], "total_cents": 0}
        )
        group["items"].append({
            "description": row["description"],
            "quantity": row["quantity"],
            "total_cents": row["total_cents"],
            "vendor": row["vendor"],
            "date": (row["ordered_at"] or "")[:10],
            "reference": row["reference"],
        })
        group["total_cents"] += row["total_cents"] or 0

    equipment_total = sum(g["total_cents"] for g in equipment.values())
    install_total = sum(g["total_cents"] for g in installation.values())

    # Everything deliberately left out, with the amount, so the schedule states
    # its own boundaries instead of quietly narrowing the picture.
    # An item forced out by hand must appear here too. Excluding it from the
    # schedule *and* from this list would make it vanish with no trace, which is
    # the failure this whole report is meant to prevent.
    excluded_rows = db.query(
        f"""SELECT bs.key, bs.name,
                   CASE WHEN li.insurable = 0 THEN 1 ELSE 0 END AS forced,
                   COALESCE(SUM(li.total_cents),0) AS amt, COUNT(*) AS n
            FROM line_items li
            JOIN orders o ON o.id = li.order_id
            LEFT JOIN boat_systems bs ON bs.id = li.system_id
            WHERE li.relevance='boat' AND o.status != 'cancelled'
              {vessel_clause}
              AND COALESCE(li.insurable, 0) != 1
              AND (li.insurable = 0 OR bs.key IS NULL OR bs.key IN ({excluded_keys}))
            GROUP BY bs.key, forced ORDER BY amt DESC""",
        params_full,
    )
    excluded = []
    for r in excluded_rows:
        key = r["key"] or "unattributed"
        if r["forced"]:
            reason = "Excluded by hand for this schedule"
        else:
            reason = EXCLUDED_SYSTEMS.get(key, "Not yet attributed to a system")
        excluded.append({
            "key": key, "name": r["name"] or "Not yet attributed",
            "total_cents": int(r["amt"]), "count": r["n"],
            "reason": reason, "forced": bool(r["forced"]),
        })

    meta = {r["key"]: r["value"] for r in db.query("SELECT key, value FROM project_meta")}
    dates = [i["date"] for g in list(equipment.values()) + list(installation.values())
             for i in g["items"] if i["date"]]

    return {
        "vessel": vessel or meta.get("vessel_name", "—"),
        "meta": meta,
        "equipment": sorted(equipment.values(), key=lambda g: -g["total_cents"]),
        "installation": sorted(installation.values(), key=lambda g: -g["total_cents"]),
        "equipment_total_cents": equipment_total,
        "installation_total_cents": install_total,
        "total_cents": equipment_total + install_total,
        "item_count": len(rows),
        "excluded": excluded,
        "excluded_total_cents": sum(e["total_cents"] for e in excluded),
        "period_start": min(dates) if dates else None,
        "period_end": max(dates) if dates else None,
    }
