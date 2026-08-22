"""Command-line interface for Ophelia's Key."""

from __future__ import annotations

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .analysis.cost import cost_report
from .analysis.demo import clear_demo, seed_demo
from .analysis.reconcile import reconcile as run_reconcile
from .analysis.risk import risk_report
from .classify.rules import apply_rules
from .classify.taxonomy import seed_systems
from .config import get_settings
from .db.database import connect, fmt_money
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


def _db():
    settings = get_settings()
    settings.ensure_dirs()
    db = connect()
    seed_systems(db)
    return db


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
    reclassify: bool = typer.Option(False, help="Re-run over already-classified items."),
    min_confidence: float = typer.Option(0.6, help="Confidence floor for auto-assignment."),
):
    """Attribute line items to boat systems."""
    db = _db()
    stats = apply_rules(db, min_confidence=min_confidence, reclassify=reclassify)
    console.print(
        f"examined {stats['examined']}  ·  [green]classified {stats['classified']}[/]  ·  "
        f"[yellow]ambiguous {stats['ambiguous']}[/]  ·  unmatched {stats['unmatched']}"
    )
    if stats["ambiguous"] or stats["unmatched"]:
        console.print("[dim]Unplaced items are left NULL rather than guessed. "
                      "Review with: okey report unclassified[/]")


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
            f"Net spend    [bold green]{fmt_money(t['net_cents'])}[/]\n"
            f"Gross        {fmt_money(t['gross_cents'])}\n"
            f"Refunded     {fmt_money(t['refunded_cents'])}\n"
            f"Capital      {fmt_money(t['capital_cents'])}   "
            f"Consumable {fmt_money(t['consumable_cents'])}   "
            f"Unattributed [yellow]{fmt_money(t['unattributed_cents'])}[/]\n"
            f"Burn rate    {fmt_money(report['monthly_burn_cents'])}/mo (trailing 3mo)",
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
