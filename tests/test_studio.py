"""Floating studio tests. Each one guards a number a reader could recompute by
hand, or a refusal the module must keep making."""

from __future__ import annotations

import math
import sys
from itertools import pairwise
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from opheliaskey.analysis.spec import ASSUMPTIONS  # noqa: E402
from opheliaskey.analysis.studio import (  # noqa: E402
    BASELINE_NOTE,
    COMPETITION_FLOW,
    FLAG_KEYS,
    FUNNEL_INPUT_KEYS,
    FUNNEL_PLANS,
    LYRICSHOW,
    MONTH_KEYS,
    OBSERVABLE,
    OVERRIDABLE,
    PARADISE_BUSKER,
    PARTNER_KINDS,
    PARTNER_STATUSES,
    PARTNERS,
    PLACEHOLDERS,
    PROBABILITIES,
    REACH_NOTE,
    ROI_NOTE,
    SIGNAL_CHAIN,
    STAGES,
    STUDIO_ASSUMPTIONS,
    STUDIO_KIT,
    TARGET_NOTE,
    WHOLE_KEYS,
    ZERO_OK,
    funnel_inputs,
    kit_budget,
    moorage_monthly,
    recorded_shows,
    resolve_assumptions,
    studio_report,
)
from opheliaskey.classify.taxonomy import seed_systems  # noqa: E402
from opheliaskey.db.database import Database, utcnow  # noqa: E402

# The boat's own audience, worked by hand once here so every test that argues
# about the funnel's mechanics — two audiences, two segments, the dock, the
# plan prices — argues with the same numbers. These are the figures with no
# partner stream counted (`BOAT_ONLY` below sets the partner church's placeholder
# audience to 0); the declared defaults add a partner and are worked next.
#   stream viewers 150 × 4 = 600; event viewers 150 × 2.0 × 2 = 600 → 1200
#   attendees 60 × 2 = 120
#   travelers 1200 × 0.70 = 840 viewers; performers 360
#   installs: travelers 840 × 0.04 = 33.6 from the stream + 120 × 0.12 = 14.4 from the
#     dock (attendees are travelers) → 48; performers 360 × 0.025 = 9 → 57 in all,
#     of which 42.6 from the stream and 14.4 from the dock
#   new paid: travelers 48 × 0.25 = 12; performers 9 × 0.05 = 0.45 → 12.45
#   steady: travelers 12 / 0.06 = 200; performers 0.45 / 0.08 = 5.625 → 205.625
#   the traveler plan's month with 60% on the annual bundle:
#     Base + Conversation Mode 0.40 × 1998 + 0.60 × 13998 / 12 = 1499.1 → 1499
#   a performer plan's month with 35% on annual billing, rounded per plan:
#     Base                 0.65 × 1499 + 0.35 × 9999 / 12  = 1265.99 → 1266
#     Base + Pro Broadcast 0.65 × 5498 + 0.35 × 57987 / 12 = 5264.99 → 5265
#     Ultimate             0.65 × 4999 + 0.35 × 39999 / 12 = 4415.99 → 4416
#   performer ARPU 0.60 × 1266 + 0.15 × 5265 + 0.25 × 4416 = 2653.35 cents
#   (at list price, i.e. annual_share 0: 0.60 × 1499 + 0.15 × 5498 + 0.25 × 4999 = 2973.85)
#   blended ARPU (12 × 1499 + 0.45 × 2653.35) / 12.45 = 1540.72 → 1541
#   MRR net (200 × 1499 + 5.625 × 2653.35) × 0.85 = 267,516.33 → $2,675.16 a month
BOAT_ONLY = {"partner_church_live_viewers": 0}
NEW_PAID_T, NEW_PAID_P = 12.0, 0.45
NEW_PAID = NEW_PAID_T + NEW_PAID_P
CHURN_T, CHURN_P = 0.06, 0.08
STEADY_T, STEADY_P = NEW_PAID_T / CHURN_T, NEW_PAID_P / CHURN_P
STEADY = STEADY_T + STEADY_P
ARPU_T = 1499
ARPU_P = 2653.35
LIST_ARPU_P = 2973.85
ARPU = (NEW_PAID_T * ARPU_T + NEW_PAID_P * ARPU_P) / NEW_PAID
KEEP = 0.85
MRR_NET = (STEADY_T * ARPU_T + STEADY_P * ARPU_P) * KEEP

# The declared defaults: the boat's 1,200 viewers plus the one committed
# partner, Partner church, at its PLACEHOLDER audience of 5,000 live viewers a
# stream × 4 Sunday streams = 20,000 viewers a month. Partner viewers join the
# stream audience before the traveler/performer split, so the same rates run
# over 21,200 viewers; the dock is unchanged. Worked in the module's own
# arithmetic order so the floats match to the last place:
#   viewers 600 + 600 + 20,000 = 21,200; travelers 14,840; performers 6,360
#   installs: travelers 14,840 × 0.04 = 593.6 + 14.4 dock = 608; performers
#     6,360 × 0.025 = 159 → 767 in all, 752.6 from screens and 14.4 from the dock
#   new paid: travelers 608 × 0.25 = 152; performers 159 × 0.05 = 7.95 → 159.95
#   steady: 152 / 0.06 = 2,533.33 + 7.95 / 0.08 = 99.375 → 2,632.7
#   blended ARPU (152 × 1499 + 7.95 × 2653.35) / 159.95 = 1556.37 → 1556
#   MRR net (2,533.33 × 1499 + 99.375 × 2653.35) × 0.85 = 3,451,971.82 → $34,519.72 a month
#   — a headline that rests on a placeholder, and the report's partner row says so.
PARTNER_VIEWERS = 5000 * 4
D_VIEWERS = 150 * 4 + 150 * 2.0 * 2 + PARTNER_VIEWERS
_D_TRAVELERS = D_VIEWERS * 0.70
_D_PERFORMERS = D_VIEWERS - _D_TRAVELERS
D_INSTALLS_T = _D_TRAVELERS * 0.04 + (60 * 2) * 0.12
D_INSTALLS_P = _D_PERFORMERS * 0.025
D_NEW_PAID_T, D_NEW_PAID_P = D_INSTALLS_T * 0.25, D_INSTALLS_P * 0.05
D_NEW_PAID = D_NEW_PAID_T + D_NEW_PAID_P
D_STEADY_T, D_STEADY_P = D_NEW_PAID_T / CHURN_T, D_NEW_PAID_P / CHURN_P
D_STEADY = D_STEADY_T + D_STEADY_P
D_ARPU = (D_NEW_PAID_T * ARPU_T + D_NEW_PAID_P * ARPU_P) / D_NEW_PAID
D_MRR_NET = (D_STEADY_T * ARPU_T + D_STEADY_P * ARPU_P) * KEEP
# New paid subscribers per stream viewer at the declared rates — what one more
# viewer is worth on any screen: 0.70 × 0.04 × 0.25 + 0.30 × 0.025 × 0.05.
YIELD = 0.70 * 0.04 * 0.25 + 0.30 * 0.025 * 0.05
# Partner artist, hypothetical and off by default: 6.19M × 2% × 1 stream = 123,800 viewers a month.
ARTIST_VIEWERS = 6_190_000 * 0.02 * 1


def _replay(months=36, new_paid_t=NEW_PAID_T, new_paid_p=NEW_PAID_P):
    """The two show-driven cohorts by hand, month by month: travelers at their
    churn and plan price, performers at theirs; yields (travelers, performers,
    net MRR, cumulative net) unrounded. Defaults to the boat's own figures;
    pass the D_ constants for the declared defaults."""
    subs_t = subs_p = cumulative = 0.0
    for _ in range(months):
        subs_t = subs_t * (1 - CHURN_T) + new_paid_t
        subs_p = subs_p * (1 - CHURN_P) + new_paid_p
        mrr = (subs_t * ARPU_T + subs_p * ARPU_P) * KEEP
        cumulative += mrr
        yield subs_t, subs_p, mrr, cumulative


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    database.migrate()
    seed_systems(database)
    return database


def _boat_order(db, system_key, cents, description="item", ordered_at="2026-08-01T00:00:00Z"):
    cur = db.execute(
        "INSERT INTO orders (source, external_order_id, ordered_at, status, total_cents, "
        "created_at, updated_at) VALUES ('test', ?, ?, 'delivered', ?, ?, ?)",
        (f"s{db.one('SELECT COUNT(*) c FROM orders')['c']}", ordered_at, cents,
         utcnow(), utcnow()))
    order_id = int(cur.lastrowid)
    sys_id = None
    if system_key:
        sys_id = db.one("SELECT id FROM boat_systems WHERE key=?", (system_key,))["id"]
    db.execute(
        "INSERT INTO line_items (order_id, line_no, description, total_cents, system_id, "
        "relevance) VALUES (?,0,?,?,?,'boat')", (order_id, description, cents, sys_id))
    return order_id


def _show(db, performed_at, unique=None, installs=None, peak=None, platform="youtube",
          kind="set", attendees=None):
    db.execute(
        """INSERT INTO show_log (performed_at, kind, platform, title, duration_minutes,
             peak_viewers, unique_viewers, attendees, installs_attributed, logged_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (performed_at, kind, platform, "set", 110, peak, unique, attendees, installs, utcnow()))


def _meta(db, key, value):
    db.execute("INSERT INTO project_meta (key, value, updated_at) VALUES (?,?,?)",
               (key, value, utcnow()))


def _defaults() -> dict[str, float]:
    return {k: v for k, (v, _) in STUDIO_ASSUMPTIONS.items()}


# --- assumptions ------------------------------------------------------------

def test_every_assumption_is_declared_with_a_note():
    """A number you cannot see is a number you cannot argue with."""
    assert OVERRIDABLE == set(STUDIO_ASSUMPTIONS)
    for key, entry in STUDIO_ASSUMPTIONS.items():
        value, note = entry
        assert isinstance(value, (int, float)) and math.isfinite(value), key
        assert isinstance(note, str) and note.strip(), key


def test_competition_assumptions_are_declared():
    defaults = _defaults()
    assert defaults["events_per_month"] == 2
    assert defaults["buskers_per_event"] == 6
    assert defaults["event_viewers_multiplier"] == 2.0
    assert defaults["dock_attendees_per_event"] == 60
    assert defaults["attendee_to_install"] == 0.12
    assert ZERO_OK == {"events_per_month", "dock_attendees_per_event", "baseline_subscribers",
                       "partner_church_live_viewers", "partner_artist_subscribers",
                       "target_subscribers"}
    assert ZERO_OK <= set(STUDIO_ASSUMPTIONS)


def test_partner_and_target_assumptions_are_declared_with_their_classes():
    """Six new judgement calls, each declared with a note: the partner artist's subscriber
    count is the owner's figure; the partner church's live audience is a PLACEHOLDER
    and says so; the live share is a benchmark band; the switch is a flag; the
    target is the owner's projection, 3,000 in 3 months."""
    defaults = _defaults()
    notes = {key: note for key, (_, note) in STUDIO_ASSUMPTIONS.items()}
    assert defaults["partner_artist_subscribers"] == 6_190_000
    assert "2026-08-22" in notes["partner_artist_subscribers"]
    assert "the partner artist's channel" in notes["partner_artist_subscribers"]
    assert defaults["partner_church_live_viewers"] == 5_000
    assert notes["partner_church_live_viewers"].startswith("ESTIMATE:")
    assert "replace with the channel's analytics" in notes["partner_church_live_viewers"]
    assert defaults["partner_live_share"] == 0.02
    assert "0.5–2%" in notes["partner_live_share"]
    assert defaults["partners_include_hypothetical"] == 0
    assert "Partner artist" in notes["partners_include_hypothetical"]
    assert "Partner church" in notes["partners_include_hypothetical"]
    assert defaults["target_subscribers"] == 3_000
    assert defaults["target_month"] == 3
    assert "3,000 in 3 months" in notes["target_month"]
    # Validation classes: a probability, a flag, whole numbers, counts that may be 0.
    assert "partner_live_share" in PROBABILITIES
    assert FLAG_KEYS == {"partners_include_hypothetical"}
    assert MONTH_KEYS == ("horizon_months", "target_month")
    assert WHOLE_KEYS == ("horizon_months", "target_month", "target_subscribers")
    assert {"partner_church_live_viewers", "partner_artist_subscribers",
            "target_subscribers"} <= ZERO_OK
    # A placeholder is a declared default whose note begins with the word.
    assert PLACEHOLDERS == {"partner_church_live_viewers"}
    assert all(notes[key].startswith("ESTIMATE") for key in PLACEHOLDERS)
    assert not any(notes[key].startswith("ESTIMATE")
                   for key in STUDIO_ASSUMPTIONS if key not in PLACEHOLDERS)
    # All six are funnel inputs — they move the month's viewers or the target the
    # page reads — and none is observable from a show.
    new = {"partner_church_live_viewers", "partner_artist_subscribers",
           "partner_live_share", "partners_include_hypothetical", "target_subscribers",
           "target_month"}
    assert new <= set(FUNNEL_INPUT_KEYS)
    assert not new & set(OBSERVABLE)


def test_realistic_funnel_defaults_are_benchmarks_with_notes():
    """The conversion rates are benchmarks, and each note says which band it
    sits in; the baseline is a figure to be entered, and 0 is 'not entered'."""
    defaults = _defaults()
    assert defaults["viewer_to_install"] == 0.025
    assert defaults["install_to_paid"] == 0.05
    assert defaults["annual_share"] == 0.35
    assert defaults["monthly_churn"] == 0.08
    assert defaults["baseline_subscribers"] == 0
    notes = {key: note for key, (_, note) in STUDIO_ASSUMPTIONS.items()}
    assert "1–3%" in notes["viewer_to_install"]
    assert "2–5%" in notes["install_to_paid"]
    assert "several times better" in notes["attendee_to_install"]
    assert "annual price ÷ 12" in notes["annual_share"]
    assert "0 means not entered, not zero" in notes["baseline_subscribers"]
    assert "annual_share" in PROBABILITIES and "baseline_subscribers" in ZERO_OK
    assert PROBABILITIES <= set(STUDIO_ASSUMPTIONS)
    # The original funnel keys now describe the performer share, and say so.
    for key in ("viewer_to_install", "install_to_paid", "annual_share", "monthly_churn"):
        assert "performer" in notes[key].lower(), key


def test_traveler_segment_defaults_encode_the_one_percent_premise():
    """The traveler is the buyer: 70% of any audience, installing at four times
    the passive rate and paying at 25% — the two multiply to the owner's 1% of
    viewers → subscribers — on the Base + Conversation Mode bundle, 60% of
    them annual, churning at a trip-driven 6%. Declared, noted, arguable."""
    defaults = _defaults()
    assert defaults["traveler_share"] == 0.70
    assert defaults["traveler_viewer_to_install"] == 0.04
    assert defaults["traveler_install_to_paid"] == 0.25
    assert defaults["traveler_viewer_to_install"] * defaults["traveler_install_to_paid"] == (
        pytest.approx(0.01))
    assert defaults["traveler_annual_share"] == 0.60
    assert defaults["traveler_monthly_churn"] == 0.06
    notes = {key: note for key, (_, note) in STUDIO_ASSUMPTIONS.items()}
    assert "Conversation Mode" in notes["traveler_share"]
    assert "1%" in notes["traveler_install_to_paid"]
    assert "Base + Conversation Mode" in notes["traveler_install_to_paid"]
    assert "between trips" in notes["traveler_monthly_churn"]
    travelers = {"traveler_share", "traveler_viewer_to_install", "traveler_install_to_paid",
                 "traveler_annual_share", "traveler_monthly_churn"}
    assert travelers <= PROBABILITIES
    assert travelers <= set(FUNNEL_INPUT_KEYS)
    assert not travelers & set(OBSERVABLE)            # nothing a show can observe


def test_resolution_order_is_declared_then_meta_then_override(db):
    _meta(db, "studio.viewers_per_show", "200")
    _meta(db, "studio.shows_per_month", "2")
    resolved = resolve_assumptions(db, {"shows_per_month": 6})
    assert resolved["viewers_per_show"] == {
        "value": 200.0, "note": STUDIO_ASSUMPTIONS["viewers_per_show"][1], "source": "meta"}
    assert resolved["shows_per_month"]["value"] == 6
    assert resolved["shows_per_month"]["source"] == "override"
    assert resolved["monthly_churn"]["source"] == "assumed"
    assert resolve_assumptions(None)["viewers_per_show"]["source"] == "assumed"


def test_malformed_meta_is_skipped_like_load_spec(db):
    _meta(db, "studio.viewers_per_show", "lots")
    _meta(db, "studio.not_a_thing", "1")
    resolved = resolve_assumptions(db)
    assert resolved["viewers_per_show"]["source"] == "assumed"
    assert "not_a_thing" not in resolved


def test_unknown_override_is_rejected():
    with pytest.raises(ValueError, match="unknown studio assumption 'viewers'"):
        resolve_assumptions(None, {"viewers": 10})


@pytest.mark.parametrize("overrides", [
    {"viewer_to_install": 1.5},
    {"install_to_paid": -0.1},
    {"monthly_churn": 0},
    {"load_encoder_w": -5},
    {"show_hours": 0},
    {"horizon_months": 0},
    {"horizon_months": 121},
    {"horizon_months": 12.5},
    {"viewers_per_show": float("nan")},
    {"viewers_per_show": float("inf")},
    {"viewers_per_show": "many"},
    {"viewers_per_show": None},
    {"attendee_to_install": 1.5},
    {"attendee_to_install": -0.01},
    {"buskers_per_event": 0},
    {"event_viewers_multiplier": 0},
    {"annual_share": 1.5},
    {"annual_share": -0.1},
    {"baseline_subscribers": -1},
    {"traveler_share": 1.5},
    {"traveler_share": -0.1},
    {"traveler_viewer_to_install": 1.01},
    {"traveler_install_to_paid": -0.01},
    {"traveler_annual_share": 2},
    {"traveler_monthly_churn": 0},
    {"traveler_monthly_churn": -0.05},
    {"partner_live_share": 1.5},
    {"partner_live_share": -0.01},
    {"partners_include_hypothetical": 2},
    {"partners_include_hypothetical": 0.5},
    {"partners_include_hypothetical": -1},
    {"partner_artist_subscribers": -1},
    {"partner_church_live_viewers": -1},
    {"target_subscribers": -1},
    {"target_subscribers": 10.5},
    {"target_month": 0},
    {"target_month": 121},
    {"target_month": 2.5},
])
def test_out_of_range_overrides_are_rejected(overrides):
    with pytest.raises(ValueError):
        resolve_assumptions(None, overrides)


def test_include_flag_must_be_exactly_zero_or_one(db):
    """The switch is on or off: 1 counts the hypothetical partners, 0 does not,
    and 0.5 of a partner is refused — from an override and from project_meta
    alike, since the resolved table is validated as a whole."""
    for value in (0, 1, 0.0, 1.0, "1"):
        assert resolve_assumptions(None, {"partners_include_hypothetical": value})[
            "partners_include_hypothetical"]["value"] == float(value)
    for bad in (2, 0.5, -1, 1.0001):
        with pytest.raises(ValueError, match="partners_include_hypothetical must be exactly 0 or 1"):
            resolve_assumptions(None, {"partners_include_hypothetical": bad})
    _meta(db, "studio.partners_include_hypothetical", "2")
    with pytest.raises(ValueError, match="must be exactly 0 or 1, got 2"):
        resolve_assumptions(db)


def test_target_is_a_whole_number_of_subscribers_by_a_whole_month_within_range():
    """The target month is held to the horizon's range, 1..120 whole months;
    the target itself is a whole number of subscribers, and 0 is allowed — no
    target — where a negative one is not."""
    ok = resolve_assumptions(None, {"target_month": 12, "target_subscribers": 2500})
    assert ok["target_month"]["value"] == 12 and isinstance(ok["target_month"]["value"], int)
    assert ok["target_subscribers"]["value"] == 2500
    assert isinstance(ok["target_subscribers"]["value"], int)
    assert resolve_assumptions(None, {"target_subscribers": 0})["target_subscribers"]["value"] == 0
    with pytest.raises(ValueError, match="target_month must be between 1 and 120, got 0"):
        resolve_assumptions(None, {"target_month": 0})
    with pytest.raises(ValueError, match="target_month must be between 1 and 120, got 121"):
        resolve_assumptions(None, {"target_month": 121})
    with pytest.raises(ValueError, match="target_month must be a whole number of months"):
        resolve_assumptions(None, {"target_month": 2.5})
    with pytest.raises(ValueError, match="target_subscribers must be a whole number of subscribers"):
        resolve_assumptions(None, {"target_subscribers": 10000.5})
    with pytest.raises(ValueError, match="target_subscribers must be 0 or greater"):
        resolve_assumptions(None, {"target_subscribers": -1})
    # horizon_months keeps its own wording and range.
    with pytest.raises(ValueError, match="horizon_months must be a whole number of months"):
        resolve_assumptions(None, {"horizon_months": 12.5})


def test_both_churns_must_be_positive_because_each_steady_state_divides_by_its_own():
    with pytest.raises(ValueError, match="traveler_monthly_churn must be greater than 0"):
        resolve_assumptions(None, {"traveler_monthly_churn": 0})
    with pytest.raises(ValueError, match="monthly_churn must be greater than 0"):
        resolve_assumptions(None, {"monthly_churn": 0})
    ok = resolve_assumptions(None, {"traveler_monthly_churn": 1, "monthly_churn": 1})
    assert ok["traveler_monthly_churn"]["value"] == 1


@pytest.mark.parametrize("key", sorted(ZERO_OK))
def test_zero_events_or_attendees_is_a_scenario_not_an_error(key):
    """A month with no competition nights, or a night nobody came to the dock
    for, is a valid what-if; a negative count is not."""
    assert resolve_assumptions(None, {key: 0})[key]["value"] == 0
    with pytest.raises(ValueError, match=f"{key} must be 0 or greater"):
        resolve_assumptions(None, {key: -1})


def test_plan_mix_must_still_partition_after_overrides():
    with pytest.raises(ValueError, match="mix"):
        resolve_assumptions(None, {"mix_base": 0.70})
    ok = resolve_assumptions(None, {"mix_base": 0.70, "mix_ultimate": 0.15})
    assert ok["mix_base"]["source"] == "override"
    assert ok["mix_base_broadcast"]["source"] == "assumed"


def test_report_carries_the_resolved_table(db):
    report = studio_report(db, {"viewers_per_show": 300})
    table = report["assumptions"]
    assert set(table) == set(STUDIO_ASSUMPTIONS)
    assert table["viewers_per_show"] == {
        "value": 300.0, "note": STUDIO_ASSUMPTIONS["viewers_per_show"][1],
        "source": "override"}
    assert all({"value", "note", "source"} == set(entry) for entry in table.values())


# --- facts, kit, signal chain -----------------------------------------------

def test_lyricshow_facts_are_the_app_prices():
    plans = LYRICSHOW["plans"]
    assert plans["base"]["monthly_cents"] == 1499
    assert plans["ultimate"]["monthly_cents"] == 4999
    assert plans["pro_broadcast"]["monthly_cents"] == 3999
    assert (plans["conversation_mode"]["monthly_cents"],
            plans["conversation_mode"]["annual_cents"]) == (499, 3999)
    assert LYRICSHOW["caption_latency_ms"] == 300
    assert [s["latency_ms"] for s in SIGNAL_CHAIN if s["latency_ms"] is not None] == [300]
    assert [s["stage"] for s in SIGNAL_CHAIN][:2] == ["Performer", "Audient iD4 mkII"]


def test_conversation_mode_facts_and_the_bundle_price(db):
    """The add-on travelers subscribe for, at the live pricing page's prices;
    the featured bundle is Base plus the add-on on each term, and the traveler
    plan in the funnel is priced from it — a price change there moves the
    funnel, nowhere else."""
    conv = LYRICSHOW["conversation_mode"]
    assert conv == {
        "monthly_cents": 499, "annual_cents": 3999,
        "bundle_annual_cents": 13998, "bundle_monthly_cents": 1998,
        "what": "Two speakers, two languages, one screen — the top half rotated for the "
                "person opposite; automatic speaker switching; the iOS app broadcasts "
                "conversation captions to the OBS overlay, so a stream can demo it live",
        "source": "lyricshow.live/pricing 2026-08-22; tiers.ts FEATURED_PACKAGE",
    }
    assert conv["bundle_monthly_cents"] == 1499 + 499
    assert conv["bundle_annual_cents"] == 9999 + 3999
    traveler = FUNNEL_PLANS[0]
    assert traveler[:2] == ("traveler_bundle", "Base + Conversation Mode")
    assert traveler[2:] == (1998, 13998, "traveler", None)
    assert [plan[4] for plan in FUNNEL_PLANS] == ["traveler", "performer", "performer",
                                                  "performer"]
    assert [plan[5] for plan in FUNNEL_PLANS[1:]] == ["mix_base", "mix_base_broadcast",
                                                     "mix_ultimate"]
    report = studio_report(db)
    assert report["lyricshow"]["conversation_mode"] == conv
    assert report["lyricshow"]["conversation_mode"] is not conv     # a copy, like the rest
    assert "Conversation Mode" in next(
        s for s in SIGNAL_CHAIN if s["stage"] == "App Store · Google Play")["detail"]


def test_kit_planned_total_and_recorded_detection(db):
    assert sum(cents for _, cents, _ in STUDIO_KIT) == 78896
    empty = kit_budget(db)
    assert empty["planned_cents"] == 78896
    assert empty["recorded"] == [] and empty["recorded_cents"] == 0

    _boat_order(db, "av_security", 18999, "Audient iD4 MKII USB-C audio interface")
    _boat_order(db, None, 9900, "Shure SM58 dynamic microphone")
    _boat_order(db, "consumables", 5000, "Moving blankets")   # not kit by keyword
    kit = kit_budget(db)
    assert [item["cents"] for item in kit["recorded"]] == [18999, 9900]
    assert kit["recorded_cents"] == 28899
    assert kit["planned_cents"] == 78896          # planning list does not shrink


def test_personal_purchases_never_count_as_kit(db):
    order = _boat_order(db, None, 9900, "Shure SM58 microphone")
    db.execute("UPDATE line_items SET relevance='personal' WHERE order_id=?", (order,))
    assert kit_budget(db)["recorded"] == []


# --- competition nights -----------------------------------------------------

def test_stages_are_the_three_the_owner_named():
    assert [stage["key"] for stage in STAGES] == ["deck", "swim_platform", "dock"]
    for stage in STAGES:
        assert set(stage) == {"key", "name", "where", "holds", "camera", "note"}
        assert all(isinstance(stage[field], str) and stage[field] for field in stage)
    by_key = {stage["key"]: stage for stage in STAGES}
    assert by_key["deck"]["name"] == "Cockpit deck"
    assert by_key["swim_platform"]["name"] == "Swim platform"
    assert by_key["dock"]["name"] == "Rear dock"
    assert "vote" in by_key["dock"]["note"]


def test_competition_flow_puts_lyric_show_on_screen():
    """The flow is data, and the steps where the app is on screen are marked —
    those are what sell it."""
    assert len(COMPETITION_FLOW) >= 8
    for step in COMPETITION_FLOW:
        assert set(step) == {"step", "role", "detail", "product"}
        assert isinstance(step["product"], bool)
        assert step["step"] and step["role"] and step["detail"]
    product_steps = [step["step"] for step in COMPETITION_FLOW if step["product"]]
    assert len(product_steps) >= 3
    assert product_steps[0] == "Lyric Show captions both"
    # The round is blind: both versions are performed live before anyone is told who wrote it.
    assert COMPETITION_FLOW[0]["step"] == "Two versions, live"
    assert COMPETITION_FLOW[-1]["step"] == "The coin"
    # Proof of presence is in person; the livestream is never the verification layer.
    presence = next(s for s in COMPETITION_FLOW if s["step"] == "Proof of presence")
    assert "never the proof" in presence["detail"]


def test_paradise_busker_facts_carry_their_citations():
    facts = PARADISE_BUSKER
    assert facts["tipping_artist_share"] == 0.80
    assert "100%" in facts["tipping_table_note"]           # the table disagrees; both cited
    assert facts["voting"] == "BNDS token, quadratic"
    assert facts["treasures"]["tiers"] == ["common", "rare", "legendary"]
    assert len(facts["treasures"]["proof"]) == 4
    assert facts["codex"] == {"score_split": "60/30/10", "threshold": 0.75}
    assert facts["membership_cents"] == {"low": 500, "high": 1000}
    assert facts["tithe_of_net_profit"] == 0.10
    assert facts["coin"].startswith("KEY WEST, PARADISE BUSKER")
    assert facts["series"] == "Song Swap"
    assert "white paper" in facts["source"].lower()
    # Every figure names the section it came from.
    for key in ("tipping_artist_share", "voting", "treasures", "codex", "membership",
                "tithe_of_net_profit", "coin", "series"):
        assert "§" in facts["notes"][key], key
    # A key ending in _cents is a promise of an integer; the notes keep clear of it.
    assert not any(key.endswith("_cents") for key in facts["notes"])


def test_report_carries_the_competition_block_as_a_copy(db):
    comp = studio_report(db)["competition"]
    assert list(comp) == ["stages", "flow", "facts"]
    assert comp["stages"] == STAGES and comp["stages"] is not STAGES
    assert comp["flow"] == COMPETITION_FLOW and comp["flow"] is not COMPETITION_FLOW
    assert comp["facts"] == PARADISE_BUSKER and comp["facts"] is not PARADISE_BUSKER
    comp["facts"]["notes"]["coin"] = "edited"
    assert PARADISE_BUSKER["notes"]["coin"] != "edited"


# --- power ------------------------------------------------------------------

def test_power_budget_matches_hand_arithmetic(db):
    power = studio_report(db)["power"]
    eff = ASSUMPTIONS["inverter_efficiency"][0]
    assert power["studio_w"] == 366
    assert [load["name"] for load in power["loads"]] == [
        "Encoder", "Interface", "Starlink", "Cameras", "Lighting", "Monitoring"]
    assert power["dc_w"] == pytest.approx(366 / eff, abs=0.01)
    assert power["session_kwh"] == pytest.approx(0.81, abs=0.01)
    assert power["usable_kwh"] == pytest.approx(13.824, abs=0.01)
    assert power["hours_on_bank"] == pytest.approx(33.99, abs=0.05)
    assert power["shows_on_bank"] == pytest.approx(17.0, abs=0.05)
    assert power["hours_on_bank_with_ac"] < power["hours_on_bank"]
    assert power["hours_on_bank_with_ac"] == pytest.approx(13.824e3 / ((366 + 1944) / eff),
                                                           abs=0.01)
    assert power["harvest_high_kwh"] == pytest.approx(11.7, abs=0.01)
    assert power["session_share_of_solar_day"] == pytest.approx(0.07, abs=0.001)
    assert power["generator_leg_w"] == 3600
    assert power["inverter_utilisation_with_ac"] == pytest.approx(2310 / 4000, abs=0.001)
    assert power["generator_utilisation_with_ac"] == pytest.approx(2310 / 3600, abs=0.001)


def test_power_reads_the_vessel_spec_not_a_copy(db):
    """A corrected bank size in project_meta must move the studio figures too."""
    _meta(db, "spec.bank_kwh", "30.72")
    power = studio_report(db)["power"]
    assert power["usable_kwh"] == pytest.approx(30.72 * 0.9, abs=0.01)
    assert power["hours_on_bank"] == pytest.approx(2 * 33.994, abs=0.1)


def test_power_ratios_refuse_rather_than_divide_by_zero(db):
    _meta(db, "spec.solar_panel_count", "0")
    power = studio_report(db)["power"]
    assert power["harvest_high_kwh"] == 0
    assert power["session_share_of_solar_day"] is None


# --- uplink -----------------------------------------------------------------

def test_uplink_verdicts_at_defaults(db):
    """1080p needs 9 Mbps against an 8 Mbps low end: conditional. 4K needs 27
    against a 25 Mbps high end: blocked. Neither is 'clear'."""
    uplink = studio_report(db)["uplink"]
    by_name = {p["name"]: p for p in uplink["profiles"]}
    hd, uhd = by_name["1080p60"], by_name["2160p30"]
    assert hd["required_mbps"] == 9.0 and hd["margin_low_mbps"] == -1.0
    assert hd["margin_high_mbps"] == 16.0 and hd["verdict"] == "conditional"
    assert uhd["required_mbps"] == 27.0 and uhd["margin_high_mbps"] == -2.0
    assert uhd["verdict"] == "blocked"
    assert uplink["caption_latency_ms"] == 300
    assert uplink["headroom"] == 1.5


def test_uplink_clears_when_the_low_end_carries_it(db):
    uplink = studio_report(db, {"starlink_upload_mbps_low": 10})["uplink"]
    assert uplink["profiles"][0]["verdict"] == "clear"


# --- funnel -----------------------------------------------------------------

def test_funnel_per_month_numbers_at_defaults(db):
    """The declared defaults: the boat's 1,200 viewers plus the partner church's
    20,000 placeholder viewers a month, run through the same two segments —
    767 installs, 159.95 new paid, 2,632.7 steady subscribers, $34,519.72 a
    month net. The figures worked by hand at the top of the file; the
    placeholder they rest on is flagged on the reach block, tested below."""
    funnel = studio_report(db)["funnel"]
    assert funnel["monthly"] == {
        "viewers_stream": 600, "viewers_event": 600, "viewers_partner": 20000,
        "viewers": 21200, "attendees": 120,
        "installs_stream": 752.6, "installs_event": 14.4, "installs": 767,
        "new_paid": 159.95,
        "travelers_viewers": 14840, "performers_viewers": 6360,
        "installs_travelers": 608, "installs_performers": 159,
        "new_paid_travelers": 152, "new_paid_performers": 7.95,
    }
    assert funnel["monthly"]["viewers"] == D_VIEWERS
    assert funnel["monthly"]["new_paid"] == round(D_NEW_PAID, 2)
    assert funnel["arpu_by_segment"] == {"travelers": 1499, "performers": 2653}
    assert funnel["arpu_gross_cents"] == round(D_ARPU) == 1556
    steady = funnel["steady_state"]
    assert steady["subscribers"] == round(D_STEADY, 1) == 2632.7
    assert steady["subscribers_travelers"] == 2533.3 and steady["subscribers_performers"] == 99.4
    assert steady["mrr_gross_cents"] == round(D_STEADY_T * ARPU_T + D_STEADY_P * ARPU_P)
    assert steady["mrr_net_cents"] == round(D_MRR_NET) == 3451972   # ≈ $34,520 a month
    assert steady["mrr_net_travelers_cents"] == round(D_STEADY_T * ARPU_T * KEEP) == 3227847
    assert steady["mrr_net_performers_cents"] == round(D_STEADY_P * ARPU_P * KEEP) == 224125
    assert steady["arr_net_cents"] == round(12 * D_MRR_NET)
    shares = {plan["key"]: plan["share"] for plan in funnel["by_plan"]}
    assert shares == {"traveler_bundle": round(152 / 159.95, 3),
                      "base": round(7.95 * 0.60 / 159.95, 3),
                      "base_broadcast": round(7.95 * 0.15 / 159.95, 3),
                      "ultimate": round(7.95 * 0.25 / 159.95, 3)}
    assert shares == {"traveler_bundle": 0.95, "base": 0.03, "base_broadcast": 0.007,
                      "ultimate": 0.012}
    new = {plan["key"]: plan["new_subscribers"] for plan in funnel["by_plan"]}
    assert new == {"traveler_bundle": 152, "base": 4.77, "base_broadcast": 1.19,
                   "ultimate": 1.99}


def test_funnel_per_month_numbers_on_the_boat_alone(db):
    """Two audiences and two segments, kept apart to new paid subscribers and
    only then summed: the boat's own figures worked by hand at the top of the
    file, with no partner stream counted."""
    funnel = studio_report(db, BOAT_ONLY)["funnel"]
    assert funnel["monthly"] == {
        "viewers_stream": 600, "viewers_event": 600, "viewers_partner": 0, "viewers": 1200,
        "attendees": 120,
        "installs_stream": 42.6, "installs_event": 14.4, "installs": 57,
        "new_paid": NEW_PAID,
        "travelers_viewers": 840, "performers_viewers": 360,
        "installs_travelers": 48, "installs_performers": 9,
        "new_paid_travelers": 12, "new_paid_performers": 0.45,
    }
    assert funnel["arpu_by_segment"] == {"travelers": 1499, "performers": 2653}
    assert funnel["arpu_gross_cents"] == round(ARPU) == 1541   # blended over all new paid
    steady = funnel["steady_state"]
    assert steady["subscribers"] == round(STEADY, 1) == 205.6
    assert steady["subscribers_travelers"] == 200 and steady["subscribers_performers"] == 5.6
    assert steady["mrr_gross_cents"] == round(STEADY_T * ARPU_T + STEADY_P * ARPU_P)
    assert steady["mrr_net_cents"] == round(MRR_NET) == 267516   # ≈ $2,675 a month
    assert steady["mrr_net_travelers_cents"] == round(STEADY_T * ARPU_T * KEEP) == 254830
    assert steady["mrr_net_performers_cents"] == round(STEADY_P * ARPU_P * KEEP) == 12686
    assert steady["arr_net_cents"] == round(12 * MRR_NET)
    # Plan shares are derived from the segment volumes: the donut reads the true mix.
    shares = {plan["key"]: plan["share"] for plan in funnel["by_plan"]}
    assert shares == {"traveler_bundle": round(12 / 12.45, 3), "base": round(0.27 / 12.45, 3),
                      "base_broadcast": round(0.0675 / 12.45, 3),
                      "ultimate": round(0.1125 / 12.45, 3)}
    assert shares == {"traveler_bundle": 0.964, "base": 0.022, "base_broadcast": 0.005,
                      "ultimate": 0.009}
    prices = {plan["key"]: plan["price_cents"] for plan in funnel["by_plan"]}
    assert prices == {"traveler_bundle": 1499, "base": 1266, "base_broadcast": 5265,
                      "ultimate": 4416}
    new = {plan["key"]: plan["new_subscribers"] for plan in funnel["by_plan"]}
    assert new == {"traveler_bundle": 12, "base": 0.27, "base_broadcast": 0.07,
                   "ultimate": 0.11}
    assert sum(plan["new_subscribers"] for plan in funnel["by_plan"]) == pytest.approx(
        NEW_PAID, abs=0.02)


def test_arpu_blends_annual_billing_per_plan(db):
    """A share of each segment pays the annual price ÷ 12 — 60% of travelers on
    the bundle, 35% of performers on their plans; the blended month is rounded
    per plan and the list prices ride along so the page can show both. Pro
    Broadcast has no annual price, so that bundle's annual is Base's annual
    plus twelve months of the add-on — the same as blending Base alone."""
    by_plan = {plan["key"]: plan for plan in studio_report(db)["funnel"]["by_plan"]}
    assert {k for plan in by_plan.values() for k in plan} == {
        "key", "name", "segment", "mix_share", "share", "price_cents", "list_monthly_cents",
        "annual_cents", "new_subscribers"}
    assert list(by_plan) == ["traveler_bundle", "base", "base_broadcast", "ultimate"]
    traveler = by_plan["traveler_bundle"]
    base, bundle, ultimate = by_plan["base"], by_plan["base_broadcast"], by_plan["ultimate"]
    assert (traveler["list_monthly_cents"], traveler["annual_cents"]) == (1998, 13998)
    assert (base["list_monthly_cents"], base["annual_cents"]) == (1499, 9999)
    assert (ultimate["list_monthly_cents"], ultimate["annual_cents"]) == (4999, 39999)
    assert (bundle["list_monthly_cents"], bundle["annual_cents"]) == (5498, 9999 + 12 * 3999)
    assert traveler["price_cents"] == round(0.40 * 1998 + 0.60 * 13998 / 12) == 1499
    assert base["price_cents"] == round(0.65 * 1499 + 0.35 * 9999 / 12) == 1266
    assert ultimate["price_cents"] == round(0.65 * 4999 + 0.35 * 39999 / 12) == 4416
    assert bundle["price_cents"] == round(0.65 * 1499 + 0.35 * 9999 / 12 + 3999) == 5265
    assert [plan[2:4] for plan in FUNNEL_PLANS] == [
        (1998, 13998), (1499, 9999), (5498, 57987), (4999, 39999)]
    # The within-segment mix is declared: the traveler plan is its segment's whole
    # mix, the performer plans split theirs as declared.
    assert {k: p["mix_share"] for k, p in by_plan.items()} == {
        "traveler_bundle": 1.0, "base": 0.60, "base_broadcast": 0.15, "ultimate": 0.25}
    assert {k: p["segment"] for k, p in by_plan.items()} == {
        "traveler_bundle": "traveler", "base": "performer", "base_broadcast": "performer",
        "ultimate": "performer"}
    # Every performer on annual billing: their month is the annual price ÷ 12 (the
    # add-on stays monthly); the traveler plan is blended on its own share.
    annual = {p["key"]: p["price_cents"]
              for p in studio_report(db, {"annual_share": 1})["funnel"]["by_plan"]}
    assert annual == {"traveler_bundle": 1499, "base": 833, "base_broadcast": 833 + 3999,
                      "ultimate": 3333}
    # And the traveler share moves only the traveler plan.
    travelers_annual = {
        p["key"]: p["price_cents"]
        for p in studio_report(db, {"traveler_annual_share": 1})["funnel"]["by_plan"]}
    assert travelers_annual == {"traveler_bundle": round(13998 / 12), "base": 1266,
                                "base_broadcast": 5265, "ultimate": 4416}


def test_annual_share_zero_reproduces_the_list_prices(db):
    """With nobody on annual billing every plan is its list monthly price: the
    traveler plan the $19.98 bundle, the performer ARPU the 2973.85 the model
    had before billing was blended."""
    funnel = studio_report(db, {"annual_share": 0, "traveler_annual_share": 0,
                                **BOAT_ONLY})["funnel"]
    assert [p["price_cents"] for p in funnel["by_plan"]] == [1998, 1499, 5498, 4999]
    assert all(p["price_cents"] == p["list_monthly_cents"] for p in funnel["by_plan"])
    assert funnel["arpu_by_segment"] == {"travelers": 1998, "performers": round(LIST_ARPU_P)}
    assert funnel["arpu_by_segment"]["performers"] == 2974
    assert funnel["arpu_gross_cents"] == round(
        (NEW_PAID_T * 1998 + NEW_PAID_P * LIST_ARPU_P) / NEW_PAID)
    steady = funnel["steady_state"]
    assert steady["mrr_gross_cents"] == round(STEADY_T * 1998 + STEADY_P * LIST_ARPU_P)
    assert steady["mrr_net_performers_cents"] == round(STEADY_P * LIST_ARPU_P * KEEP)


def test_zero_events_collapses_to_the_single_stream_funnel(db):
    """With no competition nights and no partner stream the model is exactly
    the single stream: 600 viewers — 420 travelers, 180 performers — 16.8 +
    4.5 installs, 4.2 + 0.225 new paid, 70 + 2.8125 steady; nothing from the
    dock. With the partner left in, the nights' 600 go and its 20,000 stay."""
    with_partner = studio_report(db, {"events_per_month": 0})["funnel"]["monthly"]
    assert with_partner["viewers_event"] == 0 and with_partner["viewers_partner"] == 20000
    assert with_partner["viewers"] == 600 + 20000 and with_partner["attendees"] == 0
    funnel = studio_report(db, {"events_per_month": 0, **BOAT_ONLY})["funnel"]
    assert funnel["monthly"] == {
        "viewers_stream": 600, "viewers_event": 0, "viewers_partner": 0, "viewers": 600,
        "attendees": 0,
        "installs_stream": 21.3, "installs_event": 0, "installs": 21.3,
        "new_paid": round(4.2 + 0.225, 2),
        "travelers_viewers": 420, "performers_viewers": 180,
        "installs_travelers": 16.8, "installs_performers": 4.5,
        "new_paid_travelers": 4.2, "new_paid_performers": round(0.225, 2),
    }
    steady = funnel["steady_state"]
    assert steady["subscribers"] == 72.8                          # 72.8125 at 1dp
    assert steady["subscribers_travelers"] == 70 and steady["subscribers_performers"] == 2.8
    assert steady["mrr_net_cents"] == round((70 * ARPU_T + 2.8125 * ARPU_P) * KEEP)


def test_dock_audience_converts_on_its_own_rate(db):
    """Attendees are not stream viewers: only attendee_to_install moves them,
    and they move the traveler column, because the dock crowd are travelers."""
    base = studio_report(db, BOAT_ONLY)["funnel"]["monthly"]
    more_dock = studio_report(db, {"attendee_to_install": 0.30, **BOAT_ONLY})["funnel"]["monthly"]
    assert more_dock["installs_stream"] == base["installs_stream"] == 42.6
    assert more_dock["installs_event"] == 36 and base["installs_event"] == 14.4
    assert more_dock["installs"] == round(42.6 + 36, 2)
    assert more_dock["installs_travelers"] == round(33.6 + 36, 2)
    assert more_dock["installs_performers"] == base["installs_performers"] == 9
    # A performer stream rate change leaves the dock and the travelers alone.
    more_stream = studio_report(db, {"viewer_to_install": 0.08, **BOAT_ONLY})["funnel"]["monthly"]
    assert more_stream["installs_event"] == 14.4
    assert more_stream["installs_performers"] == round(360 * 0.08, 2) == 28.8
    assert more_stream["installs_travelers"] == 48
    assert more_stream["installs_stream"] == round(33.6 + 28.8, 2)
    # And a traveler stream rate change leaves the performers alone.
    more_travelers = studio_report(db, {"traveler_viewer_to_install": 0.10,
                                        **BOAT_ONLY})["funnel"]["monthly"]
    assert more_travelers["installs_performers"] == 9
    assert more_travelers["installs_travelers"] == round(84 + 14.4, 2)
    assert more_travelers["installs_stream"] == round(84 + 9, 2)
    # Four nights with a 200-strong dock: the figures the parity scenarios use.
    busy = studio_report(db, {"events_per_month": 4, "dock_attendees_per_event": 200,
                              **BOAT_ONLY})
    monthly = busy["funnel"]["monthly"]
    assert monthly["viewers_event"] == 1200 and monthly["viewers"] == 1800
    assert monthly["travelers_viewers"] == 1260 and monthly["performers_viewers"] == 540
    assert monthly["attendees"] == 800 and monthly["installs_event"] == 96
    assert monthly["installs_stream"] == round(1260 * 0.04 + 540 * 0.025, 2) == 63.9
    assert monthly["installs"] == round(63.9 + 96, 2)


# --- the two segments ---------------------------------------------------------

def test_traveler_share_zero_reproduces_the_performer_only_figures(db):
    """With no travelers in the audience the stream is the original performer
    funnel: 1,200 viewers at 2.5% → 30 installs, at 5% → 1.5 new paid, over 8%
    churn 18.75 steady at the performer ARPU. The dock crowd are still
    travelers — they had the demo in person — so the traveler column carries
    the dock alone; with nobody on the dock either, the whole model is the
    performer-only one, plan shares and trajectory included."""
    funnel = studio_report(db, {"traveler_share": 0, **BOAT_ONLY})["funnel"]
    monthly = funnel["monthly"]
    assert monthly["travelers_viewers"] == 0 and monthly["performers_viewers"] == 1200
    assert monthly["installs_performers"] == 30 and monthly["installs_stream"] == 30
    assert monthly["installs_travelers"] == monthly["installs_event"] == 14.4
    assert monthly["new_paid_performers"] == 1.5
    assert monthly["new_paid_travelers"] == round(14.4 * 0.25, 2) == 3.6
    steady = funnel["steady_state"]
    assert steady["subscribers_performers"] == round(1.5 / 0.08, 1) == 18.8
    assert steady["mrr_net_performers_cents"] == round(18.75 * ARPU_P * KEEP) == 42288
    assert steady["subscribers_travelers"] == round(3.6 / 0.06, 1) == 60
    assert funnel["arpu_by_segment"]["performers"] == 2653

    alone = studio_report(db, {"traveler_share": 0, "attendee_to_install": 0,
                               **BOAT_ONLY})["funnel"]
    assert alone["monthly"]["installs"] == 30 and alone["monthly"]["new_paid"] == 1.5
    assert alone["monthly"]["new_paid_travelers"] == 0
    assert alone["steady_state"]["subscribers"] == 18.8
    assert alone["steady_state"]["subscribers_travelers"] == 0
    assert alone["steady_state"]["mrr_net_travelers_cents"] == 0
    assert alone["steady_state"]["mrr_net_cents"] == 42288
    assert alone["arpu_gross_cents"] == 2653                    # the performer ARPU alone
    assert {p["key"]: p["share"] for p in alone["by_plan"]} == {
        "traveler_bundle": 0, "base": 0.60, "base_broadcast": 0.15, "ultimate": 0.25}
    assert {p["key"]: p["new_subscribers"] for p in alone["by_plan"]} == pytest.approx({
        "traveler_bundle": 0, "base": 0.9, "base_broadcast": 0.225, "ultimate": 0.375},
        abs=0.006)
    subs, cumulative = 0.0, 0.0
    for row in alone["trajectory"]:
        subs = subs * 0.92 + 1.5
        cumulative += subs * ARPU_P * KEEP
        assert row["subscribers_travelers"] == 0
        assert row["subscribers"] == row["subscribers_performers"] == round(subs, 1)
        assert row["cumulative_net_cents"] == round(cumulative)


def test_traveler_share_one_leaves_performer_plans_at_zero(db):
    """Everyone in the audience is a traveler: 1,200 × 4% + 14.4 from the dock
    = 62.4 installs, 15.6 new paid, all on Base + Conversation Mode; the
    performer plans take nobody and the blended ARPU is the traveler plan's."""
    funnel = studio_report(db, {"traveler_share": 1, **BOAT_ONLY})["funnel"]
    monthly = funnel["monthly"]
    assert monthly["performers_viewers"] == 0 and monthly["travelers_viewers"] == 1200
    assert monthly["installs_performers"] == 0 and monthly["new_paid_performers"] == 0
    assert monthly["installs_travelers"] == round(48 + 14.4, 2) == 62.4
    assert monthly["installs_stream"] == 48 and monthly["installs"] == 62.4
    assert monthly["new_paid_travelers"] == monthly["new_paid"] == 15.6
    for plan in funnel["by_plan"]:
        if plan["segment"] == "performer":
            assert plan["new_subscribers"] == 0 and plan["share"] == 0
        else:
            assert plan["new_subscribers"] == 15.6 and plan["share"] == 1.0
    assert funnel["arpu_gross_cents"] == funnel["arpu_by_segment"]["travelers"] == 1499
    steady = funnel["steady_state"]
    assert steady["subscribers_performers"] == 0 and steady["mrr_net_performers_cents"] == 0
    assert steady["subscribers"] == steady["subscribers_travelers"] == 260
    assert steady["mrr_net_cents"] == steady["mrr_net_travelers_cents"] == round(
        260 * 1499 * KEEP)
    assert all(row["subscribers_performers"] == 0 for row in funnel["trajectory"])


def test_dock_attendees_convert_at_the_traveler_rate_onto_the_traveler_plan(db):
    """The dock crowd had the two-language demo in person: their installs are
    traveler installs, they pay at the traveler rate, and they land on Base +
    Conversation Mode. The performer paid rate cannot move them; the traveler
    one can."""
    dock_only = {"traveler_share": 0, "viewer_to_install": 0}     # nothing from the stream
    funnel = studio_report(db, dock_only)["funnel"]
    monthly = funnel["monthly"]
    assert monthly["installs"] == monthly["installs_event"] == monthly["installs_travelers"] == 14.4
    assert monthly["installs_performers"] == 0 and monthly["new_paid_performers"] == 0
    assert monthly["new_paid_travelers"] == monthly["new_paid"] == round(14.4 * 0.25, 2)
    by_plan = {p["key"]: p for p in funnel["by_plan"]}
    assert by_plan["traveler_bundle"]["new_subscribers"] == 3.6
    assert by_plan["traveler_bundle"]["share"] == 1.0
    assert all(by_plan[k]["new_subscribers"] == 0 for k in ("base", "base_broadcast",
                                                            "ultimate"))
    assert funnel["steady_state"]["subscribers_travelers"] == round(3.6 / 0.06, 1) == 60
    # The performer paid rate does not touch them; the traveler paid rate does.
    same = studio_report(db, dict(dock_only, install_to_paid=0.5))["funnel"]["monthly"]
    assert same["new_paid_travelers"] == 3.6 and same["new_paid"] == 3.6
    moved = studio_report(db, dict(dock_only, traveler_install_to_paid=0.5))["funnel"]["monthly"]
    assert moved["new_paid_travelers"] == 7.2 and moved["new_paid"] == 7.2


def test_derived_plan_shares_sum_to_one(db):
    """Plan shares are derived — each plan's new subscribers over all new paid
    — so across both segments they partition the month's subscribers; with
    nobody converting there is nothing to share and every share is 0."""
    funnel = studio_report(db)["funnel"]
    shares = [plan["share"] for plan in funnel["by_plan"]]
    assert sum(shares) == pytest.approx(1.0, abs=0.002)        # 0.95 + 0.03 + 0.007 + 0.012
    new_paid = funnel["monthly"]["new_paid"]
    for plan in funnel["by_plan"]:
        expected = (funnel["monthly"]["new_paid_travelers"] if plan["segment"] == "traveler"
                    else funnel["monthly"]["new_paid_performers"] * plan["mix_share"])
        assert plan["share"] == round(expected / new_paid, 3)
    # Across scenarios the rounded shares partition to within their rounding.
    for overrides in ({"traveler_share": 0.5}, {"traveler_share": 0.2, "install_to_paid": 0.2},
                      {"events_per_month": 0}, {"mix_base": 0.7, "mix_ultimate": 0.15}):
        plans = studio_report(db, overrides)["funnel"]["by_plan"]
        assert sum(p["share"] for p in plans) == pytest.approx(1.0, abs=0.002), overrides
        assert sum(p["mix_share"] for p in plans if p["segment"] == "performer") == (
            pytest.approx(1.0, abs=1e-6))
        assert sum(p["mix_share"] for p in plans if p["segment"] == "traveler") == 1.0
    nobody = studio_report(db, {"viewer_to_install": 0, "traveler_viewer_to_install": 0,
                                "attendee_to_install": 0})["funnel"]
    assert nobody["monthly"]["new_paid"] == 0
    assert [p["share"] for p in nobody["by_plan"]] == [0, 0, 0, 0]
    assert [p["new_subscribers"] for p in nobody["by_plan"]] == [0, 0, 0, 0]
    assert nobody["arpu_gross_cents"] == 2653          # the performer ARPU, not a 0 ÷ 0


def test_arpu_is_blended_over_new_paid_by_segment(db):
    """The donut's centre: travelers at the bundle's blended month, performers
    at their mix-weighted month, weighted by who actually converted — 12 of
    12.45 are travelers, so the blend sits near the bundle."""
    funnel = studio_report(db, BOAT_ONLY)["funnel"]
    assert funnel["arpu_by_segment"] == {"travelers": ARPU_T, "performers": round(ARPU_P)}
    assert funnel["arpu_gross_cents"] == round(
        (12 * ARPU_T + 0.45 * ARPU_P) / 12.45) == 1541
    # At the defaults 152 of 159.95 are travelers: the blend sits a little higher.
    assert studio_report(db)["funnel"]["arpu_gross_cents"] == round(
        (152 * ARPU_T + 7.95 * ARPU_P) / 159.95) == 1556
    # Half the audience travelers: the blend moves toward the performer price.
    half = studio_report(db, {"traveler_share": 0.5})["funnel"]
    new_paid_t = half["monthly"]["new_paid_travelers"]
    new_paid_p = half["monthly"]["new_paid_performers"]
    assert half["arpu_gross_cents"] == round(
        (new_paid_t * ARPU_T + new_paid_p * ARPU_P) / (new_paid_t + new_paid_p))
    assert 1499 < half["arpu_gross_cents"] < 2653


def test_steady_totals_are_the_segment_sums(db):
    """Two steady states, each its segment's new paid over its own churn,
    summed only at the end; the subscription lens repeats them by segment."""
    for overrides in ({}, {"traveler_share": 0.3}, {"monthly_churn": 0.2},
                      {"traveler_monthly_churn": 0.1, "events_per_month": 0}):
        report = studio_report(db, overrides or None)
        steady = report["funnel"]["steady_state"]
        monthly = report["funnel"]["monthly"]
        assert steady["subscribers"] == pytest.approx(
            steady["subscribers_travelers"] + steady["subscribers_performers"], abs=0.11)
        assert steady["mrr_net_cents"] == pytest.approx(
            steady["mrr_net_travelers_cents"] + steady["mrr_net_performers_cents"], abs=1)
        assert steady["arr_net_cents"] == pytest.approx(12 * steady["mrr_net_cents"], abs=6)
        assert steady["mrr_gross_cents"] == pytest.approx(steady["mrr_net_cents"] / KEEP, abs=1)
        churn_t = report["funnel"]["inputs"]["traveler_monthly_churn"]["value"]
        churn_p = report["funnel"]["inputs"]["monthly_churn"]["value"]
        # new paid is reported at 2dp, so half a cent of a subscriber over churn.
        assert steady["subscribers_travelers"] == pytest.approx(
            monthly["new_paid_travelers"] / churn_t, abs=0.06 + 0.005 / churn_t)
        assert steady["subscribers_performers"] == pytest.approx(
            monthly["new_paid_performers"] / churn_p, abs=0.06 + 0.005 / churn_p)
        sub = report["lenses"]["subscription"]
        assert sub["by_segment"] == {
            "travelers": {"steady_subscribers": steady["subscribers_travelers"],
                          "steady_mrr_net_cents": steady["mrr_net_travelers_cents"]},
            "performers": {"steady_subscribers": steady["subscribers_performers"],
                           "steady_mrr_net_cents": steady["mrr_net_performers_cents"]}}
        assert sub["steady_subscribers"] == steady["subscribers"]
        assert sub["steady_mrr_net_cents"] == steady["mrr_net_cents"]


def test_trajectory_totals_are_the_cohort_sums(db):
    """Two cohorts run separately — travelers at 6% churn and the bundle's
    price, performers at 8% and theirs — and every row's totals are their sum
    (plus the baseline's decayed remainder); replayed by hand to the cent."""
    rows = studio_report(db, BOAT_ONLY)["funnel"]["trajectory"]
    for row, (subs_t, subs_p, mrr, cumulative) in zip(rows, _replay(), strict=True):
        assert row["subscribers_travelers"] == round(subs_t, 1)
        assert row["subscribers_performers"] == round(subs_p, 1)
        assert row["subscribers"] == round(subs_t + subs_p, 1)
        assert row["show_driven_subscribers"] == row["subscribers"]
        assert row["subscribers"] == pytest.approx(
            row["subscribers_travelers"] + row["subscribers_performers"]
            + row["baseline_subscribers"], abs=0.11)
        assert row["mrr_net_cents"] == round(mrr)
        assert row["cumulative_net_cents"] == round(cumulative)
    assert rows[-1]["subscribers_travelers"] == pytest.approx(STEADY_T * (1 - 0.94 ** 36), abs=0.06)
    assert rows[-1]["subscribers_performers"] == pytest.approx(STEADY_P * (1 - 0.92 ** 36), abs=0.06)
    # With a baseline the per-segment columns are untouched: it is neither cohort.
    with_base = studio_report(db, {"baseline_subscribers": 300, **BOAT_ONLY})["funnel"]["trajectory"]
    for row, plain in zip(with_base, rows, strict=True):
        assert row["subscribers_travelers"] == plain["subscribers_travelers"]
        assert row["subscribers_performers"] == plain["subscribers_performers"]
        assert row["subscribers"] == pytest.approx(
            row["subscribers_travelers"] + row["subscribers_performers"]
            + row["baseline_subscribers"], abs=0.16)


def test_every_funnel_input_names_its_source(db):
    inputs = studio_report(db, {"monthly_churn": 0.1})["funnel"]["inputs"]
    assert set(inputs) == {
        "viewers_per_show", "shows_per_month", "viewer_to_install", "install_to_paid",
        "monthly_churn", "store_commission", "annual_share", "baseline_subscribers",
        "events_per_month", "event_viewers_multiplier", "dock_attendees_per_event",
        "attendee_to_install", "traveler_share", "traveler_viewer_to_install",
        "traveler_install_to_paid", "traveler_annual_share", "traveler_monthly_churn",
        "partner_church_live_viewers", "partner_artist_subscribers",
        "partner_live_share", "partners_include_hypothetical", "target_subscribers",
        "target_month"}
    assert set(inputs) == set(FUNNEL_INPUT_KEYS)
    assert inputs["annual_share"] == {"value": 0.35, "source": "assumed"}
    assert inputs["traveler_share"] == {"value": 0.70, "source": "assumed"}
    assert inputs["traveler_monthly_churn"] == {"value": 0.06, "source": "assumed"}
    assert inputs["baseline_subscribers"] == {"value": 0, "source": "assumed"}
    assert inputs["monthly_churn"] == {"value": 0.1, "source": "override"}
    assert inputs["dock_attendees_per_event"] == {"value": 60, "source": "assumed"}
    assert inputs["partner_church_live_viewers"] == {"value": 5000, "source": "assumed"}
    assert inputs["partner_artist_subscribers"] == {"value": 6_190_000, "source": "assumed"}
    assert inputs["partners_include_hypothetical"] == {"value": 0, "source": "assumed"}
    assert inputs["target_subscribers"] == {"value": 3_000, "source": "assumed"}
    assert inputs["target_month"] == {"value": 3, "source": "assumed"}
    assert all(e["source"] in {"assumed", "observed", "override", "meta"}
               for e in inputs.values())


def test_trajectory_starts_at_new_paid_and_converges(db):
    """With no baseline entered the book starts empty, and the show-driven
    columns are the columns: there is nothing to subtract. At the defaults
    month one is the month's 159.95 new paid — 152 travelers, 7.95 performers
    — earning $2,116.01 net."""
    report = studio_report(db)
    rows = report["funnel"]["trajectory"]
    steady = report["funnel"]["steady_state"]["subscribers"]
    assert len(rows) == 36 and rows[0]["month"] == 1
    assert list(rows[0]) == [
        "month", "subscribers", "subscribers_travelers", "subscribers_performers",
        "baseline_subscribers", "mrr_net_cents", "cumulative_net_cents",
        "show_driven_subscribers", "show_driven_mrr_net_cents",
        "show_driven_cumulative_net_cents"]
    assert rows[0]["subscribers"] == round(D_NEW_PAID, 1) == pytest.approx(159.95, abs=0.06)
    assert rows[0]["subscribers_travelers"] == 152 and rows[0]["subscribers_performers"] == 8.0
    assert rows[0]["mrr_net_cents"] == round((D_NEW_PAID_T * ARPU_T + D_NEW_PAID_P * ARPU_P) * KEEP)
    assert rows[0]["mrr_net_cents"] == 211601
    assert rows[0]["cumulative_net_cents"] == rows[0]["mrr_net_cents"]
    # And on the boat alone, the 12.45 the hand arithmetic gives.
    boat = studio_report(db, BOAT_ONLY)["funnel"]["trajectory"][0]
    assert boat["subscribers"] == round(NEW_PAID, 1)                # 12.45 at 1dp
    assert boat["subscribers_travelers"] == 12 and boat["subscribers_performers"] == 0.5
    assert boat["mrr_net_cents"] == round((NEW_PAID_T * ARPU_T + NEW_PAID_P * ARPU_P) * KEEP)
    assert boat["mrr_net_cents"] == 16305
    subs = [r["subscribers"] for r in rows]
    assert subs == sorted(subs) and subs[-1] < steady
    assert steady - subs[-1] < steady - subs[11] < steady - subs[0]
    cumulative = [r["cumulative_net_cents"] for r in rows]
    assert cumulative == sorted(cumulative)
    assert all(b > a for a, b in pairwise(cumulative))
    for row in rows:
        assert row["baseline_subscribers"] == 0
        assert row["show_driven_subscribers"] == row["subscribers"]
        assert row["show_driven_mrr_net_cents"] == row["mrr_net_cents"]
        assert row["show_driven_cumulative_net_cents"] == row["cumulative_net_cents"]


def test_cumulative_is_rounded_once_from_the_float(db):
    """Summing rounded rows would drift by a cent here and there; the column
    is rounded from the running float instead."""
    rows = studio_report(db)["funnel"]["trajectory"]
    summed = 0
    for row, (_, _, mrr, running) in zip(rows, _replay(36, D_NEW_PAID_T, D_NEW_PAID_P),
                                         strict=True):
        summed += row["mrr_net_cents"]
        assert row["mrr_net_cents"] == round(mrr)
        assert row["cumulative_net_cents"] == round(running)
        assert row["show_driven_cumulative_net_cents"] == round(running)
    # The rounded rows drift from the float; the column does not.
    assert abs(summed - rows[-1]["cumulative_net_cents"]) <= len(rows) // 2


# --- baseline -----------------------------------------------------------------

def test_trajectory_starts_at_the_baseline_and_show_driven_columns_exclude_it(db):
    """A declared baseline of 500 paying subscribers: the book starts there and
    decays while the shows add to it; the show-driven columns are the same
    numbers as with no baseline at all, because the studio can only claim the
    subscribers it brought. The baseline is a mixed book nobody has split by
    need, so it decays at the performer churn and earns the blended ARPU — a
    declared choice, not a segment."""
    plain = studio_report(db, BOAT_ONLY)
    report = studio_report(db, {"baseline_subscribers": 500, **BOAT_ONLY})
    rows, plain_rows = report["funnel"]["trajectory"], plain["funnel"]["trajectory"]
    assert rows[0]["subscribers"] == round(500 * 0.92 + NEW_PAID, 1)
    assert rows[0]["baseline_subscribers"] == 460.0
    assert rows[0]["show_driven_subscribers"] == round(NEW_PAID, 1)
    assert rows[0]["subscribers_travelers"] == 12 and rows[0]["subscribers_performers"] == 0.5
    show_mrr = (NEW_PAID_T * ARPU_T + NEW_PAID_P * ARPU_P) * KEEP
    assert rows[0]["mrr_net_cents"] == round(460 * ARPU * KEEP + show_mrr)
    assert rows[0]["show_driven_mrr_net_cents"] == round(show_mrr)
    base = 500.0
    for row, plain_row, (subs_t, subs_p, _, _) in zip(rows, plain_rows, _replay(), strict=True):
        base *= 0.92
        assert row["baseline_subscribers"] == round(base, 1)
        assert row["subscribers"] == round(subs_t + subs_p + base, 1)
        assert row["show_driven_subscribers"] == round(subs_t + subs_p, 1)
        # Show-driven is exactly the no-baseline model, to the cent.
        assert row["show_driven_mrr_net_cents"] == plain_row["mrr_net_cents"]
        assert row["show_driven_cumulative_net_cents"] == plain_row["cumulative_net_cents"]
        assert row["cumulative_net_cents"] > row["show_driven_cumulative_net_cents"]
    # The baseline decays toward nothing; the whole book converges on the same steady state.
    assert rows[-1]["baseline_subscribers"] == round(500 * 0.92 ** 36, 1) < 25
    assert report["funnel"]["steady_state"] == plain["funnel"]["steady_state"]
    # The month-12 lens row carries both views and says which is which.
    m12 = report["lenses"]["subscription"]["month_12"]
    assert m12["subscribers"] == rows[11]["subscribers"] > m12["show_driven_subscribers"]
    assert m12["show_driven_cumulative_net_cents"] == plain_rows[11]["cumulative_net_cents"]
    assert report["funnel"]["inputs"]["baseline_subscribers"] == {
        "value": 500.0, "source": "override"}


def test_breakeven_is_unchanged_by_the_baseline(db):
    """Payback is read off the show-driven columns: a baseline of 5,000
    subscribers would 'pay back' the kit in month 1 if the book's own revenue
    counted, and it must not."""
    _boat_order(db, "moorage", 20000, "dockage", ordered_at="2026-08-01T00:00:00Z")
    _boat_order(db, "electronics_nav", 250_000)
    plain = studio_report(db, BOAT_ONLY)["breakeven"]
    with_baseline = studio_report(db, {"baseline_subscribers": 5000, **BOAT_ONLY})["breakeven"]
    assert plain["kit_month"] is not None and plain["slip_month"] is not None
    assert plain["project_month"] is not None
    assert with_baseline == plain
    rows = studio_report(db, {"baseline_subscribers": 5000, **BOAT_ONLY})["funnel"]["trajectory"]
    assert rows[0]["cumulative_net_cents"] > 250_000      # the book alone would clear it
    assert rows[0]["show_driven_cumulative_net_cents"] < 78896
    assert with_baseline["kit_month"] == next(
        r["month"] for r in rows if r["show_driven_cumulative_net_cents"] >= 78896)
    assert with_baseline["slip_month"] == next(
        r["month"] for r in rows if r["show_driven_mrr_net_cents"] >= 20000)


def test_baseline_block_says_not_entered_until_it_is(db):
    report = studio_report(db)
    assert report["baseline"] == {"subscribers": 0, "source": "assumed", "note": BASELINE_NOTE}
    assert "okey studio baseline --subscribers N" in BASELINE_NOTE
    assert "studio.baseline_subscribers" in BASELINE_NOTE
    _meta(db, "studio.baseline_subscribers", "120")
    report = studio_report(db)
    assert report["baseline"] == {"subscribers": 120.0, "source": "meta", "note": BASELINE_NOTE}
    assert report["funnel"]["trajectory"][0]["baseline_subscribers"] == round(120 * 0.92, 1)
    forced = studio_report(db, {"baseline_subscribers": 0})["baseline"]
    assert forced["subscribers"] == 0 and forced["source"] == "override"


def test_horizon_override_shortens_the_trajectory(db):
    report = studio_report(db, {"horizon_months": 6})
    assert len(report["funnel"]["trajectory"]) == 6
    assert report["lenses"]["subscription"]["month_12"] is None
    assert report["breakeven"]["horizon_months"] == 6


# --- reach: partner streams -------------------------------------------------

PARTNER_ROW_KEYS = [
    "key", "name", "handle", "kind", "status", "active", "streams_per_month", "subscribers",
    "live_viewers_per_stream", "placeholder", "viewers_per_month", "international_share",
    "viewers_abroad", "new_paid_per_month", "note"]


def test_partners_are_data_committed_first():
    """Two partners, committed first: Partner church (a church, four Sunday
    streams, its audience held in a placeholder) and Partner artist (an artist,
    hypothetical, its audience derived from the owner's subscriber count).
    Each row names exactly one assumption that holds its audience figure."""
    assert [p["key"] for p in PARTNERS] == ["church", "artist"]
    assert [p["status"] for p in PARTNERS] == ["committed", "hypothetical"]
    assert PARTNER_STATUSES == ("committed", "hypothetical")
    assert PARTNER_KINDS == ("church", "artist")
    for partner in PARTNERS:
        assert set(partner) == {"key", "name", "handle", "kind", "status", "streams_per_month",
                                "subscribers_key", "live_viewers_per_stream_key",
                                "international_share", "note"}
        assert partner["status"] in PARTNER_STATUSES and partner["kind"] in PARTNER_KINDS
        assert partner["streams_per_month"] > 0
        assert 0 <= partner["international_share"] <= 1
        assert partner["note"]
        keys = [partner["subscribers_key"], partner["live_viewers_per_stream_key"]]
        assert sum(key is not None for key in keys) == 1                   # exactly one
        assert next(key for key in keys if key) in STUDIO_ASSUMPTIONS
    church, artist = PARTNERS
    assert church["kind"] == "church" and church["streams_per_month"] == 4
    assert church["live_viewers_per_stream_key"] == "partner_church_live_viewers"
    assert church["international_share"] == 0.15
    assert "OBS overlay" in church["note"] and "placeholder" in church["note"]
    assert artist["kind"] == "artist" and artist["streams_per_month"] == 1
    assert artist["subscribers_key"] == "partner_artist_subscribers"
    assert artist["international_share"] == 0.70
    assert artist["handle"] == "a 6.19M-subscriber channel · YouTube"
    assert "6.19M" in artist["note"] and "bilingual demo" in artist["note"]


def test_reach_at_defaults_counts_the_committed_partner_only(db):
    """Partner church counts by default: 5,000 × 4 = 20,000 viewers a month,
    15% of them abroad, 147.5 new paid at the per-viewer yield; its audience
    figure is flagged as the placeholder it is. Partner artist is on the list at its
    full size — 6.19M × 2% × 1 = 123,800 — but inactive, so it adds nothing.
    The totals sum the counted partners only; the languages are the app's."""
    report = studio_report(db)
    reach = report["reach"]
    assert list(reach) == ["partners", "viewers_partner_per_month", "viewers_abroad_per_month",
                           "viewers_total_per_month", "languages", "note"]
    assert [list(row) for row in reach["partners"]] == [PARTNER_ROW_KEYS, PARTNER_ROW_KEYS]
    church, artist = reach["partners"]
    assert church == {
        "key": "church", "name": "Partner church",
        "handle": "Sunday live streams", "kind": "church",
        "status": "committed", "active": True, "streams_per_month": 4,
        "subscribers": None, "live_viewers_per_stream": 5000, "placeholder": True,
        "viewers_per_month": 20000, "international_share": 0.15, "viewers_abroad": 3000,
        "new_paid_per_month": round(20000 * YIELD, 2), "note": PARTNERS[0]["note"]}
    assert church["new_paid_per_month"] == 147.5
    assert artist == {
        "key": "artist", "name": "Partner artist", "handle": "a 6.19M-subscriber channel · YouTube",
        "kind": "artist", "status": "hypothetical", "active": False, "streams_per_month": 1,
        "subscribers": 6_190_000, "live_viewers_per_stream": None, "placeholder": False,
        "viewers_per_month": ARTIST_VIEWERS, "international_share": 0.70,
        "viewers_abroad": round(ARTIST_VIEWERS * 0.70, 2), "new_paid_per_month": 0,
        "note": PARTNERS[1]["note"]}
    assert artist["viewers_per_month"] == 123800 and artist["viewers_abroad"] == 86660
    assert isinstance(church["active"], bool) and isinstance(artist["active"], bool)
    assert isinstance(church["placeholder"], bool)
    assert reach["viewers_partner_per_month"] == 20000 == report["funnel"]["monthly"]["viewers_partner"]
    assert reach["viewers_abroad_per_month"] == 3000                      # counted partners only
    assert reach["viewers_total_per_month"] == 21200 == report["funnel"]["monthly"]["viewers"]
    assert reach["languages"] == {"base": 20, "total": 80} == LYRICSHOW["languages"]
    assert reach["note"] == REACH_NOTE and "declared, not measured" in REACH_NOTE
    # The whole-valued counts read as the whole numbers they are.
    assert (church["live_viewers_per_stream"], artist["subscribers"]) == (5000, 6_190_000)


def test_hypothetical_partner_counts_only_when_the_switch_is_on(db):
    """With the switch on Partner artist is counted: 20,000 + 123,800 = 143,800
    partner viewers, 3,000 + 86,660 abroad, and 913.03 new paid a month from
    the artist's stream at the per-viewer yield. The switch moves only the
    hypothetical row; a bigger live share moves the artist's audience, and
    only the artist's."""
    base = studio_report(db)
    on = studio_report(db, {"partners_include_hypothetical": 1})
    reach, monthly = on["reach"], on["funnel"]["monthly"]
    assert [row["active"] for row in reach["partners"]] == [True, True]
    assert reach["viewers_partner_per_month"] == 20000 + 123800 == 143800
    assert monthly["viewers_partner"] == 143800 and monthly["viewers"] == 1200 + 143800
    assert reach["viewers_total_per_month"] == 145000
    assert reach["viewers_abroad_per_month"] == 3000 + 86660 == 89660
    artist = reach["partners"][1]
    assert artist["viewers_per_month"] == 123800                         # unchanged: it was always listed
    assert artist["new_paid_per_month"] == pytest.approx(123800 * YIELD, abs=0.01)
    assert artist["new_paid_per_month"] == pytest.approx(913.03, abs=0.01)
    assert artist["placeholder"] is False
    assert reach["partners"][0] == base["reach"]["partners"][0]            # the church is untouched
    # A 5% live share: 6.19M × 0.05 = 309,500 viewers from the artist; the church
    # is counted directly and does not move.
    share = studio_report(db, {"partners_include_hypothetical": 1, "partner_live_share": 0.05})
    assert share["reach"]["partners"][1]["viewers_per_month"] == 309500
    assert share["reach"]["partners"][0]["viewers_per_month"] == 20000
    assert share["reach"]["viewers_partner_per_month"] == 329500
    # Off, the live share still sizes the listed row but counts nothing.
    off = studio_report(db, {"partner_live_share": 0.05})
    assert off["reach"]["partners"][1]["viewers_per_month"] == 309500
    assert off["reach"]["partners"][1]["new_paid_per_month"] == 0
    assert off["reach"]["viewers_partner_per_month"] == 20000
    # The flag resolves from project_meta like any assumption.
    _meta(db, "studio.partners_include_hypothetical", "1")
    via_meta = studio_report(db)
    assert via_meta["funnel"]["inputs"]["partners_include_hypothetical"] == {
        "value": 1.0, "source": "meta"}
    assert via_meta["reach"]["viewers_partner_per_month"] == 143800


def test_partner_viewers_enter_the_funnel_before_the_split(db):
    """A partner's viewer is a viewer: the month's viewers are stream + event
    + partner, and the traveler/performer split, the install rates and the
    paid rates run over all of them. `installs_stream` is every non-dock
    install, partner viewers included; only the viewer count is kept apart.
    Each partner row's new paid is its counted viewers at the per-viewer
    yield, so the rows and the funnel agree: the month's new paid is every
    viewer at that yield plus the dock crowd at the traveler paid rate."""
    for overrides in ({}, {"partners_include_hypothetical": 1},
                      {"partners_include_hypothetical": 1, "partner_live_share": 0.01},
                      {"traveler_share": 0.5}, {"events_per_month": 0}):
        report = studio_report(db, overrides or None)
        v = {k: e["value"] for k, e in report["funnel"]["inputs"].items()}
        m = report["funnel"]["monthly"]
        assert m["viewers"] == pytest.approx(
            m["viewers_stream"] + m["viewers_event"] + m["viewers_partner"], abs=0.02)
        assert m["travelers_viewers"] == pytest.approx(m["viewers"] * v["traveler_share"], abs=0.02)
        assert m["performers_viewers"] == pytest.approx(m["viewers"] - m["travelers_viewers"],
                                                       abs=0.02)
        assert m["installs_stream"] == pytest.approx(
            m["travelers_viewers"] * v["traveler_viewer_to_install"]
            + m["performers_viewers"] * v["viewer_to_install"], abs=0.02)
        assert m["installs"] == pytest.approx(m["installs_stream"] + m["installs_event"], abs=0.02)
        per_viewer = (v["traveler_share"] * v["traveler_viewer_to_install"]
                      * v["traveler_install_to_paid"]
                      + (1 - v["traveler_share"]) * v["viewer_to_install"] * v["install_to_paid"])
        assert m["new_paid"] == pytest.approx(
            m["viewers"] * per_viewer + m["installs_event"] * v["traveler_install_to_paid"],
            abs=0.02)
        partners = report["reach"]["partners"]
        for row in partners:
            expected = row["viewers_per_month"] * per_viewer if row["active"] else 0
            assert row["new_paid_per_month"] == pytest.approx(expected, abs=0.01), row["key"]
        boat_viewers = m["viewers_stream"] + m["viewers_event"]
        assert sum(row["new_paid_per_month"] for row in partners) + boat_viewers * per_viewer \
            + m["installs_event"] * v["traveler_install_to_paid"] == pytest.approx(m["new_paid"],
                                                                                 abs=0.03)
    # And with no partner counted, the funnel is the boat's own to the last place.
    assert studio_report(db, BOAT_ONLY)["funnel"]["monthly"]["viewers_partner"] == 0


def test_placeholder_flag_clears_when_the_figure_is_entered(db):
    """the partner church's audience is a labelled placeholder until the owner enters
    it: the row says so while the declared 5,000 is the figure in use, and
    stops saying so the moment project_meta (okey studio partner) or an
    override supplies one — the figure moves, the flag clears. the partner artist's
    subscriber count is the owner's, never a placeholder."""
    row = studio_report(db)["reach"]["partners"][0]
    assert row["placeholder"] is True and row["live_viewers_per_stream"] == 5000
    _meta(db, "studio.partner_church_live_viewers", "2500")
    report = studio_report(db)
    row = report["reach"]["partners"][0]
    assert row["placeholder"] is False
    assert row["live_viewers_per_stream"] == 2500 and row["viewers_per_month"] == 10000
    assert report["funnel"]["inputs"]["partner_church_live_viewers"] == {
        "value": 2500.0, "source": "meta"}
    assert report["funnel"]["monthly"]["viewers_partner"] == 10000
    assert report["reach"]["viewers_abroad_per_month"] == 1500
    forced = studio_report(db, {"partner_church_live_viewers": 8000})["reach"]["partners"][0]
    assert forced["placeholder"] is False and forced["viewers_per_month"] == 32000
    # The artist's count resolves from meta too, and was never a placeholder.
    _meta(db, "studio.partner_artist_subscribers", "7000000")
    on = studio_report(db, {"partners_include_hypothetical": 1})
    artist = on["reach"]["partners"][1]
    assert artist["subscribers"] == 7_000_000 and artist["placeholder"] is False
    assert artist["viewers_per_month"] == 140000
    assert on["funnel"]["inputs"]["partner_artist_subscribers"]["source"] == "meta"
    # A partner audience set to nothing is a scenario, and the row says 0, not None.
    zero = studio_report(db, BOAT_ONLY)["reach"]["partners"][0]
    assert zero["viewers_per_month"] == 0 and zero["new_paid_per_month"] == 0
    assert zero["active"] is True and zero["placeholder"] is False


# --- the target -------------------------------------------------------------

TARGET_KEYS = ["subscribers", "month", "subscribers_at_target_month", "reached_month", "shortfall",
               "required_new_paid_per_month", "required_viewers_per_month", "on_track", "note"]


TEN_K = {"target_subscribers": 10_000}


def test_target_of_ten_thousand_is_read_against_the_trajectory(db):
    """A 10,000-by-month-3 target, passed explicitly (the default is 3,000). At the defaults the book stands at 451.2 in month 3
    and never reaches 10,000 within the horizon; the closed form says 3,541.58
    new paid a month would do it — 10,000 ÷ (1 + 0.94 + 0.94²) at the
    traveler churn — which at the per-viewer yield is 480,214 stream viewers
    a month. Not on track."""
    report = studio_report(db, TEN_K)
    target, rows = report["target"], report["funnel"]["trajectory"]
    assert list(target) == TARGET_KEYS
    assert target["subscribers"] == 10_000 and target["month"] == 3
    assert isinstance(target["subscribers"], int) and isinstance(target["month"], int)
    assert target["subscribers_at_target_month"] == rows[2]["subscribers"] == 451.2
    assert target["reached_month"] is None
    assert all(row["subscribers"] < 10_000 for row in rows)
    assert target["shortfall"] == round(10_000 - 451.2, 1) == 9548.8
    cohort = sum(0.94 ** i for i in range(3))
    assert target["required_new_paid_per_month"] == round(10_000 / cohort, 2) == 3541.58
    assert target["required_viewers_per_month"] == round(10_000 / cohort / YIELD, 2) == 480213.98
    assert target["on_track"] is False
    assert target["note"] == TARGET_NOTE
    # The requirement is a property of the rates, not of the partner set: the
    # same with the hypothetical partner on, which only changes where the book stands.
    on = studio_report(db, {**TEN_K, "partners_include_hypothetical": 1})["target"]
    assert on["required_new_paid_per_month"] == 3541.58
    assert on["required_viewers_per_month"] == 480213.98
    assert on["subscribers_at_target_month"] == 3026.5 and on["shortfall"] == 6973.5
    assert on["reached_month"] == 14 and on["on_track"] is False
    rows_on = studio_report(db, {**TEN_K, "partners_include_hypothetical": 1})["funnel"]["trajectory"]
    assert rows_on[13]["subscribers"] >= 10_000 > rows_on[12]["subscribers"]


def test_target_closed_form_and_on_track_semantics(db):
    """Reached month is the first rounded trajectory row at or above the
    target; on track means reached by the target month, the target month
    itself included. The closed form: required new paid a month × Σ(1−c)^i
    over the target months is the target, at the traveler churn, to the cent."""
    rows = studio_report(db, TEN_K)["funnel"]["trajectory"]
    subs = [row["subscribers"] for row in rows]
    for target, month in ((100, 3), (400, 3), (451, 3), (452, 3), (500, 3), (2000, 12),
                          (2500, 24), (3000, 36)):
        block = studio_report(db, {"target_subscribers": target, "target_month": month})["target"]
        reached = next((i + 1 for i, s in enumerate(subs) if s >= target), None)
        assert block["reached_month"] == reached, (target, month)
        assert block["on_track"] is (reached is not None and reached <= month), (target, month)
        assert block["subscribers_at_target_month"] == subs[month - 1]
        assert block["shortfall"] == round(max(0, target - subs[month - 1]), 1)
        cohort = sum((1 - 0.06) ** i for i in range(month))
        assert block["required_new_paid_per_month"] == round(target / cohort, 2)
        assert block["required_new_paid_per_month"] * cohort == pytest.approx(
            target, abs=0.005 * cohort + 1e-9)                  # rounded to the cent, × months
        assert block["required_viewers_per_month"] == round(target / cohort / YIELD, 2)
    # The boundary cases, named: 300 is reached in month 2 (310.1), on track;
    # 451 in month 3 exactly (451.2), on track; 452 in month 4, not.
    at = lambda t: studio_report(db, {"target_subscribers": t})["target"]  # noqa: E731
    assert (at(300)["reached_month"], at(300)["on_track"], at(300)["shortfall"]) == (2, True, 0)
    assert (at(451)["reached_month"], at(451)["on_track"], at(451)["shortfall"]) == (3, True, 0)
    assert (at(452)["reached_month"], at(452)["on_track"], at(452)["shortfall"]) == (4, False, 0.8)
    # One month: the requirement is the target itself, and the churn cancels.
    one = studio_report(db, {"target_subscribers": 160, "target_month": 1,
                             "traveler_monthly_churn": 0.3})["target"]
    assert one["required_new_paid_per_month"] == 160
    assert one["subscribers_at_target_month"] == rows[0]["subscribers"]
    assert one["on_track"] is False                     # 159.9 in month 1, not 160
    # A higher traveler churn needs more new paid for the same target; the
    # performer churn does not enter the closed form.
    churny = studio_report(db, {**TEN_K, "traveler_monthly_churn": 0.2})["target"]
    assert churny["required_new_paid_per_month"] == round(10_000 / (1 + 0.8 + 0.64), 2)
    assert studio_report(db, {**TEN_K, "monthly_churn": 0.5})["target"]["required_new_paid_per_month"] == 3541.58


def test_target_counts_the_baseline_but_the_requirement_does_not(db):
    """A baseline on the books is real and counts toward the target — 11,000
    paying today decays to 10,120 and the month's new paid lifts it back over
    10,000 in month 1 — while what it takes is computed from a standing start
    at the current rates: the same closed form with or without a baseline."""
    plain = studio_report(db, TEN_K)["target"]
    with_base = studio_report(db, {**TEN_K, "baseline_subscribers": 11_000})
    target, rows = with_base["target"], with_base["funnel"]["trajectory"]
    assert rows[0]["subscribers"] == round(11_000 * 0.92 + 159.95, 1) >= 10_000
    assert target["reached_month"] == 1 and target["on_track"] is True
    # Reached in month 1, but the baseline keeps decaying: by month 3 the book
    # is back under the line, and the shortfall is read at the target month —
    # on track by the letter, short at the month, and both are reported.
    assert target["subscribers_at_target_month"] == rows[2]["subscribers"] < 10_000
    assert target["shortfall"] == round(10_000 - rows[2]["subscribers"], 1) > 0
    assert target["required_new_paid_per_month"] == plain["required_new_paid_per_month"]
    assert target["required_viewers_per_month"] == plain["required_viewers_per_month"]
    # A smaller baseline that decays below the line before the shows lift it:
    # 9,900 → 9,108 + 159.9 in month 1, reached later or not at all.
    small = studio_report(db, {**TEN_K, "baseline_subscribers": 9_900})["target"]
    assert small["reached_month"] != 1 and small["on_track"] is False


def test_target_of_zero_is_no_target(db):
    """0 means no target: the block is still present, the book's standing in
    the month is still read, and every figure that needs a target — reached
    month, shortfall, the requirements, on track — is None, not 0."""
    target = studio_report(db, {"target_subscribers": 0})["target"]
    assert target == {
        "subscribers": 0, "month": 3, "subscribers_at_target_month": 451.2,
        "reached_month": None, "shortfall": None, "required_new_paid_per_month": None,
        "required_viewers_per_month": None, "on_track": None, "note": TARGET_NOTE}


def test_target_month_beyond_the_horizon_has_no_row_to_read(db):
    """A six-month horizon and a month-12 target: there is no month 12 to read
    the book at, so that figure and the shortfall are None; reached month is
    still searched within the horizon, and the closed form still says what
    month 12 would take."""
    report = studio_report(db, {**TEN_K, "horizon_months": 6, "target_month": 12})
    target = report["target"]
    assert len(report["funnel"]["trajectory"]) == 6
    assert target["month"] == 12
    assert target["subscribers_at_target_month"] is None and target["shortfall"] is None
    assert target["reached_month"] is None and target["on_track"] is False
    cohort = sum(0.94 ** i for i in range(12))
    assert target["required_new_paid_per_month"] == round(10_000 / cohort, 2)
    assert target["required_viewers_per_month"] == round(10_000 / cohort / YIELD, 2)
    # Reached within the short horizon, the target month past it: on track.
    short = studio_report(db, {"horizon_months": 6, "target_month": 12, "target_subscribers": 400,
                               **BOAT_ONLY})["target"]
    assert short["subscribers_at_target_month"] is None
    boat_rows = studio_report(db, {**TEN_K, "horizon_months": 6, **BOAT_ONLY})["funnel"]["trajectory"]
    assert all(row["subscribers"] < 400 for row in boat_rows)
    assert short["reached_month"] is None and short["on_track"] is False
    reached = studio_report(db, {"horizon_months": 6, "target_month": 12,
                                 "target_subscribers": 400})["target"]
    assert reached["reached_month"] == 3 and reached["on_track"] is True


def test_required_viewers_is_none_when_viewers_convert_nobody(db):
    """If no viewer installs, no number of viewers reaches the target: the
    requirement in viewers is None — not infinity, not a scaled-up dock —
    while the requirement in new paid still stands."""
    nobody = studio_report(db, {**TEN_K, "viewer_to_install": 0, "traveler_viewer_to_install": 0})["target"]
    assert nobody["required_new_paid_per_month"] == 3541.58
    assert nobody["required_viewers_per_month"] is None
    assert nobody["reached_month"] is None and nobody["on_track"] is False
    # Only performers converting: the yield is the performer share's alone.
    performers = studio_report(db, {**TEN_K, "traveler_viewer_to_install": 0})["target"]
    assert performers["required_viewers_per_month"] == round(
        3541.5781272134864 / (0.30 * 0.025 * 0.05), 2)
    # A target of 0 with nobody converting is still just "no target".
    none = studio_report(db, {"viewer_to_install": 0, "traveler_viewer_to_install": 0,
                              "target_subscribers": 0})["target"]
    assert none["required_viewers_per_month"] is None and none["on_track"] is None


def test_reach_and_target_are_in_the_report_after_the_funnel(db):
    """The two blocks follow the funnel they are computed from, in this
    order, and the reach totals are the funnel's own viewers."""
    report = studio_report(db, {"partners_include_hypothetical": 1, "target_month": 6})
    keys = list(report)
    assert keys[keys.index("funnel") + 1:keys.index("funnel") + 4] == ["reach", "target", "lenses"]
    assert report["reach"]["viewers_total_per_month"] == report["funnel"]["monthly"]["viewers"]
    assert report["reach"]["viewers_partner_per_month"] == (
        report["funnel"]["monthly"]["viewers_partner"])
    assert report["target"]["subscribers_at_target_month"] == (
        report["funnel"]["trajectory"][5]["subscribers"])
    assert report["target"]["month"] == 6


# --- money discipline -------------------------------------------------------

def _walk(node, path=""):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk(value, f"{path}[{i}]")
    else:
        yield path, node


def test_every_cents_figure_is_an_integer(db):
    _boat_order(db, "moorage", 61800)
    _show(db, "2026-08-10", unique=140, installs=6)
    _show(db, "2026-08-15", unique=260, installs=9, kind="competition", attendees=55)
    report = studio_report(db, {"viewers_per_show": 137.5, "shows_per_month": 3,
                                "dock_attendees_per_event": 33.3,
                                "baseline_subscribers": 120})
    seen = 0
    for path, value in _walk(report):
        if path.rsplit(".", 1)[-1].split("[")[0].endswith("_cents") and value is not None:
            seen += 1
            # bool is an int subclass; a True in a cents column would be a bug too.
            assert isinstance(value, int) and not isinstance(value, bool), (path, value)
    assert seen > 60


# --- lenses -----------------------------------------------------------------

def test_lenses_are_never_summed(db):
    lenses = studio_report(db)["lenses"]
    assert set(lenses) == {"subscription", "acquisition_displaced", "catalog", "excluded"}
    for path, _ in _walk(lenses):
        assert "total" not in path.lower() and "combined" not in path.lower(), path


def test_lens_figures_at_defaults(db):
    lenses = studio_report(db)["lenses"]
    sub = lenses["subscription"]
    assert list(sub) == ["steady_subscribers", "steady_mrr_net_cents", "steady_arr_net_cents",
                         "month_12", "by_segment"]
    assert sub["steady_subscribers"] == round(D_STEADY, 1) == 2632.7
    assert sub["steady_mrr_net_cents"] == round(D_MRR_NET) == 3451972
    assert sub["month_12"]["subscribers"] == pytest.approx(
        D_STEADY_T * (1 - 0.94 ** 12) + D_STEADY_P * (1 - 0.92 ** 12), abs=0.06)
    assert sub["month_12"]["show_driven_subscribers"] == sub["month_12"]["subscribers"]
    assert sub["by_segment"] == {
        "travelers": {"steady_subscribers": 2533.3,
                      "steady_mrr_net_cents": round(D_STEADY_T * ARPU_T * KEEP)},
        "performers": {"steady_subscribers": 99.4,
                       "steady_mrr_net_cents": round(D_STEADY_P * ARPU_P * KEEP)}}
    acq = lenses["acquisition_displaced"]
    # Every install, stream and dock, traveler and performer alike, partner
    # viewer or the boat's own, is one a paid campaign did not buy.
    assert acq == {"installs_per_month": 767, "cpi_cents": 350,
                   "monthly_cents": 268450, "annual_cents": 3221400}
    # The boat alone: 57 installs, $199.50 a month displaced.
    boat = studio_report(db, BOAT_ONLY)["lenses"]
    assert boat["subscription"]["steady_subscribers"] == round(STEADY, 1) == 205.6
    assert boat["subscription"]["steady_mrr_net_cents"] == round(MRR_NET) == 267516
    assert boat["subscription"]["month_12"]["subscribers"] == round(
        STEADY_T * (1 - 0.94 ** 12) + STEADY_P * (1 - 0.92 ** 12), 1)
    assert boat["subscription"]["by_segment"] == {
        "travelers": {"steady_subscribers": 200.0,
                      "steady_mrr_net_cents": round(STEADY_T * ARPU_T * KEEP)},
        "performers": {"steady_subscribers": 5.6,
                       "steady_mrr_net_cents": round(STEADY_P * ARPU_P * KEEP)}}
    assert boat["acquisition_displaced"] == {"installs_per_month": 57, "cpi_cents": 350,
                                             "monthly_cents": 19950, "annual_cents": 239400}
    cat = lenses["catalog"]
    assert cat["songs_per_month"] == 32 and cat["songs_per_year"] == 384
    assert cat["buskers_per_month"] == 12 and cat["buskers_per_year"] == 144
    assert cat["priced"] is False and cat["note"]
    assert len(lenses["excluded"]) == 4   # incl. the BNDS economy, kept apart


def test_catalog_buskers_follow_the_event_count(db):
    cat = studio_report(db, {"events_per_month": 0})["lenses"]["catalog"]
    assert cat["buskers_per_month"] == 0 and cat["buskers_per_year"] == 0
    cat = studio_report(db, {"events_per_month": 3, "buskers_per_event": 5})["lenses"]["catalog"]
    assert cat["buskers_per_month"] == 15 and cat["buskers_per_year"] == 180


# --- breakeven --------------------------------------------------------------

def test_kit_pays_back_in_month_one_at_defaults_and_month_three_on_the_boat_alone(db):
    """Replay the trajectory by hand. At the defaults the placeholder partner
    audience clears the $788.96 kit in its first month ($2,116 net); on the
    boat alone show-driven cumulative net crosses it in month 3 — $163 the
    first month, $479 by the second, $939 by the third."""
    report = studio_report(db)
    expected = next(month for month, (_, _, _, cumulative)
                    in enumerate(_replay(36, D_NEW_PAID_T, D_NEW_PAID_P), start=1)
                    if round(cumulative) >= 78896)
    assert expected == 1
    assert report["breakeven"]["kit_month"] == 1
    assert report["funnel"]["trajectory"][0]["show_driven_cumulative_net_cents"] == 211601
    assert report["breakeven"]["kit_planned_cents"] == 78896
    assert report["breakeven"]["horizon_months"] == 36
    boat = studio_report(db, BOAT_ONLY)
    expected = next(month for month, (_, _, _, cumulative) in enumerate(_replay(), start=1)
                    if round(cumulative) >= 78896)
    assert expected == 3
    assert boat["breakeven"]["kit_month"] == 3
    assert boat["breakeven"]["kit_planned_cents"] == 78896


def test_project_payback_is_none_beyond_the_horizon(db):
    _boat_order(db, "electronics_nav", 1_000_000_000)      # $10M: beyond 36 months of anything here
    breakeven = studio_report(db)["breakeven"]
    assert breakeven["project_spend_cents"] == 1_000_000_000
    assert breakeven["project_month"] is None
    assert breakeven["moorage_monthly_cents"] is None
    assert breakeven["slip_month"] is None


def test_moorage_monthly_is_averaged_over_dated_months(db):
    assert moorage_monthly(db) is None
    _boat_order(db, "moorage", 61800, "July dockage", ordered_at="2026-07-01T00:00:00Z")
    _boat_order(db, "moorage", 2424, "Electric", ordered_at="2026-07-01T00:00:00Z")
    _boat_order(db, "moorage", 61800, "August dockage", ordered_at="2026-08-01T00:00:00Z")
    assert moorage_monthly(db) == round((61800 + 2424 + 61800) / 2)


def test_slip_month_is_the_first_month_net_mrr_covers_moorage(db):
    _boat_order(db, "moorage", 20000, "dockage", ordered_at="2026-08-01T00:00:00Z")
    report = studio_report(db)
    rows = report["funnel"]["trajectory"]
    expected = next(r["month"] for r in rows if r["show_driven_mrr_net_cents"] >= 20000)
    assert report["breakeven"]["slip_month"] == expected
    assert report["breakeven"]["moorage_monthly_cents"] == 20000


# --- roi ---------------------------------------------------------------------

def test_roi_at_defaults_is_read_off_the_show_driven_trajectory(db):
    """Every ROI figure is derived from the subscription lens, not a fourth
    lens: cumulative show-driven net over the kit, the steady month spread over
    shows and viewers, and what the kit costs per install in its first year."""
    report = studio_report(db)
    roi, rows, steady = report["roi"], report["funnel"]["trajectory"], report["funnel"]["steady_state"]
    assert list(roi) == ["kit_cents", "month_12", "horizon", "per_show_net_cents",
                         "per_viewer_net_cents", "cost_per_install_cents", "payback", "note"]
    assert roi["kit_cents"] == 78896 == report["breakeven"]["kit_planned_cents"]
    m12 = rows[11]["show_driven_cumulative_net_cents"]
    assert roi["month_12"] == {"show_driven_cumulative_net_cents": m12,
                               "roi_multiple_on_kit": round(m12 / 78896, 2)}
    assert m12 == round(list(_replay(12, D_NEW_PAID_T, D_NEW_PAID_P))[-1][3]) == 13291361
    assert roi["month_12"]["roi_multiple_on_kit"] == 168.47
    last = rows[-1]["show_driven_cumulative_net_cents"]
    assert roi["horizon"] == {"show_driven_cumulative_net_cents": last,
                              "roi_multiple_on_kit": round(last / 78896, 2),
                              "share_of_project_spend": None}        # nothing spent yet
    # 3,451,972 net a month over 4 sets + 2 nights, and over 21,200 viewers —
    # the partner's viewers are viewers; its streams are not the boat's shows.
    assert roi["per_show_net_cents"] == round(steady["mrr_net_cents"] / 6) == 575329
    assert roi["per_viewer_net_cents"] == round(steady["mrr_net_cents"] / 21200) == 163
    # 767 installs a month × 12: the kit costs 9¢ an install against a $3.50 paid CPI.
    assert roi["cost_per_install_cents"] == round(78896 / (767 * 12)) == 9
    assert roi["cost_per_install_cents"] < report["lenses"]["acquisition_displaced"]["cpi_cents"]
    assert roi["payback"] == {"kit_month": 1, "slip_month": None, "project_month": None}
    assert roi["note"] == ROI_NOTE and "show-driven" in ROI_NOTE
    # The boat alone: $10,256 by month 12, 13× the kit; $445.86 a show, $2.23 a
    # viewer, $1.15 an install, payback in month 3.
    boat = studio_report(db, BOAT_ONLY)
    roi, rows, steady = boat["roi"], boat["funnel"]["trajectory"], boat["funnel"]["steady_state"]
    m12 = rows[11]["show_driven_cumulative_net_cents"]
    assert m12 == round(list(_replay(12))[-1][3]) == 1025640
    assert roi["month_12"] == {"show_driven_cumulative_net_cents": m12, "roi_multiple_on_kit": 13.0}
    assert roi["per_show_net_cents"] == round(steady["mrr_net_cents"] / 6) == 44586
    assert roi["per_viewer_net_cents"] == round(steady["mrr_net_cents"] / 1200) == 223
    assert roi["cost_per_install_cents"] == round(78896 / (57 * 12)) == 115
    assert roi["payback"] == {"kit_month": 3, "slip_month": None, "project_month": None}


def test_roi_share_of_project_spend_and_payback_follow_the_ledger(db):
    _boat_order(db, "electronics_nav", 1_000_000)
    _boat_order(db, "moorage", 20000, "dockage", ordered_at="2026-08-01T00:00:00Z")
    report = studio_report(db)
    roi, be, rows = report["roi"], report["breakeven"], report["funnel"]["trajectory"]
    last = rows[-1]["show_driven_cumulative_net_cents"]
    assert roi["horizon"]["share_of_project_spend"] == round(last / 1_020_000, 2)
    assert roi["payback"] == {"kit_month": be["kit_month"], "slip_month": be["slip_month"],
                              "project_month": be["project_month"]}
    assert be["project_month"] is not None and be["slip_month"] is not None


def test_roi_ignores_the_baseline(db):
    """A baseline of 5,000 subscribers is real money, and none of it is the
    studio's return: every ROI figure is identical with and without it."""
    _boat_order(db, "electronics_nav", 250_000)
    assert studio_report(db, {"baseline_subscribers": 5000})["roi"] == studio_report(db)["roi"]


def test_roi_fields_are_none_not_zero_when_undefined(db):
    """An empty database has nothing spent, so no share of spend; a short
    horizon has no month 12; no installs means no cost per install; a set that
    nobody watched leaves no viewer to spread the month over."""
    roi = studio_report(db)["roi"]
    assert roi["horizon"]["share_of_project_spend"] is None
    assert roi["month_12"] is not None
    assert studio_report(db, {"horizon_months": 6})["roi"]["month_12"] is None
    nothing = studio_report(db, {"viewer_to_install": 0, "traveler_viewer_to_install": 0,
                                 "attendee_to_install": 0})["roi"]
    assert nothing["cost_per_install_cents"] is None
    assert nothing["month_12"]["roi_multiple_on_kit"] == 0.0
    assert nothing["per_show_net_cents"] == 0 and nothing["payback"]["kit_month"] is None
    _show(db, "2026-08-10", unique=0, installs=0)             # observed: nobody watched
    empty_room = studio_report(db, {"events_per_month": 0, **BOAT_ONLY})
    assert empty_room["funnel"]["inputs"]["viewers_per_show"] == {"value": 0.0,
                                                                  "source": "observed"}
    assert empty_room["funnel"]["monthly"]["viewers"] == 0
    assert empty_room["roi"]["per_viewer_net_cents"] is None
    assert empty_room["roi"]["cost_per_install_cents"] is None
    # With the partner counted the month still has its 20,000 viewers: a figure, not None.
    still = studio_report(db, {"events_per_month": 0})
    assert still["funnel"]["monthly"]["viewers"] == 20000
    assert still["roi"]["per_viewer_net_cents"] is not None


# --- recorded shows ---------------------------------------------------------

def test_no_shows_means_modeled_inputs(db):
    report = studio_report(db)
    assert report["recorded"]["shows"] == 0
    assert report["recorded"]["competitions"] == 0
    assert report["recorded"]["unique_viewers"] is None
    assert report["recorded"]["installs"] is None
    assert report["recorded"]["observed_viewers_per_show"] is None
    assert report["recorded"]["observed_viewer_to_install"] is None
    assert report["recorded"]["observed_attendees_per_event"] is None
    assert report["recorded"]["observed_event_viewers_per_night"] is None
    assert report["recorded"]["observed_event_multiplier"] is None
    assert report["recorded"]["viewers_counted_shows"] == 0
    assert report["recorded"]["install_rate_shows"] == 0
    assert report["recorded"]["event_viewers_counted_nights"] == 0
    assert report["recorded"]["attendees_counted_nights"] == 0
    assert report["recorded"]["ignored_observations"] == []
    assert report["recorded"]["rows"] == []
    assert report["funnel"]["inputs"]["viewers_per_show"]["source"] == "assumed"
    assert report["funnel"]["inputs"]["dock_attendees_per_event"]["source"] == "assumed"
    assert report["funnel"]["inputs"]["event_viewers_multiplier"]["source"] == "assumed"


def test_recorded_shows_replace_the_assumed_inputs(db):
    _show(db, "2026-08-10", unique=140, installs=6, peak=80)
    _show(db, "2026-08-17", unique=160, installs=10, peak=95)
    report = studio_report(db, BOAT_ONLY)            # the boat's own funnel, no partner stream
    rec = report["recorded"]
    assert rec["shows"] == 2 and rec["competitions"] == 0
    assert rec["unique_viewers"] == 300 and rec["installs"] == 16
    assert rec["observed_viewers_per_show"] == 150.0 and rec["viewers_counted_shows"] == 2
    assert rec["observed_viewer_to_install"] == round(16 / 300, 3)
    assert rec["install_rate_shows"] == 2
    assert [r["performed_at"] for r in rec["rows"]] == ["2026-08-17", "2026-08-10"]
    assert all(r["kind"] == "set" and r["attendees"] is None for r in rec["rows"])

    inputs = report["funnel"]["inputs"]
    assert inputs["viewers_per_show"] == {"value": 150.0, "source": "observed"}
    assert inputs["viewer_to_install"] == {"value": round(16 / 300, 3), "source": "observed"}
    assert inputs["install_to_paid"]["source"] == "assumed"   # a show cannot observe this
    assert inputs["dock_attendees_per_event"]["source"] == "assumed"   # nobody counted
    assert inputs["event_viewers_multiplier"]["source"] == "assumed"   # no night logged
    # The funnel actually uses the observed numbers: 150 × 4 + 150 × 2 × 2 viewers,
    # the performer share of them at the observed rate; the traveler share stays
    # declared — a show cannot tell which need a viewer had.
    assert inputs["traveler_viewer_to_install"]["source"] == "assumed"
    monthly = report["funnel"]["monthly"]
    assert monthly["viewers"] == 1200
    assert monthly["installs_performers"] == round(360 * round(16 / 300, 3), 2)
    assert monthly["installs_stream"] == round(840 * 0.04 + 360 * round(16 / 300, 3), 2)
    assert monthly["installs"] == round(840 * 0.04 + 360 * round(16 / 300, 3) + 14.4, 2)


def test_viewers_per_show_averages_solo_sets_only(db):
    """A month that matches the defaults exactly — two sets at 150, two
    competition nights at 300 — must model 1,200 viewers, not 1,800: the
    night's audience is the multiplied one, and averaging it into the per-set
    figure would multiply it twice. The nights are their own observation, and
    the ratio of the two is the observed multiple."""
    _show(db, "2026-08-03", unique=150, installs=6)
    _show(db, "2026-08-10", unique=150, installs=6)
    _show(db, "2026-08-15", unique=300, installs=21, kind="competition", attendees=60)
    _show(db, "2026-08-22", unique=300, installs=21, kind="competition", attendees=60)
    report = studio_report(db, BOAT_ONLY)
    rec = report["recorded"]
    assert rec["shows"] == 4 and rec["competitions"] == 2
    assert rec["unique_viewers"] == 900 and rec["installs"] == 54      # every row, for the record
    assert rec["observed_viewers_per_show"] == 150.0 and rec["viewers_counted_shows"] == 2
    assert rec["observed_event_viewers_per_night"] == 300.0
    assert rec["event_viewers_counted_nights"] == 2
    assert rec["observed_event_multiplier"] == 2.0
    assert OBSERVABLE["event_viewers_multiplier"] == "observed_event_multiplier"
    inputs = report["funnel"]["inputs"]
    assert inputs["viewers_per_show"] == {"value": 150.0, "source": "observed"}
    assert inputs["event_viewers_multiplier"] == {"value": 2.0, "source": "observed"}
    monthly = report["funnel"]["monthly"]
    assert monthly["viewers_stream"] == 600 and monthly["viewers_event"] == 600
    assert monthly["viewers"] == 1200
    # Nights that draw three times a set: the recorded audience replaces
    # "viewers per set × 2.0" with what the nights actually drew.
    _show(db, "2026-08-29", unique=600, installs=30, kind="competition", attendees=60)
    busier = studio_report(db, BOAT_ONLY)
    assert busier["recorded"]["observed_event_viewers_per_night"] == 400.0
    assert busier["recorded"]["observed_event_multiplier"] == round(400 / 150, 3)
    assert busier["funnel"]["monthly"]["viewers_event"] == pytest.approx(800, abs=0.5)
    assert busier["funnel"]["monthly"]["viewers_stream"] == 600


def test_competition_nights_alone_cannot_observe_a_per_set_figure(db):
    """With only nights logged there is no set to compare them to: the
    night's audience is reported, but viewers per set and the multiple stay
    declared rather than guessed from half a ratio."""
    _show(db, "2026-08-15", unique=300, installs=9, kind="competition", attendees=50)
    report = studio_report(db)
    rec = report["recorded"]
    assert rec["observed_viewers_per_show"] is None and rec["viewers_counted_shows"] == 0
    assert rec["observed_event_viewers_per_night"] == 300.0
    assert rec["observed_event_multiplier"] is None
    assert rec["observed_viewer_to_install"] is None and rec["install_rate_shows"] == 0
    assert rec["unique_viewers"] == 300 and rec["installs"] == 9
    inputs = report["funnel"]["inputs"]
    assert inputs["viewers_per_show"] == {"value": 150, "source": "assumed"}
    assert inputs["event_viewers_multiplier"] == {"value": 2.0, "source": "assumed"}
    assert inputs["viewer_to_install"] == {"value": 0.025, "source": "assumed"}
    assert inputs["dock_attendees_per_event"] == {"value": 50.0, "source": "observed"}


def test_competition_night_installs_stay_out_of_the_stream_rate(db):
    """A night's installs include the dock crowd's — nobody at the dock counts
    them apart — so they would put attendee installs over stream viewers and
    then the dock rate would add them again. The stream rate is drawn from
    solo sets only: 12 of 300, not 54 of 900."""
    _show(db, "2026-08-03", unique=150, installs=6)
    _show(db, "2026-08-10", unique=150, installs=6)
    _show(db, "2026-08-15", unique=300, installs=21, kind="competition", attendees=60)
    _show(db, "2026-08-22", unique=300, installs=21, kind="competition", attendees=60)
    report = studio_report(db, BOAT_ONLY)
    rec = report["recorded"]
    assert rec["observed_viewer_to_install"] == 0.04 and rec["install_rate_shows"] == 2
    assert report["funnel"]["inputs"]["viewer_to_install"] == {"value": 0.04,
                                                               "source": "observed"}
    monthly = report["funnel"]["monthly"]
    assert monthly["installs_performers"] == 14.4               # 360 × 0.04, the observed rate
    assert monthly["installs_stream"] == 48                     # 840 × 0.04 + 360 × 0.04
    assert monthly["installs_event"] == 14.4                    # 120 × 0.12, once
    assert monthly["installs"] == 62.4


def test_install_rate_needs_both_counts_on_the_same_set(db):
    """Installs from one show over viewers from another is not a rate. A set
    with a viewer count but no install count, or the reverse, adds to the
    totals for the record and to neither side of the ratio."""
    _show(db, "2026-08-03", unique=140, installs=6)
    _show(db, "2026-08-10", unique=None, installs=10)           # promo-code installs only
    rec = recorded_shows(db)
    assert rec["unique_viewers"] == 140 and rec["installs"] == 16
    assert rec["observed_viewers_per_show"] == 140.0 and rec["viewers_counted_shows"] == 1
    assert rec["observed_viewer_to_install"] == round(6 / 140, 3)
    assert rec["install_rate_shows"] == 1                       # "from 1 show with both counts"
    _show(db, "2026-08-17", unique=200, installs=None)          # viewers known, installs not
    rec = recorded_shows(db)
    assert rec["observed_viewers_per_show"] == 170.0 and rec["viewers_counted_shows"] == 2
    assert rec["observed_viewer_to_install"] == round(6 / 140, 3)
    assert rec["install_rate_shows"] == 1
    inputs = funnel_inputs(resolve_assumptions(db), rec)
    assert inputs["viewer_to_install"] == {"value": round(6 / 140, 3), "source": "observed"}


def test_install_rate_is_none_when_the_only_paired_set_had_no_viewers(db):
    """(10 viewers, installs unknown) + (0 viewers, 0 installs): the only
    like-for-like row has no viewers to divide by, so there is no rate — not
    0%, which would collapse the funnel's stream installs to nothing."""
    _show(db, "2026-08-03", unique=10, installs=None)
    _show(db, "2026-08-10", unique=0, installs=0)
    rec = recorded_shows(db)
    assert rec["observed_viewers_per_show"] == 5.0 and rec["viewers_counted_shows"] == 2
    assert rec["observed_viewer_to_install"] is None and rec["install_rate_shows"] == 1
    inputs = funnel_inputs(resolve_assumptions(db), rec)
    assert inputs["viewer_to_install"]["source"] == "assumed"


def test_out_of_range_observations_are_ignored_and_explained(db):
    """A logged row can say things an override is refused for: negative
    viewers, more installs than viewers. Such an observation is not modeled —
    the declared value stands, the input stays 'assumed' — and the recorded
    block lists it with the reason, so the reader can see it tried."""
    _show(db, "2026-08-03", unique=-50, installs=3)
    report = studio_report(db, BOAT_ONLY)
    rec = report["recorded"]
    assert rec["shows"] == 1 and rec["unique_viewers"] == -50     # the record is the record
    assert rec["observed_viewers_per_show"] is None
    assert rec["observed_viewer_to_install"] is None              # no viewers to divide by
    assert rec["ignored_observations"] == [{
        "key": "viewers_per_show", "field": "observed_viewers_per_show", "value": -50.0,
        "reason": "viewers_per_show must be 0 or greater, got -50; the declared value stands"}]
    inputs = report["funnel"]["inputs"]
    assert inputs["viewers_per_show"] == {"value": 150, "source": "assumed"}
    assert inputs["viewer_to_install"] == {"value": 0.025, "source": "assumed"}
    assert report["funnel"]["monthly"]["viewers"] == 1200
    assert report["funnel"]["steady_state"]["subscribers"] > 0

    # Dock QR installs exceeding stream viewers on a set: a rate above 1.
    db.execute("DELETE FROM show_log")
    _show(db, "2026-08-10", unique=20, installs=30)
    report = studio_report(db)
    rec = report["recorded"]
    assert rec["observed_viewers_per_show"] == 20.0               # in range, taken
    assert rec["observed_viewer_to_install"] is None
    assert rec["install_rate_shows"] == 1
    assert rec["ignored_observations"] == [{
        "key": "viewer_to_install", "field": "observed_viewer_to_install", "value": 1.5,
        "reason": "viewer_to_install must be within [0, 1], got 1.5; the declared value stands"}]
    assert report["funnel"]["inputs"]["viewers_per_show"] == {"value": 20.0,
                                                              "source": "observed"}
    assert report["funnel"]["inputs"]["viewer_to_install"] == {"value": 0.025,
                                                               "source": "assumed"}
    # A negative dock count is withheld the same way.
    db.execute("DELETE FROM show_log")
    _show(db, "2026-08-15", unique=300, kind="competition", attendees=-5)
    rec = recorded_shows(db)
    assert rec["observed_attendees_per_event"] is None and rec["attendees_counted_nights"] == 1
    assert [entry["key"] for entry in rec["ignored_observations"]] == [
        "dock_attendees_per_event"]
    # Belt and braces: funnel_inputs itself refuses a hand-built out-of-range observation.
    rigged = dict(rec, observed_viewer_to_install=1.5, observed_attendees_per_event=-5)
    inputs = funnel_inputs(resolve_assumptions(db), rigged)
    assert inputs["viewer_to_install"]["source"] == "assumed"
    assert inputs["dock_attendees_per_event"]["source"] == "assumed"


def test_attendees_are_observed_from_competition_rows(db):
    """The dock crowd is averaged over the nights somebody counted it; a set
    with no count, or a night nobody counted, leaves the average alone."""
    _show(db, "2026-08-10", unique=140, installs=6)                       # a set, no dock
    _show(db, "2026-08-15", unique=300, kind="competition", attendees=80)
    _show(db, "2026-08-22", unique=340, kind="competition", attendees=40)
    _show(db, "2026-08-29", unique=310, kind="competition")               # not counted
    report = studio_report(db)
    rec = report["recorded"]
    assert rec["shows"] == 4 and rec["competitions"] == 3
    assert rec["observed_attendees_per_event"] == 60.0
    assert rec["attendees_counted_nights"] == 2                 # of 3 nights
    assert [(r["kind"], r["attendees"]) for r in rec["rows"]] == [
        ("competition", None), ("competition", 40), ("competition", 80), ("set", None)]

    inputs = report["funnel"]["inputs"]
    assert inputs["dock_attendees_per_event"] == {"value": 60.0, "source": "observed"}
    assert inputs["attendee_to_install"]["source"] == "assumed"     # not observable
    assert inputs["events_per_month"]["source"] == "assumed"        # cadence is a plan
    assert report["funnel"]["monthly"]["attendees"] == 120
    # An explicit override still beats the observed crowd.
    forced = studio_report(db, {"dock_attendees_per_event": 200})["funnel"]["inputs"]
    assert forced["dock_attendees_per_event"] == {"value": 200.0, "source": "override"}


def test_explicit_override_beats_observed(db):
    _show(db, "2026-08-10", unique=140, installs=6)
    inputs = studio_report(db, {"viewers_per_show": 500})["funnel"]["inputs"]
    assert inputs["viewers_per_show"] == {"value": 500.0, "source": "override"}
    assert inputs["viewer_to_install"]["source"] == "observed"


def test_blank_show_counts_stay_none_not_zero(db):
    """A logged show with no viewer count is 'nobody wrote it down', not zero
    viewers, and must not drag the observed average to nothing."""
    _show(db, "2026-08-10")
    rec = recorded_shows(db)
    assert rec["shows"] == 1
    assert rec["unique_viewers"] is None and rec["installs"] is None
    assert rec["observed_viewers_per_show"] is None and rec["viewers_counted_shows"] == 0
    assert rec["observed_attendees_per_event"] is None
    assert rec["ignored_observations"] == []
    resolved = resolve_assumptions(db)
    assert funnel_inputs(resolved, rec)["viewers_per_show"]["source"] == "assumed"

    _show(db, "2026-08-17", unique=120)          # viewers known, installs not
    rec = recorded_shows(db)
    assert rec["observed_viewers_per_show"] == 120.0
    assert rec["viewers_counted_shows"] == 1 and rec["shows"] == 2    # "1 of 2 counted"
    assert rec["observed_viewer_to_install"] is None and rec["install_rate_shows"] == 0
    inputs = funnel_inputs(resolved, rec)
    assert inputs["viewers_per_show"]["source"] == "observed"
    assert inputs["viewer_to_install"]["source"] == "assumed"
    assert inputs["dock_attendees_per_event"]["source"] == "assumed"


def test_show_log_defaults_a_row_to_a_set(db):
    """The CLI writes `kind` explicitly; the schema default guards everything
    else that inserts a show without saying what kind it is."""
    db.execute(
        """INSERT INTO show_log (performed_at, platform, unique_viewers, logged_at)
           VALUES ('2026-08-10', 'youtube', 90, ?)""", (utcnow(),))
    row = recorded_shows(db)["rows"][0]
    assert row["kind"] == "set" and row["attendees"] is None


# --- inherited capital and report shape -------------------------------------

def test_inherited_capital_is_unpriced_until_attributed(db):
    inherited = studio_report(db)["inherited"]
    assert "Starlink" in inherited["installed"]
    assert [a["key"] for a in inherited["attributed"]] == [
        "av_security", "connectivity", "solar_generation", "energy_storage",
        "power_conversion", "generator"]
    assert inherited["attributed_cents"] == 0
    assert inherited["note"] and "unpriced" in inherited["note"]

    _boat_order(db, "connectivity", 59900, "Starlink Mini")
    _boat_order(db, "energy_storage", 200000, "LiFePO4")   # a boat system, not studio capital
    inherited = studio_report(db)["inherited"]
    assert inherited["attributed_cents"] == 59900
    assert inherited["note"] is None
    by_key = {a["key"]: a["cents"] for a in inherited["attributed"]}
    assert by_key["energy_storage"] == 200000


def test_report_key_order_and_vessel(db):
    _meta(db, "vessel_name", "Ophelia's Key")
    _meta(db, "registration_mark", "MT9740CA")
    report = studio_report(db)
    assert list(report) == [
        "vessel", "lyricshow", "signal_chain", "competition", "inherited", "kit", "power",
        "uplink", "funnel", "reach", "target", "lenses", "breakeven", "roi", "baseline",
        "recorded", "assumptions"]
    assert report["vessel"]["name"] == "Ophelia's Key"
    assert report["vessel"]["registration_mark"] == "MT9740CA"
    assert report["vessel"]["make_model"]            # declared fact before meta is seeded
    assert report["lyricshow"] is not LYRICSHOW      # callers cannot mutate the facts


def test_show_log_from_before_the_competition_columns_is_migrated(tmp_path):
    """A database that created show_log before kind/attendees existed must not
    crash the report; migrate() adds the columns like any other additive change."""
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE show_log (id INTEGER PRIMARY KEY, performed_at TEXT, platform TEXT,
           title TEXT, duration_minutes INTEGER, peak_viewers INTEGER, unique_viewers INTEGER,
           installs_attributed INTEGER, note TEXT, logged_at TEXT NOT NULL)"""
    )
    conn.execute(
        "INSERT INTO show_log (performed_at, unique_viewers, logged_at) VALUES ('2026-08-01', 90, 'x')"
    )
    conn.commit()
    conn.close()
    database = Database(path)
    database.migrate()
    seed_systems(database)
    recorded = studio_report(database)["recorded"]
    assert recorded["shows"] == 1
    assert recorded["rows"][0]["kind"] == "set"
    assert recorded["rows"][0]["attendees"] is None
