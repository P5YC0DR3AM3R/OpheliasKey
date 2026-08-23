"""Command-line interface for Ophelia's Key."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from .analysis.cost import cost_report
from .analysis.demo import clear_demo, seed_demo
from .analysis.reconcile import reconcile as run_reconcile
from .analysis.risk import risk_report
from .analysis.spec import spec_report
from .analysis.studio import OBSERVABLE, PARTNERS, STUDIO_CAPITAL_SYSTEMS, studio_report
from .analysis.reward import reward_report
from .classify.rules import apply_rules
from .classify.taxonomy import seed_systems, seed_vessel_meta
from .config import get_settings
from .db.database import connect, fmt_money, utcnow
from .parsing.vendors_util import resolve_vendor
from .parsing.registry import parse_pending, reset_derived

app = typer.Typer(
    help="Purchase intelligence for the Ophelia's Key boat project.",
    no_args_is_help=True,
    add_completion=False,
)
ingest_app = typer.Typer(help="Pull raw data from a source.", no_args_is_help=True)
report_app = typer.Typer(help="Analysis reports.", no_args_is_help=True)
app.add_typer(ingest_app, name="ingest")
app.add_typer(report_app, name="report")

console = Console()


def _a_usable() -> float:
    """Usable-depth assumption, so the panel can show nominal alongside usable."""
    from .analysis.spec import ASSUMPTIONS

    return ASSUMPTIONS["usable_depth_lifepo4"][0]


def _db():
    settings = get_settings()
    settings.ensure_dirs()
    db = connect()
    seed_systems(db)
    seed_vessel_meta(db)
    return db


log_app = typer.Typer(
    help="Record labor, nights aboard and shows — the inputs the reward and studio reports "
         "cannot read from receipts.",
    no_args_is_help=True,
)
app.add_typer(log_app, name="log")


@log_app.command("labor")
def log_labor(
    hours: float = typer.Argument(..., help="Hours worked."),
    system: str = typer.Option("", "--system", help="Boat system key."),
    note: str = typer.Option("", "--note", help="What was done."),
    date: str = typer.Option("", "--date", help="YYYY-MM-DD; defaults to today."),
    rate: float = typer.Option(0.0, "--rate", help="Override the yard rate, $/hour."),
):
    """Record work performed rather than paid for.

    Labor avoided is the one part of return that is genuinely dollar-for-dollar,
    so it is recorded from real hours rather than estimated.
    """
    db = _db()
    system_id = None
    if system:
        row = db.one("SELECT id FROM boat_systems WHERE key=?", (system,))
        if row is None:
            console.print(f"[red]unknown system '{system}'[/]")
            raise typer.Exit(1)
        system_id = row["id"]
    db.execute(
        """INSERT INTO labor_log (system_id, hours, description, performed_at,
             rate_cents, logged_at) VALUES (?,?,?,?,?,?)""",
        (system_id, hours, note or None, date or utcnow()[:10],
         int(rate * 100) or None, utcnow()),
    )
    console.print(f"[green]logged {hours:g}h[/]" + (f" against {system}" if system else ""))


@log_app.command("nights")
def log_nights(
    nights: int = typer.Argument(..., help="Nights spent aboard."),
    start: str = typer.Option("", "--from", help="YYYY-MM-DD."),
    end: str = typer.Option("", "--to", help="YYYY-MM-DD."),
    location: str = typer.Option("", "--location"),
    note: str = typer.Option("", "--note"),
):
    """Record nights aboard. Use value cannot be inferred from receipts."""
    db = _db()
    db.execute(
        """INSERT INTO usage_log (nights, start_date, end_date, location, note, logged_at)
           VALUES (?,?,?,?,?,?)""",
        (nights, start or None, end or None, location or None, note or None, utcnow()),
    )
    total = db.one("SELECT COALESCE(SUM(nights),0) n FROM usage_log")
    console.print(f"[green]logged {nights} nights[/] — {total['n']} total aboard")


SHOW_KINDS: tuple[str, ...] = ("set", "competition")


@log_app.command("show")
def log_show(
    date: str = typer.Option("", "--date", help="YYYY-MM-DD; defaults to today."),
    kind: str = typer.Option(
        "set", "--kind", help="set | competition — a solo set or a Paradise Busker night."),
    platform: str = typer.Option("", "--platform",
                                 help="youtube | twitch | kick | facebook | multi."),
    title: str = typer.Option("", "--title", help="Set or episode title."),
    minutes: int | None = typer.Option(
        None, "--minutes", help="Stream length in minutes, 0 or more."),
    peak: int | None = typer.Option(None, "--peak", help="Peak concurrent viewers, 0 or more."),
    unique: int | None = typer.Option(None, "--unique", help="Unique viewers, 0 or more."),
    attendees: int | None = typer.Option(
        None, "--attendees",
        help="People on the rear dock and swim platform, if counted; 0 or more."),
    installs: int | None = typer.Option(
        None, "--installs",
        help="Installs traced to this show: store analytics or promo code; 0 or more."),
    note: str = typer.Option("", "--note"),
):
    """Record a livestream set or a competition night performed aboard.

    The studio report models viewers, install rate and the dock crowd until a
    show exists; from the first logged show, the observed figures replace the
    assumed ones. Leave a count out when nobody wrote it down — a missing
    number is not a zero, and the report keeps the difference.
    """
    if kind not in SHOW_KINDS:
        console.print(
            f"[red]--kind must be one of {', '.join(SHOW_KINDS)}; got '{escape(kind)}'[/]")
        raise typer.Exit(1)
    # A count is 0 or more. A negative one is refused before anything is
    # written, the way a negative override is: the model would otherwise
    # carry it into a negative install rate and a negative return.
    counts = {"--minutes": minutes, "--peak": peak, "--unique": unique,
              "--attendees": attendees, "--installs": installs}
    for flag, value in counts.items():
        if value is not None and value < 0:
            console.print(
                f"[red]{flag} must be 0 or greater, got {value} — leave a count out when nobody "
                f"wrote it down; it is never negative[/]")
            raise typer.Exit(1)
    db = _db()
    performed = date or utcnow()[:10]
    db.execute(
        """INSERT INTO show_log (performed_at, kind, platform, title, duration_minutes,
             peak_viewers, unique_viewers, attendees, installs_attributed, note, logged_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (performed, kind, platform or None, title or None, minutes, peak, unique, attendees,
         installs, note or None, utcnow()),
    )
    total = db.one("SELECT COUNT(*) n FROM show_log")
    # User strings are escaped: a title like "[live] Set one" is a title, not
    # markup, and must never break this line or the report's table.
    console.print(
        f"[green]logged {'competition night' if kind == 'competition' else 'show'}[/] "
        f"{escape(performed)}"
        + (f" on {escape(platform)}" if platform else "")
        + (f" — {escape(title)}" if title else "")
        + (f"  ·  {attendees} on the dock" if attendees is not None else "")
        + f"  ·  {total['n']} recorded"
    )


# --- studio inputs ----------------------------------------------------------
# What the studio report cannot read from the ledger or the show log: today's
# paying subscribers live in App Store Connect, Google Play and Firestore, and
# a partner channel's audience lives in that channel's analytics, so they are
# declared here and the report says where they came from.

studio_app = typer.Typer(
    help="Floating studio inputs the report cannot read from the ledger.", no_args_is_help=True)
app.add_typer(studio_app, name="studio")

BASELINE_KEY = "studio.baseline_subscribers"

# Each partner row names the one assumption that holds its audience figure —
# a church's live viewers per stream, an artist's subscriber count — and
# `okey studio partner` writes that assumption to project_meta, so the report
# reads it as `meta` and the PLACEHOLDER flag on the row clears.
PARTNER_BY_KEY: dict[str, dict] = {partner["key"]: partner for partner in PARTNERS}


def _partner_audience_key(partner: dict) -> str:
    return partner["live_viewers_per_stream_key"] or partner["subscribers_key"]


def _partner_meta_key(partner: dict) -> str:
    return f"studio.{_partner_audience_key(partner)}"


def _print_baseline_effect(report: dict) -> None:
    """The baseline as the report reads it, and what it does and does not move."""
    baseline, breakeven = report["baseline"], report["breakeven"]
    horizon = breakeven["horizon_months"]
    if baseline["subscribers"] > 0:
        console.print(
            f"[bold]baseline[/]   {baseline['subscribers']:g} subscribers "
            f"({_sources(baseline['source'])}) — the trajectory starts here")
    else:
        console.print("[bold]baseline[/]   not entered — okey studio baseline --subscribers N")
    m12 = report["lenses"]["subscription"]["month_12"]
    if m12:
        console.print(
            f"month 12   {m12['subscribers']:g} subscribers, "
            f"{fmt_money(m12['mrr_net_cents'])}/mo net — "
            f"{m12['show_driven_subscribers']:g} show-driven, "
            f"{fmt_money(m12['show_driven_mrr_net_cents'])}/mo of it from the shows")
    console.print(
        f"payback    kit {_month(breakeven['kit_month'], horizon)} — counts show-driven revenue "
        f"only, so the baseline does not move it")
    console.print(f"[dim]{baseline['note']}[/]")


@studio_app.command("baseline")
def studio_baseline(
    subscribers: int | None = typer.Option(
        None, "--subscribers",
        help="Paying subscribers today, from App Store Connect / Google Play / Firestore."),
    clear: bool = typer.Option(False, "--clear", help="Forget the entered figure."),
):
    """Declare today's paying subscribers — where the trajectory starts.

    The real figure is not on this machine, so it is entered rather than read,
    and 0 reads as "not entered", not zero. The studio report starts its
    trajectory at the baseline and keeps the show-driven figures apart from it:
    payback and ROI count only the subscribers the shows bring, so the baseline
    moves the book, never the kit's return. With no flag, prints the figure as
    the report currently reads it.
    """
    if subscribers is not None and clear:
        console.print("[red]--subscribers and --clear are two different instructions; give one[/]")
        raise typer.Exit(1)
    if subscribers is not None and subscribers < 0:
        console.print(f"[red]--subscribers must be 0 or greater, got {subscribers}[/]")
        raise typer.Exit(1)
    db = _db()
    if clear:
        db.execute("DELETE FROM project_meta WHERE key = ?", (BASELINE_KEY,))
        console.print("[green]baseline cleared[/] — not entered")
    elif subscribers is not None:
        db.execute(
            """INSERT INTO project_meta (key, value, updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                 updated_at=excluded.updated_at""",
            (BASELINE_KEY, str(subscribers), utcnow()),
        )
        console.print(f"[green]baseline set[/] {subscribers} paying subscribers" + (
            " — 0 reads as not entered" if subscribers == 0 else ""))
    try:
        report = studio_report(db)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)
    _print_baseline_effect(report)


def _print_partner_effect(report: dict, key: str) -> None:
    """The partner as the report reads it — its audience and where the figure
    came from, what it adds to the month, whether it is counted — and what
    that does to the steady state and the target. A figure still at the
    declared stand-in is called PLACEHOLDER here as loudly as in the report."""
    partner = PARTNER_BY_KEY[key]
    row = next(r for r in report["reach"]["partners"] if r["key"] == key)
    inputs = report["funnel"]["inputs"]
    source = _sources(inputs[_partner_audience_key(partner)]["source"])
    steady = report["funnel"]["steady_state"]
    console.print(f"[bold]partner[/]    {row['name']} ({key}) — {_partner_status(row)}")
    console.print(
        f"audience   {_audience(row, inputs['partner_live_share']['value'])} ({source}) × "
        f"{row['streams_per_month']} stream(s)/mo = {_count(row['viewers_per_month'])} viewers/mo, "
        f"{_count(row['viewers_abroad'])} abroad"
        + (f", {_count(row['new_paid_per_month'])} new paid/mo at the current rates"
           if row["active"] else " — not counted while off; okey report studio "
                                 "--with-hypothetical counts it"))
    if row["placeholder"]:
        console.print(f"           {_placeholder_line(row, key)}")
    console.print(
        f"steady     {steady['subscribers']:g} subscribers · "
        f"{fmt_money(steady['mrr_net_cents'])}/mo net, with every counted partner")
    console.print(
        f"target     {_target_phrase(report['target'], report['breakeven']['horizon_months'])}")


@studio_app.command("partner")
def studio_partner(
    key: str = typer.Argument(
        ..., help=f"Partner key: {' | '.join(PARTNER_BY_KEY)} — the Reach panel lists them."),
    live_viewers: int | None = typer.Option(
        None, "--live-viewers",
        help="Live viewers per stream, from the channel's analytics — for a church; 0 or more."),
    subscribers: int | None = typer.Option(
        None, "--subscribers",
        help="Channel subscribers — for an artist; the live share is the partner_live_share "
             "assumption; 0 or more."),
    clear: bool = typer.Option(
        False, "--clear", help="Forget the entered figure; the declared value stands again."),
):
    """Enter a partner channel's audience — the figure the Reach panel runs on.

    A partner's audience lives in its channel analytics, not on this machine,
    so it is entered rather than read: a church's live viewers per Sunday
    stream, an artist's subscriber count. the partner church's figure is a labelled
    PLACEHOLDER until it is entered here, and the report says so on the row
    and in the headline for as long as that lasts. With no flag, prints the
    figure as the report currently reads it.
    """
    partner = PARTNER_BY_KEY.get(key)
    if partner is None:
        console.print(f"[red]unknown partner '{escape(key)}' — one of: "
                      f"{', '.join(PARTNER_BY_KEY)}[/]")
        raise typer.Exit(1)
    given = {flag: value for flag, value in (("--live-viewers", live_viewers),
                                              ("--subscribers", subscribers))
             if value is not None}
    if len(given) > 1:
        console.print("[red]a partner has one audience figure; give --live-viewers or "
                      "--subscribers, not both[/]")
        raise typer.Exit(1)
    if given and clear:
        console.print(f"[red]{next(iter(given))} and --clear are two different instructions; "
                      f"give one[/]")
        raise typer.Exit(1)
    # The figure must be in the partner's own unit: a church counts live
    # viewers a stream, an artist's viewers are derived from its subscribers.
    wanted = "--live-viewers" if partner["kind"] == "church" else "--subscribers"
    if given and wanted not in given:
        unit = ("live viewers per stream" if partner["kind"] == "church"
                else "its subscriber count, with the live share as the partner_live_share "
                     "assumption")
        article = "an" if partner["kind"][0] in "aeiou" else "a"
        console.print(f"[red]{partner['name']} is {article} {partner['kind']}: its audience is "
                      f"{unit} — okey studio partner {key} {wanted} N[/]")
        raise typer.Exit(1)
    value = given.get(wanted)
    if value is not None and value < 0:
        console.print(f"[red]{wanted} must be 0 or greater, got {value}[/]")
        raise typer.Exit(1)
    db = _db()
    meta_key = _partner_meta_key(partner)
    if clear:
        db.execute("DELETE FROM project_meta WHERE key = ?", (meta_key,))
        console.print(f"[green]{key} cleared[/] — the declared figure stands")
    elif value is not None:
        db.execute(
            """INSERT INTO project_meta (key, value, updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                 updated_at=excluded.updated_at""",
            (meta_key, str(value), utcnow()),
        )
        console.print(f"[green]{key} set[/] {wanted.lstrip('-').replace('-', ' ')} {value:,} "
                      f"→ project_meta {meta_key}")
    try:
        report = studio_report(db)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)
    _print_partner_effect(report, key)


gmail_app = typer.Typer(help="Gmail connection setup.", no_args_is_help=True)
app.add_typer(gmail_app, name="gmail")


@gmail_app.command("setup")
def gmail_setup():
    """Check Gmail prerequisites and print exactly what is missing."""
    from .sources.gmail import DEFAULT_QUERY, SCOPES

    settings = get_settings()
    secret = Path(settings.gmail_client_secret_file)
    token = Path(settings.gmail_token_file)

    table = Table("check", "status", "detail")
    ok = True

    if secret.exists():
        table.add_row("OAuth client", "[green]found[/]", str(secret))
    else:
        ok = False
        table.add_row("OAuth client", "[red]missing[/]", str(secret))

    if token.exists():
        table.add_row("Authorized token", "[green]found[/]", str(token))
    else:
        table.add_row("Authorized token", "[yellow]not yet[/]",
                      "created on first `okey ingest gmail`")

    try:
        import googleapiclient  # noqa: F401
        import google_auth_oauthlib  # noqa: F401
        table.add_row("Python packages", "[green]installed[/]",
                      "google-api-python-client, google-auth-oauthlib")
    except ImportError:
        ok = False
        table.add_row("Python packages", "[red]missing[/]",
                      "pip install google-api-python-client google-auth-oauthlib")

    console.print(table)
    console.print(f"\n[dim]scope:[/] {SCOPES[0]}")
    console.print(f"[dim]since:[/] {settings.gmail_since}")
    console.print(f"[dim]query:[/] {DEFAULT_QUERY[:110]}...")

    if not ok:
        console.print(
            Panel(
                "1. Open [cyan]console.cloud.google.com[/] and create (or pick) a project.\n"
                "2. APIs & Services → Library → enable [cyan]Gmail API[/].\n"
                "3. APIs & Services → OAuth consent screen → External → add your own\n"
                "   address as a Test user. Staying in Testing mode is fine.\n"
                "4. Credentials → Create credentials → OAuth client ID →\n"
                "   Application type [cyan]Desktop app[/].\n"
                f"5. Download the JSON and save it to:\n   [cyan]{secret}[/]\n"
                "6. Run [cyan]okey ingest gmail --full[/]. A browser opens once for\n"
                "   consent; the token is cached afterwards.\n\n"
                "[dim]Read-only scope. Nothing is ever sent, deleted or modified.[/]",
                title="Gmail setup — remaining steps", border_style="yellow",
            )
        )
    else:
        console.print("\n[green]Ready. Run `okey ingest gmail --full`.[/]")


add_app = typer.Typer(help="Record invoices the parser cannot read.", no_args_is_help=True)
app.add_typer(add_app, name="add")

VESSEL_DEFAULT = "Ophelia's Key"


@add_app.command("invoice")
def add_invoice(
    amount: float = typer.Argument(..., help="Invoice total in dollars."),
    vendor: str = typer.Option(..., "--vendor", help="Vendor name."),
    system: str = typer.Option(..., "--system", help="Boat system key."),
    date: str = typer.Option(..., "--date", help="YYYY-MM-DD."),
    reference: str = typer.Option("", "--ref", help="Invoice or statement number."),
    note: str = typer.Option("", "--note", help="What it was for."),
    vessel: str = typer.Option(VESSEL_DEFAULT, "--vessel", help="Which vessel."),
    personal: bool = typer.Option(False, "--personal", help="Not project spend."),
    insurable: bool = typer.Option(
        None, "--insurable/--not-insurable",
        help="Override whether this appears on the insurance schedule."),
):
    """Record an invoice by hand.

    Most real marine invoices arrive as a PDF or a portal link with no amount in
    the email body, so they cannot be parsed. Entering them here keeps them out
    of the blind spot rather than out of the totals.
    """
    db = _db()
    row = db.one("SELECT id FROM boat_systems WHERE key=?", (system,))
    if row is None:
        console.print(f"[red]unknown system '{system}'[/]")
        console.print("[dim]list them with: okey systems[/]")
        raise typer.Exit(1)

    cents = int(round(amount * 100))
    vendor_id = resolve_vendor(db, name=vendor)
    external = reference or f"{vendor}-{date}-{cents}"

    existing = db.one(
        "SELECT id FROM orders WHERE source='manual' AND external_order_id=?", (external,))
    if existing:
        console.print(f"[yellow]already recorded as order {existing['id']}[/]")
        raise typer.Exit(1)

    with db.tx():
        cur = db.execute(
            """INSERT INTO orders (source, external_order_id, vendor_id, ordered_at,
                 status, total_cents, currency, vessel, reference, created_at, updated_at)
               VALUES ('manual',?,?,?,'delivered',?,'USD',?,?,?,?)""",
            (external, vendor_id, f"{date}T00:00:00Z", cents, vessel,
             reference or None, utcnow(), utcnow()),
        )
        order_id = int(cur.lastrowid)
        db.execute(
            """INSERT INTO line_items (order_id, line_no, description, quantity,
                 unit_price_cents, total_cents, system_id, classified_by, classify_conf,
                 classified_at, relevance, relevance_by, relevance_conf, insurable)
               VALUES (?,0,?,1,?,?,?,'manual',1.0,?,?, 'manual',1.0,?)""",
            (order_id, note or f"{vendor} {reference or 'invoice'}".strip(), cents, cents,
             row["id"], utcnow(), "personal" if personal else "boat",
             None if insurable is None else int(insurable)),
        )
    console.print(
        f"[green]recorded[/] {vendor} {reference or ''} {fmt_money(cents)} → {system}"
        + (f" [dim]({vessel})[/]" if vessel != VESSEL_DEFAULT else "")
    )


@add_app.command("commitment")
def add_commitment(
    description: str = typer.Argument(..., help="What the work is."),
    vendor: str = typer.Option("", "--vendor"),
    system: str = typer.Option("", "--system", help="Boat system key."),
    estimate: float = typer.Option(0.0, "--estimate", help="Estimated cost; omit if unknown."),
    scheduled: str = typer.Option("", "--scheduled", help="YYYY-MM-DD."),
    reference: str = typer.Option("", "--ref", help="Repair order or quote number."),
    note: str = typer.Option("", "--note"),
    vessel: str = typer.Option(VESSEL_DEFAULT, "--vessel"),
):
    """Record work that is authorized or scheduled but not yet invoiced.

    Omit --estimate when the cost is genuinely unknown; it stays NULL rather
    than becoming zero, and the reports say how many commitments are unpriced.
    """
    db = _db()
    system_id = None
    if system:
        row = db.one("SELECT id FROM boat_systems WHERE key=?", (system,))
        if row is None:
            console.print(f"[red]unknown system '{system}'[/]")
            raise typer.Exit(1)
        system_id = row["id"]
    vendor_id = resolve_vendor(db, name=vendor) if vendor else None

    db.execute(
        """INSERT OR IGNORE INTO commitments (vendor_id, system_id, description,
             estimate_cents, scheduled_for, reference, vessel, note, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (vendor_id, system_id, description,
         int(round(estimate * 100)) if estimate else None,
         scheduled or None, reference or None, vessel, note or None, utcnow()),
    )
    console.print(
        f"[green]committed[/] {description[:52]}"
        + (f" — {fmt_money(int(round(estimate*100)))}" if estimate else " — [yellow]no estimate[/]")
        + (f", scheduled {scheduled}" if scheduled else "")
    )


@add_app.command("invoiced")
def commitment_invoiced(reference: str = typer.Argument(..., help="Commitment reference.")):
    """Close a commitment once its invoice has been recorded."""
    db = _db()
    cur = db.execute("UPDATE commitments SET status='invoiced' WHERE reference=?", (reference,))
    if cur.rowcount:
        console.print(f"[green]closed {cur.rowcount} commitment(s) for {reference}[/]")
    else:
        console.print(f"[yellow]no open commitment with reference {reference}[/]")


@app.command()
def systems():
    """List boat system keys."""
    db = _db()
    table = Table("key", "name", "capital", "description")
    for row in db.query(
        "SELECT key, name, is_capital, description FROM boat_systems ORDER BY sort_order"
    ):
        table.add_row(row["key"], row["name"], "yes" if row["is_capital"] else "no",
                      (row["description"] or "")[:52])
    console.print(table)


@app.command("amazon")
def amazon_setup():
    """How to connect Amazon, and what is configured now."""
    settings = get_settings()
    table = Table("path", "status", "detail")

    if settings.amazon_configured:
        table.add_row("Business API", "[green]configured[/]", "LWA credentials present")
    else:
        table.add_row("Business API", "[yellow]not configured[/]",
                      "needs developer-program approval")

    csv_dir = Path(settings.amazon_csv_dir)
    found = sorted(csv_dir.rglob("*.csv")) if csv_dir.exists() else []
    if found:
        table.add_row("Data export", "[green]found[/]",
                      f"{len(found)} CSV file(s) in {csv_dir}")
    else:
        table.add_row("Data export", "[yellow]no files[/]", str(csv_dir))
    console.print(table)

    console.print(
        Panel(
            "[bold]Fastest path — works today, no approval needed[/]\n"
            "1. Go to [cyan]amazon.com/hz/privacy-central/data-requests/preview.html[/]\n"
            "2. Request [cyan]Your Orders[/] and confirm by email.\n"
            "3. Amazon emails a download link in a few hours to a few days.\n"
            f"4. Unzip it into [cyan]{csv_dir}[/]\n"
            "5. Run [cyan]okey ingest amazon-csv[/] then [cyan]okey parse[/]\n\n"
            "[dim]Look for Retail.OrderHistory.1.csv — one row per item, every "
            "order, ASIN, unit price, quantity, tax and status.[/]\n\n"
            "[bold]Ongoing sync — needs approval[/]\n"
            "Your account is already Amazon Business (orders read 'On behalf of\n"
            "Micah Read MGMT', and you hold B2B protection plans), so you can apply:\n"
            "1. Amazon Business → Business Settings → System Integrations.\n"
            "2. Register a developer application; note the LWA client id and secret.\n"
            "3. Authorize it to get a refresh token.\n"
            "4. Put all three in [cyan].env[/] as OKEY_AMAZON_CLIENT_ID / "
            "_CLIENT_SECRET / _REFRESH_TOKEN\n"
            "5. Run [cyan]okey ingest amazon --full[/]\n\n"
            "[dim]Approval is not guaranteed and can take weeks. Both paths feed the\n"
            "same parser, so nothing is wasted by starting with the export.[/]",
            title="Connecting Amazon", border_style="yellow",
        )
    )


@app.command()
def init():
    """Create the database and seed the boat-system taxonomy."""
    db = _db()
    count = len(db.query("SELECT id FROM boat_systems"))
    console.print(
        Panel(
            f"Database ready at [cyan]{get_settings().db_path}[/]\n"
            f"{count} boat systems seeded.",
            title="initialized",
            border_style="green",
        )
    )


@app.command()
def status():
    """Show ingestion and processing state."""
    db = _db()
    table = Table("source", "raw docs", "unparsed", "last run", "status", title="Sources")
    sources = db.query("SELECT DISTINCT source FROM raw_documents")
    known = {r["source"] for r in sources} | {"gmail", "amazon_business", "plaid"}
    for source in sorted(known):
        total = db.one("SELECT COUNT(*) n FROM raw_documents WHERE source=?", (source,))
        pending = db.one(
            "SELECT COUNT(*) n FROM raw_documents WHERE source=? AND parsed_at IS NULL",
            (source,),
        )
        state = db.one("SELECT last_run_at, last_status FROM sync_state WHERE source=?", (source,))
        table.add_row(
            source,
            str(total["n"] if total else 0),
            str(pending["n"] if pending else 0),
            (state["last_run_at"] if state else None) or "—",
            (state["last_status"] if state else None) or "—",
        )
    console.print(table)

    counts = db.one(
        """SELECT (SELECT COUNT(*) FROM orders) o,
                  (SELECT COUNT(*) FROM line_items) li,
                  (SELECT COUNT(*) FROM line_items WHERE system_id IS NULL) unc,
                  (SELECT COUNT(*) FROM transactions) t,
                  (SELECT COUNT(*) FROM reconciliations) rc"""
    )
    console.print(
        f"orders [bold]{counts['o']}[/]  ·  line items [bold]{counts['li']}[/] "
        f"([yellow]{counts['unc']}[/] unclassified)  ·  transactions [bold]{counts['t']}[/] "
        f" ·  reconciled [bold]{counts['rc']}[/]"
    )


@ingest_app.command("gmail")
def ingest_gmail(full: bool = typer.Option(False, help="Re-scan from the project start date.")):
    """Fetch order emails from Gmail."""
    from .sources.gmail import GmailSource

    db = _db()
    result = GmailSource().sync(db, full=full)
    console.print(f"[green]{result.summary()}[/]")
    for err in result.errors[:10]:
        console.print(f"  [red]{err}[/]")


@ingest_app.command("amazon")
def ingest_amazon(full: bool = typer.Option(False, help="Re-scan from the project start date.")):
    """Fetch purchase data from the Amazon Business Reconciliation API."""
    from .sources.amazon_business import AmazonBusinessSource

    db = _db()
    result = AmazonBusinessSource().sync(db, full=full)
    console.print(f"[green]{result.summary()}[/]")


@ingest_app.command("amazon-csv")
def ingest_amazon_csv(
    directory: str = typer.Option("", "--dir", help="Folder holding the Amazon export."),
    since: str = typer.Option("", "--since", help="Only orders on/after YYYY-MM-DD."),
):
    """Import the Amazon 'Request My Data' order-history export.

    This path needs no API approval. Unzip the export anywhere under the
    configured folder and point this at it.
    """
    from .sources.amazon_csv import AmazonCsvSource, import_refunds

    db = _db()
    source = AmazonCsvSource(directory or None, since=since or None)
    result = source.sync(db)
    refunds = import_refunds(db, source.directory)
    if refunds["read"]:
        console.print(
            f"[green]refunds: {refunds['linked']} linked "
            f"({fmt_money(refunds['amount_cents'])})[/]"
            + (f", {refunds['unmatched']} for orders outside this export"
               if refunds["unmatched"] else "")
        )
    if result.errors:
        for err in result.errors:
            console.print(f"[red]{err}[/]")
        if not result.fetched:
            raise typer.Exit(1)
    console.print(f"[green]{result.summary()}[/]")
    console.print("[dim]Now run: okey parse && okey classify[/]")


@ingest_app.command("plaid")
def ingest_plaid(full: bool = typer.Option(False, help="Ignore the stored cursor.")):
    """Sync bank and card transactions from Plaid."""
    from .sources.plaid_source import PlaidSource

    db = _db()
    result = PlaidSource().sync(db, full=full)
    console.print(f"[green]{result.summary()}[/]")


@app.command()
def parse(reparse: bool = typer.Option(False, help="Rebuild all derived data from raw.")):
    """Turn raw documents into orders and line items."""
    db = _db()
    if reparse:
        reset_derived(db)
        console.print("[yellow]derived tables cleared; rebuilding from raw[/]")
    stats = parse_pending(db)
    console.print(
        f"[green]parsed {stats['parsed']}[/], skipped {stats['skipped']}, "
        f"[red]failed {stats['failed']}[/]"
    )


@app.command()
def classify(
    llm: bool = typer.Option(False, "--llm", help="Run the LLM pass over what rules could not place."),
    reclassify: bool = typer.Option(False, help="Re-run over already-classified items."),
    min_confidence: float = typer.Option(0.6, help="Confidence floor for auto-assignment."),
    effort: str = typer.Option("medium", help="LLM effort: low | medium | high | xhigh | max."),
):
    """Attribute line items to boat systems, and decide boat vs personal."""
    db = _db()
    stats = apply_rules(db, min_confidence=min_confidence, reclassify=reclassify)
    console.print(
        f"[bold]rules[/]  examined {stats['examined']}  ·  "
        f"[green]{stats['relevance_boat']} boat[/] · {stats['relevance_personal']} personal · "
        f"[yellow]{stats['relevance_deferred']} deferred[/]  ·  "
        f"systems set {stats['system_set']}, ambiguous {stats['system_ambiguous']}, "
        f"unmatched {stats['system_unmatched']}"
    )

    if llm:
        from .classify.llm import apply_llm

        try:
            result = apply_llm(db, effort=effort)
        except Exception as exc:
            console.print(f"[red]LLM pass failed: {type(exc).__name__}: {exc}[/]")
            raise typer.Exit(1)
        console.print(
            f"[bold]llm[/]    examined {result['examined']} in {result['batches']} batch(es)  ·  "
            f"[green]{result['boat']} boat[/] · {result['personal']} personal · "
            f"[yellow]{result['ambiguous']} ambiguous[/]  ·  "
            f"systems set {result['systems_set']}  ·  "
            f"[yellow]{result['needs_review']} need review[/]"
        )
        for err in result["errors"][:5]:
            console.print(f"  [red]{err}[/]")
    else:
        pending = db.one("SELECT COUNT(*) n FROM v_review_queue")
        if pending and pending["n"]:
            console.print(
                f"[dim]{pending['n']} items unresolved. Run `okey classify --llm` to "
                f"classify them with vessel context, then `okey review`.[/]"
            )


@app.command()
def review(
    limit: int = typer.Option(30, help="How many items to show."),
    item: int = typer.Option(0, "--item", help="Line item id to set."),
    mark: str = typer.Option("", "--mark", help="boat | personal (with --item)."),
    system: str = typer.Option("", "--system", help="System key to assign (with --item)."),
):
    """Show or clear the human review queue.

    Manual verdicts are final: neither the rules nor the LLM will overwrite them.
    """
    db = _db()

    if item:
        if mark not in ("boat", "personal", ""):
            console.print("[red]--mark must be 'boat' or 'personal'[/]")
            raise typer.Exit(1)
        with db.tx():
            if mark:
                db.execute(
                    """UPDATE line_items SET relevance=?, relevance_by='manual',
                         relevance_conf=1.0 WHERE id=?""",
                    (mark, item),
                )
            if system:
                row = db.one("SELECT id FROM boat_systems WHERE key=?", (system,))
                if row is None:
                    console.print(f"[red]unknown system '{system}'[/]")
                    raise typer.Exit(1)
                db.execute(
                    """UPDATE line_items SET system_id=?, classified_by='manual',
                         classify_conf=1.0, classified_at=? WHERE id=?""",
                    (row["id"], utcnow(), item),
                )
        console.print(f"[green]item {item} updated[/]")
        return

    rows = db.query("SELECT * FROM v_review_queue ORDER BY total_cents DESC LIMIT ?", (limit,))
    if not rows:
        console.print("[green]Review queue is empty.[/]")
        return

    total = db.one("SELECT COUNT(*) n, COALESCE(SUM(total_cents),0) amt FROM v_review_queue")
    console.print(
        f"[yellow]{total['n']} items awaiting review, {fmt_money(total['amt'])} at stake[/]\n"
    )
    table = Table("id", "item", "amount", "vendor", "call", "why")
    for row in rows:
        call = row["relevance"] or "—"
        conf = f" {row['relevance_conf']:.2f}" if row["relevance_conf"] else ""
        table.add_row(
            str(row["id"]), row["description"][:46], fmt_money(row["total_cents"]),
            (row["vendor"] or "—")[:16], f"{call}{conf}", (row["relevance_note"] or "")[:44],
        )
    console.print(table)
    console.print(
        "[dim]Set with: okey review --item <id> --mark boat --system electronics_nav[/]"
    )


@app.command()
def reconcile():
    """Match orders against bank transactions."""
    db = _db()
    stats = run_reconcile(db)
    console.print(
        f"examined {stats['examined']}  ·  [green]matched {stats['matched']}[/]  ·  "
        f"[yellow]ambiguous {stats['ambiguous']}[/]  ·  unmatched {stats['unmatched']}"
    )


@report_app.command("cost")
def report_cost():
    """Cost breakdown by system, vendor and month."""
    db = _db()
    report = cost_report(db)
    t = report["totals"]

    console.print(
        Panel(
            f"Project spend  [bold green]{fmt_money(t['net_cents'])}[/]  "
            f"({t['boat_item_count']} of {t['item_count']} line items)\n"
            f"Refunded       {fmt_money(t['refunded_cents'])}\n"
            f"Excluded       {fmt_money(t['personal_cents'])} personal\n"
            f"Unreviewed     [yellow]{fmt_money(t['unreviewed_cents'])}[/] "
            f"across {t['unreviewed_count']} items — could move the total either way\n"
            f"Capital        {fmt_money(t['capital_cents'])}   "
            f"Consumable {fmt_money(t['consumable_cents'])}   "
            f"Unattributed [yellow]{fmt_money(t['unattributed_cents'])}[/]\n"
            f"Burn rate      {fmt_money(report['monthly_burn_cents'])}/mo (trailing 3mo)",
            title="Ophelia's Key — cost",
            border_style="cyan",
        )
    )

    table = Table("system", "items", "spend", "budget", "variance", "% of plan")
    for row in report["by_system"]:
        variance = row["variance_cents"]
        colour = "red" if variance and variance > 0 else "green"
        table.add_row(
            row["name"],
            str(row["items"]),
            fmt_money(row["spend_cents"]),
            fmt_money(row["planned_cents"]),
            f"[{colour}]{fmt_money(variance)}[/]" if variance is not None else "—",
            f"{row['pct_of_plan']}%" if row["pct_of_plan"] is not None else "—",
        )
    console.print(table)

    vendors = Table("vendor", "orders", "spend", title="Top vendors")
    for row in report["by_vendor"][:10]:
        vendors.add_row(row["vendor"], str(row["orders"]), fmt_money(row["spend_cents"]))
    console.print(vendors)


@report_app.command("risk")
def report_risk():
    """Risk findings, most severe first."""
    db = _db()
    report = risk_report(db)
    counts = report["counts"]

    header = (
        f"Net spend      {fmt_money(report['net_spend_cents'])}\n"
        f"Budget         {fmt_money(report['budget_total_cents'])}\n"
    )
    if report["remaining_cents"] is not None:
        colour = "red" if report["remaining_cents"] < 0 else "green"
        header += f"Remaining      [{colour}]{fmt_money(report['remaining_cents'])}[/]\n"
    header += (
        f"Findings       [red]{counts['high']} high[/] · "
        f"[yellow]{counts['medium']} medium[/] · {counts['low']} low"
    )
    console.print(Panel(header, title="Ophelia's Key — risk", border_style="magenta"))

    table = Table("sev", "finding", "amount", "detail")
    palette = {"high": "red", "medium": "yellow", "low": "dim"}
    for finding in report["findings"]:
        table.add_row(
            f"[{palette[finding['severity']]}]{finding['severity']}[/]",
            finding["title"],
            fmt_money(finding["amount_cents"]),
            finding["detail"],
        )
    console.print(table)

    spec_counts = report["spec_counts"]
    if any(spec_counts.values()):
        console.print(
            f"\n[blue]Specification risk:[/] [red]{spec_counts['high']} high[/] · "
            f"[yellow]{spec_counts['medium']} medium[/] · {spec_counts['low']} low  "
            f"[dim]— see `okey report spec`[/]"
        )
        for finding in report["spec_findings"][:3]:
            console.print(f"  [{palette[finding['severity']]}]●[/] {finding['title']}")


@report_app.command("commitments")
def report_commitments():
    """Work committed but not yet invoiced."""
    from .analysis.commitments import commitment_summary

    db = _db()
    summary = commitment_summary(db)
    if not summary["count"]:
        console.print("[green]Nothing committed and unbilled.[/]")
        return

    header = f"Committed items      {summary['count']}"
    if summary["priced_count"]:
        header += (
            f"\nEstimated            {fmt_money(summary['estimated_cents'])}"
            f"  [dim](from {summary['priced_count']} of {summary['count']} items)[/]"
        )
    if summary["unpriced_count"] == summary["count"]:
        header += (
            "\n[yellow]Estimated            unknown — none of these are quoted yet[/]"
        )
    elif summary["unpriced_count"]:
        header += (
            f"\n[yellow]Without an estimate  {summary['unpriced_count']} — "
            f"the real figure is higher[/]"
        )
    if summary["next_scheduled"]:
        header += f"\nNext scheduled       {summary['next_scheduled']}"
    console.print(Panel(header, title="Ophelia's Key — committed work",
                        border_style="yellow"))

    table = Table("scheduled", "vendor", "work", "system", "estimate", "ref")
    for item in summary["items"]:
        table.add_row(
            item["scheduled_for"] or "—", (item["vendor"] or "—")[:16],
            item["description"][:44], (item["system_name"] or "—")[:18],
            fmt_money(item["estimate_cents"]) if item["estimate_cents"]
            else "[yellow]unknown[/]",
            item["reference"] or "—",
        )
    console.print(table)


@report_app.command("duplicates")
def report_duplicates(window: int = typer.Option(7, help="Days between orders.")):
    """Identical orders close together — possible double charges."""
    from .analysis.risk import duplicate_orders

    db = _db()
    if not duplicate_orders(db, window_days=window):
        console.print("[green]No duplicate-looking orders.[/]")
        return
    rows = db.query(
        """SELECT a.external_order_id fid, b.external_order_id sid, a.total_cents amt,
                  a.ordered_at fat, b.ordered_at sat, v.canonical_name vendor
           FROM orders a
           JOIN orders b ON b.vendor_id=a.vendor_id AND b.total_cents=a.total_cents
                        AND b.id > a.id
                        AND julianday(b.ordered_at) - julianday(a.ordered_at) <= ?
           LEFT JOIN vendors v ON v.id=a.vendor_id
           WHERE a.status!='cancelled' AND b.status!='cancelled' AND a.total_cents > 0
           ORDER BY a.total_cents DESC""", (window,))
    table = Table("amount", "vendor", "first order", "second order", "gap")
    for r in rows:
        gap = ""
        if r["fat"] and r["sat"]:
            from datetime import datetime
            d1 = datetime.fromisoformat(r["fat"].replace("Z", ""))
            d2 = datetime.fromisoformat(r["sat"].replace("Z", ""))
            gap = f"{(d2 - d1).days}d"
        table.add_row(fmt_money(r["amt"]), r["vendor"] or "—",
                      f'{r["fid"]}\n{(r["fat"] or "")[:10]}',
                      f'{r["sid"]}\n{(r["sat"] or "")[:10]}', gap)
    console.print(table)
    console.print("[dim]Check your card statement; mark false positives with "
                  "`okey review --item <id> --mark personal` or leave them.[/]")


@report_app.command("priceless")
def report_priceless():
    """Line items that exist but carry no price."""
    db = _db()
    rows = db.query(
        """SELECT li.id, li.description, li.quantity, li.asin, v.canonical_name AS vendor,
                  o.ordered_at, o.external_order_id
           FROM line_items li
           JOIN orders o ON o.id = li.order_id
           LEFT JOIN vendors v ON v.id = o.vendor_id
           WHERE li.relevance='boat' AND o.status != 'cancelled'
             AND li.unit_price_cents IS NULL AND COALESCE(li.total_cents,0) = 0
           ORDER BY o.ordered_at DESC"""
    )
    if not rows:
        console.print("[green]Every line item has a price.[/]")
        return
    console.print(
        f"[yellow]{len(rows)} item(s) counting as zero in every total.[/]\n"
    )
    table = Table("id", "date", "item", "qty", "asin", "order")
    for row in rows:
        table.add_row(str(row["id"]), (row["ordered_at"] or "—")[:10],
                      row["description"][:44], f"{row['quantity']:g}",
                      row["asin"] or "—", row["external_order_id"][:20])
    console.print(table)


@report_app.command("unpriced")
def report_unpriced():
    """Invoice emails whose amount is only inside an attachment."""
    db = _db()
    rows = db.query(
        """SELECT external_id, occurred_at, parse_error FROM raw_documents
           WHERE parse_error LIKE '%attachment%' ORDER BY occurred_at DESC"""
    )
    if not rows:
        console.print("[green]No unpriced invoices.[/]")
        return
    console.print(
        f"[yellow]{len(rows)} invoice email(s) the parser could not price.[/] "
        "Enter these by hand — they are missing from every total.\n"
    )
    table = Table("date", "message", "why")
    for row in rows:
        table.add_row((row["occurred_at"] or "—")[:10], row["external_id"][:20],
                      row["parse_error"][:64])
    console.print(table)


@report_app.command("insurance")
def report_insurance(
    pdf: str = typer.Option("", "--pdf", help="Write a PDF to this path."),
    vessel: str = typer.Option("Ophelia's Key", "--vessel", help="Restrict to one vessel."),
):
    """Equipment and professional installation, for an insurer.

    Deliberately narrower than the cost report: slip fees, registration, title,
    insurance premiums, transport, storage, consumables and tools are excluded,
    each by a named rule that the schedule prints.
    """
    from .analysis.insurance import schedule

    db = _db()
    report = schedule(db, vessel=vessel)

    console.print(
        Panel(
            f"Vessel                 {report['vessel']}\n"
            f"Period                 {report['period_start'] or '—'} to "
            f"{report['period_end'] or '—'}\n\n"
            f"Equipment              {fmt_money(report['equipment_total_cents'])}\n"
            f"Professional install   {fmt_money(report['installation_total_cents'])}\n"
            f"[bold]Total claimed          {fmt_money(report['total_cents'])}[/]\n\n"
            f"[dim]Excluded               {fmt_money(report['excluded_total_cents'])} "
            f"(slip, fees, transport, consumables, tools)[/]",
            title="Ophelia's Key — insurance schedule", border_style="blue",
        )
    )

    for heading, groups in (("Equipment", report["equipment"]),
                            ("Professional installation", report["installation"])):
        if not groups:
            continue
        table = Table("system", "items", "amount", title=heading)
        for group in groups:
            table.add_row(group["name"], str(len(group["items"])),
                          fmt_money(group["total_cents"]))
        console.print(table)

    if report["excluded"]:
        table = Table("excluded", "amount", "why")
        for entry in report["excluded"]:
            table.add_row(entry["name"], fmt_money(entry["total_cents"]), entry["reason"][:56])
        console.print(table)

    if pdf:
        from datetime import datetime, timezone

        from .analysis.insurance_pdf import render

        path = render(report, pdf, prepared_on=datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        console.print(f"\n[green]PDF written to[/] {path}")


@report_app.command("reward")
def report_reward(
    assumptions: bool = typer.Option(False, "--assumptions", help="Show the assumption table."),
):
    """What the spending actually returned.

    Four separate lenses, deliberately not summed. Refit spend does not return
    dollar-for-dollar at resale; the defensible return is capability and use.
    """
    db = _db()
    report = reward_report(db)
    rec, labor = report["recovery"], report["labor"]
    caps, use = report["capability"], report["use_value"]

    console.print(
        Panel(
            f"Spent on the vessel   [bold]{fmt_money(rec['vessel_spend_cents'])}[/]\n"
            f"Plausibly recoverable [green]{fmt_money(rec['recoverable_cents'])}[/] "
            f"({rec['recovery_pct']}%)\n"
            f"Startup investment    {fmt_money(rec['sunk_cents'])}\n"
            + (f"Not yet attributed    [yellow]{fmt_money(rec['unattributed_cents'])}[/] "
               f"across {rec['unattributed_count']} items — no recovery rate applies\n"
               if rec["unattributed_cents"] else "")
            + (f"Retained as tools     {fmt_money(rec['tool_residual_cents'])} "
               f"(does not convey with the boat)\n" if rec["tool_spend_cents"] else "")
            + "\n[dim]Refit spend does not return dollar-for-dollar at resale. The investment\n"
              "figure is what it took to start — exchanged for capability and use.[/]",
            title="Ophelia's Key — reward", border_style="green",
        )
    )

    table = Table("system", "spend", "rate", "recoverable", "investment")
    for line in rec["lines"]:
        table.add_row(
            line["name"], fmt_money(line["spend_cents"]), f"{line['rate']*100:.0f}%",
            f"[green]{fmt_money(line['recoverable_cents'])}[/]",
            f"[red]{fmt_money(line['sunk_cents'])}[/]",
        )
    console.print(table)

    # --- labor avoided ---
    console.print("\n[bold]Labor avoided[/]")
    if labor["logged"]:
        console.print(
            f"  {labor['hours']:g}h performed rather than purchased = "
            f"[green]{fmt_money(labor['value_cents'])}[/] at "
            f"{fmt_money(labor['rate_cents'])}/hr"
        )
        for entry in labor["by_system"][:6]:
            console.print(
                f"    [dim]{entry['name']}[/] {entry['hours']:g}h — "
                f"{fmt_money(entry['value_cents'])}"
            )
    else:
        console.print(
            "  [dim]No hours logged. This is the one component of return that is "
            "genuinely dollar-for-dollar, so it is recorded rather than estimated:[/]\n"
            "  [dim]okey log labor 12 --system electronics_nav --note \"GO9 install\"[/]"
        )

    # --- capability ---
    console.print("\n[bold]Capability delivered[/]")
    autonomy = (
        "solar covers the load indefinitely"
        if caps["days_without_ac"] is None
        else f"{caps['days_without_ac']:.1f} days"
    )
    console.print(f"  Energy autonomy, no AC     {autonomy}")
    if caps["days_with_ac_min"] is not None:
        console.print(
            f"  Energy autonomy, with AC   {caps['days_with_ac_min']:.1f}"
            f"-{caps['days_with_ac_max']:.1f} days before generator or shore power"
        )
    console.print(f"  AC runtime on the bank     {caps['ac_hours_on_bank']:.1f}h")
    console.print(
        f"  Usable storage             {caps['usable_kwh']:.2f} kWh of "
        f"{caps['usable_kwh'] / _a_usable():.2f} kWh nominal"
    )
    if caps["cents_per_kwh_storage"]:
        console.print(
            f"  Cost of storage            {fmt_money(caps['cents_per_kwh_storage'])}/kWh"
        )
    if caps["cents_per_watt_nameplate"]:
        console.print(
            f"  Cost of solar              {fmt_money(caps['cents_per_watt_nameplate'])}/W "
            f"nameplate, [yellow]{fmt_money(caps['cents_per_watt_realistic'])}/W[/] at "
            f"realistic output"
        )

    # --- use value ---
    console.print("\n[bold]Use value[/]")
    if use["logged"]:
        console.print(
            f"  {use['nights']} nights aboard at "
            f"[green]{fmt_money(use['cost_per_night_cents'])}/night[/] "
            f"against the startup investment"
        )
        console.print(
            f"  Alternative would have cost {fmt_money(use['value_realized_cents'])} "
            f"at {fmt_money(use['alternative_nightly_cents'])}/night"
        )
        if use["nights_remaining"]:
            console.print(
                f"  [yellow]{use['nights_remaining']} more nights[/] until the startup investment "
                f"is beaten by the alternative"
            )
        else:
            console.print("  [green]The startup investment is already covered by the alternative.[/]")
    else:
        console.print(
            f"  [dim]No nights logged. Break-even is {use['breakeven_nights']} nights at "
            f"{fmt_money(use['alternative_nightly_cents'])}/night against "
            f"{fmt_money(rec['sunk_cents'])} sunk:[/]\n"
            "  [dim]okey log nights 14 --from 2026-07-01 --note \"summer cruise\"[/]"
        )

    if assumptions:
        table = Table("assumption", "value", "basis", title="\nReward assumptions")
        for key, meta in report["assumptions"].items():
            table.add_row(key, f"{meta['value']:g}", meta["note"])
        console.print(table)
        rates = Table("system", "recovery", "basis", title="\nRecovery rates")
        for line in rec["lines"]:
            rates.add_row(line["name"], f"{line['rate']*100:.0f}%", line["basis"])
        console.print(rates)
    else:
        console.print("\n[dim]Run with --assumptions to see the rates behind these.[/]")


def _num(value) -> str:
    """A count or rate for display. None stays a dash — it was never a zero."""
    if value is None:
        return "—"
    return str(value) if isinstance(value, int) else f"{value:g}"


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def _month(value, horizon: int) -> str:
    return f"month {value} of {horizon}" if value is not None else f"not within {horizon} months"


def _sources(*sources: str) -> str:
    """Tag a figure with where its inputs came from; name each when they differ."""
    palette = {"assumed": "dim", "meta": "cyan", "override": "yellow", "observed": "green"}
    distinct = list(dict.fromkeys(sources))
    return " · ".join(f"[{palette.get(s, 'dim')}]{s}[/]" for s in distinct)


# The funnel inputs a logged show can measure, in the words the headline uses.
OBSERVABLE_LABELS: dict[str, str] = {
    "viewers_per_show": "viewers per set",
    "viewer_to_install": "stream install rate",
    "event_viewers_multiplier": "competition multiple",
    "dock_attendees_per_event": "dock crowd",
}


def _basis(recorded: dict, inputs: dict[str, dict]) -> str:
    """What the return rests on, read from the same sources the funnel table prints.

    Driven by the recorded show count and the source of each observable input,
    so the sentence can never disagree with the source column below it: shows
    logged without counts are still modeled, and one observed input does not
    make the whole return observed.
    """
    if recorded["shows"] == 0:
        return "No shows recorded yet — return is modeled from declared rates"
    groups: dict[str, list[str]] = {"observed": [], "modeled": [], "overridden": []}
    for key in OBSERVABLE:
        source = inputs[key]["source"]
        group = ("observed" if source == "observed"
                 else "overridden" if source == "override" else "modeled")
        groups[group].append(OBSERVABLE_LABELS.get(key, key))
    parts = [f"{name}: {', '.join(labels)}" for name, labels in groups.items() if labels]
    return f"{recorded['shows']} show(s) recorded — {'; '.join(parts)}"


def _ratio(value, why: str) -> str:
    """An ROI multiple, or the reason there is none."""
    return f"— ({why})" if value is None else f"×{value:g}"


def _money_or(cents, why: str) -> str:
    return f"— ({why})" if cents is None else fmt_money(cents)


def _count(value) -> str:
    """A count with thousands separators and no trailing zeros — 20,000 ·
    9,548.8 · 0.45 — for figures big enough that `:g` would lose digits
    (6,190,000 subscribers is not 6.19e+06). None stays a dash."""
    if value is None:
        return "—"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


# --- reach and the target ---------------------------------------------------
# Partner streams are data: each row says whether it has committed (counted)
# or is hypothetical (counted only with --with-hypothetical), where its
# audience figure came from, and how much of its audience is abroad — a
# declared share, not a measurement. A figure still at the declared stand-in
# is called PLACEHOLDER wherever it prints, with the command that replaces it.


def _partner_status(row: dict) -> str:
    if row["status"] == "committed":
        return "[green]committed[/] · counted"
    if row["active"]:
        return "[yellow]prospective[/] · counted"
    return "[dim]prospective · off[/]"


def _audience(row: dict, live_share: float) -> str:
    """A partner's audience in its own unit: live viewers a stream for a
    church, subscribers at the declared live share for an artist."""
    if row["kind"] == "artist":
        return f"{_count(row['subscribers'])} subscribers × {live_share * 100:g}% live share"
    return f"{_count(row['live_viewers_per_stream'])} live viewers per stream"


def _placeholder_line(row: dict, key: str) -> str:
    """The loud one: this partner's audience is a stand-in, not a figure."""
    return (f"[bold]ESTIMATE[/]  {row['name']}'s "
            f"{_count(row['live_viewers_per_stream'])} live viewers per stream is an estimate; "
            f"set the channel's figure: okey studio partner {key} "
            f"--live-viewers N")


def _target_phrase(target: dict, horizon: int) -> str:
    """The target in one phrase: on track, or how far short and when (if ever)
    the trajectory gets there; none set reads as none set."""
    if not target["subscribers"]:
        return ("none set — okey report studio --target N reads one for the run; "
                "studio.target_subscribers in project_meta keeps it")
    goal = f"{target['subscribers']:,} by month {target['month']}"
    reached = target["reached_month"]
    when = (f"reached in month {reached}" if reached is not None
            else f"not within {horizon} months")
    if target["on_track"]:
        return f"{goal}: [green]on track[/] — {when}"
    at_month = target["subscribers_at_target_month"]
    if at_month is None:
        return f"{goal}: month {target['month']} is past the {horizon}-month horizon — {when}"
    return (f"{goal}: [yellow]{_count(target['shortfall'])} short[/] — "
            f"{_count(at_month)} at month {target['month']}, {when}")


def _print_reach(report: dict) -> None:
    """Who the overlay is in front of each month, partner by partner.

    Every row carries its full audience — what the partner adds, or would add
    — whether it is counted, and its source; the totals sum only the counted
    rows, so this table and the funnel's Viewers row can never disagree. A
    partner still at its declared stand-in is marked PLACEHOLDER on the row
    and again below it, with the command that replaces the figure.
    """
    reach, monthly = report["reach"], report["funnel"]["monthly"]
    inputs = report["funnel"]["inputs"]
    live_share = inputs["partner_live_share"]["value"]
    table = Table(
        "partner", "status", "streams/mo", "audience", "viewers/mo", "abroad", "new paid/mo",
        "source",
        title=f"\nReach — {_count(reach['viewers_total_per_month'])} viewers a month on every "
              f"screen the overlay is on",
    )
    new_paid = 0.0
    for row in reach["partners"]:
        partner = PARTNER_BY_KEY[row["key"]]
        sources = [inputs[_partner_audience_key(partner)]["source"]]
        if row["kind"] == "artist":
            sources.append(inputs["partner_live_share"]["source"])
        source = _sources(*sources)
        if row["placeholder"]:
            source = f"[bold]ESTIMATE[/] · {source}"
        style = "" if row["active"] else "dim"
        new_paid += row["new_paid_per_month"]
        table.add_row(
            f"[bold]{row['name']}[/]", _partner_status(row), str(row["streams_per_month"]),
            _audience(row, live_share),
            _count(row["viewers_per_month"]) + ("" if row["active"] else " · off"),
            f"{_count(row['viewers_abroad'])} ({row['international_share'] * 100:.0f}%)",
            _count(row["new_paid_per_month"]), source, style=style,
        )
    table.add_row(
        "[bold]Partners counted[/]", "", "", "",
        f"[bold]{_count(reach['viewers_partner_per_month'])}[/]",
        f"[bold]{_count(reach['viewers_abroad_per_month'])}[/]",
        f"[bold]{_count(round(new_paid, 2))}[/]", "",
    )
    console.print(table)
    console.print(
        f"  All screens  {_count(reach['viewers_total_per_month'])} viewers a month = "
        f"{_count(monthly['viewers_stream'] + monthly['viewers_event'])} the boat's own "
        f"({_count(monthly['viewers_stream'])} stream + {_count(monthly['viewers_event'])} event) "
        f"+ {_count(reach['viewers_partner_per_month'])} from partner streams; "
        f"{_count(reach['viewers_abroad_per_month'])} of them abroad"
    )
    languages = reach["languages"]
    console.print(f"  Languages    {languages['base']} base · {languages['total']} in all — "
                  f"{reach['note']}")
    for row in reach["partners"]:
        if not row["active"]:
            console.print(
                f"  [dim]Off          {row['name']} ({row['status']}) would add "
                f"{_count(row['viewers_per_month'])} viewers a month, "
                f"{_count(row['viewers_abroad'])} abroad — okey report studio "
                f"--with-hypothetical counts it[/]")
    for row in reach["partners"]:
        if row["placeholder"]:
            console.print(f"  {_placeholder_line(row, row['key'])}")


def _print_target(report: dict) -> None:
    """The owner's target, read against the trajectory and answered in its
    own terms: where the book stands in the target month, when (if ever) the
    target is reached, and what it takes at the current rates and cadence."""
    target, horizon = report["target"], report["breakeven"]["horizon_months"]
    v = {key: entry["value"] for key, entry in report["funnel"]["inputs"].items()}
    reach = report["reach"]
    if not target["subscribers"]:
        console.print(Panel(
            f"Target            {_target_phrase(target, horizon)}\n"
            f"At current rates  {_count(target['subscribers_at_target_month'])} subscribers at "
            f"month {target['month']}\n\n[dim]{target['note']}[/]",
            title="Target — none set", border_style="magenta"))
        return
    reached = target["reached_month"]
    when = (f"reached in month {reached}" if reached is not None
            else f"not within {horizon} months")
    at_month = target["subscribers_at_target_month"]
    if at_month is None:
        standing = (f"month {target['month']} is past the {horizon}-month horizon "
                    f"(horizon_months) — {when}")
    elif target["on_track"]:
        standing = (f"{_count(at_month)} subscribers at month {target['month']} — "
                    f"[green]on track[/], {when}")
    else:
        standing = (f"{_count(at_month)} subscribers at month {target['month']} — "
                    f"[yellow]{_count(target['shortfall'])} short[/]; {when}")
    shows = v["shows_per_month"] + v["events_per_month"]
    required_viewers = target["required_viewers_per_month"]
    now = f"against {_count(reach['viewers_total_per_month'])} a month on every screen now"
    if required_viewers is None:
        viewers = "no number of viewers — the current rates convert nobody"
        per_show = "—"
    else:
        viewers = f"{_count(required_viewers)} viewers a month at the current rates, {now}"
        per_show = (f"{_count(round(required_viewers / shows, 2))} viewers at {shows:g} shows a "
                    f"month ({v['shows_per_month']:g} sets + {v['events_per_month']:g} nights)"
                    if shows > 0 else "no shows in the month to spread them over")
    console.print(Panel(
        f"Target            {target['subscribers']:,} paying subscribers by month "
        f"{target['month']}\n"
        f"At current rates  {standing}\n"
        f"What it takes     {_count(target['required_new_paid_per_month'])} new paid a month from "
        f"a standing start ({v['traveler_monthly_churn'] * 100:g}% traveler churn) = {viewers}\n"
        f"Per show          {per_show}\n"
        f"\n[dim]{target['note']}[/]",
        title="Target — read against the trajectory", border_style="magenta"))


@report_app.command("studio")
def report_studio(
    viewers: float | None = typer.Option(None, "--viewers", help="Unique viewers per show."),
    shows: float | None = typer.Option(None, "--shows", help="Shows per month."),
    install_rate: float | None = typer.Option(
        None, "--install-rate", help="Share of viewers who install, 0–1."),
    paid_rate: float | None = typer.Option(
        None, "--paid-rate", help="Share of installs that start a paid plan, 0–1."),
    churn: float | None = typer.Option(None, "--churn", help="Monthly subscriber churn, 0–1."),
    events: float | None = typer.Option(
        None, "--events", help="Competition nights per month; 0 is a valid month."),
    attendees: float | None = typer.Option(
        None, "--attendees", help="People on the rear dock and swim platform per night."),
    traveler_share: float | None = typer.Option(
        None, "--traveler-share",
        help="Share of the audience whose need is Conversation Mode — travelers — 0–1; "
             "the rest are performers."),
    traveler_paid: float | None = typer.Option(
        None, "--traveler-paid",
        help="Share of traveler installs that start Base + Conversation Mode, 0–1."),
    with_hypothetical: bool = typer.Option(
        False, "--with-hypothetical",
        help="Count the partners that have not committed yet (Partner artist); off by default."),
    partner_live_share: float | None = typer.Option(
        None, "--partner-live-share",
        help="Share of an artist partner's subscribers who watch a given live stream, 0–1."),
    target: int | None = typer.Option(
        None, "--target", help="Paying subscribers to reach; 0 is no target."),
    target_month: int | None = typer.Option(
        None, "--target-month", help="Month by which to reach it, 1–120."),
    assumptions: bool = typer.Option(False, "--assumptions", help="Show the assumption table."),
    html: str = typer.Option("", "--html", help="Write the page as standalone HTML to this path."),
):
    """The boat as a floating production studio.

    Four questions, answered separately: can the bank power a show, can
    Starlink carry it, what does the kit cost beyond what is aboard, and what
    does the show plausibly return in Lyric Show subscriptions. The return is
    modeled from declared assumptions until shows are logged, and it comes in
    three lenses that are never summed. The audience is two segments by need
    — travelers, who subscribe for Conversation Mode, and performers, the
    original funnel — each with its own rates, plan and churn, kept apart to
    the steady state and summed only there. Competition nights add a second
    audience — the crowd on the rear dock, travelers who had the demo in
    person — with its own install rate, and a block on where Lyric Show is on
    screen during one. Partner streams — other stages the overlay is on — add
    their viewers to the month; the Reach panel lists them as data, committed
    partners counted and hypothetical ones only with --with-hypothetical, and
    marks an audience figure still at its placeholder. The Target panel reads
    the owner's target against the trajectory. The ROI panel reads the kit's
    return off the show-driven trajectory alone: a declared baseline of
    today's subscribers (okey studio baseline) starts the book, never the kit.
    """
    db = _db()
    flags = {
        "viewers_per_show": viewers, "shows_per_month": shows,
        "viewer_to_install": install_rate, "install_to_paid": paid_rate,
        "monthly_churn": churn,
        "events_per_month": events, "dock_attendees_per_event": attendees,
        "traveler_share": traveler_share, "traveler_install_to_paid": traveler_paid,
        # The switch is an override only when thrown: the default stays the
        # declared 0 and reads as assumed, like every other untouched input.
        "partners_include_hypothetical": 1 if with_hypothetical else None,
        "partner_live_share": partner_live_share,
        "target_subscribers": target, "target_month": target_month,
    }
    overrides = {key: value for key, value in flags.items() if value is not None}
    try:
        report = studio_report(db, overrides or None)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1)

    # A page that cannot be written is refused before the report is printed:
    # one red line up front, not the whole report and then a traceback.
    if html:
        from .web.app import render_studio_standalone

        try:
            Path(html).write_text(render_studio_standalone(db, overrides), encoding="utf-8")
        except OSError as exc:
            console.print(f"[red]could not write {escape(html)}: {escape(str(exc))}[/]")
            raise typer.Exit(1)

    funnel, steady = report["funnel"], report["funnel"]["steady_state"]
    breakeven, recorded, kit = report["breakeven"], report["recorded"], report["kit"]
    horizon = breakeven["horizon_months"]

    # --- headline ---
    basis = _basis(recorded, funnel["inputs"])
    if breakeven["moorage_monthly_cents"] is None:
        slip = "no moorage spend recorded, so nothing to cover"
    else:
        slip = (f"{_month(breakeven['slip_month'], horizon)}  —  "
                f"{fmt_money(breakeven['moorage_monthly_cents'])}/mo slip")
    # Two segments by need, named on the headline: the book is their sum and
    # nothing else, so the reader sees which need carries it. The target is
    # one phrase; a counted partner still at its placeholder audience is named
    # here, loudly, because the headline rests on it.
    reach = report["reach"]
    placeholders = [row for row in reach["partners"] if row["active"] and row["placeholder"]]
    placeholder_lines = "".join(
        f"\n[bold]ESTIMATE[/]          {row['name']}'s "
        f"{_count(row['live_viewers_per_stream'])} live viewers a stream is an estimate: "
        f"{_count(row['viewers_per_month'])} of {_count(reach['viewers_total_per_month'])} "
        f"viewers depend on it — okey studio partner {row['key']} --live-viewers N"
        for row in placeholders)
    console.print(
        Panel(
            f"Steady state      [bold]{steady['subscribers']:g} subscribers[/] "
            f"({steady['subscribers_travelers']:g} travelers · "
            f"{steady['subscribers_performers']:g} performers)  ·  "
            f"[green]{fmt_money(steady['mrr_net_cents'])}[/]/mo net  ·  "
            f"[green]{fmt_money(steady['arr_net_cents'])}[/]/yr net\n"
            f"Target            {_target_phrase(report['target'], horizon)}\n"
            f"Kit payback       {_month(breakeven['kit_month'], horizon)}  —  "
            f"{fmt_money(kit['planned_cents'])} planned, "
            f"{fmt_money(kit['recorded_cents'])} bought\n"
            f"Slip covered      {slip}\n"
            f"Project payback   {_month(breakeven['project_month'], horizon)}  —  "
            f"{fmt_money(breakeven['project_spend_cents'])} project spend"
            f"{placeholder_lines}\n"
            f"\n[dim]{basis}. Three return figures below, reported separately.[/]",
            title="Ophelia's Key — floating studio", border_style="cyan",
        )
    )

    # --- roi ---
    # Derived from the subscription lens, not a fourth one, and read off the
    # show-driven columns: the baseline's own revenue never flatters the kit.
    roi, baseline = report["roi"], report["baseline"]
    m12, hz = roi["month_12"], roi["horizon"]
    cpi_cents = report["lenses"]["acquisition_displaced"]["cpi_cents"]
    if m12 is None:
        at_12 = "— (horizon shorter than 12 months)"
    else:
        at_12 = (f"{_ratio(m12['roi_multiple_on_kit'], 'no kit priced')} at month 12 "
                 f"({fmt_money(m12['show_driven_cumulative_net_cents'])} show-driven net)")
    at_horizon = (f"{_ratio(hz['roi_multiple_on_kit'], 'no kit priced')} at month {horizon} "
                  f"({fmt_money(hz['show_driven_cumulative_net_cents'])})")
    share = hz["share_of_project_spend"]
    of_project = ("— of project spend (nothing spent yet)" if share is None
                  else f"{share * 100:.0f}% of project spend")
    # No moorage recorded is nothing to cover, not a slip uncovered for 36 months.
    slip_payback = ("— (no moorage recorded)" if breakeven["moorage_monthly_cents"] is None
                    else _month(roi["payback"]["slip_month"], horizon))
    if baseline["subscribers"] > 0:
        baseline_line = (f"{baseline['subscribers']:g} subscribers ({baseline['source']}) — the "
                         f"trajectory starts here; payback and ROI count show-driven revenue only")
    else:
        baseline_line = "not entered — okey studio baseline --subscribers N"
    console.print(
        Panel(
            f"Kit ROI           {at_12}  ·  {at_horizon}  ·  {of_project}\n"
            f"Per show          {_money_or(roi['per_show_net_cents'], 'no shows in the month')}"
            f"/mo net per show at steady state  ·  "
            f"{_money_or(roi['per_viewer_net_cents'], 'no viewers')} per viewer\n"
            f"Cost per install  "
            f"{_money_or(roi['cost_per_install_cents'], 'no installs')} of kit per year-one "
            f"install  vs  {fmt_money(cpi_cents)} paid CPI\n"
            f"Payback           kit {_month(roi['payback']['kit_month'], horizon)}  ·  "
            f"slip {slip_payback}  ·  "
            f"project {_month(roi['payback']['project_month'], horizon)}\n"
            f"Baseline          {baseline_line}\n"
            f"\n[dim]{roi['note']}[/]",
            title="ROI — the kit, on show-driven revenue", border_style="green",
        )
    )

    # --- power ---
    power = report["power"]
    table = Table("load", "watts", title="\nPower")
    for load in power["loads"]:
        table.add_row(load["name"], f"{load['watts']:g}")
    table.add_row("[bold]Studio[/]", f"[bold]{power['studio_w']:g}[/]")
    table.add_row("[dim]at the battery[/]", f"[dim]{power['dc_w']:g}[/]")
    console.print(table)
    console.print(
        f"  A {power['show_hours']:g}h set              {power['session_kwh']:.2f} kWh of "
        f"{power['usable_kwh']:.2f} usable  ·  {_pct(power['session_share_of_solar_day'])} "
        f"of a {power['harvest_high_kwh']:.1f} kWh solar day"
    )
    console.print(
        f"  On the bank alone     {power['hours_on_bank']:.1f}h = {power['shows_on_bank']:.1f} "
        f"sets  ·  {power['hours_on_bank_with_ac']:.1f}h with the AC running"
    )
    console.print(
        f"  Inverter              {_pct(power['inverter_utilisation'])} of "
        f"{power['inverter_watts_continuous']:g} W  ·  "
        f"{_pct(power['inverter_utilisation_with_ac'])} with the AC  ·  "
        f"generator leg {_pct(power['generator_utilisation_with_ac'])}"
    )

    # --- uplink ---
    uplink = report["uplink"]
    table = Table(
        "profile", "bitrate", "required", "margin low", "margin high", "verdict",
        title=f"\nUplink — Starlink {uplink['upload_low_mbps']:g}–{uplink['upload_high_mbps']:g} "
              f"Mbps up, ×{uplink['headroom']:g} headroom, "
              f"captions {uplink['caption_latency_ms']} ms",
    )
    palette = {"clear": "green", "conditional": "yellow", "blocked": "red"}
    for profile in uplink["profiles"]:
        table.add_row(
            profile["name"], f"{profile['bitrate_mbps']:g} Mbps",
            f"{profile['required_mbps']:g} Mbps", f"{profile['margin_low_mbps']:+g}",
            f"{profile['margin_high_mbps']:+g}",
            f"[{palette[profile['verdict']]}]{profile['verdict']}[/]",
        )
    console.print(table)

    # --- kit ---
    # The priced list, what the ledger says is already bought, and the capital
    # the studio inherits from the refit — priced only once the ledger
    # attributes it, never guessed.
    inherited = report["inherited"]
    table = Table(
        "item", "price", "basis",
        title=f"\nKit — {fmt_money(kit['planned_cents'])} planned, "
              f"{fmt_money(kit['recorded_cents'])} bought",
    )
    for item in kit["planned"]:
        table.add_row(item["name"], fmt_money(item["cents"]), f"[dim]{item['note']}[/]")
    table.add_row("[bold]Planned[/]", f"[bold]{fmt_money(kit['planned_cents'])}[/]",
                  "[dim]the purchase list; payback and ROI are measured against it[/]")
    if kit["recorded"]:
        for line in kit["recorded"]:
            table.add_row(f"  {escape(line['description'][:48])}", fmt_money(line["cents"]),
                          "[green]bought[/] — a ledger line that matches the kit")
        table.add_row("[bold]Bought[/]", f"[bold]{fmt_money(kit['recorded_cents'])}[/]", "")
    else:
        table.add_row("  [dim]nothing in the ledger matches the kit yet[/]", fmt_money(0), "")
    if inherited["attributed_cents"]:
        for system in inherited["attributed"]:
            if system["key"] in STUDIO_CAPITAL_SYSTEMS and system["cents"]:
                table.add_row(f"  {system['name']}", fmt_money(system["cents"]),
                              "[cyan]inherited[/] — bought for the boat, attributed in the "
                              "ledger; not part of the kit")
        table.add_row("[bold]Inherited A/V & connectivity[/]",
                      f"[bold]{fmt_money(inherited['attributed_cents'])}[/]",
                      "[dim]already aboard; never summed into the kit[/]")
    console.print(table)
    if inherited["note"]:
        console.print(f"  [dim]{inherited['note']}[/]")

    # --- funnel ---
    v = {key: entry["value"] for key, entry in funnel["inputs"].items()}
    src = {key: entry["source"] for key, entry in funnel["inputs"].items()}
    monthly, arpu = funnel["monthly"], funnel["arpu_by_segment"]
    conv = report["lyricshow"]["conversation_mode"]
    # Two audiences (stream, dock) and two segments by need (travelers,
    # performers), kept apart until the end: each total row carries the split
    # and the indented rows say where each half came from. The dock crowd are
    # travelers — they had the two-language demo in person — so the dock
    # installs sit inside the travelers' row, paid at the traveler rate.
    travelers_from_stream = monthly["installs_travelers"] - monthly["installs_event"]
    # Partner viewers are a third source, counted before the split: the
    # counted partners' audience keys and the hypothetical switch are the
    # sources of that row, so a what-if on the switch reads as an override.
    counted = [row for row in reach["partners"] if row["active"]]
    partner_sources = [src[_partner_audience_key(PARTNER_BY_KEY[row["key"]])]
                       for row in counted] + [src["partners_include_hypothetical"]]
    table = Table("stage", "per month", "how", "source", title="\nFunnel")
    table.add_row(
        "Viewers", f"{monthly['viewers']:g}",
        f"{monthly['viewers_stream']:g} stream + {monthly['viewers_event']:g} event + "
        f"{monthly['viewers_partner']:g} partner",
        _sources(src["viewers_per_show"], src["shows_per_month"],
                 src["event_viewers_multiplier"], src["events_per_month"], *partner_sources),
    )
    table.add_row(
        "  stream", f"{monthly['viewers_stream']:g}",
        f"{v['viewers_per_show']:g} per show × {v['shows_per_month']:g} shows",
        _sources(src["viewers_per_show"], src["shows_per_month"]),
    )
    table.add_row(
        "  event", f"{monthly['viewers_event']:g}",
        f"{v['viewers_per_show']:g} × {v['event_viewers_multiplier']:g} per night × "
        f"{v['events_per_month']:g} nights",
        _sources(src["viewers_per_show"], src["event_viewers_multiplier"],
                 src["events_per_month"]),
    )
    by_partner = " + ".join(f"{_count(row['viewers_per_month'])} {row['name']}"
                            for row in counted)
    table.add_row(
        "  partner", f"{monthly['viewers_partner']:g}",
        (f"{len(counted)} counted partner stream(s): {by_partner} — see Reach" if counted
         else "no partner counted — see Reach"),
        _sources(*partner_sources),
    )
    table.add_row(
        "  travelers", f"{monthly['travelers_viewers']:g}",
        f"{v['traveler_share'] * 100:.0f}% of viewers — the need is Conversation Mode",
        _sources(src["traveler_share"]),
    )
    table.add_row(
        "  performers", f"{monthly['performers_viewers']:g}",
        f"the other {(1 - v['traveler_share']) * 100:.0f}% — streamers, worship teams, "
        f"the original funnel",
        _sources(src["traveler_share"]),
    )
    table.add_row(
        "Attendees", f"{monthly['attendees']:g}",
        f"{v['dock_attendees_per_event']:g} on the dock × {v['events_per_month']:g} nights "
        f"— travelers, with the demo in front of them",
        _sources(src["dock_attendees_per_event"], src["events_per_month"]),
    )
    table.add_row(
        "Installs", f"{monthly['installs']:g}",
        f"{monthly['installs_stream']:g} stream + {monthly['installs_event']:g} dock",
        _sources(src["traveler_viewer_to_install"], src["viewer_to_install"],
                 src["attendee_to_install"]),
    )
    table.add_row(
        "  travelers", f"{monthly['installs_travelers']:g}",
        f"{travelers_from_stream:g} stream ({v['traveler_viewer_to_install'] * 100:.1f}% of "
        f"{monthly['travelers_viewers']:g} traveler viewers) + {monthly['installs_event']:g} "
        f"dock ({v['attendee_to_install'] * 100:.1f}% of {monthly['attendees']:g} attendees)",
        _sources(src["traveler_viewer_to_install"], src["attendee_to_install"]),
    )
    table.add_row(
        "  performers", f"{monthly['installs_performers']:g}",
        f"{v['viewer_to_install'] * 100:.1f}% of {monthly['performers_viewers']:g} "
        f"performer viewers",
        _sources(src["viewer_to_install"]),
    )
    table.add_row(
        "New paid", f"{monthly['new_paid']:g}",
        f"{monthly['new_paid_travelers']:g} travelers + "
        f"{monthly['new_paid_performers']:g} performers",
        _sources(src["traveler_install_to_paid"], src["install_to_paid"]),
    )
    table.add_row(
        "  travelers", f"{monthly['new_paid_travelers']:g}",
        f"{v['traveler_install_to_paid'] * 100:.1f}% of {monthly['installs_travelers']:g} "
        f"traveler installs → Base + Conversation Mode",
        _sources(src["traveler_install_to_paid"]),
    )
    table.add_row(
        "  performers", f"{monthly['new_paid_performers']:g}",
        f"{v['install_to_paid'] * 100:.1f}% of {monthly['installs_performers']:g} "
        f"performer installs",
        _sources(src["install_to_paid"]),
    )
    # Each plan's month is priced as subscribers pay it: the segment's annual
    # share at the annual price ÷ 12, the rest at list. The plan's share of all
    # new paid is derived from the two segments' volumes — the true mix — and
    # a performer plan also shows its declared share within the performer
    # segment. The store's annual price is shown only where the store has one
    # for that plan: the featured bundle has its own, the Pro Broadcast add-on
    # is monthly only.
    store_plans = report["lyricshow"]["plans"]
    for plan in funnel["by_plan"]:
        store = store_plans.get(plan["key"])
        if plan["segment"] == "traveler":
            within, annual_src = "all travelers", src["traveler_annual_share"]
            billing = f"{fmt_money(conv['bundle_annual_cents'])}/yr, the featured bundle"
        else:
            within = f"{plan['mix_share'] * 100:.0f}% of performers"
            annual_src = src["annual_share"]
            if store is not None and store.get("annual_cents"):
                billing = f"{fmt_money(store['annual_cents'])}/yr"
            elif plan["annual_cents"] is None:
                billing = "monthly only"
            else:
                billing = "annual on the tier, the add-on monthly"
        table.add_row(
            f"  {plan['name']}", f"{plan['new_subscribers']:g}",
            f"{plan['share'] * 100:.1f}% of new paid ({within}) at "
            f"{fmt_money(plan['price_cents'])}/mo blended "
            f"(list {fmt_money(plan['list_monthly_cents'])}/mo · {billing})",
            _sources(annual_src),
        )
    table.add_row(
        "Steady subscribers", f"{steady['subscribers']:g}",
        f"{steady['subscribers_travelers']:g} travelers + "
        f"{steady['subscribers_performers']:g} performers — each segment's new paid ÷ "
        f"its own churn",
        _sources(src["traveler_monthly_churn"], src["monthly_churn"]),
    )
    table.add_row(
        "  travelers", f"{steady['subscribers_travelers']:g}",
        f"{monthly['new_paid_travelers']:g} new paid ÷ "
        f"{v['traveler_monthly_churn'] * 100:.1f}% monthly churn — trips end",
        _sources(src["traveler_monthly_churn"]),
    )
    table.add_row(
        "  performers", f"{steady['subscribers_performers']:g}",
        f"{monthly['new_paid_performers']:g} new paid ÷ "
        f"{v['monthly_churn'] * 100:.1f}% monthly churn",
        _sources(src["monthly_churn"]),
    )
    table.add_row(
        "MRR net", fmt_money(steady["mrr_net_cents"]),
        f"{fmt_money(funnel['arpu_gross_cents'])} blended ARPU over all new paid, less "
        f"{v['store_commission'] * 100:.0f}% store commission",
        _sources(src["traveler_annual_share"], src["annual_share"], src["store_commission"]),
    )
    table.add_row(
        "  travelers", fmt_money(steady["mrr_net_travelers_cents"]),
        f"{fmt_money(arpu['travelers'])} ARPU on Base + Conversation Mode, "
        f"{v['traveler_annual_share'] * 100:.0f}% on annual billing",
        _sources(src["traveler_annual_share"]),
    )
    table.add_row(
        "  performers", fmt_money(steady["mrr_net_performers_cents"]),
        f"{fmt_money(arpu['performers'])} ARPU across the performer plans, "
        f"{v['annual_share'] * 100:.0f}% on annual billing",
        _sources(src["annual_share"]),
    )
    console.print(table)
    # Who buys, in two sentences. The traveler rates are the owner's premise —
    # every traveler met so far says they would pay for Conversation Mode —
    # declared as assumptions like every other rate, so the product printed
    # here follows the table above and any override. Broken into lines by
    # hand so each phrase prints whole.
    travelers_subscribe = v["traveler_viewer_to_install"] * v["traveler_install_to_paid"]
    pad = " " * 12
    console.print(
        f"  [bold]Who buys[/]  Travelers first — anyone across a language line who sees "
        f"two-language captions on screen and wants Conversation Mode on a phone of their own:\n"
        f"{pad}{v['traveler_share'] * 100:.0f}% of the audience "
        f"(traveler_share: {_sources(src['traveler_share'])}), of whom "
        f"{v['traveler_viewer_to_install'] * 100:.1f}% install and "
        f"{v['traveler_install_to_paid'] * 100:.1f}% pay — "
        f"{travelers_subscribe * 100:.1f}% of traveler viewers subscribing,\n"
        f"{pad}the owner's premise declared as the traveler_* assumptions and as arguable as "
        f"every other rate.\n"
        f"{pad}Performers second — streamers, worship teams, the original funnel: the other "
        f"{(1 - v['traveler_share']) * 100:.0f}%, at "
        f"{v['viewer_to_install'] * 100:.1f}% install, {v['install_to_paid'] * 100:.1f}% pay "
        f"and {v['monthly_churn'] * 100:.1f}% churn on the performer plans."
    )

    # --- reach and the target ---
    # Who the overlay is in front of beyond the boat, and the owner's target
    # read against the trajectory — both from the same effective inputs the
    # funnel ran on, so nothing here is a second model.
    _print_reach(report)
    _print_target(report)

    # --- lenses ---
    lenses = report["lenses"]
    sub, acq, cat = lenses["subscription"], lenses["acquisition_displaced"], lenses["catalog"]
    console.print("\n[bold]Return — three figures, reported separately[/]")
    console.print(
        f"  Subscription           [green]{fmt_money(sub['steady_mrr_net_cents'])}/mo[/] net at "
        f"steady state, {fmt_money(sub['steady_arr_net_cents'])}/yr"
    )
    # The same steady state by segment — two needs, two churns, two prices —
    # so the reader can see which share carries the month; the line above is
    # their sum and nothing else.
    seg = sub["by_segment"]
    console.print(
        f"                         travelers {seg['travelers']['steady_subscribers']:g} "
        f"subscribers, {fmt_money(seg['travelers']['steady_mrr_net_cents'])}/mo · "
        f"performers {seg['performers']['steady_subscribers']:g} subscribers, "
        f"{fmt_money(seg['performers']['steady_mrr_net_cents'])}/mo"
    )
    if sub["month_12"]:
        m12 = sub["month_12"]
        show_driven = ""
        if m12["baseline_subscribers"]:
            show_driven = (f" — of which {m12['show_driven_subscribers']:g} subscribers and "
                           f"{fmt_money(m12['show_driven_cumulative_net_cents'])} are show-driven")
        console.print(
            f"                         month 12: {m12['subscribers']:g} subscribers, "
            f"{fmt_money(m12['mrr_net_cents'])}/mo, "
            f"{fmt_money(m12['cumulative_net_cents'])} cumulative{show_driven}"
        )
    console.print(
        f"  Acquisition displaced  {acq['installs_per_month']:g} installs/mo × "
        f"{fmt_money(acq['cpi_cents'])} CPI = [green]{fmt_money(acq['monthly_cents'])}/mo[/], "
        f"{fmt_money(acq['annual_cents'])}/yr"
    )
    console.print(
        f"  Catalog                {cat['songs_per_month']:g} songs/mo, "
        f"{cat['songs_per_year']:g}/yr — [yellow]not priced[/]. {cat['note']}."
    )
    for line in lenses["excluded"]:
        console.print(f"  [dim]Excluded — {line}[/]")

    # --- competition night ---
    # The setting the app is sold in, not a lens: the three stages, one night's
    # flow with the steps where Lyric Show is on screen marked, and Paradise
    # Busker's own facts. Nothing printed here is summed into the return.
    comp = report["competition"]
    console.print("\n[bold]Competition night — Paradise Busker × Key West Treasure Hunt[/]")
    for stage in comp["stages"]:
        console.print(
            f"  [bold]{stage['name']:<14}[/] {stage['where']} · holds {stage['holds']} · "
            f"{stage['camera']} · [dim]{stage['note']}[/]"
        )
    console.print("  [cyan]◆[/] marks a step with Lyric Show on screen — that is what sells it")
    for number, step in enumerate(comp["flow"], start=1):
        mark = "[cyan]◆[/]" if step["product"] else "[dim]·[/]"
        console.print(f"  {number}. {mark} [bold]{step['step']}[/] — {step['role']}: "
                      f"{step['detail']}")
    facts = comp["facts"]
    treasures, codex, membership = facts["treasures"], facts["codex"], facts["membership_cents"]
    table = Table("fact", "value", title="\nParadise Busker — the facts, from the sources")
    table.add_row("Tipping", f"{_pct(facts['tipping_artist_share'])} to the artist\n"
                             f"[dim]{facts['tipping_table_note']}[/]")
    table.add_row("Voting", facts["voting"])
    # The round mechanics come from the deck rather than the white paper; the
    # module carries them when it has them, and the table shows them then.
    if facts.get("competition"):
        round_ = facts["competition"]
        table.add_row("The round", f"{' · '.join(round_['votes'])}; "
                                   f"{fmt_money(round_['vote_price_cents'])} a vote, "
                                   f"{_pct(round_['grand_prize_share'])} of the pool to the "
                                   f"Grand Prize")
    table.add_row("Treasures", f"proof of attendance: {' + '.join(treasures['proof'])}; "
                               f"tiers {' / '.join(treasures['tiers'])}")
    if facts.get("ar_booths"):
        table.add_row("AR booths", facts["ar_booths"])
    table.add_row("Codex Engine", f"lyrics scored {codex['score_split']}; "
                                  f"{_pct(codex['threshold'])} or better qualifies")
    table.add_row("Membership",
                  f"{fmt_money(membership['low'])}–{fmt_money(membership['high'])} a month")
    table.add_row("Tithe", f"{_pct(facts['tithe_of_net_profit'])} of net profit to charity")
    table.add_row("The coin", facts["coin"])
    table.add_row("Series", facts["series"])
    console.print(table)
    console.print(f"  [dim]Sources: {facts['source']}[/]")

    # --- recorded shows ---
    console.print("\n[bold]Recorded shows[/]")
    if recorded["shows"]:
        table = Table("date", "kind", "platform", "title", "min", "peak", "unique", "dock",
                      "installs")
        # Titles, platforms and dates are the user's strings: escaped, never
        # parsed as markup.
        for row in recorded["rows"]:
            table.add_row(
                escape(row["performed_at"] or "—"), escape(row["kind"]),
                escape(row["platform"] or "—"), escape((row["title"] or "—")[:32]),
                _num(row["duration_minutes"]), _num(row["peak_viewers"]),
                _num(row["unique_viewers"]), _num(row["attendees"]),
                _num(row["installs_attributed"]),
            )
        console.print(table)
        # Each observed figure names the rows it is drawn from: an average over
        # the sets that were counted is not the total divided by every show.
        sets = recorded["shows"] - recorded["competitions"]
        nights = recorded["competitions"]
        rate = recorded["observed_viewer_to_install"]
        multiple = recorded["observed_event_multiplier"]
        console.print(
            f"  {recorded['shows']} show(s), {recorded['competitions']} competition night(s) · "
            f"{_num(recorded['unique_viewers'])} unique viewers · "
            f"{_num(recorded['installs'])} installs logged"
        )
        console.print(
            f"  observed  {_num(recorded['observed_viewers_per_show'])} viewers per counted show "
            f"({recorded['viewers_counted_shows']} of {sets} sets counted) · "
            f"{'—' if rate is None else f'{rate * 100:.1f}%'} install rate "
            f"(from {recorded['install_rate_shows']} set(s) with both counts) · "
            f"{_num(recorded['observed_attendees_per_event'])} on the dock per counted night "
            f"({recorded['attendees_counted_nights']} of {nights} counted)"
        )
        if nights:
            console.print(
                f"            {_num(recorded['observed_event_viewers_per_night'])} viewers per "
                f"counted night ({recorded['event_viewers_counted_nights']} of {nights} counted)"
                + (f" → ×{multiple:g} a counted set's audience" if multiple is not None
                   else " → no counted set to compare against; the declared multiple stands")
            )
        for item in recorded["ignored_observations"]:
            console.print(
                f"  [yellow]ignored[/] {item['key']} = {item['value']:g}: {item['reason']}")
    else:
        console.print(
            "  [dim]No shows recorded yet — figures are modeled. Record one with:[/]\n"
            "  [dim]okey log show --date 2026-08-22 --platform youtube --title \"Set one\" "
            "--minutes 110 --peak 80 --unique 140 --installs 6[/]\n"
            "  [dim]okey log show --kind competition --date 2026-08-29 --platform youtube "
            "--title \"Busker night\" --unique 300 --attendees 55 --installs 9[/]"
        )

    if assumptions:
        table = Table("assumption", "value", "basis", "source", title="\nStudio assumptions")
        for key, meta in report["assumptions"].items():
            table.add_row(key, f"{meta['value']:g}", meta["note"], _sources(meta["source"]))
        console.print(table)
    else:
        console.print("\n[dim]Run with --assumptions to see the numbers behind these.[/]")

    if html:
        console.print(f"\n[green]page written to[/] {escape(html)}")


@report_app.command("spec")
def report_spec(
    assumptions: bool = typer.Option(False, "--assumptions", help="Show the assumption table."),
):
    """Engineering risk from the installed specification.

    Compares what the vessel has against what it can deliver. These are
    estimates from stated specs, not measurements — every assumption is listed
    with `--assumptions` and can be overridden via project_meta.
    """
    report = spec_report(_db())
    counts = report["counts"]
    console.print(
        Panel(
            f"Findings   [red]{counts['high']} high[/] · [yellow]{counts['medium']} medium[/] "
            f"· {counts['low']} low\n"
            f"[dim]Estimated from the installed specification, not measured.[/]",
            title="Ophelia's Key — specification risk",
            border_style="blue",
        )
    )

    palette = {"high": "red", "medium": "yellow", "low": "dim"}
    for finding in report["findings"]:
        colour = palette[finding["severity"]]
        console.print(
            f"\n[{colour}]● {finding['severity'].upper()}[/]  [bold]{finding['title']}[/]"
        )
        if finding["numbers"]:
            console.print(
                "   " + "   ".join(f"[dim]{k}[/] {v}" for k, v in finding["numbers"].items())
            )
        console.print(f"   {finding['detail']}", highlight=False)
        if assumptions and finding["assumptions"]:
            for line in finding["assumptions"]:
                console.print(f"   [dim]assumes {line}[/]")

    if assumptions:
        table = Table("assumption", "value", "basis", title="\nAll assumptions")
        for key, meta in report["assumptions"].items():
            table.add_row(key, f"{meta['value']:g}", meta["note"])
        console.print(table)
    else:
        console.print("\n[dim]Run with --assumptions to see the numbers behind these.[/]")


@report_app.command("unclassified")
def report_unclassified(limit: int = 40):
    """Line items awaiting attribution."""
    db = _db()
    rows = db.query(
        "SELECT description, total_cents, vendor FROM v_unclassified "
        "ORDER BY total_cents DESC LIMIT ?", (limit,))
    if not rows:
        console.print("[green]Everything is classified.[/]")
        return
    table = Table("description", "amount", "vendor")
    for row in rows:
        table.add_row(row["description"][:70], fmt_money(row["total_cents"]), row["vendor"] or "—")
    console.print(table)


@app.command()
def demo(clear: bool = typer.Option(False, help="Remove demo data instead of adding it.")):
    """Load a realistic sample refit so the pipeline can be exercised without credentials."""
    db = _db()
    if clear:
        removed = clear_demo(db)
        console.print(f"[yellow]removed {removed} demo orders[/]")
        return
    stats = seed_demo(db)
    console.print(f"[green]seeded {stats['orders_created']} demo orders[/]")
    usage = stats.get("usage", {})
    if usage.get("labor_entries"):
        console.print(
            f"[green]seeded {usage['labor_entries']} labor entries and "
            f"{usage['nights']} nights aboard[/]"
        )
    console.print("[dim]Now run: okey classify && okey report cost[/]")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000):
    """Run the local dashboard."""
    import uvicorn

    _db()
    console.print(f"[cyan]dashboard on http://{host}:{port}[/]")
    uvicorn.run("opheliaskey.web.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    app()
