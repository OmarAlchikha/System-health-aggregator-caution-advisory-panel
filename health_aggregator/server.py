"""HTTP server tying sources → aggregator → web panel.

Stdlib-only (http.server + threads): the point of the project is the
annunciation logic, and zero dependencies means it runs on any machine with
Python 3.10+ — no pip, no node. The UI polls /api/state at 2 Hz; see README
for why polling beat SSE/websockets here.
"""

from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .cas import CautionAdvisorySystem
from .envelope import Envelope
from .sources import BusTransferSim, HarnessSim, SspcSim

STATIC_DIR = Path(__file__).parent / "static"

SCENARIOS = {
    # scenario name -> source that owns it (for the demo buttons)
    "sspc_overload": "sspc", "sspc_short": "sspc", "sspc_normal": "sspc",
    "harness_open": "harness", "harness_short_gnd": "harness",
    "harness_short_pwr": "harness", "harness_high_res": "harness",
    "harness_intermittent": "harness", "harness_repair": "harness",
    "bus_main_fail": "bus_xfr", "bus_xfer_fail": "bus_xfr",
    "bus_restore": "bus_xfr",
}


class App:
    def __init__(self, history_path: Path | None = None):
        self.cas = CautionAdvisorySystem(history_path=history_path)
        self.q: "queue.Queue[Envelope]" = queue.Queue()
        self.sources = {
            s.SOURCE_ID: s
            for s in (SspcSim(self.q), HarnessSim(self.q), BusTransferSim(self.q))
        }
        self._pump = threading.Thread(target=self._pump_loop, daemon=True)

    def start(self):
        self._pump.start()
        for s in self.sources.values():
            s.start()

    def _pump_loop(self):
        while True:
            self.cas.ingest(self.q.get())

    def inject(self, scenario: str, channel: str | None) -> bool:
        src = self.sources.get(SCENARIOS.get(scenario, ""))
        return bool(src and src.inject(scenario, channel))

    def reset(self):
        for s in self.sources.values():
            s.reset()
        self.cas.reset_latched()


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the console quiet
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/api/state":
                self._json(app.cas.snapshot())
            elif url.path == "/api/history":
                limit = int(parse_qs(url.query).get("limit", ["200"])[0])
                self._json(app.cas.history(limit))
            elif url.path in ("/", "/index.html"):
                self._file("index.html", "text/html")
            elif url.path == "/app.js":
                self._file("app.js", "application/javascript")
            elif url.path == "/style.css":
                self._file("style.css", "text/css")
            else:
                self._json({"error": "not found"}, 404)

        def _file(self, name, ctype):
            p = STATIC_DIR / name
            body = p.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", f"{ctype}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "bad json"}, 400)
            if self.path == "/api/ack":
                app.cas.acknowledge()
                self._json({"ok": True})
            elif self.path == "/api/reset":
                app.reset()
                self._json({"ok": True})
            elif self.path == "/api/inject":
                ok = app.inject(payload.get("scenario", ""), payload.get("channel"))
                self._json({"ok": ok}, 200 if ok else 400)
            else:
                self._json({"error": "not found"}, 404)

    return Handler


def main(host="127.0.0.1", port=8000, history_path: Path | None = None):
    app = App(history_path=history_path)
    app.start()
    httpd = ThreadingHTTPServer((host, port), make_handler(app))
    print(f"Health aggregator panel: http://{host}:{port}  (all sources SIMULATED)")
    httpd.serve_forever()
