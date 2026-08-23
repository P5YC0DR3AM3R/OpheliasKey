"""Local dashboard.

Read-only over the same analysis functions the CLI uses, so the two can never
disagree. Binds to localhost; the database holds personal purchase history.
"""

from __future__ import annotations

import json

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ..db.database import utcnow

from ..analysis.cost import cost_report
from ..analysis.risk import risk_report
from ..analysis.reward import reward_report
from ..analysis.studio import OVERRIDABLE, studio_report
from ..classify.taxonomy import seed_systems, seed_vessel_meta
from ..db.database import connect, fmt_money

app = FastAPI(title="Ophelia's Key")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["money"] = fmt_money
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
MODEL_JS = STATIC_DIR / "studio-model.js"

# Two faces of the same report: the growth story (default) and the full working.
STUDIO_PAGES = {"growth": "growth.html", "full": "studio.html"}


def _db():
    db = connect()
    seed_systems(db)
    seed_vessel_meta(db)
    return db


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    db = _db()
    cost = cost_report(db)
    risk = risk_report(db)
    reward = reward_report(db)

    months = cost["by_month"]
    peak = max((m["spend_cents"] for m in months), default=1) or 1
    review_queue = db.query(
        "SELECT * FROM v_review_queue ORDER BY total_cents DESC LIMIT 20"
    )
    review_total = db.one(
        "SELECT COUNT(*) n, COALESCE(SUM(total_cents),0) amt FROM v_review_queue"
    )
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "cost": cost,
            "reward": reward,
            "risk": risk,
            "months": months,
            "peak": peak,
            "review_queue": review_queue,
            "review_total": review_total,
            "nav_current": "ledger",
        },
    )


@app.get("/api/cost")
def api_cost():
    return JSONResponse(cost_report(_db()))


@app.get("/api/reward")
def api_reward():
    return JSONResponse(reward_report(_db()))


@app.get("/api/risk")
def api_risk():
    return JSONResponse(risk_report(_db()))


# --- floating studio --------------------------------------------------------
#
# The livestream studio page reads the same studio_report the CLI prints. The
# sliders call /api/studio with overrides so the Python model stays the source
# of truth; the page carries a JavaScript mirror only for instant feedback and
# for the standalone export, which has no server to ask.

def _studio_overrides(**params: float | None) -> dict[str, float]:
    overrides = {k: v for k, v in params.items() if v is not None}
    unknown = set(overrides) - OVERRIDABLE
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown studio assumption {sorted(unknown)}")
    return overrides


def _studio_report_or_400(db, overrides: dict[str, float]) -> dict:
    """The model refuses bad inputs with ValueError; absurd-but-finite ones overflow
    the arithmetic. Both are the caller's input, so both are a 400, not a 500."""
    try:
        return studio_report(db, overrides or None)
    except (ValueError, OverflowError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _studio_context(db, overrides: dict[str, float], standalone: bool) -> dict:
    report = _studio_report_or_400(db, overrides)
    # `<` is escaped so a note can never close the script tag it is embedded in.
    report_json = json.dumps(report).replace("<", "\\u003c")
    model_js = MODEL_JS.read_text(encoding="utf-8")
    return {
        "report": report,
        "report_json": report_json,
        "vessel": report["vessel"],
        "standalone": standalone,
        # The shared JS model: linked when served, inlined in the export.
        "model_js": model_js.replace("</script", "<\\/script") if standalone else "",
        "asset_version": str(int(MODEL_JS.stat().st_mtime)),
        # Stamped by the server: the standalone export is opened long after it is
        # made, and the browser's clock would say when it was read, not written.
        "generated_at": utcnow()[:16].replace("T", " ") + " UTC",
    }


def render_studio_standalone(
    db, overrides: dict[str, float] | None = None, page: str = "growth"
) -> str:
    """A studio page as one self-contained HTML file, for sharing.

    Same template, same report; the only difference is that the page never
    calls back to a server, so the sliders use the embedded model mirror."""
    ctx = _studio_context(db, overrides or {}, standalone=True)
    return templates.get_template(STUDIO_PAGES.get(page, STUDIO_PAGES["growth"])).render(**ctx)


@app.get("/studio", response_class=HTMLResponse)
def studio_page(
    request: Request,
    viewers_per_show: float | None = Query(None, gt=0, le=1_000_000),
    shows_per_month: float | None = Query(None, gt=0, le=62),
    viewer_to_install: float | None = Query(None, ge=0, le=1),
    install_to_paid: float | None = Query(None, ge=0, le=1),
    monthly_churn: float | None = Query(None, gt=0, le=1),
    events_per_month: float | None = Query(None, ge=0, le=31),
    dock_attendees_per_event: float | None = Query(None, ge=0, le=100_000),
    traveler_share: float | None = Query(None, ge=0, le=1),
    traveler_install_to_paid: float | None = Query(None, ge=0, le=1),
    partner_live_share: float | None = Query(None, ge=0, le=1),
    partners_include_hypothetical: float | None = Query(None, ge=0, le=1),
    target_subscribers: float | None = Query(None, ge=0, le=100_000_000),
    target_month: float | None = Query(None, ge=1, le=120),
    traveler_viewer_to_install: float | None = Query(None, ge=0, le=1),
    traveler_monthly_churn: float | None = Query(None, gt=0, le=1),
    partner_church_live_viewers: float | None = Query(None, ge=0, le=10_000_000),
    partner_artist_subscribers: float | None = Query(None, ge=0, le=1_000_000_000),
):
    db = _db()
    overrides = _studio_overrides(
        viewers_per_show=viewers_per_show, shows_per_month=shows_per_month,
        viewer_to_install=viewer_to_install, install_to_paid=install_to_paid,
        monthly_churn=monthly_churn, events_per_month=events_per_month,
        dock_attendees_per_event=dock_attendees_per_event, traveler_share=traveler_share,
        traveler_install_to_paid=traveler_install_to_paid, partner_live_share=partner_live_share,
        partners_include_hypothetical=partners_include_hypothetical,
        target_subscribers=target_subscribers, target_month=target_month,
        traveler_viewer_to_install=traveler_viewer_to_install,
        traveler_monthly_churn=traveler_monthly_churn,
        partner_church_live_viewers=partner_church_live_viewers,
        partner_artist_subscribers=partner_artist_subscribers,
    )
    return templates.TemplateResponse(
        request, "growth.html", {**_studio_context(db, overrides, standalone=False), "nav_current": "growth"}
    )


@app.get("/studio/full", response_class=HTMLResponse)
def studio_full_page(
    request: Request,
    viewers_per_show: float | None = Query(None, gt=0, le=1_000_000),
    shows_per_month: float | None = Query(None, gt=0, le=62),
    viewer_to_install: float | None = Query(None, ge=0, le=1),
    install_to_paid: float | None = Query(None, ge=0, le=1),
    monthly_churn: float | None = Query(None, gt=0, le=1),
    events_per_month: float | None = Query(None, ge=0, le=31),
    dock_attendees_per_event: float | None = Query(None, ge=0, le=100_000),
    traveler_share: float | None = Query(None, ge=0, le=1),
    traveler_install_to_paid: float | None = Query(None, ge=0, le=1),
    partner_live_share: float | None = Query(None, ge=0, le=1),
    partners_include_hypothetical: float | None = Query(None, ge=0, le=1),
    target_subscribers: float | None = Query(None, ge=0, le=100_000_000),
    target_month: float | None = Query(None, ge=1, le=120),
    traveler_viewer_to_install: float | None = Query(None, ge=0, le=1),
    traveler_monthly_churn: float | None = Query(None, gt=0, le=1),
    partner_church_live_viewers: float | None = Query(None, ge=0, le=10_000_000),
    partner_artist_subscribers: float | None = Query(None, ge=0, le=1_000_000_000),
):
    """The full working: the boat as a studio, competition nights, power, uplink,
    the accounting lenses — everything the growth story leaves out."""
    db = _db()
    overrides = _studio_overrides(
        viewers_per_show=viewers_per_show, shows_per_month=shows_per_month,
        viewer_to_install=viewer_to_install, install_to_paid=install_to_paid,
        monthly_churn=monthly_churn, events_per_month=events_per_month,
        dock_attendees_per_event=dock_attendees_per_event, traveler_share=traveler_share,
        traveler_install_to_paid=traveler_install_to_paid, partner_live_share=partner_live_share,
        partners_include_hypothetical=partners_include_hypothetical,
        target_subscribers=target_subscribers, target_month=target_month,
        traveler_viewer_to_install=traveler_viewer_to_install,
        traveler_monthly_churn=traveler_monthly_churn,
        partner_church_live_viewers=partner_church_live_viewers,
        partner_artist_subscribers=partner_artist_subscribers,
    )
    return templates.TemplateResponse(
        request, "studio.html", {**_studio_context(db, overrides, standalone=False), "nav_current": "full"}
    )


@app.get("/studio.html", response_class=HTMLResponse)
def studio_export(
    page: str = Query("growth", pattern="^(growth|full)$"),
    viewers_per_show: float | None = Query(None, gt=0, le=1_000_000),
    shows_per_month: float | None = Query(None, gt=0, le=62),
    viewer_to_install: float | None = Query(None, ge=0, le=1),
    install_to_paid: float | None = Query(None, ge=0, le=1),
    monthly_churn: float | None = Query(None, gt=0, le=1),
    events_per_month: float | None = Query(None, ge=0, le=31),
    dock_attendees_per_event: float | None = Query(None, ge=0, le=100_000),
    traveler_share: float | None = Query(None, ge=0, le=1),
    traveler_install_to_paid: float | None = Query(None, ge=0, le=1),
    partner_live_share: float | None = Query(None, ge=0, le=1),
    partners_include_hypothetical: float | None = Query(None, ge=0, le=1),
    target_subscribers: float | None = Query(None, ge=0, le=100_000_000),
    target_month: float | None = Query(None, ge=1, le=120),
    traveler_viewer_to_install: float | None = Query(None, ge=0, le=1),
    traveler_monthly_churn: float | None = Query(None, gt=0, le=1),
    partner_church_live_viewers: float | None = Query(None, ge=0, le=10_000_000),
    partner_artist_subscribers: float | None = Query(None, ge=0, le=1_000_000_000),
):
    """The standalone page, for saving and sharing. Same as `okey report studio --html`.
    `?page=full` exports the full working instead of the growth story."""
    overrides = _studio_overrides(
        viewers_per_show=viewers_per_show, shows_per_month=shows_per_month,
        viewer_to_install=viewer_to_install, install_to_paid=install_to_paid,
        monthly_churn=monthly_churn, events_per_month=events_per_month,
        dock_attendees_per_event=dock_attendees_per_event, traveler_share=traveler_share,
        traveler_install_to_paid=traveler_install_to_paid, partner_live_share=partner_live_share,
        partners_include_hypothetical=partners_include_hypothetical,
        target_subscribers=target_subscribers, target_month=target_month,
        traveler_viewer_to_install=traveler_viewer_to_install,
        traveler_monthly_churn=traveler_monthly_churn,
        partner_church_live_viewers=partner_church_live_viewers,
        partner_artist_subscribers=partner_artist_subscribers,
    )
    return HTMLResponse(render_studio_standalone(_db(), overrides, page=page))


@app.get("/api/studio")
def api_studio(
    viewers_per_show: float | None = Query(None, gt=0, le=1_000_000),
    shows_per_month: float | None = Query(None, gt=0, le=62),
    viewer_to_install: float | None = Query(None, ge=0, le=1),
    install_to_paid: float | None = Query(None, ge=0, le=1),
    monthly_churn: float | None = Query(None, gt=0, le=1),
    events_per_month: float | None = Query(None, ge=0, le=31),
    dock_attendees_per_event: float | None = Query(None, ge=0, le=100_000),
    traveler_share: float | None = Query(None, ge=0, le=1),
    traveler_install_to_paid: float | None = Query(None, ge=0, le=1),
    partner_live_share: float | None = Query(None, ge=0, le=1),
    partners_include_hypothetical: float | None = Query(None, ge=0, le=1),
    target_subscribers: float | None = Query(None, ge=0, le=100_000_000),
    target_month: float | None = Query(None, ge=1, le=120),
    traveler_viewer_to_install: float | None = Query(None, ge=0, le=1),
    traveler_monthly_churn: float | None = Query(None, gt=0, le=1),
    partner_church_live_viewers: float | None = Query(None, ge=0, le=10_000_000),
    partner_artist_subscribers: float | None = Query(None, ge=0, le=1_000_000_000),
):
    db = _db()
    overrides = _studio_overrides(
        viewers_per_show=viewers_per_show, shows_per_month=shows_per_month,
        viewer_to_install=viewer_to_install, install_to_paid=install_to_paid,
        monthly_churn=monthly_churn, events_per_month=events_per_month,
        dock_attendees_per_event=dock_attendees_per_event, traveler_share=traveler_share,
        traveler_install_to_paid=traveler_install_to_paid, partner_live_share=partner_live_share,
        partners_include_hypothetical=partners_include_hypothetical,
        target_subscribers=target_subscribers, target_month=target_month,
        traveler_viewer_to_install=traveler_viewer_to_install,
        traveler_monthly_churn=traveler_monthly_churn,
        partner_church_live_viewers=partner_church_live_viewers,
        partner_artist_subscribers=partner_artist_subscribers,
    )
    return JSONResponse(_studio_report_or_400(db, overrides))
