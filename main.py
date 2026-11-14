"""Find recently-funded startups that hire remotely (all results are remote-friendly).

  python main.py                                   # >= $20M, last 90 days
  python main.py --experience 0 1-2 --skills python react --relocation-only
  python main.py --rounds "series a" --investor sequoia a16z --role engineer
  python server.py                                 # web UI instead of CLI
"""
from __future__ import annotations

import argparse
import sys

from hunt.pipeline import DEFAULTS, run
from hunt.sources import ATS_BOARDS, FUNDING_SOURCES, JOB_SOURCES, SOURCE_INFO


def build_params(a: argparse.Namespace) -> dict:
    """argparse namespace -> pipeline params. Note `v is not None`, not `v not in (None, False)`:
    the latter silently dropped real zeros such as --min-amount 0 (0 == False in Python)."""
    params = {k: v for k, v in vars(a).items()
              if k not in ("list_sources", "out", "no_cache") and v is not None and v is not False}
    if a.no_cache:
        params["use_cache"] = False
    return params


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list-sources", action="store_true")
    ap.add_argument("--out", default="output")
    ap.add_argument("--pages", type=int, default=DEFAULTS["pages"], help="pages per paginated source")
    ap.add_argument("--delay", type=float, default=DEFAULTS["delay"], help="seconds between requests")
    ap.add_argument("--funding", nargs="*", choices=FUNDING_SOURCES, help="funding sources (default all)")
    ap.add_argument("--jobs", nargs="*", choices=JOB_SOURCES, help="job sources (default all)")
    ap.add_argument("--ats", nargs="*", choices=ATS_BOARDS, help="company career boards to check (default all)")
    ap.add_argument("--feed", nargs="*", dest="custom_feeds", metavar="URL", help="extra RSS/Atom job feed URLs")
    ap.add_argument("--track", nargs="*", metavar="COMPANY", help="extra companies to look up on career boards")

    f = ap.add_argument_group("funding filters")
    f.add_argument("--min-amount", type=float, default=20, help="min round, USD millions (default 20)")
    f.add_argument("--max-amount", type=float, help="max round, USD millions")
    f.add_argument("--days", type=int, default=90, help="funded within N days (default 90)")
    f.add_argument("--rounds", nargs="*", metavar="ROUND", help="seed 'series a' 'series b' ...")
    f.add_argument("--investor", nargs="*", metavar="NAME", dest="investors", help="lead investor contains: sequoia a16z")
    f.add_argument("--company", nargs="*", metavar="NAME", dest="companies", help="company name contains")

    j = ap.add_argument_group("job filters (jobs must be remote-friendly)")
    j.add_argument("--experience", nargs="*", metavar="YRS", help="years of experience: 0 1-2 2-3 4+")
    j.add_argument("--keep-unknown-exp", action="store_true", help="keep jobs whose experience can't be determined")
    j.add_argument("--skills", nargs="*", metavar="SKILL", help="job mentions any of these: python react 'machine learning'")
    j.add_argument("--all-skills", action="store_true", help="require ALL --skills instead of any")
    j.add_argument("--role", nargs="*", metavar="KW", dest="roles", help="title/tags contain any: engineer backend")
    j.add_argument("--exclude-role", nargs="*", metavar="KW", dest="exclude_roles", help="drop titles containing: intern senior")
    j.add_argument("--location", nargs="*", metavar="KW", dest="locations", help="job location contains: europe canada")
    j.add_argument("--relocation-only", action="store_true", help="also require relocation/visa sponsorship")
    j.add_argument("--job-days", type=int, help="jobs posted within N days (undated jobs kept)")
    j.add_argument("--allow-non-remote", action="store_true", help="include non-remote jobs (off by default)")
    j.add_argument("--no-ats", action="store_true", help="skip Greenhouse/Lever/Ashby lookups")
    j.add_argument("--no-cache", action="store_true", help="ignore the 6-hour career-board cache (fresh but slower)")
    j.add_argument("--only-matches", action="store_true", help="only list companies with >=1 matching job")
    a = ap.parse_args()

    if a.list_sources:
        titles = {"funding": "Funding sources (--funding)", "jobs": "Job boards (--jobs)",
                  "ats": "Company career boards (--ats)"}
        for group, items in SOURCE_INFO.items():
            print()
            print(titles[group])
            for key, (label, desc, site) in items.items():
                print(f"  {key:<15} {label:<20} {site}")
        print()
        print("Also: each company's own website (to find its job board), plus any feed you add with --feed URL.")
        return

    try:
        res = run(build_params(a), log=lambda m: print(m, file=sys.stderr), out_dir=a.out)
    except ValueError as e:
        ap.error(str(e))
    print(f"Files in ./{a.out}/ ({res['funding_rounds']} rounds, {res['matched_jobs']} matching jobs)", file=sys.stderr)


if __name__ == "__main__":
    main()
