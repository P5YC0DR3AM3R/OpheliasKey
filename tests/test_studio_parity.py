"""The studio page carries a JavaScript mirror of the funnel model so its sliders
answer instantly and the standalone export works without a server. A mirror that
drifts from the Python would show one number on the page and print another in
the CLI, so this runs the real JS block in node against studio_report()."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from opheliaskey.analysis.studio import studio_report  # noqa: E402
from opheliaskey.classify.taxonomy import seed_systems, seed_vessel_meta  # noqa: E402
from opheliaskey.db.database import Database  # noqa: E402

MODEL_JS = Path(__file__).resolve().parents[1] / "src/opheliaskey/web/static/studio-model.js"
NODE = shutil.which("node")

SCENARIOS = [
    {},
    {"viewers_per_show": 600, "shows_per_month": 8},
    {"viewer_to_install": 0.12, "install_to_paid": 0.2, "monthly_churn": 0.03},
    {"viewers_per_show": 40, "shows_per_month": 1, "monthly_churn": 0.2},
    {"events_per_month": 0},
    {"events_per_month": 4, "dock_attendees_per_event": 200, "attendee_to_install": 0.3},
    {"traveler_share": 0},
    {"traveler_share": 1, "traveler_install_to_paid": 0.1},
    {"partners_include_hypothetical": 1},
    {"partners_include_hypothetical": 1, "partner_live_share": 0.05, "target_subscribers": 25000, "target_month": 6},
]


def _mirror_js() -> str:
    src = MODEL_JS.read_text()
    start = src.index("// @@mirror-start")
    end = src.index("// @@mirror-end")
    return src[start:end]


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "parity.db")
    d.migrate()
    seed_systems(d)
    seed_vessel_meta(d)
    return d


def _run_mirror(base: dict, inputs: dict, tmp_path) -> dict:
    script = (
        _mirror_js()
        + "\nconst rep = " + json.dumps(base) + ";\n"
        + "const inputs = " + json.dumps(inputs) + ";\n"
        + "process.stdout.write(JSON.stringify(computeFunnel(rep, inputs)));\n"
    )
    js = tmp_path / "mirror.js"
    js.write_text(script)
    out = subprocess.run([NODE, str(js)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.mark.skipif(NODE is None, reason="node not installed")
def test_js_mirror_matches_python_with_a_baseline(db, tmp_path):
    """A real subscriber baseline changes the trajectory but not the payback: the
    show-driven columns and the ROI block must agree between Python and the page."""
    from opheliaskey.db.database import utcnow

    db.execute(
        "INSERT INTO project_meta (key, value, updated_at) VALUES ('studio.baseline_subscribers', '40', ?)",
        (utcnow(),),
    )
    base = studio_report(db)
    assert base["funnel"]["inputs"]["baseline_subscribers"]["value"] == 40
    inputs = {k: v["value"] for k, v in base["funnel"]["inputs"].items()
              if k in ("viewers_per_show", "shows_per_month", "viewer_to_install", "install_to_paid",
                       "monthly_churn", "events_per_month", "dock_attendees_per_event")}
    got = _run_mirror(base, inputs, tmp_path)
    for py_row, js_row in zip(base["funnel"]["trajectory"], got["funnel"]["trajectory"], strict=True):
        for key in ("mrr_net_cents", "cumulative_net_cents", "show_driven_mrr_net_cents",
                    "show_driven_cumulative_net_cents"):
            assert abs(py_row[key] - js_row[key]) <= 1, (py_row["month"], key)
        assert js_row["show_driven_subscribers"] == pytest.approx(py_row["show_driven_subscribers"], abs=0.11)
    for key in ("kit_month", "project_month", "slip_month"):
        assert got["breakeven"][key] == base["breakeven"][key], key
    roi_py, roi_js = base["roi"], got["roi"]
    assert abs(roi_js["horizon"]["show_driven_cumulative_net_cents"]
               - roi_py["horizon"]["show_driven_cumulative_net_cents"]) <= 1
    assert roi_js["horizon"]["roi_multiple_on_kit"] == pytest.approx(roi_py["horizon"]["roi_multiple_on_kit"], abs=0.02)
    for key in ("per_show_net_cents", "per_viewer_net_cents", "cost_per_install_cents"):
        assert (roi_js[key] is None) == (roi_py[key] is None), key
        if roi_py[key] is not None:
            assert abs(roi_js[key] - roi_py[key]) <= 1, key


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("overrides", SCENARIOS)
def test_js_mirror_matches_python(db, tmp_path, overrides):
    base = studio_report(db)
    want = studio_report(db, overrides or None)
    inputs = {k: v["value"] for k, v in want["funnel"]["inputs"].items()}
    script = (
        _mirror_js()
        + "\nconst rep = " + json.dumps(base) + ";\n"
        + "const inputs = " + json.dumps(inputs) + ";\n"
        + "process.stdout.write(JSON.stringify(computeFunnel(rep, inputs)));\n"
    )
    js = tmp_path / "mirror.js"
    js.write_text(script)
    out = subprocess.run([NODE, str(js)], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)

    for key in ("viewers", "viewers_stream", "viewers_event", "viewers_partner", "attendees", "installs",
                "installs_stream", "installs_event", "new_paid", "travelers_viewers",
                "performers_viewers", "installs_travelers", "installs_performers",
                "new_paid_travelers", "new_paid_performers"):
        assert got["funnel"]["monthly"][key] == pytest.approx(want["funnel"]["monthly"][key], abs=0.02), key
    ss_py, ss_js = want["funnel"]["steady_state"], got["funnel"]["steady_state"]
    assert ss_js["subscribers"] == pytest.approx(ss_py["subscribers"], abs=0.11)
    for key in ("subscribers_travelers", "subscribers_performers"):
        assert ss_js[key] == pytest.approx(ss_py[key], abs=0.11), key
    for key in ("mrr_net_travelers_cents", "mrr_net_performers_cents"):
        assert abs(ss_js[key] - ss_py[key]) <= 1, key
    for py_plan, js_plan in zip(want["funnel"]["by_plan"], got["funnel"]["by_plan"], strict=True):
        assert py_plan["key"] == js_plan["key"]
        assert js_plan["new_subscribers"] == pytest.approx(py_plan["new_subscribers"], abs=0.02)
        assert js_plan["share"] == pytest.approx(py_plan["share"], abs=0.002)
    for key in ("mrr_gross_cents", "mrr_net_cents", "arr_net_cents"):
        assert abs(ss_js[key] - ss_py[key]) <= 1, key
    assert abs(got["funnel"]["arpu_gross_cents"] - want["funnel"]["arpu_gross_cents"]) <= 1

    for py_row, js_row in zip(want["funnel"]["trajectory"], got["funnel"]["trajectory"], strict=True):
        assert py_row["month"] == js_row["month"]
        assert abs(py_row["mrr_net_cents"] - js_row["mrr_net_cents"]) <= 1
        assert abs(py_row["cumulative_net_cents"] - js_row["cumulative_net_cents"]) <= 1

    for key in ("kit_month", "project_month", "slip_month"):
        assert got["breakeven"][key] == want["breakeven"][key], key
    # reach and target ride the same inputs
    assert got["reach"]["viewers_partner_per_month"] == pytest.approx(want["reach"]["viewers_partner_per_month"], abs=0.05)
    assert got["reach"]["viewers_abroad_per_month"] == pytest.approx(want["reach"]["viewers_abroad_per_month"], abs=0.05)
    for py_p, js_p in zip(want["reach"]["partners"], got["reach"]["partners"], strict=True):
        assert py_p["key"] == js_p["key"] and py_p["active"] == js_p["active"]
        assert js_p["viewers_per_month"] == pytest.approx(py_p["viewers_per_month"], abs=0.05)
        assert js_p["new_paid_per_month"] == pytest.approx(py_p["new_paid_per_month"], abs=0.05)
    tg_py, tg_js = want["target"], got["target"]
    for key in ("subscribers", "month", "reached_month", "on_track"):
        assert tg_js[key] == tg_py[key], key
    for key in ("subscribers_at_target_month", "shortfall", "required_new_paid_per_month", "required_viewers_per_month"):
        assert (tg_js[key] is None) == (tg_py[key] is None), key
        if tg_py[key] is not None:
            assert tg_js[key] == pytest.approx(tg_py[key], rel=1e-3, abs=0.2), key
    acq_py, acq_js = want["lenses"]["acquisition_displaced"], got["lenses"]["acquisition_displaced"]
    assert abs(acq_js["monthly_cents"] - acq_py["monthly_cents"]) <= 1
