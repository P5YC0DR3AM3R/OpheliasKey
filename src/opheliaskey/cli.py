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
from .analysis.spec import spec_report
from .classify.rules import apply_rules
from .classify.taxonomy import seed_systems, seed_vessel_meta
from .config import get_settings
from .db.database import connect, fmt_money, utcnow
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
    seed_vessel_meta(db)
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
