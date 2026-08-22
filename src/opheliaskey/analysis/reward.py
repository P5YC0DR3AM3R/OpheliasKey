"""Reward analysis.

The honest starting point: **refit spend does not return dollar-for-dollar at
resale, and most of it never will.** A module that implied otherwise would be
flattering and useless. What can be defended is measured here in four separate
lenses, deliberately not summed into a single number:

  1. **Recoverable vs sunk** — what a buyer plausibly pays for, and what is gone.
  2. **Labor avoided** — work performed instead of purchased. The one component
     that genuinely is dollar-for-dollar, so it is *recorded*, never estimated.
  3. **Capability delivered** — what the money bought in units that matter:
     kWh of autonomy, days at anchor, dollars per watt actually harvested.
  4. **Use value** — cost per night aboard against the alternative, and how many
     nights it takes for the sunk portion to have been worth spending.

Recovery rates are heuristics with wide error bars. They live in one declared
table for the same reason the specification assumptions do: a number you cannot
see is a number you cannot argue with.
"""

from __future__ import annotations

from ..db.database import Database
from .cost import totals
from .spec import ASSUMPTIONS, load_spec

# --- declared assumptions ---------------------------------------------------

# Fraction of spend a buyer plausibly pays for at resale, by system. These are
# judgement calls informed by how brokers actually discount: electronics date
# fastest, documented engine work holds best, and anything consumed is gone.
RECOVERY_RATES: dict[str, tuple[float, str]] = {
    "vessel_acquisition": (0.80, "A 1988 hull bought near the bottom of its depreciation "
                                 "curve holds value far better than anything fitted to it"),
    "propulsion":       (0.50, "Documented engine and drivetrain work is a real value driver"),
    "trailer":          (0.60, "Separately sellable and holds value independently"),
    "tender":           (0.55, "Separately sellable"),
    "hull_structure":   (0.45, "Structural work protects value more than it adds"),
    "ground_tackle":    (0.45, "Transferable gear with a used market"),
    "deck_hardware":    (0.40, "Durable, but buyers rarely pay full freight"),
    "solar_generation": (0.35, "Desirable, discounted as panel technology dates"),
    "energy_storage":   (0.35, "Large banks are attractive; cycle history is unknown to a buyer"),
    "power_conversion": (0.35, "Discounted with the rest of the power system"),
    "generator":        (0.35, "Portable units sell separately at used-tool prices"),
    "climate":          (0.35, "Valued on a liveaboard, discounted on age"),
    "paint_coatings":   (0.35, "Improves presentation; fades from the ledger quickly"),
    "ac_distribution":  (0.30, "Infrastructure a buyer expects rather than pays extra for"),
    "dc_distribution":  (0.30, "Same"),
    "interior":         (0.30, "Taste-dependent; rarely matches the next owner's"),
    "plumbing":         (0.30, "Expected to work, not paid extra for"),
    "safety":           (0.30, "Often transfers with the boat at little credit"),
    "steering":         (0.35, "Expected to work"),
    "electronics_nav":  (0.25, "Marine electronics date fast and are discounted hard"),
    "dive":             (0.25, "Specialist gear, thin used market"),
    "connectivity":     (0.20, "Subscription-tied hardware, obsolesces quickly"),
    "av_security":      (0.20, "Consumer-grade electronics, steep depreciation"),
    "professional_install": (0.35, "Quality installation supports value but is rarely itemised by a buyer"),
    "moorage":          (0.00, "Consumed"),
    "consumables":      (0.00, "Consumed"),
    "yard_services":    (0.00, "Consumed"),
    "fees_admin":       (0.00, "Consumed"),
}

# Tools are excluded from vessel value entirely — they do not convey with the
# boat — but they are not destroyed either. Reported on their own line.
TOOL_RESIDUAL = (0.50, "Retains value as tools; does not convey with the vessel")

DEFAULT_UNKNOWN_RECOVERY = (0.30, "Default for systems without a declared rate")

REWARD_ASSUMPTIONS: dict[str, tuple[float, str]] = {
    "yard_labor_rate_dollars": (
        140.0, "Typical marine yard labor rate per hour, US, 2026"),
    "house_base_load_kwh_day": (
        3.0, "Liveaboard baseline: refrigeration, lighting, pumps, electronics, no AC"),
    "alternative_nightly_cost_dollars": (
        180.0, "What a night aboard displaces: modest hotel, or marina plus rent ashore"),
}


def _r(key: str) -> float:
    return REWARD_ASSUMPTIONS[key][0]


def _a(key: str) -> float:
    return ASSUMPTIONS[key][0]


# --- lens 1: recoverable vs sunk -------------------------------------------


def recovery(db: Database) -> dict:
    """Estimate what a buyer plausibly pays for, and what is permanently sunk."""
    rows = db.query(
        """SELECT bs.key, bs.name, COALESCE(SUM(li.total_cents), 0) AS spend_cents
           FROM boat_systems bs
           JOIN line_items li ON li.system_id = bs.id AND li.relevance = 'boat'
           GROUP BY bs.id HAVING spend_cents > 0
           ORDER BY spend_cents DESC"""
    )
    lines, vessel_spend, recoverable = [], 0, 0
    tool_spend = tool_residual = 0

    for row in rows:
        spend = int(row["spend_cents"])
        if row["key"] == "tools":
            tool_spend += spend
            tool_residual += int(spend * TOOL_RESIDUAL[0])
            continue
        rate, basis = RECOVERY_RATES.get(row["key"], DEFAULT_UNKNOWN_RECOVERY)
        recovered = int(spend * rate)
        vessel_spend += spend
        recoverable += recovered
        lines.append({
            "key": row["key"], "name": row["name"], "spend_cents": spend,
            "rate": rate, "recoverable_cents": recovered,
            "sunk_cents": spend - recovered, "basis": basis,
        })

    # Boat spend with no system has no recovery rate, so it cannot be estimated
    # — but dropping it silently would understate both the spend base and the
    # sunk figure. Report it as its own number instead.
    row = db.one(
        """SELECT COALESCE(SUM(total_cents), 0) AS amt, COUNT(*) AS n
           FROM line_items WHERE relevance = 'boat' AND system_id IS NULL"""
    )
    unattributed = int(row["amt"]) if row else 0

    return {
        "lines": lines,
        "unattributed_cents": unattributed,
        "unattributed_count": int(row["n"]) if row else 0,
        "vessel_spend_cents": vessel_spend,
        "recoverable_cents": recoverable,
        "sunk_cents": vessel_spend - recoverable,
        "recovery_pct": round(recoverable / vessel_spend * 100, 1) if vessel_spend else None,
        "tool_spend_cents": tool_spend,
        "tool_residual_cents": tool_residual,
    }


# --- lens 2: labor avoided --------------------------------------------------


def labor_avoided(db: Database) -> dict:
    """Value of work performed rather than purchased.

    Recorded from `okey log labor`, never estimated: guessing hours would
    manufacture return out of nothing.
    """
    rows = db.query(
        """SELECT l.hours, l.rate_cents, bs.name AS system_name, bs.key AS system_key
           FROM labor_log l LEFT JOIN boat_systems bs ON bs.id = l.system_id"""
    )
    default_rate_cents = int(_r("yard_labor_rate_dollars") * 100)
    total_hours = 0.0
    total_cents = 0
    by_system: dict[str, dict] = {}

    for row in rows:
        rate = row["rate_cents"] or default_rate_cents
        value = int(row["hours"] * rate)
        total_hours += row["hours"]
        total_cents += value
        key = row["system_key"] or "unassigned"
        entry = by_system.setdefault(
            key, {"name": row["system_name"] or "Unassigned", "hours": 0.0, "value_cents": 0}
        )
        entry["hours"] += row["hours"]
        entry["value_cents"] += value

    return {
        "hours": round(total_hours, 1),
        "value_cents": total_cents,
        "rate_cents": default_rate_cents,
        "by_system": sorted(by_system.values(), key=lambda e: -e["value_cents"]),
        "logged": bool(rows),
    }


# --- lens 3: capability delivered -------------------------------------------


def capability(db: Database) -> dict:
    """What the money bought, in units that matter on a boat.

    Reads the same specification the risk checks use, so capability figures and
    risk findings can never disagree with each other.
    """
    spec = load_spec(db)
    nameplate_w = spec["solar_panel_count"] * spec["solar_panel_watts_nameplate"]
    real_low = nameplate_w * _a("flexible_panel_derate_low")
    real_high = nameplate_w * _a("flexible_panel_derate_high")
    psh = _a("peak_sun_hours")
    harvest_low = real_low * psh / 1000
    harvest_high = real_high * psh / 1000

    usable_kwh = spec["bank_kwh"] * _a("usable_depth_lifepo4")
    base_load = _r("house_base_load_kwh_day")
    ac_kwh = spec["ac_load_watts"] * spec["ac_hours_per_day"] / 1000 / _a("inverter_efficiency")

    # Days at anchor before an outside source is needed, in both modes.
    def days(load_kwh: float, harvest_kwh: float) -> float | None:
        deficit = load_kwh - harvest_kwh
        return None if deficit <= 0 else usable_kwh / deficit

    spend_by = {
        row["key"]: int(row["spend_cents"])
        for row in db.query(
            """SELECT bs.key, COALESCE(SUM(li.total_cents),0) AS spend_cents
               FROM boat_systems bs
               JOIN line_items li ON li.system_id = bs.id AND li.relevance='boat'
               GROUP BY bs.id"""
        )
    }
    storage_spend = spend_by.get("energy_storage", 0)
    solar_spend = spend_by.get("solar_generation", 0)

    return {
        "usable_kwh": round(usable_kwh, 2),
        "harvest_low_kwh": round(harvest_low, 1),
        "harvest_high_kwh": round(harvest_high, 1),
        "base_load_kwh": base_load,
        "ac_load_kwh": round(ac_kwh, 1),
        # None means solar covers the load and autonomy is not battery-limited.
        "days_without_ac": days(base_load, harvest_high),
        # Named by the outcome, not the input: a low harvest gives fewer days.
        "days_with_ac_min": days(base_load + ac_kwh, harvest_low),
        "days_with_ac_max": days(base_load + ac_kwh, harvest_high),
        "ac_hours_on_bank": round(
            usable_kwh * 1000 / (spec["ac_load_watts"] / _a("inverter_efficiency")), 1),
        "storage_spend_cents": storage_spend,
        "solar_spend_cents": solar_spend,
        "cents_per_kwh_storage": int(storage_spend / spec["bank_kwh"]) if storage_spend else None,
        "cents_per_watt_nameplate": int(solar_spend / nameplate_w) if solar_spend else None,
        "cents_per_watt_realistic": int(solar_spend / real_high) if solar_spend else None,
        "nameplate_w": nameplate_w,
        "realistic_w_high": round(real_high),
    }


# --- lens 4: use value ------------------------------------------------------


def use_value(db: Database, sunk_cents: int) -> dict:
    """Cost per night aboard, and the break-even against the alternative.

    Amortizes the *sunk* portion only. The recoverable portion is not consumed
    by using the boat, so charging it against nights aboard would double-count.
    """
    row = db.one("SELECT COALESCE(SUM(nights), 0) AS n FROM usage_log")
    nights = int(row["n"]) if row else 0
    alternative_cents = int(_r("alternative_nightly_cost_dollars") * 100)
    breakeven_nights = (sunk_cents // alternative_cents) if alternative_cents else None

    return {
        "nights": nights,
        "alternative_nightly_cents": alternative_cents,
        "cost_per_night_cents": (sunk_cents // nights) if nights else None,
        "breakeven_nights": breakeven_nights,
        "nights_remaining": max(0, (breakeven_nights or 0) - nights),
        "value_realized_cents": nights * alternative_cents,
        "logged": nights > 0,
    }


def reward_report(db: Database) -> dict:
    rec = recovery(db)
    labor = labor_avoided(db)
    caps = capability(db)
    use = use_value(db, rec["sunk_cents"])
    spent = totals(db)["net_cents"]

    return {
        "net_spend_cents": spent,
        "recovery": rec,
        "labor": labor,
        "capability": caps,
        "use_value": use,
        "assumptions": {
            k: {"value": v, "note": note} for k, (v, note) in REWARD_ASSUMPTIONS.items()
        },
    }
