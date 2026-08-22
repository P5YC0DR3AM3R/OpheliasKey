"""Command-line interface for Ophelia's Key."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .analysis.cost import cost_report
from .analysis.demo import clear_demo, seed_demo
from .analysis.reconcile import reconcile as run_reconcile
from .analysis.risk import risk_report
from .analysis.spec import spec_report
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


log_app = typer.Typer(help="Record labor and usage for reward analysis.", no_args_is_help=True)
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
            f"\n[yellow]Estimated            unknown — none of these are quoted yet[/]"
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
            f"Permanently sunk      [red]{fmt_money(rec['sunk_cents'])}[/]\n"
            + (f"Not yet attributed    [yellow]{fmt_money(rec['unattributed_cents'])}[/] "
               f"across {rec['unattributed_count']} items — no recovery rate applies\n"
               if rec["unattributed_cents"] else "")
            + (f"Retained as tools     {fmt_money(rec['tool_residual_cents'])} "
               f"(does not convey with the boat)\n" if rec["tool_spend_cents"] else "")
            + "\n[dim]Refit spend does not return dollar-for-dollar at resale. The sunk\n"
              "figure is not waste — it is what you exchanged for capability and use.[/]",
            title="Ophelia's Key — reward", border_style="green",
        )
    )

    table = Table("system", "spend", "rate", "recoverable", "sunk")
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
            f"against the sunk cost"
        )
        console.print(
            f"  Alternative would have cost {fmt_money(use['value_realized_cents'])} "
            f"at {fmt_money(use['alternative_nightly_cents'])}/night"
        )
        if use["nights_remaining"]:
            console.print(
                f"  [yellow]{use['nights_remaining']} more nights[/] until the sunk cost "
                f"is beaten by the alternative"
            )
        else:
            console.print("  [green]The sunk cost is already beaten by the alternative.[/]")
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
