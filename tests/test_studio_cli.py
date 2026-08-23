"""The studio CLI. `okey log show` records a set or a competition night, `okey
studio baseline` declares today's paying subscribers, `okey studio partner`
enters a partner channel's audience, `okey report studio` prints what the
module computes — headline, ROI, kit, funnel, reach, target, lenses, the
competition night and the recorded shows — and a bad override, an unknown
kind or partner, a negative count or an unwritable page is refused with a
message, not a traceback. The commands run against a temporary database, never
the real one.

Two sets of numbers are pinned here, both worked by hand at the top of
tests/test_studio.py. The boat's own — 1200 viewers = 840 travelers + 360
performers, 57 installs (48 travelers with the 14.4 dock folded in, 9
performers), 12.45 new paid a month, 205.6 steady subscribers (200 travelers ·
5.6 performers), $15.41 blended ARPU, $2,675.16/mo net, kit payback in month 3
— are read after `_boat_only` zeroes the committed partner's placeholder
audience through `okey studio partner`, the command that replaces it. The
declared defaults add that partner — Partner church at a ESTIMATE 5,000 live
viewers × 4 Sunday streams = 20,000 viewers a month — for 21,200 viewers, 767
installs, 159.95 new paid, 2,632.7 steady subscribers and $34,519.72/mo net,
with the headline, the Reach panel and the partner's row all saying the figure
is a stand-in. `--with-hypothetical` adds Partner artist (6.19M × 2% × 1 = 123,800
viewers) for 145,000 viewers and 17,656.4 steady subscribers. `--traveler-share
0 --events 0` on the boat alone is the pre-traveler single-stream performer
funnel to the cent."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from opheliaskey import config
from opheliaskey.analysis.studio import (
    COMPETITION_FLOW,
    PARADISE_BUSKER,
    PARTNERS,
    STAGES,
    STUDIO_KIT,
)
from opheliaskey.classify.taxonomy import seed_systems
from opheliaskey.cli import app
from opheliaskey.db.database import Database, utcnow

runner = CliRunner()

CHURCH_META = "studio.partner_church_live_viewers"
ARTIST_META = "studio.partner_artist_subscribers"


@pytest.fixture
def cli_db(tmp_path, monkeypatch):
    """Point the CLI at a fresh database.

    Settings are read from OKEY_* environment variables and cached on first
    use, so the cache is cleared as well as the environment set. COLUMNS is
    widened so Rich does not wrap the phrases the tests look for.
    """
    db_path = tmp_path / "cli.db"
    monkeypatch.setenv("OKEY_DB_PATH", str(db_path))
    monkeypatch.setenv("OKEY_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("OKEY_AMAZON_CSV_DIR", str(tmp_path / "amazon"))
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setattr(config, "_settings", None)
    return db_path


def _run(*args: str):
    result = runner.invoke(app, list(args))
    return result.exit_code, result.output


def _seeded(db_path) -> Database:
    """The CLI's database, migrated and seeded, for tests that plant rows directly."""
    db = Database(db_path)
    db.migrate()
    seed_systems(db)
    return db


def _boat_order(db: Database, system_key, cents, description="item"):
    cur = db.execute(
        "INSERT INTO orders (source, external_order_id, ordered_at, status, total_cents, "
        "created_at, updated_at) VALUES ('test', ?, '2026-08-01T00:00:00Z', 'delivered', ?, ?, ?)",
        (f"s{db.one('SELECT COUNT(*) c FROM orders')['c']}", cents, utcnow(), utcnow()))
    order_id = int(cur.lastrowid)
    sys_id = None
    if system_key:
        sys_id = db.one("SELECT id FROM boat_systems WHERE key=?", (system_key,))["id"]
    db.execute(
        "INSERT INTO line_items (order_id, line_no, description, total_cents, system_id, "
        "relevance) VALUES (?,0,?,?,?,'boat')", (order_id, description, cents, sys_id))


def _show(db: Database, performed_at, unique=None, installs=None, kind="set", attendees=None):
    db.execute(
        """INSERT INTO show_log (performed_at, kind, platform, title, unique_viewers, attendees,
             installs_attributed, logged_at) VALUES (?,?,?,?,?,?,?,?)""",
        (performed_at, kind, "youtube", "planted", unique, attendees, installs, utcnow()))


def _boat_only():
    """Zero the committed partner's placeholder audience through the command
    that replaces it, so the boat's own hand-worked figures can be pinned: a
    partner whose audience is 0 is counted and adds nothing."""
    code, out = _run("studio", "partner", "church", "--live-viewers", "0")
    assert code == 0, out
    assert "ESTIMATE" not in out


def _meta(db_path, key):
    row = Database(db_path).one("SELECT value FROM project_meta WHERE key = ?", (key,))
    return None if row is None else row["value"]


# --- log show ---------------------------------------------------------------

def test_log_show_inserts_a_row_and_the_report_reads_it(cli_db):
    code, out = _run(
        "log", "show", "--date", "2026-08-01", "--platform", "youtube", "--title", "Set one",
        "--minutes", "110", "--peak", "80", "--unique", "140", "--installs", "6",
    )
    assert code == 0, out
    assert "logged show" in out and "1 recorded" in out

    rows = Database(cli_db).query("SELECT * FROM show_log")
    assert len(rows) == 1
    row = rows[0]
    assert row["performed_at"] == "2026-08-01"
    assert row["platform"] == "youtube"
    assert row["title"] == "Set one"
    assert row["duration_minutes"] == 110
    assert (row["peak_viewers"], row["unique_viewers"], row["installs_attributed"]) == (80, 140, 6)
    assert row["logged_at"]

    # Recorded beats modeled: the report now carries the observed inputs.
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "observed" in out
    assert "Set one" in out
    assert "No shows recorded yet" not in out
    # 6 installs from 140 viewers is a 4.3% observed install rate, drawn from
    # the one set that had both counts; the per-set average names its rows.
    assert "4.3% install rate (from 1 set(s) with both counts)" in out
    assert "140 viewers per counted show (1 of 1 sets counted)" in out
    # A plain set: no competition night, nobody counted a dock crowd.
    assert "1 show(s), 0 competition night(s)" in out
    assert "— on the dock per counted night (0 of 0 counted)" in out
    # The headline says exactly which inputs the set could observe.
    assert ("1 show(s) recorded — observed: viewers per set, stream install rate; "
            "modeled: competition multiple, dock crowd") in out


def test_log_show_leaves_unknown_counts_null(cli_db):
    """A count nobody wrote down is NULL, not zero — the report keeps the difference."""
    code, out = _run("log", "show", "--platform", "twitch")
    assert code == 0, out
    row = Database(cli_db).one("SELECT * FROM show_log")
    assert row["unique_viewers"] is None
    assert row["installs_attributed"] is None
    assert row["performed_at"]  # defaults to today
    # Without --kind it is a set, and an uncounted dock is NULL too.
    assert row["kind"] == "set"
    assert row["attendees"] is None


def test_log_show_records_a_competition_night_and_its_dock_crowd(cli_db):
    code, out = _run(
        "log", "show", "--kind", "competition", "--date", "2026-08-29", "--platform", "youtube",
        "--title", "Busker night one", "--unique", "300", "--attendees", "55", "--installs", "9",
    )
    assert code == 0, out
    assert "logged competition night" in out and "55 on the dock" in out
    row = Database(cli_db).one("SELECT * FROM show_log")
    assert row["kind"] == "competition"
    assert row["attendees"] == 55
    assert (row["unique_viewers"], row["installs_attributed"]) == (300, 9)

    # The counted crowd replaces the assumed dock figure (observed, 55 a
    # night), and the recorded table says which kind of show it was.
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "observed" in out
    assert "1 show(s), 1 competition night(s)" in out
    assert "55 on the dock × 2 nights" in out
    assert "55 on the dock per counted night (1 of 1 counted)" in out
    assert "competition" in out and "Busker night one" in out
    # A competition night alone cannot observe a per-set figure, a stream
    # install rate or the multiple — the headline and the summary both say so.
    assert ("1 show(s) recorded — observed: dock crowd; modeled: viewers per set, "
            "stream install rate, competition multiple") in out
    assert ("300 viewers per counted night (1 of 1 counted) → no counted set to compare "
            "against; the declared multiple stands") in out
    assert "150 × 2 per night × 2 nights" in out


def test_log_show_refuses_an_unknown_kind(cli_db):
    code, out = _run("log", "show", "--kind", "nonsense", "--platform", "twitch")
    assert code == 1
    assert "--kind must be one of set, competition" in out
    assert "nonsense" in out
    # The refused row was never written: one valid set afterwards is the only row.
    code, out = _run("log", "show", "--platform", "twitch")
    assert code == 0, out
    rows = Database(cli_db).query("SELECT kind FROM show_log")
    assert [row["kind"] for row in rows] == ["set"]


def test_log_show_refuses_negative_counts_before_writing(cli_db):
    """A count is 0 or more. A negative one would ride into the funnel as a
    negative install rate, so it is refused at the prompt, like a negative
    override, and nothing is written."""
    db = _seeded(cli_db)     # the refusal comes before the CLI opens the database
    for flag in ("--minutes", "--peak", "--unique", "--attendees", "--installs"):
        code, out = _run("log", "show", "--unique", "100", flag, "-5")
        assert code == 1, out
        assert f"{flag} must be 0 or greater, got -5" in out
        assert "Traceback" not in out
    assert db.one("SELECT COUNT(*) n FROM show_log")["n"] == 0
    # Zero is a count: nobody installed, and that was written down.
    code, out = _run("log", "show", "--unique", "100", "--installs", "0")
    assert code == 0, out
    assert db.one("SELECT installs_attributed i FROM show_log")["i"] == 0


def test_log_show_user_strings_are_never_markup(cli_db):
    """A title like "[live] Set one" is a title. It must print as typed and must
    not break the report's table on every later run."""
    code, out = _run("log", "show", "--date", "2026-08-01", "--title", "[live] Set one",
                     "--platform", "[/]", "--unique", "140", "--installs", "6")
    assert code == 0, out
    assert "[live] Set one" in out and "on [/]" in out
    row = Database(cli_db).one("SELECT title, platform FROM show_log")
    assert (row["title"], row["platform"]) == ("[live] Set one", "[/]")
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "[live] Set one" in out and "[/]" in out
    assert "Traceback" not in out and "MarkupError" not in out
    # The refusal for a bad kind quotes the user's string without parsing it.
    code, out = _run("log", "show", "--kind", "[bold]x")
    assert code == 1 and "'[bold]x'" in out


def test_log_help_names_the_shows(cli_db):
    code, out = _run("log", "--help")
    assert code == 0, out
    assert "shows" in out and "studio" in out
    assert "reward analysis." not in out


# --- report studio ----------------------------------------------------------

def test_report_studio_prints_headline_and_empty_state(cli_db):
    code, out = _run("report", "studio")
    assert code == 0, out
    # Headline at the declared defaults: the boat's 1,200 viewers plus Free
    # partner church's estimated 20,000 — 767 installs a month, 2,632.7 steady
    # subscribers named by segment, $34,519.72/mo net, $414,236.62/yr.
    assert "2632.7 subscribers (2533.3 travelers · 99.4 performers)" in out
    assert "$34,519.72" in out
    assert "$414,236.62" in out
    # The target in one phrase, and the placeholder the headline rests on,
    # named loudly with the command that replaces it.
    assert "Target            3,000 by month 3: 2,548.8 short — 451.2 at month 3, not within 36 months" in out
    assert ("ESTIMATE          Partner church's 5,000 live viewers a stream is an estimate: 20,000 of "
            "21,200 viewers depend on it — okey studio partner church --live-viewers N") in out
    # Kit payback lands inside the horizon; the kit is priced at $788.96.
    assert "$788.96" in out
    assert "Kit payback       month 1 of 36" in out
    # Sections are all present.
    for heading in ("Power", "Uplink", "Kit —", "Funnel", "Reach —", "Target —", "Return", "ROI",
                    "Competition night", "Recorded shows"):
        assert heading in out
    assert "reported separately" in out
    # The headline basis and the empty state agree: nothing recorded, all modeled.
    assert "No shows recorded yet — return is modeled from declared rates" in out
    assert "No shows recorded yet — figures are modeled" in out
    assert "okey log show" in out
    assert "--kind competition" in out
    assert "conditional" in out and "blocked" in out
    # Nothing logged, so every funnel input is assumed; nothing is observed.
    assert "observed" not in out
    # Assumptions are behind a flag.
    assert "Run with --assumptions" in out
    assert "Weekly show cadence" not in out


def test_report_studio_boat_alone_is_the_hand_worked_headline(cli_db):
    """With the committed partner's audience entered as 0 the report is the
    boat's own funnel to the cent — 205.6 steady subscribers, $2,675.16/mo,
    kit payback in month 3 — and nothing on it is a placeholder any more."""
    _boat_only()
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "205.6 subscribers (200 travelers · 5.6 performers)" in out
    assert "$2,675.16" in out
    assert "$32,101.96" in out
    assert "Kit payback       month 3 of 36" in out
    assert "ESTIMATE" not in out
    # The partner's 0 is read from project_meta, and the Viewers row says so.
    assert "600 stream + 600 event + 0 partner" in out
    assert "meta" in out
    assert "Target            3,000 by month 3: 2,964.9 short — 35.1 at month 3, not within 36 months" in out


def test_report_studio_funnel_shows_the_two_audiences_and_the_two_segments(cli_db):
    """Every stage carries the total and two sub-rows — travelers and performers
    — so a reader can see which need a number came from; the dock crowd sit
    inside the travelers' rows, because they had the demo in person."""
    _boat_only()
    code, out = _run("report", "studio")
    assert code == 0, out
    # Viewers: 150 × 4 shows = 600 stream, 150 × 2 × 2 nights = 600 event, no
    # partner audience, split 70/30 by need.
    assert "600 stream + 600 event + 0 partner" in out
    assert "1 counted partner stream(s): 0 Partner church — see Reach" in out
    assert "150 per show × 4 shows" in out
    assert "150 × 2 per night × 2 nights" in out
    assert "70% of viewers — the need is Conversation Mode" in out
    assert "the other 30% — streamers, worship teams, the original funnel" in out
    # Attendees: 60 on the dock × 2 nights = 120, travelers on their own rate.
    assert "60 on the dock × 2 nights — travelers, with the demo in front of them" in out
    # Installs: 42.6 stream + 14.4 dock by audience; 48 travelers (33.6 from
    # the stream at 4%, 14.4 from the dock at 12%) + 9 performers (2.5%).
    assert "42.6 stream + 14.4 dock" in out
    assert "33.6 stream (4.0% of 840 traveler viewers) + 14.4 dock (12.0% of 120 attendees)" in out
    assert "2.5% of 360 performer viewers" in out
    # New paid: 25% of traveler installs onto the bundle, 5% of performer installs.
    assert "12 travelers + 0.45 performers" in out
    assert "25.0% of 48 traveler installs → Base + Conversation Mode" in out
    assert "5.0% of 9 performer installs" in out
    # Each plan's month is priced as its segment pays it — 60% of travelers and
    # 35% of performers on annual billing — with the list price beside it and
    # the store's annual price only where the store has one: the bundle has its
    # own, the Pro Broadcast add-on is monthly only. Plan shares are derived
    # from the two segments' volumes, with the declared performer mix beside.
    assert ("96.4% of new paid (all travelers) at $14.99/mo blended "
            "(list $19.98/mo · $139.98/yr, the featured bundle)") in out
    assert "2.2% of new paid (60% of performers) at $12.66/mo blended (list $14.99/mo · $99.99/yr)" in out
    assert ("0.5% of new paid (15% of performers) at $52.65/mo blended "
            "(list $54.98/mo · annual on the tier, the add-on monthly)") in out
    assert "0.9% of new paid (25% of performers) at $44.16/mo blended (list $49.99/mo · $399.99/yr)" in out
    # Steady state by segment, each over its own churn; MRR by segment at its
    # own ARPU, and the blended ARPU over all new paid.
    assert "200 travelers + 5.6 performers — each segment's new paid ÷ its own churn" in out
    assert "12 new paid ÷ 6.0% monthly churn" in out
    assert "0.45 new paid ÷ 8.0% monthly churn" in out
    assert "$15.41 blended ARPU over all new paid, less 15% store commission" in out
    assert "$14.99 ARPU on Base + Conversation Mode, 60% on annual billing" in out
    assert "$26.53 ARPU across the performer plans, 35% on annual billing" in out
    # The subscription lens prints the same split, summed only on the line above.
    assert "travelers 200 subscribers, $2,548.30/mo · performers 5.6 subscribers, $126.86/mo" in out


def test_report_studio_who_buys_line_follows_the_rates(cli_db):
    """Two sentences under the funnel: travelers first, performers second, with
    the traveler product printed from the effective rates — the owner's 1%
    premise, declared and arguable — so an override moves the sentence too."""
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "Who buys" in out
    assert "Travelers first" in out
    assert "Conversation Mode on a phone of their own" in out
    assert "70% of the audience (traveler_share: assumed)" in out
    assert "4.0% install and 25.0% pay — 1.0% of traveler viewers subscribing" in out
    assert "the owner's premise declared as the traveler_* assumptions" in out
    assert "as arguable as every other rate" in out
    assert "Performers second" in out
    assert "the other 30%, at 2.5% install, 5.0% pay and 8.0% churn" in out
    # A what-if on the traveler paid rate moves the product, and says so.
    code, out = _run("report", "studio", "--traveler-paid", "0.1")
    assert code == 0, out
    assert "4.0% install and 10.0% pay — 0.4% of traveler viewers subscribing" in out
    assert "1.0% of traveler viewers" not in out
    code, out = _run("report", "studio", "--traveler-share", "0.5")
    assert code == 0, out
    assert "50% of the audience (traveler_share: override)" in out
    assert "the other 50%, at 2.5% install" in out


def test_report_studio_assumptions_table(cli_db):
    code, out = _run("report", "studio", "--assumptions")
    assert code == 0, out
    assert "Studio assumptions" in out
    for key in ("viewer_to_install", "monthly_churn", "load_starlink_w", "horizon_months",
                "events_per_month", "attendee_to_install", "annual_share",
                "baseline_subscribers", "traveler_share", "traveler_viewer_to_install",
                "traveler_install_to_paid", "traveler_annual_share", "traveler_monthly_churn",
                "partner_church_live_viewers", "partner_artist_subscribers",
                "partner_live_share", "partners_include_hypothetical", "target_subscribers",
                "target_month"):
        assert key in out
    assert "Weekly show cadence" in out
    # The traveler rates say whose premise they carry; the placeholder says
    # what it is in its own note; the target says whose it is.
    assert "the stated 1% of viewers" in out
    assert "ESTIMATE: the partner church's live viewers per Sunday stream" in out
    assert "the projection is 3,000 in 3 months" in out
    assert "assumed" in out


def test_report_studio_flags_become_overrides(cli_db):
    _boat_only()
    code, out = _run("report", "studio", "--viewers", "300", "--shows", "8", "--churn", "0.05")
    assert code == 0, out
    assert "override" in out
    # 300 viewers × 8 shows = 2400 stream viewers, plus 300 × 2 × 2 nights = 1200 event.
    assert "2400 stream + 1200 event" in out
    assert "3600" in out
    assert "300 per show × 8 shows" in out
    # 3600 viewers split 2520 / 1080; --churn is the performer churn, so the
    # performers settle at 1.35 / 5% = 27 and the travelers at 28.8 / 6% = 480.
    assert "507 subscribers (480 travelers · 27 performers)" in out
    assert "1.35 new paid ÷ 5.0% monthly churn" in out
    assert "28.8 new paid ÷ 6.0% monthly churn" in out


def test_report_studio_events_and_attendees_become_overrides(cli_db):
    _boat_only()
    code, out = _run("report", "studio", "--events", "4", "--attendees", "200")
    assert code == 0, out
    assert "override" in out
    # 150 × 2 per night × 4 nights = 1200 event viewers on top of 600 stream;
    # 200 on the dock × 4 nights = 800 attendees; installs 63.9 stream + 96 dock,
    # the dock folded into the travelers' 146.4.
    assert "600 stream + 1200 event" in out
    assert "200 on the dock × 4 nights" in out
    assert "63.9 stream + 96 dock" in out
    assert "50.4 stream (4.0% of 1260 traveler viewers) + 96 dock (12.0% of 800 attendees)" in out
    # 146.4 × 25% = 36.6 travelers over 6% churn = 610; 13.5 × 5% = 0.675
    # performers over 8% = 8.4: 618.4 steady.
    assert "618.4 subscribers (610 travelers · 8.4 performers)" in out


def test_report_studio_with_no_competition_nights_is_the_single_stream_funnel(cli_db):
    """A month with no competition nights is a scenario, and it is exactly the
    funnel the studio had before the dock existed."""
    _boat_only()
    code, out = _run("report", "studio", "--events", "0")
    assert code == 0, out
    assert "600 stream + 0 event" in out
    assert "60 on the dock × 0 nights" in out
    assert "21.3 stream + 0 dock" in out
    # 420 travelers × 4% × 25% = 4.2 over 6% = 70; 180 performers × 2.5% × 5%
    # = 0.225 over 8% = 2.8: 72.8 steady.
    assert "72.8 subscribers (70 travelers · 2.8 performers)" in out
    assert "$955.34" in out and "$11,464.04" in out


def test_report_studio_traveler_share_zero_is_the_performer_only_funnel(cli_db):
    """With no travelers in the audience the stream is the original performer
    funnel — 1200 viewers at 2.5% → 30 installs at 5% → 1.5 new paid over 8%
    churn = 18.8 steady — while the dock crowd are still travelers: they had the
    demo in person, so 14.4 dock installs pay at 25% and settle at 60. With no
    competition nights either, the report is the pre-traveler single-stream
    funnel to the cent: 9.4 subscribers, $211.44/mo, $2,537.27/yr."""
    _boat_only()
    code, out = _run("report", "studio", "--traveler-share", "0")
    assert code == 0, out
    assert "override" in out
    assert "0% of viewers — the need is Conversation Mode" in out
    assert "the other 100% — streamers, worship teams, the original funnel" in out
    assert "30 stream + 14.4 dock" in out
    assert "0 stream (4.0% of 0 traveler viewers) + 14.4 dock (12.0% of 120 attendees)" in out
    assert "2.5% of 1200 performer viewers" in out
    assert "3.6 travelers + 1.5 performers" in out
    assert "78.8 subscribers (60 travelers · 18.8 performers)" in out
    assert "1.5 new paid ÷ 8.0% monthly churn" in out
    assert "$422.88" in out        # 18.75 × $26.53 × 85%: the performer MRR alone

    code, out = _run("report", "studio", "--traveler-share", "0", "--events", "0")
    assert code == 0, out
    assert "15 stream + 0 dock" in out
    assert "0 travelers + 0.75 performers" in out
    assert "9.4 subscribers (0 travelers · 9.4 performers)" in out
    assert "$211.44" in out and "$2,537.27" in out
    # Nobody lands on the bundle; the performer plans carry the declared mix
    # and the blended ARPU is the performer ARPU alone.
    assert "0.0% of new paid (all travelers)" in out
    assert "60.0% of new paid (60% of performers) at $12.66/mo blended" in out
    assert "15.0% of new paid (15% of performers) at $52.65/mo blended" in out
    assert "25.0% of new paid (25% of performers) at $44.16/mo blended" in out
    assert "$26.53 blended ARPU over all new paid, less 15% store commission" in out
    assert "Kit payback       month 11 of 36" in out


def test_report_studio_traveler_paid_becomes_an_override(cli_db):
    _boat_only()
    code, out = _run("report", "studio", "--traveler-paid", "0.1")
    assert code == 0, out
    assert "override" in out
    # 48 traveler installs × 10% = 4.8 over 6% churn = 80; the performers are
    # untouched at 5.6.
    assert "10.0% of 48 traveler installs → Base + Conversation Mode" in out
    assert "4.8 travelers + 0.45 performers" in out
    assert "85.6 subscribers (80 travelers · 5.6 performers)" in out
    # The page, when written, carries the same override.
    html = Path(str(cli_db) + ".html")
    code, out = _run("report", "studio", "--traveler-share", "0.5", "--traveler-paid", "0.1",
                     "--html", str(html))
    assert code == 0, out
    page = html.read_text(encoding="utf-8")
    assert '"traveler_share": {"value": 0.5, "source": "override"}' in page
    assert '"traveler_install_to_paid": {"value": 0.1, "source": "override"}' in page


def test_report_studio_refuses_bad_override(cli_db):
    code, out = _run("report", "studio", "--churn", "0")
    assert code == 1
    assert "monthly_churn must be greater than 0" in out

    code, out = _run("report", "studio", "--install-rate", "1.5")
    assert code == 1
    assert "viewer_to_install must be within [0, 1]" in out

    # Zero nights is a month; a negative count of them is not.
    code, out = _run("report", "studio", "--events", "-1")
    assert code == 1
    assert "events_per_month must be 0 or greater" in out

    # The traveler rates are probabilities like the rest.
    code, out = _run("report", "studio", "--traveler-share", "1.5")
    assert code == 1
    assert "traveler_share must be within [0, 1]" in out
    code, out = _run("report", "studio", "--traveler-paid", "-0.1")
    assert code == 1
    assert "traveler_install_to_paid must be within [0, 1]" in out
    assert "Traceback" not in out


def test_report_studio_basis_sentence_follows_the_sources(cli_db):
    """The headline cannot disagree with the funnel's source column or the
    Recorded shows table: it is built from the show count and the source of
    each observable input, group by group."""
    # A show logged without counts is recorded, and everything is still modeled.
    code, out = _run("log", "show", "--platform", "twitch", "--title", "Uncounted set")
    assert code == 0, out
    code, out = _run("report", "studio")
    assert code == 0, out
    assert ("1 show(s) recorded — modeled: viewers per set, stream install rate, "
            "competition multiple, dock crowd") in out
    assert "No shows recorded yet" not in out
    assert "Uncounted set" in out
    # A counted dock crowd is observed; a what-if on the same input is overridden.
    code, out = _run("log", "show", "--kind", "competition", "--attendees", "40")
    assert code == 0, out
    code, out = _run("report", "studio")
    assert code == 0, out
    assert ("2 show(s) recorded — observed: dock crowd; modeled: viewers per set, "
            "stream install rate, competition multiple") in out
    code, out = _run("report", "studio", "--attendees", "50")
    assert code == 0, out
    assert ("2 show(s) recorded — modeled: viewers per set, stream install rate, "
            "competition multiple; overridden: dock crowd") in out


def test_report_studio_recorded_summary_names_the_rows_each_figure_is_drawn_from(cli_db):
    """An average over the sets that were counted is not the total divided by
    every show, and the line says so. Four sets, two with a viewer count (10
    and 0): 5 viewers per counted show, 2 of 4 counted; no set carries both
    counts, so there is no install rate."""
    db = _seeded(cli_db)
    _show(db, "2026-08-01", unique=10)
    _show(db, "2026-08-02", unique=0)
    _show(db, "2026-08-03")
    _show(db, "2026-08-04")
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "4 show(s), 0 competition night(s) · 10 unique viewers · — installs logged" in out
    assert "5 viewers per counted show (2 of 4 sets counted)" in out
    assert "— install rate (from 0 set(s) with both counts)" in out
    # One set with both counts gives the rate its only honest row.
    _show(db, "2026-08-05", unique=140, installs=6)
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "4.3% install rate (from 1 set(s) with both counts)" in out
    assert "(3 of 5 sets counted)" in out


def test_report_studio_reports_an_ignored_observation(cli_db):
    """A row planted past the prompt's floor cannot drive the model negative:
    the observation is listed as ignored and the declared value stands."""
    db = _seeded(cli_db)
    _show(db, "2026-08-01", unique=-50, installs=3)
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "ignored viewers_per_show = -50" in out
    assert "the declared value stands" in out
    assert "150 per show × 4 shows" in out
    assert "1 show(s) recorded — modeled: viewers per set" in out


def test_report_studio_prints_the_competition_night(cli_db):
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "Competition night — Paradise Busker × Key West Treasure Hunt" in out
    # The three stages, one line each.
    for stage in STAGES:
        assert stage["name"] in out and stage["where"] in out
    # The flow is data: every step prints numbered, in order, and the ones with
    # Lyric Show on screen carry the mark.
    for number, step in enumerate(COMPETITION_FLOW, start=1):
        mark = "◆" if step["product"] else "·"
        assert f"{number}. {mark} {step['step']}" in out
    marked = sum(1 for step in COMPETITION_FLOW if step["product"])
    assert out.count("◆") == marked + 1      # the steps plus the legend line
    # The facts, in one table, cited to the sources.
    for fact in ("80% to the artist", "BNDS token, quadratic", "common / rare / legendary",
                 "60/30/10", "75% or better", "$5.00–$10.00 a month", "10% of net profit",
                 "KEY WEST, PARADISE BUSKER", PARADISE_BUSKER["series"], "Sources:",
                 "white paper"):
        assert fact in out


# --- kit ---------------------------------------------------------------------

def test_report_studio_prints_the_priced_kit_checked_against_the_ledger(cli_db):
    """The README's third question is answered with a list, not two totals:
    every planned item with its price, whatever ledger line matches the kit,
    and the inherited A/V and connectivity spend once the ledger attributes it."""
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "Kit — $788.96 planned, $0.00 bought" in out
    for name, cents, _ in STUDIO_KIT:
        assert name in out
        assert f"${cents / 100:,.2f}" in out
    assert "nothing in the ledger matches the kit yet" in out
    # Nothing attributed to A/V or connectivity: the inherited capital is
    # unpriced, and the report says so rather than guessing.
    assert "inherited capital is unpriced" in out
    assert "Inherited A/V & connectivity" not in out

    db = _seeded(cli_db)
    _boat_order(db, "av_security", 19999, "Audient iD4 mkII USB-C audio interface")
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "Kit — $788.96 planned, $199.99 bought" in out
    assert "Audient iD4 mkII USB-C audio interface" in out
    assert "bought — a ledger line that matches the kit" in out
    # The same line is attributed to A/V, so the inherited capital is priced now.
    assert "Inherited A/V & connectivity" in out
    assert "inherited capital is unpriced" not in out
    assert "$199.99 planned" not in out    # the planned total does not move


# --- roi -----------------------------------------------------------------------

def test_report_studio_prints_the_roi_panel(cli_db):
    _boat_only()
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "ROI — the kit, on show-driven revenue" in out
    # Show-driven cumulative net at month 12 is $10,256.40 on a $788.96 kit.
    assert "×13 at month 12 ($10,256.40 show-driven net)" in out
    assert "×75.16 at month 36 ($59,299.67)" in out
    assert "— of project spend (nothing spent yet)" in out
    # $2,675.16 a month over 4 sets + 2 nights, over 1200 viewers.
    assert "$445.86/mo net per show at steady state" in out
    assert "$2.23 per viewer" in out
    # $788.96 over 57 installs × 12, against the paid CPI.
    assert "$1.15 of kit per year-one install  vs  $3.50 paid CPI" in out
    assert "kit month 3 of 36" in out
    # No moorage in the ledger is nothing to cover, not a slip uncovered for 36 months.
    assert "slip — (no moorage recorded)" in out
    assert "Baseline          not entered — okey studio baseline --subscribers N" in out
    assert "ROI uses show-driven revenue only" in out


def test_report_studio_roi_share_of_project_spend_follows_the_ledger(cli_db):
    _boat_only()
    db = _seeded(cli_db)
    _boat_order(db, "plumbing", 100000, "a pump")
    code, out = _run("report", "studio")
    assert code == 0, out
    # $59,299.67 show-driven over 36 months against $1,000.00 spent.
    assert "5930% of project spend" in out
    assert "$1,000.00 project spend" in out


def test_report_studio_roi_is_none_safe(cli_db):
    """No shows in the month: no per-show figure, no per-viewer figure, no
    installs to price — dashes with the reason, never a zero or a crash. The
    committed partner is zeroed first, because a partner stream brings
    viewers and installs whether or not the boat played."""
    _boat_only()
    code, out = _run("report", "studio", "--shows", "0.0001", "--events", "0",
                     "--install-rate", "0")
    assert code == 0, out
    assert "— (no installs) of kit per year-one install" in out
    assert "Traceback" not in out


def test_report_studio_roi_at_the_defaults_rests_on_the_partner(cli_db):
    """With the partner church's placeholder 20,000 viewers counted, the kit pays back
    in month 1 and the multiples are the partner's: $132,913.61 show-driven by
    month 12 on a $788.96 kit, $5,753.29/mo per boat show, $0.09 of kit per
    year-one install over 767 installs."""
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "×168.47 at month 12 ($132,913.61 show-driven net)" in out
    assert "×972.21 at month 36 ($767,032.50)" in out
    assert "$5,753.29/mo net per show at steady state" in out
    assert "$1.63 per viewer" in out
    assert "$0.09 of kit per year-one install  vs  $3.50 paid CPI" in out
    assert "kit month 1 of 36" in out


# --- reach and the target --------------------------------------------------------

def test_report_studio_reach_panel_lists_the_partners_as_data(cli_db):
    """One row per partner, committed first: name, status, cadence, the
    audience in its own unit, viewers a month, the declared abroad share, new
    paid a month at the per-viewer yield, and the source of the figure. The
    totals sum only the counted rows; the languages line and the reach note
    say why one stream crosses borders; the off partner says what it would add
    and how to count it; and the placeholder is marked on the row, below the
    table, and in the headline — each time with the command that replaces it."""
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "Reach — 21,200 viewers a month on every screen the overlay is on" in out
    for partner in PARTNERS:
        assert partner["name"] in out
    # Partner church: committed, 4 Sunday streams at the placeholder 5,000 →
    # 20,000 viewers, 15% abroad = 3,000, 20,000 × 0.007375 = 147.5 new paid.
    assert "committed · counted" in out
    assert "5,000 live viewers per stream" in out
    assert "3,000 (15%)" in out
    assert "147.5" in out
    assert "ESTIMATE · assumed" in out
    # Partner artist: hypothetical and off by default — its full figure is shown and
    # marked off, and nothing of it enters the totals.
    assert "prospective · off" in out
    assert "6,190,000 subscribers × 2% live share" in out
    assert "123,800 · off" in out
    assert "86,660 (70%)" in out
    assert ("Off          Partner artist (hypothetical) would add 123,800 viewers a month, 86,660 abroad "
            "— okey report studio --with-hypothetical counts it") in out
    assert "prospective · counted" not in out
    assert ("All screens  21,200 viewers a month = 1,200 the boat's own (600 stream + 600 event) "
            "+ 20,000 from partner streams; 3,000 of them abroad") in out
    assert "Languages    20 base · 80 in all — Captions render in the viewer's own language" in out
    assert "the international shares are declared, not measured" in out
    assert ("ESTIMATE  Partner church's 5,000 live viewers per stream is an estimate; set the "
            "channel's figure: okey studio partner church --live-viewers N") in out
    # The Viewers row in the funnel names the same partner and the same figure.
    assert "600 stream + 600 event + 20000 partner" in out
    assert "1 counted partner stream(s): 20,000 Partner church — see Reach" in out
    # Three mentions of the placeholder: the headline, the row, the line below.
    assert out.count("ESTIMATE") == 3


def test_report_studio_with_hypothetical_counts_artist_and_moves_the_headline(cli_db):
    """--with-hypothetical is the partners_include_hypothetical switch as an
    override: the partner artist's 6.19M × 2% × 1 = 123,800 viewers join the month, for
    145,000 viewers, 17,656.4 steady subscribers and $231,637.51/mo — and the
    target, 3,000 by month 3, is on track — reached in month 3 — instead of never."""
    code, out = _run("report", "studio", "--with-hypothetical")
    assert code == 0, out
    assert "override" in out
    assert "17656.4 subscribers (16976.7 travelers · 679.7 performers)" in out
    assert "$231,637.51" in out
    assert "Reach — 145,000 viewers a month on every screen the overlay is on" in out
    assert "prospective · counted" in out
    assert "123,800" in out and "123,800 · off" not in out
    assert "913.02" in out           # 123,800 × 0.007375, the partner artist's new paid a month
    assert "1,060.52" in out         # the counted partners' new paid together
    assert "Off          Partner artist" not in out
    assert "600 stream + 600 event + 143800 partner" in out
    assert "2 counted partner stream(s): 20,000 Partner church + 123,800 Partner artist — see Reach" in out
    assert ("All screens  145,000 viewers a month = 1,200 the boat's own (600 stream + 600 event) "
            "+ 143,800 from partner streams; 89,660 of them abroad") in out
    assert "Target            3,000 by month 3: on track — reached in month 3" in out
    # The placeholder is still the placeholder: counting Partner artist does not enter
    # the partner church's figure.
    assert "ESTIMATE          Partner church's 5,000 live viewers a stream is an estimate: 20,000 of 145,000 viewers depend on it" in out
    # The live share is a what-if too: 3% of 6.19M is 185,700 viewers a stream.
    code, out = _run("report", "studio", "--with-hypothetical", "--partner-live-share", "0.03")
    assert code == 0, out
    assert "6,190,000 subscribers × 3% live share" in out
    assert "185,700" in out
    assert "Reach — 206,900 viewers a month" in out


def test_report_studio_target_panel_reads_the_trajectory(cli_db):
    """The target, where the book stands that month, when (if ever) it is
    reached, and what it takes: 3,000 ÷ (1 + 0.94 + 0.94²) = 1,062.47 new paid
    a month at the traveler churn, 144,064.2 viewers a month at the per-viewer
    yield, 24,010.7 per boat show at 4 sets + 2 nights."""
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "Target — read against the trajectory" in out
    assert "Target            3,000 paying subscribers by month 3" in out
    assert "At current rates  451.2 subscribers at month 3 — 2,548.8 short; not within 36 months" in out
    assert ("What it takes     1,062.47 new paid a month from a standing start (6% traveler churn) "
            "= 144,064.2 viewers a month at the current rates, against 21,200 a month on every "
            "screen now") in out
    assert "Per show          24,010.7 viewers at 6 shows a month (4 sets + 2 nights)" in out
    assert "Computed at the current rates and partner set" in out
    # A reachable target: 5,000 by month 6 with both partners and a 3% live
    # share is on track — reached in month 4 — and the headline says so.
    code, out = _run("report", "studio", "--target", "5000", "--target-month", "6",
                     "--with-hypothetical", "--partner-live-share", "0.03")
    assert code == 0, out
    assert "Target            5,000 by month 6: on track — reached in month 4" in out
    assert "Target            5,000 paying subscribers by month 6" in out
    assert "At current rates  7,886.4 subscribers at month 6 — on track, reached in month 4" in out
    assert "967.34 new paid a month" in out
    # The target month past the horizon has no row to read, and says so.
    code, out = _run("report", "studio", "--target-month", "40", "--with-hypothetical")
    assert code == 0, out
    assert "month 40 is past the 36-month horizon (horizon_months) — reached in month 3" in out
    assert "Target            3,000 by month 40: on track — reached in month 3" in out
    # Rates that convert nobody: no number of viewers, not a division by zero.
    code, out = _run("report", "studio", "--install-rate", "0", "--traveler-share", "0")
    assert code == 0, out
    assert "= no number of viewers — the current rates convert nobody" in out
    assert "Per show          —" in out
    assert "Traceback" not in out


def test_report_studio_target_zero_is_no_target(cli_db):
    code, out = _run("report", "studio", "--target", "0")
    assert code == 0, out
    assert ("Target            none set — okey report studio --target N reads one for the run; "
            "studio.target_subscribers in project_meta keeps it") in out
    assert "Target — none set" in out
    # The book's standing in the month is still read; nothing else needs a target.
    assert "At current rates  451.2 subscribers at month 3" in out
    assert "short" not in out and "What it takes" not in out


def test_report_studio_refuses_bad_reach_and_target_flags(cli_db):
    code, out = _run("report", "studio", "--target", "-1")
    assert code == 1
    assert "target_subscribers must be 0 or greater" in out
    code, out = _run("report", "studio", "--target-month", "0")
    assert code == 1
    assert "target_month must be between 1 and 120" in out
    code, out = _run("report", "studio", "--target-month", "121")
    assert code == 1
    assert "target_month must be between 1 and 120" in out
    code, out = _run("report", "studio", "--partner-live-share", "2")
    assert code == 1
    assert "partner_live_share must be within [0, 1]" in out
    assert "Traceback" not in out


# --- studio partner ---------------------------------------------------------------

def test_studio_partner_round_trip(cli_db):
    """the partner church's audience is a placeholder until entered. With no flag the
    command prints the figure as the report reads it and calls it ESTIMATE;
    --live-viewers N writes studio.partner_church_live_viewers, the report
    reads it as meta, the placeholder marks clear and every figure downstream
    moves — 12,000 × 4 = 48,000 viewers, 6,030.6 steady subscribers; --clear
    forgets it and the placeholder is back."""
    code, out = _run("studio", "partner", "church")
    assert code == 0, out
    assert "partner    Partner church (church) — committed · counted" in out
    assert ("audience   5,000 live viewers per stream (assumed) × 4 stream(s)/mo = 20,000 viewers/mo, "
            "3,000 abroad, 147.5 new paid/mo at the current rates") in out
    assert "ESTIMATE" in out and "okey studio partner church --live-viewers N" in out
    assert "steady     2632.7 subscribers · $34,519.72/mo net" in out
    assert "target     3,000 by month 3: 2,548.8 short — 451.2 at month 3, not within 36 months" in out
    assert _meta(cli_db, CHURCH_META) is None

    code, out = _run("studio", "partner", "church", "--live-viewers", "12000")
    assert code == 0, out
    assert "church set live viewers 12,000 → project_meta studio.partner_church_live_viewers" in out
    assert ("audience   12,000 live viewers per stream (meta) × 4 stream(s)/mo = 48,000 viewers/mo, "
            "7,200 abroad, 354 new paid/mo at the current rates") in out
    assert "ESTIMATE" not in out
    assert "steady     6030.6 subscribers · $79,102.10/mo net" in out
    assert "target     3,000 by month 3: 1,966.3 short — 1,033.7 at month 3, reached in" in out
    assert _meta(cli_db, CHURCH_META) == "12000"

    # The report reads it: meta on the row, no placeholder anywhere.
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "6030.6 subscribers (5800 travelers · 230.6 performers)" in out
    assert "12,000 live viewers per stream" in out
    assert "48,000" in out and "7,200 (15%)" in out
    assert "Reach — 49,200 viewers a month" in out
    assert "600 stream + 600 event + 48000 partner" in out
    assert "ESTIMATE" not in out
    assert "meta" in out

    code, out = _run("studio", "partner", "church", "--clear")
    assert code == 0, out
    assert "church cleared — the declared figure stands" in out
    assert "5,000 live viewers per stream (assumed)" in out
    assert "ESTIMATE" in out
    assert _meta(cli_db, CHURCH_META) is None
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "2632.7 subscribers" in out and out.count("ESTIMATE") == 3


def test_studio_partner_enters_an_artist_as_subscribers(cli_db):
    """the partner artist's audience is its subscriber count; the figure is kept while the
    partner is off and counted with --with-hypothetical: 7M × 2% = 140,000."""
    code, out = _run("studio", "partner", "artist")
    assert code == 0, out
    assert "partner    Partner artist (artist) — prospective · off" in out
    assert ("audience   6,190,000 subscribers × 2% live share (assumed) × 1 stream(s)/mo = "
            "123,800 viewers/mo, 86,660 abroad — not counted while off; okey report studio "
            "--with-hypothetical counts it") in out
    code, out = _run("studio", "partner", "artist", "--subscribers", "7000000")
    assert code == 0, out
    assert "artist set subscribers 7,000,000 → project_meta studio.partner_artist_subscribers" in out
    assert "7,000,000 subscribers × 2% live share (meta) × 1 stream(s)/mo = 140,000 viewers/mo" in out
    assert _meta(cli_db, ARTIST_META) == "7000000"
    # Off, the headline does not move; counted, it is the new figure.
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "2632.7 subscribers" in out
    assert "7,000,000 subscribers × 2% live share" in out and "140,000 · off" in out
    code, out = _run("report", "studio", "--with-hypothetical")
    assert code == 0, out
    assert "600 stream + 600 event + 160000 partner" in out
    assert "2 counted partner stream(s): 20,000 Partner church + 140,000 Partner artist — see Reach" in out


def test_studio_partner_refusals(cli_db):
    """An unknown key, a figure in the wrong unit, two figures, a negative
    one, or a figure and --clear together are refused before anything is
    written; zero is a figure."""
    db = _seeded(cli_db)     # the refusals come before the CLI opens the database
    code, out = _run("studio", "partner", "nobody", "--live-viewers", "5")
    assert code == 1
    assert "unknown partner 'nobody' — one of: church, artist" in out
    code, out = _run("studio", "partner", "church", "--subscribers", "5")
    assert code == 1
    assert ("Partner church is a church: its audience is live viewers per stream — okey studio "
            "partner church --live-viewers N") in out
    code, out = _run("studio", "partner", "artist", "--live-viewers", "5")
    assert code == 1
    assert ("Partner artist is an artist: its audience is its subscriber count, with the live share as "
            "the partner_live_share assumption — okey studio partner artist --subscribers N") in out
    code, out = _run("studio", "partner", "church", "--live-viewers", "5", "--subscribers", "5")
    assert code == 1
    assert "a partner has one audience figure; give --live-viewers or --subscribers, not both" in out
    code, out = _run("studio", "partner", "church", "--live-viewers", "-5")
    assert code == 1
    assert "--live-viewers must be 0 or greater, got -5" in out
    code, out = _run("studio", "partner", "church", "--live-viewers", "5", "--clear")
    assert code == 1
    assert "--live-viewers and --clear are two different instructions; give one" in out
    assert "Traceback" not in out
    assert db.one("SELECT COUNT(*) n FROM project_meta WHERE key LIKE 'studio.partner%'")["n"] == 0
    # Zero is a figure — a stream nobody watched — and it is not the placeholder.
    code, out = _run("studio", "partner", "church", "--live-viewers", "0")
    assert code == 0, out
    assert "0 live viewers per stream (meta)" in out and "ESTIMATE" not in out
    assert "steady     205.6 subscribers · $2,675.16/mo net" in out
    assert _meta(cli_db, CHURCH_META) == "0"


# --- studio baseline -----------------------------------------------------------

def test_studio_baseline_round_trip(cli_db):
    _boat_only()
    # Nothing entered: the command says so and points at itself.
    code, out = _run("studio", "baseline")
    assert code == 0, out
    assert "not entered — okey studio baseline --subscribers N" in out
    assert Database(cli_db).one(
        "SELECT value FROM project_meta WHERE key='studio.baseline_subscribers'") is None

    code, out = _run("studio", "baseline", "--subscribers", "120")
    assert code == 0, out
    assert "baseline set" in out and "120 paying subscribers" in out
    assert "120 subscribers (meta) — the trajectory starts here" in out
    # Month 12: the book is baseline plus shows; the show-driven part is the
    # same 108.4 subscribers the default run has.
    assert "152.5 subscribers" in out
    assert "108.4 show-driven" in out
    assert "kit month 3 of 36 — counts show-driven revenue only" in out
    row = Database(cli_db).one(
        "SELECT value FROM project_meta WHERE key='studio.baseline_subscribers'")
    assert row["value"] == "120"

    # The report reads it: the baseline line, the month-12 split, and the same
    # payback month as before — the baseline moves the book, never the kit.
    code, out = _run("report", "studio")
    assert code == 0, out
    assert ("Baseline          120 subscribers (meta) — the trajectory starts here; payback "
            "and ROI count show-driven revenue only") in out
    assert "of which 108.4 subscribers and $10,256.40 are show-driven" in out
    assert "Kit payback       month 3 of 36" in out
    assert "×13 at month 12 ($10,256.40 show-driven net)" in out
    assert "baseline_subscribers" not in out      # only behind --assumptions
    code, out = _run("report", "studio", "--assumptions")
    assert code == 0, out
    assert "baseline_subscribers" in out and "meta" in out

    code, out = _run("studio", "baseline", "--clear")
    assert code == 0, out
    assert "baseline cleared" in out and "not entered" in out
    assert Database(cli_db).one(
        "SELECT value FROM project_meta WHERE key='studio.baseline_subscribers'") is None
    code, out = _run("report", "studio")
    assert code == 0, out
    assert "Baseline          not entered" in out


def test_studio_baseline_refusals(cli_db):
    db = _seeded(cli_db)     # the refusals come before the CLI opens the database
    code, out = _run("studio", "baseline", "--subscribers", "-3")
    assert code == 1
    assert "--subscribers must be 0 or greater, got -3" in out
    code, out = _run("studio", "baseline", "--subscribers", "5", "--clear")
    assert code == 1
    assert "two different instructions" in out
    assert db.one("SELECT value FROM project_meta WHERE key='studio.baseline_subscribers'") is None
    # Zero is accepted and read as "not entered", and the command says so.
    code, out = _run("studio", "baseline", "--subscribers", "0")
    assert code == 0, out
    assert "0 reads as not entered" in out
    assert "not entered — okey studio baseline --subscribers N" in out


# --- html ------------------------------------------------------------------------

def test_report_studio_writes_standalone_html(cli_db, tmp_path):
    target = tmp_path / "studio.html"
    code, out = _run("report", "studio", "--viewers", "200", "--events", "3",
                     "--html", str(target))
    assert code == 0, out
    assert "page written to" in out
    html = target.read_text(encoding="utf-8")
    assert "Lyric Show Growth" in html and "Are we on track" in html   # --html writes the growth story
    # The overrides travelled into the page's embedded report.
    assert '"viewers_per_show": {"value": 200.0, "source": "override"}' in html
    assert '"events_per_month": {"value": 3.0, "source": "override"}' in html
    # So do the reach and target flags; the switch is an override only when thrown.
    assert '"partners_include_hypothetical": {"value": 0, "source": "assumed"}' in html
    code, out = _run("report", "studio", "--with-hypothetical", "--partner-live-share", "0.03",
                     "--target", "5000", "--target-month", "6", "--html", str(target))
    assert code == 0, out
    html = target.read_text(encoding="utf-8")
    assert '"partners_include_hypothetical": {"value": 1.0, "source": "override"}' in html
    assert '"partner_live_share": {"value": 0.03, "source": "override"}' in html
    assert '"target_subscribers": {"value": 5000, "source": "override"}' in html
    assert '"target_month": {"value": 6, "source": "override"}' in html


def test_report_studio_refuses_an_unwritable_html_path(cli_db, tmp_path):
    """One red line up front, not the whole report and then a traceback."""
    code, out = _run("report", "studio", "--html", str(tmp_path / "missing" / "dir" / "x.html"))
    assert code == 1
    assert "could not write" in out and "x.html" in out
    assert "Traceback" not in out
    assert "Steady state" not in out
    assert "page written to" not in out
