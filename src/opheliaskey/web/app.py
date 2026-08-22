"""Local dashboard.

Read-only over the same analysis functions the CLI uses, so the two can never
disagree. Binds to localhost; the database holds personal purchase history.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..analysis.cost import cost_report
from ..analysis.risk import risk_report
from ..analysis.reward import reward_report
from ..classify.taxonomy import seed_systems, seed_vessel_meta
from ..db.database import connect, fmt_money

app = FastAPI(title="Ophelia's Key")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["money"] = fmt_money


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
