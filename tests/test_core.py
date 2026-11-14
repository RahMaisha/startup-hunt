"""Offline tests (no network): python -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hunt.core import (Job, exp_bucket_matches, is_remote, norm, offers_relocation, parse_amount,  # noqa: E402
                       parse_experience, remote_in_text, safe_url, skill_in)
from hunt import sources  # noqa: E402


class FakeResp:
    def __init__(self, text="", content=b""):
        self.text, self.content = text, content


class FakeClient:
    """Returns canned bodies by URL substring; raises for anything else."""
    def __init__(self, pages):
        self.pages = pages

    def get(self, url, **kw):
        for key, body in self.pages.items():
            if key in url:
                return FakeResp(text=body, content=body.encode())
        raise RuntimeError("404 " + url)


class Parsing(unittest.TestCase):
    def test_amount(self):
        self.assertEqual(parse_amount("$400.0M · Series C"), 4e8)
        self.assertEqual(parse_amount("$1.0B"), 1e9)
        self.assertEqual(parse_amount("raises $2.5 million"), 2.5e6)
        self.assertIsNone(parse_amount("Series A"))

    def test_experience(self):
        cases = [("Engineer", "3+ years of experience", (3, 99)), ("Engineer", "5-7 years", (5, 7)),
                 ("Engineer", "1 to 2 years", (1, 2)), ("New Grad SWE", "", (0, 1)),
                 ("Senior Engineer", "", (5, 99)), ("Staff Engineer", "", (8, 99)),
                 ("International Sales Rep", "", (None, None)), ("Engineer", "Strong leadership", (None, None))]
        for title, desc, want in cases:
            self.assertEqual(parse_experience(title, desc), want, (title, desc))

    def test_experience_buckets(self):
        self.assertTrue(exp_bucket_matches(0, 1, "0"))
        self.assertTrue(exp_bucket_matches(2, 99, "1-2"))      # "2+" overlaps 1-2
        self.assertFalse(exp_bucket_matches(5, 99, "1-2"))
        self.assertTrue(exp_bucket_matches(5, 99, "4+"))
        self.assertFalse(exp_bucket_matches(None, None, "0"))  # unknown never matches

    def test_skills_word_boundaries(self):
        self.assertTrue(skill_in("c++", "We use C++ and Rust"))
        self.assertTrue(skill_in("node.js", "Node.js backend"))
        self.assertFalse(skill_in("go", "good to know"))
        self.assertFalse(skill_in("java", "JavaScript only"))
        self.assertTrue(skill_in("machine learning", "applied Machine Learning"))

    def test_remote_and_relocation(self):
        self.assertTrue(is_remote("United States (Remote)"))
        self.assertFalse(is_remote("Berlin, Germany"))
        self.assertTrue(offers_relocation("We offer visa sponsorship and relocation"))
        self.assertTrue(remote_in_text("We are remote-first"))
        self.assertFalse(remote_in_text("hybrid, 3 days a week in the office"))
        self.assertFalse(remote_in_text("manage remote teams"))

    def test_job_hybrid_guard(self):
        j = Job("x", "Co", "Engineer (Hybrid)", "Remote or NYC", remote=True)
        self.assertFalse(j.remote)
        j = Job("x", "Co", "Engineer", "Remote - US", remote=True, desc="3+ years of Python")
        self.assertTrue(j.remote)
        self.assertEqual(j.experience, "3+")
        self.assertIn("python", j.skills)

    def test_norm(self):
        self.assertEqual(norm("Serval, Inc."), norm("serval"))
        self.assertNotEqual(norm("Hex"), norm("Hexa"))

    def test_safe_url(self):
        self.assertFalse(safe_url("http://127.0.0.1/x"))
        self.assertFalse(safe_url("http://192.168.0.5/"))
        self.assertFalse(safe_url("file:///etc/passwd"))
        self.assertFalse(safe_url("ftp://example.com/"))


class Sources(unittest.TestCase):
    def test_slugs_prefer_website_domain(self):
        s = sources._slugs("Serval AI", "https://www.serval.com/")
        self.assertEqual(s[0], "serval")
        self.assertIn("serval-ai", s)

    def test_discover_boards(self):
        html = ('<a href="https://jobs.ashbyhq.com/whop">Jobs</a> '
                '<a href="https://boards.greenhouse.io/embed/job_board?for=acme">x</a> '
                '<a href="https://ats.rippling.com/en-GB/differentai/jobs/123">y</a>')
        c = FakeClient({"/careers": html})
        got = sources.discover_boards(c, "https://example.com", sources.ATS_BOARDS)
        self.assertIn(("ashby", "whop"), got)
        self.assertIn(("greenhouse", "acme"), got)
        self.assertIn(("rippling", "differentai"), got)
        self.assertEqual(sources.discover_boards(c, "", sources.ATS_BOARDS), [])

    def test_custom_feed_rss(self):
        rss = """<rss><channel>
          <item><title>Backend Engineer at Acme</title><link>https://acme.test/1</link>
            <pubDate>Mon, 02 Nov 2026 10:00:00 +0000</pubDate>
            <description>Fully remote. 3+ years of Python.</description></item>
          <item><title>Beta: Designer</title><link>https://beta.test/2</link><description>On-site only</description></item>
        </channel></rss>"""
        orig = sources.safe_url
        sources.safe_url = lambda u: True  # skip DNS in offline test
        try:
            jobs = list(sources.custom_feed(FakeClient({"feed": rss}), "https://jobs.example.com/feed"))
        finally:
            sources.safe_url = orig
        self.assertEqual([(j.company, j.title) for j in jobs], [("Acme", "Backend Engineer"), ("Beta", "Designer")])
        self.assertTrue(jobs[0].remote)
        self.assertFalse(jobs[1].remote)
        self.assertEqual(jobs[0].date, "2026-11-02")
        self.assertEqual(jobs[0].experience, "3+")

    def test_custom_feed_rejects_private_url(self):
        with self.assertRaises(ValueError):
            list(sources.custom_feed(FakeClient({}), "http://127.0.0.1/feed"))


if __name__ == "__main__":
    unittest.main()
