"""The /studio page and its API. Same report as the CLI, so they cannot disagree;
the page embeds it as JSON and the sliders round-trip through /api/studio."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from fastapi.testclient import TestClient  # noqa: E402

from opheliaskey.db.database import Database  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A client whose app talks to a fresh temp database."""
    from opheliaskey.web import app as web

    db_path = tmp_path / "web.db"
    real = Database(db_path)
    real.migrate()

    def _connect(path=None):
        d = Database(db_path)
        d.migrate()
        return d

    monkeypatch.setattr(web, "connect", _connect)
    return TestClient(web.app)


def _embedded_report(html: str) -> dict:
    m = re.search(r"window\.__STUDIO__ = (\{.*?\}); window\.__STUDIO_LIVE__", html, re.S)
    assert m, "report JSON not embedded"
    return json.loads(m.group(1))


def test_studio_page_renders_with_embedded_report(client):
    r = client.get("/studio")
    assert r.status_code == 200
    html = r.text
    assert "Lyric Show Growth" in html and "Are we on track" in html
    full = client.get("/studio/full")
    assert full.status_code == 200 and "Floating Production Studio" in full.text
    assert '/static/studio-model.js' in html and client.get("/static/studio-model.js").status_code == 200
    assert "window.__STUDIO_LIVE__ = true" in html
    report = _embedded_report(html)
    assert list(report)[:3] == ["vessel", "lyricshow", "signal_chain"]
    assert report["recorded"]["shows"] == 0
    # The default scenario is honest about being modeled.
    assert all(v["source"] == "assumed" for v in report["funnel"]["inputs"].values())


def test_api_matches_page_and_accepts_overrides(client):
    page = _embedded_report(client.get("/studio").text)
    api = client.get("/api/studio").json()
    assert api["funnel"]["steady_state"] == page["funnel"]["steady_state"]

    r = client.get("/api/studio", params={"viewers_per_show": 300, "monthly_churn": 0.05})
    assert r.status_code == 200
    body = r.json()
    assert body["funnel"]["inputs"]["viewers_per_show"] == {"value": 300.0, "source": "override"}
    assert body["funnel"]["inputs"]["monthly_churn"]["source"] == "override"
    assert body["funnel"]["monthly"]["viewers"] > api["funnel"]["monthly"]["viewers"]
    assert body["assumptions"]["viewers_per_show"]["source"] == "override"


def test_api_honours_competition_sliders_and_bounds(client):
    base = client.get("/api/studio").json()
    r = client.get("/api/studio", params={"events_per_month": 0, "dock_attendees_per_event": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["funnel"]["inputs"]["events_per_month"] == {"value": 0.0, "source": "override"}
    assert body["funnel"]["monthly"]["viewers_event"] == 0
    assert body["funnel"]["monthly"]["attendees"] == 0
    assert body["funnel"]["monthly"]["viewers"] < base["funnel"]["monthly"]["viewers"]
    # absurd-but-finite inputs are refused at the edge, never a 500
    assert client.get("/api/studio", params={"viewers_per_show": 1e9}).status_code == 422
    assert client.get("/studio", params={"viewers_per_show": 1e9}).status_code == 422
    big = client.get("/api/studio", params={"viewers_per_show": 1_000_000, "shows_per_month": 62,
                                             "dock_attendees_per_event": 100_000, "events_per_month": 31})
    assert big.status_code in (200, 400)


def test_traveler_segment_is_on_the_page_and_in_the_api(client):
    report = _embedded_report(client.get("/studio").text)
    monthly = report["funnel"]["monthly"]
    assert monthly["installs"] == pytest.approx(monthly["installs_travelers"] + monthly["installs_performers"], abs=0.02)
    assert "traveler_share" in report["funnel"]["inputs"]
    keys = [p["key"] for p in report["funnel"]["by_plan"]]
    assert "traveler_bundle" in keys
    # the sliders the page sends are honoured server-side
    r = client.get("/api/studio", params={"traveler_share": 0})
    assert r.status_code == 200
    assert r.json()["funnel"]["monthly"]["new_paid_travelers"] == pytest.approx(
        r.json()["funnel"]["monthly"]["installs_event"] * report["funnel"]["inputs"]["traveler_install_to_paid"]["value"], abs=0.05)


def test_reach_and_target_are_on_the_page(client):
    html = client.get("/studio").text
    report = _embedded_report(html)
    assert "Who puts the overlay in front of people" in html and 'id="trackBig"' in html
    full = client.get("/studio/full").text
    assert 'id="targetBig"' in full and "Competition night" in full
    keys = [p["key"] for p in report["reach"]["partners"]]
    assert keys[0] == "church" and "artist" in keys
    committed = [p for p in report["reach"]["partners"] if p["status"] == "committed"]
    assert all(p["active"] for p in committed)
    assert report["target"]["subscribers"] == 3000 and report["target"]["month"] == 3
    # the hypothetical switch and the target are honoured server-side
    on = client.get("/api/studio", params={"partners_include_hypothetical": 1}).json()
    assert on["reach"]["viewers_partner_per_month"] > report["reach"]["viewers_partner_per_month"]
    tg = client.get("/api/studio", params={"target_subscribers": 50, "target_month": 12}).json()["target"]
    assert tg["subscribers"] == 50 and tg["month"] == 12


def test_page_is_server_stamped_and_names_three_lenses(client):
    html = client.get("/studio").text
    assert "generated 20" in html           # the server's clock, not the reader's
    assert 'id="exportLink"' in html
    full = client.get("/studio/full").text
    assert "reported as three separate figures" in full and "Four lenses" not in full
    assert 'id="summary"' in full   # the full working leads with the answer too


def test_api_rejects_bad_overrides(client):
    # FastAPI bounds reject impossible values before the model sees them.
    assert client.get("/api/studio", params={"monthly_churn": 0}).status_code == 422
    assert client.get("/api/studio", params={"viewer_to_install": 1.5}).status_code == 422
    # Unknown keys are ignored by FastAPI; the model is only ever handed known ones.
    assert client.get("/api/studio", params={"nonsense": 1}).status_code == 200


def test_standalone_export_embeds_everything(client, tmp_path):
    from opheliaskey.web import app as web

    html = web.render_studio_standalone(web.connect(), {"shows_per_month": 8})
    assert "window.__STUDIO_LIVE__ = false" in html
    assert "/api/studio" in html  # the fetch path exists in the JS but is gated by LIVE
    report = _embedded_report(html)
    assert report["funnel"]["inputs"]["shows_per_month"]["value"] == 8
    # A `<` in any note must not be able to close the script tag: the embedded
    # JSON payload itself carries no raw `<` at all.
    payload = html.split("window.__STUDIO__ = ", 1)[1].split("; window.__STUDIO_LIVE__", 1)[0]
    assert "<" not in payload


def test_competition_layer_is_on_the_page(client):
    html = client.get("/studio/full").text
    assert "Competition night" in html
    report = _embedded_report(html)
    comp = report["competition"]
    assert [s["key"] for s in comp["stages"]] == ["deck", "swim_platform", "dock"]
    assert sum(1 for step in comp["flow"] if step["product"]) >= 3
    monthly = report["funnel"]["monthly"]
    assert monthly["installs"] == pytest.approx(monthly["installs_stream"] + monthly["installs_event"], abs=0.02)
    # the two audiences are separate inputs with their own conversion rates
    assert "attendee_to_install" in report["funnel"]["inputs"]
    assert "dock_attendees_per_event" in report["funnel"]["inputs"]


def test_export_route_serves_standalone(client):
    r = client.get("/studio.html", params={"shows_per_month": 6})
    assert r.status_code == 200
    assert "window.__STUDIO_LIVE__ = false" in r.text
    assert "StudioModel" in r.text and "/static/studio-model.js" not in r.text   # the model is inlined
    assert _embedded_report(r.text)["funnel"]["inputs"]["shows_per_month"]["value"] == 6
    full = client.get("/studio.html", params={"page": "full"})
    assert full.status_code == 200 and "Floating Production Studio" in full.text
    assert client.get("/studio.html", params={"page": "nope"}).status_code == 422


def test_every_page_has_the_site_nav(client):
    # /review is a first-class page: the dashboard surfaces a review queue, and
    # without somewhere to act on it the queue is only an observation.
    for path in ("/ledger", "/studio", "/studio/full", "/review"):
        html = client.get(path).text
        assert 'class="sitenav"' in html, path
        assert 'href="/ledger"' in html, path
        assert 'href="/studio"' in html and 'href="/studio/full"' in html, path
        assert 'href="/review"' in html, path
        assert 'aria-current="page"' in html, path
    # the standalone export has no server behind it, so no nav
    assert 'class="sitenav"' not in client.get("/studio.html").text


def test_page_links_from_dashboard(client):
    assert 'href="/studio"' in client.get("/ledger").text


def test_root_serves_the_site(client):
    # The landing page is the front door; docs and numbers are one link away.
    home = client.get("/").text
    assert 'href="ledger"' in home
    assert 'href="manual/"' in home
