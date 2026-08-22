"""Specification-level risk analysis.

Purchase data answers "what did this cost". It cannot answer "will it work".
These checks compare the vessel's installed specification against what the
hardware can actually deliver — nameplate versus realistic harvest, inverter
demand versus BMS ceiling, load versus runtime.

Every finding is an **estimate from stated specifications**, not a measurement.
Assumptions are declared once in `ASSUMPTIONS`, cited in the output of every
check that uses them, and overridable from `project_meta`. A finding you cannot
audit is a finding you should not trust.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..db.database import Database

# --- installed specification ------------------------------------------------
# Defaults describe Ophelia's Key as specified. Any key can be corrected by
# writing a numeric value to project_meta under `spec.<key>`.

DEFAULT_SPEC: dict[str, float] = {
    # solar
    "solar_panel_count": 8,
    "solar_panel_watts_nameplate": 500,
    "solar_series_per_string": 4,
    "solar_parallel_strings": 2,
    # charge controller
    "mppt_amps": 140,
    "mppt_vin_min": 55,
    "mppt_vin_max": 350,
    # inverter
    "inverter_watts_continuous": 4000,
    "inverter_output_amps": 33,
    "inverter_voltage_ac": 120,
    # 24V bank
    "bank_nominal_voltage": 25.6,
    "bank_amp_hours": 600,
    "bank_kwh": 15.36,
    "bms_amps_per_unit": 200,
    "bank_units_series": 2,
    # isolated 12V house bank
    "house_12v_amp_hours": 320,
    "house_12v_voltage": 12,
    # generator
    "generator_watts": 8000,
    "generator_circuit_amps": 30,
    # primary load
    "ac_load_watts": 1944,
    "ac_load_amps": 16.2,
    "ac_hours_per_day": 8,
}

# --- declared assumptions ---------------------------------------------------
# These are the judgement calls. They are separated from the spec so a reader
# can disagree with one number and see exactly which findings move.

ASSUMPTIONS: dict[str, tuple[float, str]] = {
    "inverter_efficiency": (
        0.90, "Typical pure-sine inverter efficiency at moderate load"),
    "flexible_panel_derate_low": (
        0.50, "Flexible panels commonly deliver well under nameplate; low estimate"),
    "flexible_panel_derate_high": (
        0.65, "Flexible panel realistic output; high estimate"),
    "usable_depth_lifepo4": (
        0.90, "Practical usable fraction of LiFePO4 capacity before BMS cutoff"),
    "peak_sun_hours": (
        4.5, "Daily peak-sun-hour equivalent; varies by latitude and season"),
    "compressor_surge_low": (
        3.0, "Locked-rotor surge multiple for a standard AC compressor, low end"),
    "compressor_surge_high": (
        5.0, "Locked-rotor surge multiple, high end (no soft start fitted)"),
    "inverter_surge_multiple": (
        2.0, "Typical short-duration surge rating as a multiple of continuous"),
}


def _a(key: str) -> float:
    return ASSUMPTIONS[key][0]


@dataclass
class SpecFinding:
    severity: str
    code: str
    title: str
    detail: str
    numbers: dict[str, str] = field(default_factory=dict)
    assumptions: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "severity": self.severity, "code": self.code, "title": self.title,
            "detail": self.detail, "numbers": self.numbers,
            "assumptions": [f"{k} = {ASSUMPTIONS[k][0]:g} ({ASSUMPTIONS[k][1]})"
                            for k in self.assumptions],
        }


def load_spec(db: Database | None = None) -> dict[str, float]:
    """Spec with any `spec.<key>` overrides from project_meta applied."""
    spec = dict(DEFAULT_SPEC)
    if db is None:
        return spec
    for row in db.query("SELECT key, value FROM project_meta WHERE key LIKE 'spec.%'"):
        name = row["key"].removeprefix("spec.")
        try:
            spec[name] = float(row["value"])
        except (TypeError, ValueError):
            continue
    return spec


# --- checks -----------------------------------------------------------------
# Each returns zero or more findings. A check that finds nothing wrong returns
# nothing; none of them fabricate reassurance.


def check_bms_headroom(s: dict) -> list[SpecFinding]:
    """Can the bank actually deliver what the inverter will ask of it?"""
    eff = _a("inverter_efficiency")
    dc_watts = s["inverter_watts_continuous"] / eff
    dc_amps = dc_watts / s["bank_nominal_voltage"]
    # Units in series carry the same current, so the bank ceiling is one unit's
    # BMS limit — not the sum. Wiring them in parallel would double it.
    ceiling = s["bms_amps_per_unit"]
    headroom = (ceiling - dc_amps) / ceiling

    if headroom >= 0.30:
        return []
    severity = "high" if headroom < 0.20 else "medium"
    return [SpecFinding(
        severity, "bms_headroom",
        "Battery BMS limit is close to full inverter demand",
        (f"At continuous output the inverter draws about {dc_amps:.0f}A from a "
         f"{ceiling:.0f}A bank — {headroom*100:.0f}% headroom. The two batteries are "
         f"in series, so they share one current path and the bank ceiling is a single "
         f"unit's {ceiling:.0f}A BMS, not {ceiling*2:.0f}A. Any surge above continuous "
         f"output can trip the BMS and drop the AC load entirely."),
        {"inverter continuous": f"{s['inverter_watts_continuous']:.0f}W AC",
         "DC draw": f"{dc_watts:.0f}W / {dc_amps:.0f}A at {s['bank_nominal_voltage']:.1f}V",
         "BMS ceiling": f"{ceiling:.0f}A", "headroom": f"{headroom*100:.0f}%"},
        ("inverter_efficiency",),
    )]


def check_ac_startup_surge(s: dict) -> list[SpecFinding]:
    """Will the air conditioner actually start on inverter power?"""
    running = s["ac_load_watts"]
    low = running * _a("compressor_surge_low")
    high = running * _a("compressor_surge_high")
    inverter_surge = s["inverter_watts_continuous"] * _a("inverter_surge_multiple")

    # Fire whenever the surge range *reaches* the inverter's capability, not
    # only when the optimistic end does. Severity distinguishes "fails on every
    # estimate" from "fails on the pessimistic one" — suppressing the second
    # case would hide a real, plausible failure behind a favourable assumption.
    if high <= inverter_surge:
        return []
    severity = "high" if low > inverter_surge else "medium"
    qualifier = (
        "Every estimate in that range exceeds the inverter."
        if low > inverter_surge
        else "The upper half of that range exceeds the inverter, so whether it starts "
             "depends on the specific compressor."
    )
    return [SpecFinding(
        severity, "ac_startup_surge",
        "Air conditioner startup surge may exceed inverter capability",
        (f"The compressor runs at {running:.0f}W but its locked-rotor draw at startup is "
         f"roughly {low:.0f}-{high:.0f}W. The inverter's estimated surge capability is "
         f"about {inverter_surge:.0f}W. {qualifier} Running load is comfortable; starting "
         f"is the problem. A soft start on the compressor typically cuts inrush by 60-70% "
         f"and resolves this outright. Verify two numbers to settle it: the compressor's "
         f"LRA from its nameplate, and the inverter's published surge rating and duration "
         f"— neither is in the current specification."),
        {"AC running": f"{running:.0f}W", "AC startup": f"{low:.0f}-{high:.0f}W",
         "inverter surge (est.)": f"{inverter_surge:.0f}W"},
        ("compressor_surge_low", "compressor_surge_high", "inverter_surge_multiple"),
    )]


def check_battery_runtime(s: dict) -> list[SpecFinding]:
    """How long does the primary load actually run on the bank?"""
    usable_kwh = s["bank_kwh"] * _a("usable_depth_lifepo4")
    draw_watts = s["ac_load_watts"] / _a("inverter_efficiency")
    hours = usable_kwh * 1000 / draw_watts
    severity = "medium" if hours < s["ac_hours_per_day"] else "low"
    return [SpecFinding(
        severity, "battery_runtime",
        f"Air conditioning runs about {hours:.1f}h on the bank alone",
        (f"{s['bank_kwh']:.2f} kWh nominal, {usable_kwh:.2f} kWh usable, against a "
         f"{draw_watts:.0f}W DC draw once inverter losses are counted. "
         + (f"That is short of the {s['ac_hours_per_day']:.0f}h/day assumed, so overnight "
            f"AC requires solar carryover, the generator, or shore power."
            if hours < s["ac_hours_per_day"] else
            "That covers the assumed daily runtime without other input.")),
        {"usable": f"{usable_kwh:.2f} kWh", "draw": f"{draw_watts:.0f}W",
         "runtime": f"{hours:.1f}h"},
        ("usable_depth_lifepo4", "inverter_efficiency"),
    )]


def check_solar_realistic_harvest(s: dict) -> list[SpecFinding]:
    """Nameplate versus what flexible panels actually deliver — and whether
    that covers the load they were presumably bought to cover."""
    nameplate = s["solar_panel_count"] * s["solar_panel_watts_nameplate"]
    ac_kwh = s["ac_load_watts"] * s["ac_hours_per_day"] / 1000 / _a("inverter_efficiency")

    if nameplate <= 0:
        # No array in service. The nameplate/derate discussion is meaningless,
        # but the absence of any charging source is itself the finding.
        usable_kwh = s["bank_kwh"] * _a("usable_depth_lifepo4")
        return [SpecFinding(
            "high", "no_charging_source",
            "No solar in service — the bank has no renewable charging source",
            (f"With no array, the {usable_kwh:.1f} kWh usable bank is refilled only by the "
             f"generator or shore power. At anchor the generator becomes a required system "
             f"rather than a backup, and its runtime is set by consumption: roughly "
             f"{usable_kwh / max(ac_kwh, 0.001):.1f} days of the assumed air-conditioning "
             f"duty cycle, or longer at house loads only. Any single-point generator failure "
             f"ends off-grid capability entirely."),
            {"array": "not in service", "usable bank": f"{usable_kwh:.1f} kWh",
             "AC daily draw": f"{ac_kwh:.1f} kWh"},
            ("usable_depth_lifepo4", "inverter_efficiency"),
        )]

    low_w = nameplate * _a("flexible_panel_derate_low")
    high_w = nameplate * _a("flexible_panel_derate_high")
    psh = _a("peak_sun_hours")
    low_kwh, high_kwh = low_w * psh / 1000, high_w * psh / 1000

    findings = [SpecFinding(
        "medium", "solar_nameplate_gap",
        "Solar array will not deliver its nameplate rating",
        (f"{s['solar_panel_count']:.0f} panels marketed at "
         f"{s['solar_panel_watts_nameplate']:.0f}W each gives {nameplate:.0f}W nameplate. "
         f"Flexible panels are routinely overrated and derate hard with heat, since they "
         f"sit flat against the surface with no air gap. Expect roughly "
         f"{low_w:.0f}-{high_w:.0f}W in good conditions, or about "
         f"{low_kwh:.1f}-{high_kwh:.1f} kWh/day. Worth measuring actual output at the "
         f"controller before sizing anything else around the {nameplate:.0f}W figure."),
        {"nameplate": f"{nameplate:.0f}W", "realistic": f"{low_w:.0f}-{high_w:.0f}W",
         "daily": f"{low_kwh:.1f}-{high_kwh:.1f} kWh"},
        ("flexible_panel_derate_low", "flexible_panel_derate_high", "peak_sun_hours"),
    )]

    if ac_kwh > high_kwh:
        findings.append(SpecFinding(
            "high", "solar_cannot_sustain_ac",
            "Solar alone cannot sustain the air conditioning load",
            (f"Air conditioning at {s['ac_load_watts']:.0f}W for "
             f"{s['ac_hours_per_day']:.0f}h/day consumes about {ac_kwh:.1f} kWh/day at the "
             f"battery, against a realistic harvest of {low_kwh:.1f}-{high_kwh:.1f} kWh/day "
             f"— a shortfall of roughly {ac_kwh - high_kwh:.1f}-{ac_kwh - low_kwh:.1f} kWh "
             f"per day before any other load is counted. Sustained AC therefore depends on "
             f"the generator or shore power, not the array."),
            {"AC daily": f"{ac_kwh:.1f} kWh", "harvest": f"{low_kwh:.1f}-{high_kwh:.1f} kWh",
             "shortfall": f"{ac_kwh - high_kwh:.1f}-{ac_kwh - low_kwh:.1f} kWh/day"},
            ("flexible_panel_derate_high", "peak_sun_hours", "inverter_efficiency"),
        ))
    return findings


def check_mppt_ceiling(s: dict) -> list[SpecFinding]:
    """Does the controller cap the array?"""
    nameplate = s["solar_panel_count"] * s["solar_panel_watts_nameplate"]
    if nameplate <= 0:
        return []          # no array to cap
    ceiling = s["mppt_amps"] * s["bank_nominal_voltage"]
    if ceiling >= nameplate:
        return []
    realistic_high = nameplate * _a("flexible_panel_derate_high")
    binding = realistic_high > ceiling
    return [SpecFinding(
        "medium" if binding else "low", "mppt_ceiling",
        "Charge controller caps below array nameplate",
        (f"The {s['mppt_amps']:.0f}A MPPT at {s['bank_nominal_voltage']:.1f}V can pass about "
         f"{ceiling:.0f}W, below the {nameplate:.0f}W nameplate array. "
         + ("Realistic panel output still exceeds this ceiling, so the controller is the "
            "binding constraint."
            if binding else
            f"Realistic output ({realistic_high:.0f}W) stays under the ceiling, so this "
            f"clipping is unlikely to bind in practice — the panel derate above is the "
            f"real limit, not the controller.")),
        {"MPPT ceiling": f"{ceiling:.0f}W", "array nameplate": f"{nameplate:.0f}W",
         "realistic array": f"{realistic_high:.0f}W"},
        ("flexible_panel_derate_high",),
    )]


def check_string_voltage(s: dict) -> list[SpecFinding]:
    """The MPPT has a lower input window. A hot string that sags below it stops
    harvesting entirely — a silent failure, not a degraded one."""
    if s["solar_panel_count"] <= 0:
        return []          # no strings to verify
    series = s["solar_series_per_string"]
    return [SpecFinding(
        "medium", "string_voltage_unverified",
        f"{series:.0f}S string voltage not verified against the MPPT window",
        (f"The controller needs {s['mppt_vin_min']:.0f}-{s['mppt_vin_max']:.0f}V at its "
         f"input. With {series:.0f} panels in series this depends entirely on each panel's "
         f"Vmp and Voc, which are not in the specification. Panel voltage falls as cells "
         f"heat, and a string that sags below {s['mppt_vin_min']:.0f}V stops producing "
         f"altogether rather than producing less. Check the panel datasheet: "
         f"{series:.0f} x Vmp must stay above {s['mppt_vin_min']:.0f}V when hot, and "
         f"{series:.0f} x Voc must stay under {s['mppt_vin_max']:.0f}V when cold."),
        {"series count": f"{series:.0f}", "MPPT window":
         f"{s['mppt_vin_min']:.0f}-{s['mppt_vin_max']:.0f}V",
         "panel Vmp/Voc": "not specified"},
    )]


def check_12v_charging_path(s: dict) -> list[SpecFinding]:
    """An isolated bank needs a charging source. The spec does not name one."""
    kwh = s["house_12v_amp_hours"] * s["house_12v_voltage"] / 1000
    return [SpecFinding(
        "medium", "house_bank_charging_unspecified",
        "Isolated 12V house bank has no stated charging source",
        (f"The {s['house_12v_amp_hours']:.0f}Ah 12V bank ({kwh:.2f} kWh) powers navigation, "
         f"VHF, pumps and lighting, but the specified charging equipment is all 24V — the "
         f"hybrid inverter and its MPPT both serve the {s['bank_nominal_voltage']:.1f}V "
         f"bank. Deliberate isolation is a sound design, but it needs a path in: a DC-DC "
         f"charger from the 24V bank, a dedicated 12V solar controller, or an alternator "
         f"feed. If one is already fitted, add it to the spec so this check stops firing."),
        {"house bank": f"{s['house_12v_amp_hours']:.0f}Ah / {kwh:.2f} kWh",
         "charging source": "not specified"},
    )]


def check_generator_leg_capacity(s: dict) -> list[SpecFinding]:
    """A generator's headline watts and what one 120V circuit can pass are
    different numbers."""
    leg_watts = s["generator_circuit_amps"] * s["inverter_voltage_ac"]
    if leg_watts >= s["generator_watts"]:
        return []
    return [SpecFinding(
        "low", "generator_leg_capacity",
        "Generator's usable 120V output is below its headline rating",
        (f"The generator is rated {s['generator_watts']:.0f}W, but a single "
         f"{s['generator_circuit_amps']:.0f}A circuit at "
         f"{s['inverter_voltage_ac']:.0f}V passes {leg_watts:.0f}W. The full "
         f"{s['generator_watts']:.0f}W is available only at 240V or split across both legs. "
         f"For an all-120V boat, plan around {leg_watts:.0f}W per leg — still comfortably "
         f"above the {s['ac_load_watts']:.0f}W air conditioner."),
        {"rated": f"{s['generator_watts']:.0f}W",
         "per 120V leg": f"{leg_watts:.0f}W",
         "AC load": f"{s['ac_load_watts']:.0f}W"},
    )]


CHECKS = [
    check_bms_headroom,
    check_ac_startup_surge,
    check_solar_realistic_harvest,
    check_battery_runtime,
    check_mppt_ceiling,
    check_string_voltage,
    check_12v_charging_path,
    check_generator_leg_capacity,
]

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def spec_report(db: Database | None = None) -> dict:
    """Run every spec check. Returns findings sorted most severe first."""
    spec = load_spec(db)
    findings: list[SpecFinding] = []
    for check in CHECKS:
        findings.extend(check(spec))
    findings.sort(key=lambda f: SEVERITY_ORDER.get(f.severity, 9))
    return {
        "findings": [f.as_dict() for f in findings],
        "counts": {
            level: sum(1 for f in findings if f.severity == level)
            for level in ("high", "medium", "low")
        },
        "spec": spec,
        "assumptions": {k: {"value": v, "note": note} for k, (v, note) in ASSUMPTIONS.items()},
    }
