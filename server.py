"""Local web UI:  python server.py   ->  http://127.0.0.1:8765

Stdlib only. Binds to localhost, runs one scrape at a time in a background thread.
"""
from __future__ import annotations

import csv
import io
import json
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from hunt.core import csv_safe
from hunt.pipeline import DEFAULTS, clean_params, run
from hunt.sources import FUNDING_SOURCES, JOB_SOURCES, SOURCE_INFO

HOST, PORT = "127.0.0.1", 8765
INDEX = Path(__file__).parent / "static" / "index.html"
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()


def _worker(job_id: str, params: dict):
    job = JOBS[job_id]
    try:
        job["result"] = run(params, log=lambda m: job["log"].append(m), out_dir="output")
        job["state"] = "done"
    except Exception as e:  # surface to the UI instead of dying silently
        job["log"].append(f"ERROR: {type(e).__name__}: {e}")
        job["state"] = "error"
    finally:
        job["t"] = time.time()


def _csv(result: dict) -> str:
    rows = [{"funded_company": c["company"], "funding_date": c["date"], "round": c["round"],
             "amount_usd": c["amount_usd"], "lead_investor": c["lead_investor"], **j}
            for c in result["companies"] for j in c["jobs"]]
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows({k: csv_safe(v) for k, v in r.items()} for r in rows)
    return buf.getvalue()


MAX_BODY = 64 * 1024          # a filter request is a few hundred bytes
KEEP_RUNS = 5                 # finished runs kept in memory for status / CSV download
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                                "form-action 'self'"),
}


def _prune_runs():
    done = sorted((j["t"], k) for k, j in JOBS.items() if j["state"] != "running")
    for _, k in done[:-KEEP_RUNS]:
        JOBS.pop(k, None)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _send(self, body: bytes, ctype="application/json", code=200, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(json.dumps(obj).encode(), code=code)

    def _host_ok(self) -> bool:
        # Blocks DNS-rebinding: a hostile site resolving its own name to 127.0.0.1 sends its own Host header
        if self.headers.get("Host", "") in ALLOWED_HOSTS:
            return True
        self._json({"error": "bad host"}, 403)
        return False

    def do_GET(self):
        if not self._host_ok():
            return
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            self._send(INDEX.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/meta":
            self._json({"funding": list(FUNDING_SOURCES), "jobs": list(JOB_SOURCES), "sources": SOURCE_INFO,
                        "defaults": DEFAULTS})
        elif path.startswith("/api/status/"):
            job = JOBS.get(path.rsplit("/", 1)[-1])
            if not job:
                return self._json({"error": "unknown job"}, 404)
            self._json({"state": job["state"], "log": job["log"], "result": job.get("result")})
        elif path.startswith("/api/csv/"):
            job = JOBS.get(path.rsplit("/", 1)[-1])
            if not job or not job.get("result"):
                return self._json({"error": "no result"}, 404)
            self._send(("﻿" + _csv(job["result"])).encode("utf-8"), "text/csv; charset=utf-8",
                       extra={"Content-Disposition": 'attachment; filename="startup-hunt-results.csv"'})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._host_ok():
            return
        if urlsplit(self.path).path != "/api/run":
            return self._json({"error": "not found"}, 404)
        # reject cross-site form posts: only accept JSON from our own page
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._json({"error": "json only"}, 415)
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            return self._json({"error": "bad length"}, 400)
        if length > MAX_BODY:
            return self._json({"error": "request too large"}, 413)
        try:
            params = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(params, dict):
                raise ValueError
        except ValueError:
            return self._json({"error": "bad json"}, 400)
        try:  # validate now so the user gets a clear message instead of a failed run
            params = clean_params(params, log=lambda m: None)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        with LOCK:
            if any(j["state"] == "running" for j in JOBS.values()):
                return self._json({"error": "a run is already in progress"}, 409)
            _prune_runs()
            job_id = uuid.uuid4().hex[:10]
            JOBS[job_id] = {"state": "running", "log": [f"Started {time.strftime('%H:%M:%S')}"], "t": time.time()}
        threading.Thread(target=_worker, args=(job_id, params), daemon=True).start()
        self._json({"id": job_id})


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"Startup Hunt UI running at {url}  (Ctrl+C to stop)", file=sys.stderr)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
