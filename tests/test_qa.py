"""Regression tests for the bugs found in the SQA pass (offline, no network)."""
import argparse
import http.client
import json
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import main  # noqa: E402
import server  # noqa: E402
from hunt import sources  # noqa: E402
from hunt.core import Job, csv_safe, exp_bucket_matches, norm, parse_experience, safe_href  # noqa: E402
from hunt.pipeline import clean_params, dedupe, write_csv_json  # noqa: E402


class Resp:
    def __init__(self, text="", status=200, location=None):
        self.text, self.content, self.status_code = text, text.encode(), status
        self.is_redirect = location is not None
        self.headers = {"Location": location} if location else {}


class CliAndParams(unittest.TestCase):
    def test_cli_keeps_zero_values(self):
        ns = argparse.Namespace(list_sources=False, out="output", no_cache=False, min_amount=0.0, days=90,
                                pages=3, relocation_only=False, skills=None)
        p = main.build_params(ns)
        self.assertEqual(p["min_amount"], 0.0)          # was silently replaced by the $20M default
        self.assertNotIn("relocation_only", p)
        self.assertNotIn("skills", p)

    def test_clean_params_clamps_and_validates(self):
        p = clean_params({"pages": 9999, "delay": 0, "days": "30", "experience": ["0", "1-2", "abc", "4+"]},
                         log=lambda m: None)
        self.assertEqual(p["pages"], 30)
        self.assertEqual(p["delay"], 0.5)                # can't be used to hammer sites
        self.assertEqual(p["days"], 30)
        self.assertEqual(p["experience"], ["0", "1-2", "4+"])
        with self.assertRaises(ValueError):
            clean_params({"min_amount": "lots"}, log=lambda m: None)
        p = clean_params({"custom_feeds": ["javascript:alert(1)", "https://ok.example/feed"], "skills": {"x": 1}},
                         log=lambda m: None)
        self.assertEqual(p["custom_feeds"], ["https://ok.example/feed"])
        self.assertEqual(p["skills"], [])

    def test_invalid_bucket_never_crashes(self):
        self.assertFalse(exp_bucket_matches(2, 3, "abc"))
        self.assertTrue(exp_bucket_matches(2, 3, " 2 - 3 "))


class DataQuality(unittest.TestCase):
    def test_null_fields_dont_crash(self):
        j = Job("x", None, None, None, True, url=None, tags=[None, "Python"])
        self.assertEqual((j.company, j.title, j.location, j.url), ("", "", "", ""))
        self.assertEqual(j.tags, ["Python"])

    def test_unsafe_urls_dropped(self):
        self.assertEqual(Job("x", "Co", "T", url="javascript:alert(1)").url, "")
        self.assertEqual(safe_href("https://ok.example/a"), "https://ok.example/a")
        self.assertEqual(safe_href("data:text/html,x"), "")

    def test_norm_never_empty(self):
        self.assertEqual(norm("AI Labs"), "ai labs")
        self.assertNotEqual(norm("AI Labs"), norm(""))
        self.assertEqual(norm("Serval Inc"), "serval")

    def test_website_host_matching(self):
        class C:
            def get(self, url, **kw):
                return Resp('<a href="https://www.linkedin.com/x">li</a><a href="https://jobs.bvp.com/x">vc</a>'
                            '<a href="https://www.dropbox.com/">site</a>')
        self.assertEqual(sources.prepdb_website(C(), "u"), "https://www.dropbox.com/")

    def test_missing_timestamp_is_blank_not_1970(self):
        self.assertEqual(sources._iso(0), "")
        self.assertEqual(sources._iso(None), "")

    def test_experience_prefers_context(self):
        d = "We were founded 10 years ago. You have 3+ years of experience with Go."
        self.assertEqual(parse_experience("Engineer", d), (3, 99))
        self.assertEqual(parse_experience("Engineer", "Over the last 3 years we grew 10x."), (None, None))
        self.assertEqual(parse_experience("Engineer", "3+ years building backend systems"), (3, 99))
        self.assertEqual(parse_experience("Engineer", "Experience: 4 years in backend"), (4, 4))

    def test_dedupe_prefers_career_board(self):
        a = Job("ashby", "Hex", "Software Engineer", url="https://jobs.ashbyhq.com/hex/1")
        b = Job("himalayas", "Hex", "Software Engineer!", url="https://himalayas.app/x")
        c = Job("remoteok", "Hex", "Designer", url="https://remoteok.com/y")
        d = Job("jobicy", "Hex", "Product Designer", url="https://remoteok.com/y")  # same URL twice
        out = dedupe([b, a, c, d])
        self.assertEqual([j.source for j in out], ["ashby", "remoteok"])


class Outputs(unittest.TestCase):
    def test_empty_run_overwrites_old_files_and_csv_is_formula_safe(self):
        d = Path(tempfile.mkdtemp())
        write_csv_json(d / "a.csv", [{"title": "=HYPERLINK(\"http://evil\")", "n": 5}])
        text = (d / "a.csv").read_text(encoding="utf-8-sig")
        self.assertIn("'=HYPERLINK", text)
        write_csv_json(d / "a.csv", [])
        self.assertEqual((d / "a.csv").read_text(encoding="utf-8-sig"), "")
        self.assertEqual(json.loads((d / "a.json").read_text()), [])
        self.assertEqual(csv_safe(5), 5)
        self.assertEqual(csv_safe("-1"), "'-1")


class FeedSecurity(unittest.TestCase):
    def test_redirect_to_private_address_blocked(self):
        class C:
            def get(self, url, **kw):
                return Resp(status=302, location="http://127.0.0.1:8765/api/meta")
        orig = sources.safe_url
        sources.safe_url = lambda u: "127.0.0.1" not in u  # offline stand-in for the DNS check
        try:
            with self.assertRaises(ValueError):
                list(sources.custom_feed(C(), "https://public.example/feed"))
        finally:
            sources.safe_url = orig


class ServerHardening(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def req(self, method, path, body=None, host=f"127.0.0.1:{server.PORT}", ctype="application/json"):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.putrequest(method, path, skip_host=True)
        c.putheader("Host", host)
        if body is not None:
            c.putheader("Content-Type", ctype)
            c.putheader("Content-Length", str(len(body)))
        c.endheaders()
        if body:
            c.send(body)
        r = c.getresponse()
        return r.status, r.read(), dict(r.getheaders())

    def test_page_has_security_headers(self):
        status, _, h = self.req("GET", "/?utm=x")       # query strings no longer 404
        self.assertEqual(status, 200)
        self.assertIn("frame-ancestors 'none'", h["Content-Security-Policy"])
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")

    def test_dns_rebinding_host_rejected(self):
        self.assertEqual(self.req("GET", "/api/meta", host="evil.example:8765")[0], 403)

    def test_body_limits_and_validation(self):
        self.assertEqual(self.req("POST", "/api/run", b"x" * (server.MAX_BODY + 1))[0], 413)
        self.assertEqual(self.req("POST", "/api/run", b"[1,2]")[0], 400)
        self.assertEqual(self.req("POST", "/api/run", b"{}", ctype="text/plain")[0], 415)
        status, body, _ = self.req("POST", "/api/run", json.dumps({"min_amount": "lots"}).encode())
        self.assertEqual(status, 400)
        self.assertIn("min_amount", json.loads(body)["error"])

    def test_old_runs_pruned(self):
        server.JOBS.clear()
        for i in range(server.KEEP_RUNS + 3):
            server.JOBS[f"j{i}"] = {"state": "done", "log": [], "t": i}
        server._prune_runs()
        self.assertEqual(len(server.JOBS), server.KEEP_RUNS)
        self.assertIn(f"j{server.KEEP_RUNS + 2}", server.JOBS)   # newest kept
        server.JOBS.clear()


if __name__ == "__main__":
    unittest.main()
