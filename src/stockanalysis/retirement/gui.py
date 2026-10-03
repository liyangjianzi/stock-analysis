"""``stock-analysis retire --gui``: a local web page that edits plan.json.

A stdlib ``http.server`` on 127.0.0.1 serves one self-contained page
(``static/planner.html``) and a small JSON API:

- ``GET  /api/plan``             the raw plan.json, where its balances come from, and a
                                 quick preview of the *saved* plan (the comparison point)
- ``POST /api/preview``          a quick preview of an edited plan (nothing is written)
- ``POST /api/plan``             validate, back up to plan.json.bak, write atomically
- ``POST /api/report``           save, then run the full report in a background thread
- ``GET  /api/report/status``    idle / running / done (+ url) / error
- ``POST /api/reload-balances``  re-read the holdings file
- ``POST /api/optimize/affordability``  highest spending / earliest retirement at a target
- ``POST /api/optimize/benefits``       best CPP / OAS start ages (~10 s)
- ``GET  /reports/<path>``       files under the output root (browsers won't follow
                                 ``file://`` links from an http page)

Previews of the saved and the edited plan use the same seed and path count, so a
difference between them is the edit's effect, not different futures.

The plan is private, so every request must carry this server's own ``Host`` (a
DNS-rebinding page can't read it) and a POST must be same-origin JSON (another
site open in the browser can't rewrite it).
"""
from __future__ import annotations

import dataclasses
import json
import mimetypes
import os
import tempfile
import threading
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path

from .. import config
from . import cli, engine, inputs, optimize, report

PREVIEW_PATHS = 1_000
HOST = "127.0.0.1"


def _error_body(e: Exception) -> dict:
    """{field, message}; ``field`` (from inputs.PlanError) lets the page highlight the input."""
    return {"field": getattr(e, "field", None), "message": str(e)}


def summarize(result: engine.PlanResult, previous: float | None = None) -> dict:
    """The report's headline numbers plus the bad-luck future, the report's gauge
    (against ``previous``, the saved plan's success) and money-left chart."""
    plan, sim, bad = result.inputs, result.simulated, result.bad_luck
    first = int(bad.first_shortfall_step[0])
    return {
        **report.headline(result),
        "legacy_bad": float(bad.legacy[0]),
        "short_years_bad": int(bad.shortfall_years[0]),
        "first_short_age_bad": plan.people[0].age + first if first < len(bad.years) else None,
        "median_return": result.average_return,
        "gauge": json.loads(report.success_meter(sim.success, previous,
                                                 title=report.lasts_label(plan)).to_json()),
        "chart": json.loads(report.money_left_chart(sim, plan).to_json()),
        "education": _education_summary(result),
    }


def _education_summary(result: engine.PlanResult) -> dict | None:
    """What the RESP covers in the average future, and the planned contributions."""
    avg = result.average
    s = avg.school
    if s is None:
        return None
    return {"cost": float(s.cost.sum()), "household": float(avg.education[:, 0].sum()),
            "student_grants": float(avg.student_grant[:, 0].sum()),
            "contributions": float(s.contribution.sum()), "grants": float(s.grant.sum()),
            "balance": s.balance, "kids": [dataclasses.asdict(k) for k in s.kids]}


class PlannerApp:
    """Everything the handler does, kept free of HTTP so it is easy to test."""

    def __init__(self, plan_path, *, holdings_path=None, out_root=None,
                 preview_paths: int = PREVIEW_PATHS, report_paths: int | None = None,
                 scenario_paths: int | None = None):
        self.plan_path = Path(plan_path)
        self.holdings_path = holdings_path
        self.out_root = Path(out_root) if out_root else config.DEFAULT_RETIREMENT_OUT
        self.preview_paths = preview_paths
        self.report_paths = report_paths
        self.scenario_paths = scenario_paths
        self._book = None               # the holdings file, read once until "reload"
        self._compute = threading.Lock()     # one engine run at a time
        self._job_lock = threading.Lock()
        self.job: dict = {"state": "idle"}

    # -- plan file -------------------------------------------------------------
    def read_raw(self) -> dict:
        if not self.plan_path.exists():
            raise FileNotFoundError(
                f"No plan at {self.plan_path}. Create one with: stock-analysis retire --init")
        return json.loads(self.plan_path.read_text(encoding="utf-8"))

    def save(self, d: dict):
        """Validate ``d``, keep the current file as .bak, then replace it atomically.
        Returns the validated plan with its balances and their source."""
        planned = self.with_balances(inputs.parse(d))   # also catches accounts it can't sort
        if self.plan_path.exists():
            self.plan_path.with_name(self.plan_path.name + ".bak").write_text(
                self.plan_path.read_text(encoding="utf-8"), encoding="utf-8")
        fd, tmp = tempfile.mkstemp(dir=self.plan_path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(d, indent=2) + "\n")   # keys in the file's own order
            os.replace(tmp, self.plan_path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return planned

    # -- balances --------------------------------------------------------------
    def reload_balances(self) -> None:
        self._book = None

    def _load_book(self, holdings_path):
        if self._book is None:
            self._book = cli._load_book(holdings_path)
        return self._book

    def with_balances(self, plan):
        return cli._with_balances(plan, self.holdings_path, load=self._load_book)

    # -- results ---------------------------------------------------------------
    def preview(self, d: dict, previous: float | None = None) -> dict:
        plan, source = self.with_balances(inputs.parse(d))
        with self._compute:
            result = engine.run(plan, paths=self.preview_paths, seed=plan.returns.seed)
        return {**summarize(result, previous), "balances_source": source}

    def affordability(self, d: dict, target: float) -> dict:
        plan, _ = self.with_balances(inputs.parse(d))
        return dataclasses.asdict(optimize.affordability(plan, target=target, paths=self.preview_paths))

    def benefits(self, d: dict) -> dict:
        plan, _ = self.with_balances(inputs.parse(d))
        res = optimize.best_benefit_ages(plan, paths=self.preview_paths)
        return dataclasses.asdict(dataclasses.replace(res, plan=None))   # the ages are in people

    def plan_state(self) -> dict:
        raw = self.read_raw()
        try:
            saved, error = self.preview(raw), None
        except (ValueError, FileNotFoundError) as e:
            saved, error = None, _error_body(e)
        return {"plan": raw, "path": str(self.plan_path), "saved": saved, "error": error,
                "report_paths": self.report_paths or raw.get("returns", {}).get("paths"),
                "strategies": inputs.STRATEGY_LABELS,
                "limits": inputs.limits(raw.get("province", "AB")),
                "defaults": inputs.defaults()}

    def start_report(self, d: dict) -> bool:
        """Save ``d`` and start the full report; False if one is already running."""
        with self._job_lock:
            if self.job["state"] == "running":
                return False
            plan, source = self.save(d)
            self.job = {"state": "running"}
        threading.Thread(target=self._run_report, args=(plan, source), daemon=True).start()
        return True

    def _run_report(self, plan, source) -> None:
        try:
            out, result = cli.generate(plan, source, paths=self.report_paths,
                                       scenario_paths=self.scenario_paths, out_root=self.out_root)
            rel = out.resolve().relative_to(self.out_root.resolve()).as_posix()
            job = {"state": "done", "url": f"/reports/{rel}", "success": result.simulated.success}
        except Exception as e:                    # surface any failure on the page
            traceback.print_exc()
            job = {"state": "error", "message": str(e)}
        with self._job_lock:
            self.job = job

    def report_file(self, rel: str) -> Path | None:
        """A file under the output root, or None (also for anything outside it)."""
        root = self.out_root.resolve()
        target = (root / rel).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            return None
        return target


class _Handler(BaseHTTPRequestHandler):
    server_version = "RetirementPlanner/1"

    @property
    def app(self) -> PlannerApp:
        return self.server.app

    def log_message(self, fmt, *args):            # keep the terminal quiet
        pass

    # -- safety ----------------------------------------------------------------
    def _allowed_hosts(self) -> set:
        port = self.server.server_address[1]
        return {f"{HOST}:{port}", f"localhost:{port}"}

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in self._allowed_hosts()

    def _post_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{h}" for h in self._allowed_hosts()}:
            return False
        return self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json"

    # -- responses -------------------------------------------------------------
    def _send(self, status, body: bytes, ctype: str, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, status=HTTPStatus.OK) -> None:
        self._send(status, json.dumps(obj).encode("utf-8"), "application/json")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    # -- routes ----------------------------------------------------------------
    def do_GET(self):
        if not self._host_ok():
            return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
        path = self.path.split("?", 1)[0]
        try:
            if path == "/":
                page = resources.files(__package__).joinpath("static/planner.html").read_bytes()
                return self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            if path == "/plotly.js":
                return self._send(HTTPStatus.OK, self.server.plotly_js(), "application/javascript",
                                  cache="max-age=86400")     # ~4.6 MB, fixed per install
            if path == "/api/plan":
                return self._json(self.app.plan_state())
            if path == "/api/report/status":
                return self._json(self.app.job)
            if path.startswith("/reports/"):
                f = self.app.report_file(path[len("/reports/"):])
                if f is None:
                    return self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
                return self._send(HTTPStatus.OK, f.read_bytes(), ctype)
        except (ValueError, FileNotFoundError) as e:
            return self._json(_error_body(e), HTTPStatus.BAD_REQUEST)
        self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

    def do_POST(self):
        if not (self._host_ok() and self._post_ok()):
            return self._send(HTTPStatus.FORBIDDEN, b"forbidden", "text/plain")
        try:
            body = self._body()
            if self.path == "/api/preview":
                return self._json(self.app.preview(body["plan"], body.get("previous")))
            if self.path == "/api/plan":
                self.app.save(body["plan"])
                return self._json(self.app.plan_state())
            if self.path == "/api/report":
                if not self.app.start_report(body["plan"]):
                    return self._json({"message": "A report is already running."},
                                      HTTPStatus.CONFLICT)
                return self._json(self.app.plan_state())    # the new saved plan, for the page
            if self.path == "/api/optimize/affordability":
                return self._json(self.app.affordability(body["plan"], float(body.get("target", 0.9))))
            if self.path == "/api/optimize/benefits":
                return self._json(self.app.benefits(body["plan"]))
            if self.path == "/api/reload-balances":
                self.app.reload_balances()
                return self._json(self.app.plan_state())
        except (ValueError, KeyError, TypeError, FileNotFoundError) as e:
            return self._json(_error_body(e), HTTPStatus.BAD_REQUEST)
        self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")


class PlannerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, app: PlannerApp, port: int = 0):
        super().__init__((HOST, port), _Handler)
        self.app = app
        self._plotly = None

    def plotly_js(self) -> bytes:
        if self._plotly is None:
            from plotly.offline import get_plotlyjs
            self._plotly = get_plotlyjs().encode("utf-8")
        return self._plotly

    @property
    def url(self) -> str:
        return f"http://{HOST}:{self.server_address[1]}/"


def serve(plan_path, *, port: int = 8765, open_browser: bool = True, holdings_path=None,
          out_root=None, report_paths: int | None = None, scenario_paths: int | None = None) -> None:
    """Run the planner page until Ctrl-C. ``report_paths`` / ``scenario_paths`` are the
    CLI's ``--paths`` / ``--scenario-paths`` for the report button."""
    app = PlannerApp(plan_path, holdings_path=holdings_path, out_root=out_root,
                     report_paths=report_paths, scenario_paths=scenario_paths)
    app.read_raw()                      # a missing plan fails here, not in the browser
    server = PlannerServer(app, port)
    print(f"Retirement planner: {server.url}  (editing {app.plan_path}; Ctrl-C to stop)")
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
