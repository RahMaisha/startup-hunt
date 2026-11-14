"""The whole run (scrape -> filter -> match), shared by the CLI and the web UI."""
from __future__ import annotations

import csv
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from .core import EXP_BUCKET, Client, Funding, csv_safe, exp_bucket_matches, norm, skill_in
from .sources import (ATS_BOARDS, FUNDING_SOURCES, JOB_SOURCES, SOURCE_INFO, ats_jobs, custom_feed,
                      prepdb_website)

DEFAULTS = dict(
    min_amount=20.0, max_amount=None, days=90, pages=3, delay=1.0,
    funding=list(FUNDING_SOURCES), jobs=list(JOB_SOURCES), ats=list(ATS_BOARDS),
    custom_feeds=[],   # extra RSS/Atom job feed URLs added by the user
    track=[],          # extra company names to look up on career boards, funded or not
    rounds=[], investors=[], companies=[],
    # job filters (remote-friendly is ALWAYS required unless allow_non_remote)
    allow_non_remote=False, relocation_only=False,
    roles=[], exclude_roles=[], locations=[], job_days=None,
    experience=[], keep_unknown_exp=False, skills=[], all_skills=False,
    no_ats=False, only_matches=False, use_cache=True,
)
CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"  # career-board lookups, 6h TTL
_LISTS = ("funding", "jobs", "ats", "custom_feeds", "track", "rounds", "investors", "companies",
          "roles", "exclude_roles", "locations", "experience", "skills")


def _has(text: str, needles) -> bool:
    t = (text or "").lower()
    return any(n.lower() in t for n in needles)


def _list(v, split_commas=True) -> list[str]:
    if isinstance(v, str):
        v = v.replace(",", "\n").split("\n") if split_commas else v.split("\n")
    return [x.strip() for x in v or [] if x and x.strip()]


def write_csv_json(path: Path, rows: list[dict]):
    """Always (over)writes both files, so an empty run never leaves the previous run's results behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows({k: csv_safe(v) for k, v in r.items()} for r in rows)
    path.with_suffix(".json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def _num(p: dict, key: str, lo: float, hi: float, kind=float, optional=False):
    v = p.get(key)
    if v in (None, "") and optional:
        return None
    try:
        x = kind(float(v))
    except (TypeError, ValueError):
        raise ValueError(f"'{key}' must be a number, got {v!r}") from None
    return min(max(x, lo), hi)


def clean_params(params: dict, log: Callable[[str], None] = print) -> dict:
    """Validate + clamp everything coming from the CLI or the web UI (never trust request bodies)."""
    p = {**DEFAULTS, **{k: v for k, v in (params or {}).items() if k in DEFAULTS and v is not None}}
    for k in _LISTS:
        p[k] = [str(x) for x in _list(p[k] if isinstance(p[k], (list, tuple, str)) else [],
                                      split_commas=(k != "custom_feeds"))]
    for k in ("allow_non_remote", "relocation_only", "keep_unknown_exp", "all_skills", "no_ats",
              "only_matches", "use_cache"):
        p[k] = bool(p[k])
    p["min_amount"] = _num(p, "min_amount", 0, 1e6)
    p["max_amount"] = _num(p, "max_amount", 0, 1e6, optional=True)
    p["days"] = _num(p, "days", 1, 3650, int)
    p["pages"] = _num(p, "pages", 1, 30, int)
    p["delay"] = _num(p, "delay", 0.5, 10)          # never hammer sites, whatever the request says
    p["job_days"] = _num(p, "job_days", 1, 3650, int, optional=True)
    bad = [b for b in p["experience"] if not EXP_BUCKET.match(b)]
    if bad:
        log(f"Ignoring invalid experience range(s): {', '.join(bad)} (use e.g. 0, 1-2, 2-3, 4+)")
    p["experience"] = [b for b in p["experience"] if EXP_BUCKET.match(b)]
    p["funding"] = [n for n in p["funding"] if n in FUNDING_SOURCES]
    p["jobs"] = [n for n in p["jobs"] if n in JOB_SOURCES]
    p["ats"] = [] if p["no_ats"] else [n for n in p["ats"] if n in ATS_BOARDS]
    p["custom_feeds"] = [u for u in p["custom_feeds"] if re.match(r"^https?://", u, re.I)][:10]
    p["track"] = p["track"][:50]
    return p


def _title_key(j) -> tuple[str, str]:
    return norm(j.company), re.sub(r"\W+", " ", j.title.lower()).strip()


def dedupe(jobs: list) -> list:
    """Same job seen twice: identical URL, or a job-board copy of a posting we also have straight
    from the company's career board (the direct career-board link wins)."""
    ats = set(ATS_BOARDS)
    direct = {_title_key(j) for j in jobs if j.source in ats}
    out, urls = [], set()
    for j in jobs:
        if j.url and j.url in urls:
            continue
        if j.source not in ats and _title_key(j) in direct:
            continue
        urls.add(j.url)
        out.append(j)
    return out


def _collect(label, name, fn, client, pages, log, stats):
    try:
        rows = list(fn(client, pages=pages))
        log(f"  {label}: {len(rows)} rows")
        stats[name] = {"label": label, "rows": len(rows), "error": None}
        return rows
    except Exception as e:  # one broken source must not kill the run
        log(f"  {label}: FAILED ({type(e).__name__}: {e})")
        stats[name] = {"label": label, "rows": 0, "error": f"{type(e).__name__}: {e}"[:160]}
        return []


def _links(company: str, website: str, prepdb_url: str) -> list[dict]:
    q = quote(company)
    out = [{"label": "LinkedIn (remote)", "url": f"https://www.linkedin.com/jobs/search/?keywords={q}&f_WT=2"},
           {"label": "Google careers", "url": f"https://www.google.com/search?q={quote(company + ' careers remote jobs')}"},
           {"label": "Indeed (remote)", "url": f"https://www.indeed.com/jobs?q={q}&l=Remote"}]
    if website:
        out.insert(0, {"label": "Website", "url": website})
    if prepdb_url:
        out.append({"label": "prepdb page", "url": prepdb_url})
    return out


def run(params: dict, log: Callable[[str], None] = print, out_dir: str | None = "output") -> dict:
    p = clean_params(params, log)

    stats: dict[str, dict] = {}
    client = Client(delay=float(p["delay"]))
    pages = int(p["pages"])

    log("Funding sources:")
    funding: list[Funding] = []
    for n in p["funding"]:
        funding += _collect(SOURCE_INFO["funding"][n][0], n, FUNDING_SOURCES[n], client, pages, log, stats)

    log("Job sources:")
    jobs = []
    for n in p["jobs"]:
        jobs += _collect(SOURCE_INFO["jobs"][n][0], n, JOB_SOURCES[n], client, pages, log, stats)
    for url in p["custom_feeds"]:
        jobs += _collect(f"Custom feed {url[:50]}", "feed:" + url,
                         lambda c, pages, u=url: custom_feed(c, u), client, pages, log, stats)

    cutoff = (date.today() - timedelta(days=int(p["days"]))).isoformat()
    lo = float(p["min_amount"]) * 1e6
    hi = float(p["max_amount"]) * 1e6 if p["max_amount"] not in (None, "") else None
    big = [f for f in funding
           if f.date >= cutoff and (f.amount_usd or 0) >= lo and (hi is None or (f.amount_usd or 0) <= hi)
           and (not p["rounds"] or _has(f.round, p["rounds"]))
           and (not p["investors"] or _has(f.lead_investor, p["investors"]))
           and (not p["companies"] or _has(f.company, p["companies"]))]
    big.sort(key=lambda f: (f.date, f.amount_usd or 0), reverse=True)
    log(f"{len(big)} funding rounds match the funding filters")

    # companies the user asked to track, even if they aren't in the funding list
    known = {norm(f.company) for f in big}
    for name in p["track"]:
        if norm(name) not in known:
            big.append(Funding(source="manual", company=name))
            known.add(norm(name))

    websites: dict[str, str] = {}
    if CACHE_DIR.exists():  # drop expired cache entries so the folder doesn't grow forever
        cutoff_t = time.time() - 6 * 3600
        for f in CACHE_DIR.glob("*.json"):
            try:
                if f.stat().st_mtime < cutoff_t:
                    f.unlink()
            except OSError:
                pass
    if p["ats"] and big:
        names = list(dict.fromkeys(f.company for f in big))
        log(f"Career boards ({', '.join(p['ats'])}) for {len(names)} companies:")
        local = threading.local()
        by_name = {f.company: f for f in big}

        def work(name: str):
            if not hasattr(local, "c"):
                cd = str(CACHE_DIR) if p["use_cache"] else None
                local.c = Client(delay=0.15, cache_dir=cd)
                local.pd = Client(delay=0.4, cache_dir=cd)
                local.site = Client(delay=0.3, cache_dir=cd)
            f = by_name[name]
            site = prepdb_website(local.pd, f.url) if f.source == "prepdb" and f.url else ""
            try:
                return name, site, list(ats_jobs(local.c, name, site, p["ats"], site=local.site))
            except Exception as e:
                return name, site, e

        found = 0
        with ThreadPoolExecutor(max_workers=6) as ex:
            for name, site, got in ex.map(work, names):
                websites[name] = site
                if isinstance(got, Exception):
                    log(f"  {name}: FAILED ({got})")
                    continue
                found += bool(got)
                jobs += got
                if got:
                    st = stats.setdefault(got[0].source, {"label": SOURCE_INFO["ats"].get(got[0].source, (got[0].source,))[0],
                                                          "rows": 0, "error": None})
                    st["rows"] += len(got)
        log(f"  boards found for {found}/{len(names)} companies")

    jcut = (date.today() - timedelta(days=int(p["job_days"]))).isoformat() if p["job_days"] else ""
    stages: list[tuple[str, Callable]] = []
    if not p["allow_non_remote"]:
        stages.append(("remote-friendly", lambda j: j.remote))
    if p["relocation_only"]:
        stages.append(("mentions relocation / visa", lambda j: j.relocation))
    if jcut:
        stages.append((f"posted in last {p['job_days']}d", lambda j: not j.date or j.date >= jcut))
    if p["roles"]:
        stages.append(("role keyword", lambda j: _has(j.title + " " + " ".join(j.tags), p["roles"])))
    if p["exclude_roles"]:
        stages.append(("not excluded role", lambda j: not _has(j.title, p["exclude_roles"])))
    if p["locations"]:
        stages.append(("location", lambda j: _has(j.location, p["locations"])))
    if p["experience"]:
        stages.append(("experience range", lambda j: (p["keep_unknown_exp"] if j.exp_min is None else
                       any(exp_bucket_matches(j.exp_min, j.exp_max, b) for b in p["experience"]))))
    if p["skills"]:
        test = all if p["all_skills"] else any
        stages.append(("skills", lambda j: test(skill_in(s, f"{j.title} {j.desc} {' '.join(j.tags)}") for s in p["skills"])))

    names_in_play = {norm(f.company) for f in big}
    pool = [j for j in jobs if norm(j.company) in names_in_play]
    funnel = [{"label": "jobs scanned (all sources)", "count": len(jobs)},
              {"label": "at your funded / tracked companies", "count": len(pool)}]
    pool = dedupe(pool)
    funnel.append({"label": "after removing duplicates", "count": len(pool)})
    for label, fn in stages:
        pool = [j for j in pool if fn(j)]
        funnel.append({"label": label, "count": len(pool)})

    by_co: dict[str, list] = {}
    for j in pool:
        by_co.setdefault(norm(j.company), []).append(j)

    companies, matched, seen = [], [], set()
    for f in big:
        key = norm(f.company)
        if key in seen:
            continue
        seen.add(key)
        js = by_co.get(key, [])
        if p["only_matches"] and not js:
            continue
        site = websites.get(f.company, "") or f.website
        companies.append({**f.to_dict(), "amount_usd": int(f.amount_usd or 0), "website": site,
                          "matching_jobs": len(js), "relocation_jobs": sum(j.relocation for j in js),
                          "links": _links(f.company, site, f.url), "jobs": [j.to_dict() for j in js]})
        matched += [{"funded_company": f.company, "funding_date": f.date, **j.to_dict()} for j in js]

    if out_dir:
        out = Path(out_dir)
        write_csv_json(out / "recent_funding.csv", [f.to_dict() for f in big])
        write_csv_json(out / "funded_companies_summary.csv",
                       [{k: v for k, v in c.items() if k not in ("jobs", "links")}
                        | {"job_urls": " | ".join(j["url"] for j in c["jobs"][:5]),
                           "linkedin_search": c["links"][0 if not c["website"] else 1]["url"]} for c in companies])
        write_csv_json(out / "matched_remote_jobs.csv", matched)

    with_jobs = sum(1 for c in companies if c["matching_jobs"])
    log(f"Done: {len(companies)} companies, {with_jobs} with matching remote jobs ({len(matched)} jobs).")
    return {"params": p, "companies": companies, "total_jobs_scraped": len(jobs), "funnel": funnel,
            "source_stats": stats, "funding_rounds": len(big), "companies_with_jobs": with_jobs,
            "matched_jobs": len(matched)}
