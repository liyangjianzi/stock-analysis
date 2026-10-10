"""`stock-analysis retire --gui`: the local planner server, offline, invented numbers."""
from __future__ import annotations

import copy
import functools
import http.client
import json
import threading
import time

import pytest

from stockanalysis import cli, holdings
from stockanalysis.retirement import gui, inputs, optimize


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(holdings, "load", lambda *a, **k: pytest.fail("holdings must not be read"))
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(inputs.TEMPLATE, indent=2) + "\n")
    app = gui.PlannerApp(path, out_root=tmp_path / "out", preview_paths=40,
                         report_paths=40, scenario_paths=20)
    srv = gui.PlannerServer(app, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _req(srv, method, path, body=None, headers=None):
    port = srv.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    h = {"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}", **(headers or {})}
    conn.request(method, path, None if body is None else json.dumps(body), h)
    r = conn.getresponse()
    data = r.read()
    conn.close()
    try:
        return r.status, json.loads(data)
    except ValueError:
        return r.status, data


def _plan(**people0):
    d = copy.deepcopy(inputs.TEMPLATE)
    d["people"][0].update(people0)
    return d


def test_serves_the_page_and_plotly(server):
    status, page = _req(server, "GET", "/")
    assert status == 200 and b"Retirement planner" in page
    status, js = _req(server, "GET", "/plotly.js")
    assert status == 200 and len(js) > 100_000
    status, css = _req(server, "GET", "/theme.css")
    assert status == 200 and b"[data-theme=dark]" in css
    status, js = _req(server, "GET", "/theme.js")
    assert status == 200 and b"window.theme" in js
    assert b'href="/theme.css"' in page and b"theme.draw(" in page


def test_plan_round_trips_with_a_preview_of_the_saved_plan(server):
    status, st = _req(server, "GET", "/api/plan")
    assert status == 200 and st["plan"] == inputs.TEMPLATE and st["error"] is None
    saved = st["saved"]
    assert 0 <= saved["success"] <= 1 and saved["paths"] == 40
    assert saved["chart"]["data"] and saved["gauge"]["data"]
    assert saved["gauge_title"] == "Chance the money lasts as long as either of you lives"
    assert saved["balances_source"] == "plan.json balances"


def test_preview_reflects_an_edit_without_writing(server):
    before = server.app.plan_path.read_text()
    _, base = _req(server, "POST", "/api/preview", {"plan": inputs.TEMPLATE})
    status, later = _req(server, "POST", "/api/preview", {"plan": _plan(retire_age=66), "previous": 0.5})
    assert status == 200
    assert later["investments_at_retirement"] > base["investments_at_retirement"]
    assert server.app.plan_path.read_text() == before


def test_same_plan_previews_identically(server):
    _, a = _req(server, "POST", "/api/preview", {"plan": inputs.TEMPLATE})
    _, b = _req(server, "POST", "/api/preview", {"plan": inputs.TEMPLATE})
    assert a["success"] == b["success"] and a["legacy"] == b["legacy"]


def test_invalid_edit_names_the_field_and_is_not_saved(server):
    before = server.app.plan_path.read_text()
    bad = _plan(retire_age=30)
    status, err = _req(server, "POST", "/api/preview", {"plan": bad})
    assert status == 400 and err["field"] == "people[0].retire_age"
    status, err = _req(server, "POST", "/api/plan", {"plan": bad})
    assert status == 400 and err["field"] == "people[0].retire_age"
    assert server.app.plan_path.read_text() == before
    assert not server.app.plan_path.with_name("plan.json.bak").exists()


def test_save_keeps_unknown_keys_and_a_backup(server):
    before = server.app.plan_path.read_text()
    edited = _plan(retire_age=62)
    status, st = _req(server, "POST", "/api/plan", {"plan": edited})
    assert status == 200 and st["plan"]["people"][0]["retire_age"] == 62
    on_disk = json.loads(server.app.plan_path.read_text())
    for key in ("_readme", "holdings", "scenarios", "balances"):
        assert on_disk[key] == inputs.TEMPLATE[key]
    assert server.app.plan_path.with_name("plan.json.bak").read_text() == before
    assert inputs.load_inputs(server.app.plan_path).people[0].retire_age == 62


def test_report_job_saves_runs_and_serves_the_report(server):
    status, st = _req(server, "POST", "/api/report", {"plan": _plan(retire_age=61)})
    assert status == 200 and st["plan"]["people"][0]["retire_age"] == 61
    job = server.app.job
    assert json.loads(server.app.plan_path.read_text())["people"][0]["retire_age"] == 61
    deadline = time.time() + 60
    while job["state"] == "running" and time.time() < deadline:
        time.sleep(0.1)
        _, job = _req(server, "GET", "/api/report/status")
    assert job["state"] == "done", job
    status, page = _req(server, "GET", job["url"])
    assert status == 200 and b"Chance the money lasts" in page


def test_a_second_report_waits_for_the_first(server):
    server.app.job = {"state": "running"}
    status, _ = _req(server, "POST", "/api/report", {"plan": inputs.TEMPLATE})
    assert status == 409


def test_foreign_origin_and_host_are_refused(server):
    status, _ = _req(server, "POST", "/api/plan", {"plan": inputs.TEMPLATE},
                     headers={"Origin": "http://evil.example"})
    assert status == 403
    status, _ = _req(server, "POST", "/api/plan", {"plan": inputs.TEMPLATE},
                     headers={"Content-Type": "text/plain"})
    assert status == 403
    status, _ = _req(server, "GET", "/api/plan", headers={"Host": "evil.example"})
    assert status == 403


def test_reports_stay_inside_the_output_root(server):
    (server.app.out_root).mkdir()
    for path in ("/reports/../plan.json", "/reports/%2e%2e/plan.json", "/reports/nothing.html"):
        status, _ = _req(server, "GET", path)
        assert status == 404


def test_gui_flag_hands_off_to_serve(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(gui, "serve", lambda path, **kw: seen.update(path=path, **kw))
    plan = tmp_path / "plan.json"
    assert cli.main(["retire", "--gui", "--inputs", str(plan), "--port", "9001", "--no-browser",
                     "--bank", "/nonexistent-bank"]) == 0
    assert seen == {"path": plan, "port": 9001, "open_browser": False, "holdings_path": None,
                    "out_root": None, "report_paths": None, "scenario_paths": None,
                    "bank": "/nonexistent-bank"}


def test_serve_fails_fast_without_a_plan(tmp_path):
    with pytest.raises(FileNotFoundError, match="--init"):
        gui.serve(tmp_path / "none.json", port=0, open_browser=False)


def test_affordability_endpoint(server):
    status, r = _req(server, "POST", "/api/optimize/affordability", {"plan": inputs.TEMPLATE, "target": 0.9})
    assert status == 200 and r["target"] == 0.9
    assert r["max_spending"] is None or r["max_spending_success"] >= 0.9
    status, err = _req(server, "POST", "/api/optimize/affordability", {"plan": _plan(retire_age=30)})
    assert status == 400 and err["field"] == "people[0].retire_age"


def test_benefits_endpoint(server, monkeypatch):
    small = functools.partial(optimize.best_benefit_ages, cpp_ages=(65, 70), oas_ages=(65, 70), workers=1)
    monkeypatch.setattr(optimize, "best_benefit_ages", small)
    status, r = _req(server, "POST", "/api/optimize/benefits", {"plan": inputs.TEMPLATE})
    assert status == 200 and r["plan"] is None and r["best_legacy"] >= r["legacy"]
    assert [c["name"] for c in r["people"]] == ["Partner A", "Partner B"]


def test_page_edits_sex_and_survivor_share(server):
    status, page = _req(server, "GET", "/")
    page = page.decode()
    assert status == 200 and "${P}.sex" in page and "spending.survivor_share" in page
    assert "It assumes everyone lives to" not in page
    status, st = _req(server, "GET", "/api/plan")
    assert st["limits"]["survivor_share"] == [0.4, 1.0]
    assert "lifespans" in st["saved"]


def test_page_edits_the_pension_match(server):
    status, page = _req(server, "GET", "/")
    assert "${P}.pension_match" in page.decode()


def test_page_edits_the_espp(server):
    status, page = _req(server, "GET", "/")
    page = page.decode()
    assert "${P}.espp.rate" in page and "data-add-espp" in page and "data-remove-espp" in page


def test_drawdown_endpoint_returns_the_table(server):
    status, r = _req(server, "POST", "/api/optimize/drawdown", {"plan": inputs.TEMPLATE})
    assert status == 200 and len(r["rows"]) == len(optimize.DRAWDOWN_TARGETS)
    assert set(r["best"]) == {"legacy", "tax", "success"} and r["current"]["target"] is None
    page = _req(server, "GET", "/")[1].decode()
    assert "opt-drawdown" in page and 'name="drawdown-goal"' in page


def test_page_edits_rrsp_room_and_childcare(server):
    page = _req(server, "GET", "/")[1].decode()
    assert "${P}.rrsp_room" in page and "education.childcare" in page


def test_page_edits_money_events(server):
    page = _req(server, "GET", "/")[1].decode()
    assert 'id="add-event"' in page and "data-remove-event" in page


def test_page_switches_the_spending_rule(server):
    page = _req(server, "GET", "/")[1].decode()
    assert 'name="spending-rule"' in page and "spending.guardrail_band" in page


def test_compare_endpoint_and_tab(server):
    d = copy.deepcopy(inputs.TEMPLATE)
    d["saved_scenarios"] = [{"name": "Spend less", "changes": {"spending.base": 70_000}}]
    status, r = _req(server, "POST", "/api/compare", {"plan": d})
    assert status == 200 and [x["name"] for x in r["rows"]] == ["Current plan", "Spend less"]
    page = _req(server, "GET", "/")[1].decode()
    assert '"compare"' in page and 'id="save-scenario"' in page and "data-apply-scenario" in page


def test_preview_carries_future_dollar_factors(server):
    status, r = _req(server, "POST", "/api/preview", {"plan": inputs.TEMPLATE})
    assert status == 200
    x = r["chart"]["data"][0]["x"]
    assert len(r["future_factor"]) == len(x) and r["future_factor"][0] == 1.0
    assert r["legacy_factor"] > 1.0 and r["retire_factor"] >= 1.0
    page = _req(server, "GET", "/")[1].decode()
    assert 'id="dollars-switch"' in page and "cost_growth." in page
