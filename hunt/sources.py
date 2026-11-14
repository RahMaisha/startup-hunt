"""One function per source. Funding sources yield Funding, job sources yield Job."""
from __future__ import annotations

import html
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Iterator
from urllib.parse import urljoin, urlparse

import defusedxml.ElementTree as ET  # safe against XXE / billion-laughs in feeds
from bs4 import BeautifulSoup

from .core import Client, Funding, Job, is_remote, offers_relocation, parse_amount, remote_in_text, safe_url

# --------------------------------------------------------------------- funding


def prepdb_funding(c: Client, pages: int = 5) -> Iterator[Funding]:
    """https://prepdb.fyi/?page=N  - table rows with ft-* cell classes."""
    base = "https://prepdb.fyi"
    for page in range(1, pages + 1):
        soup = BeautifulSoup(c.get(f"{base}/", params={"page": page}).text, "html.parser")
        rows = soup.select("tbody tr")
        if not rows:
            return
        for tr in rows:
            a = tr.select_one("td.ft-company a")
            cells = {k: tr.select_one(f"td.ft-{k}") for k in ("round", "lead", "date")}
            if not a or not all(cells.values()):
                continue  # one malformed row shouldn't fail the whole source
            rnd = cells["round"].get_text(" ", strip=True)
            lead = cells["lead"].get_text(" ", strip=True).removeprefix("Led by").strip()
            parts = [p.strip() for p in rnd.split("·")]
            round_name = parts[-1] if parts and parts[-1] not in ("—", "") and not parts[-1].startswith("$") else ""
            yield Funding(
                source="prepdb",
                company=a.get_text(strip=True),
                date=cells["date"].get_text(strip=True),
                round=round_name,
                amount_usd=parse_amount(rnd),
                lead_investor="" if lead == "—" else lead,
                url=base + a["href"],
            )


def techcrunch_funding(c: Client, pages: int = 1) -> Iterator[Funding]:
    """TechCrunch venture + startups RSS. Company/amount are heuristically parsed from headlines."""
    pat = re.compile(r"^(?P<co>[A-Z0-9][\w.&' -]{1,40}?)\s+(?:raises|raised|lands|secures|nabs|snags|bags|closes|gets|hits|announces)\b", re.I)
    seen = set()
    for feed in ("https://techcrunch.com/category/venture/feed/", "https://techcrunch.com/category/startups/feed/"):
        try:
            root = ET.fromstring(c.get(feed).content)
        except Exception:
            continue
        for it in root.iter("item"):
            title = html.unescape(it.findtext("title", ""))
            m = pat.match(title)
            link = it.findtext("link", "")
            if not m or link in seen or parse_amount(title) is None:
                continue
            seen.add(link)
            try:
                date = datetime.strptime(it.findtext("pubDate", ""), "%a, %d %b %Y %H:%M:%S %z").date().isoformat()
            except ValueError:
                date = ""
            yield Funding("techcrunch", m.group("co").strip(), date, "", parse_amount(title), "", link)


# ------------------------------------------------------------------------ jobs


def prepdb_jobs(c: Client, pages: int = 3) -> Iterator[Job]:
    """https://prepdb.fyi/jobs  - cards from funded startups (US/Canada)."""
    base = "https://prepdb.fyi"
    for page in range(1, pages + 1):
        soup = BeautifulSoup(c.get(f"{base}/jobs", params={"page": page}).text, "html.parser")
        cards = soup.select('li[class*="internships_card__"]')
        if not cards:
            return
        for li in cards:
            a = li.select_one('a[class*="cardLink"]')
            sub = li.select_one('p[class*="cardSub"]')
            comp = li.select_one('span[class*="internships_company"]')
            if not (a and sub and comp):
                continue
            loc = sub.get_text(" ", strip=True).split("·", 1)[-1].strip()
            text = li.get_text(" ", strip=True)
            sal = re.search(r"(?:CA)?\$\d+k[–-](?:CA)?\$\d+k/yr|\$\d+k/yr", text)
            yield Job(
                "prepdb_jobs", comp.get_text(strip=True), a.get_text(strip=True), loc,
                remote=is_remote(loc, a.get_text()), relocation=offers_relocation(text),
                salary=sal.group(0) if sal else "", url=base + a["href"], desc=text,
                tags=[s.get_text(strip=True) for s in li.select('span[class*="skill"]')],
            )


def remoteok(c: Client, **_) -> Iterator[Job]:
    """Public JSON API (please credit remoteok.com if you republish)."""
    for j in c.get("https://remoteok.com/api").json():
        if "position" not in j:
            continue
        desc = BeautifulSoup(j.get("description", ""), "html.parser").get_text(" ")
        sal = f"${j['salary_min']:,}-${j['salary_max']:,}" if j.get("salary_min") else ""
        yield Job("remoteok", j.get("company", ""), j["position"], j.get("location", "") or "Remote",
                  True, offers_relocation(desc), sal, (j.get("date") or "")[:10], j.get("url", ""), j.get("tags", []), desc=desc)


def weworkremotely(c: Client, **_) -> Iterator[Job]:
    """RSS feed: titles look like 'Company: Job title'."""
    root = ET.fromstring(c.get("https://weworkremotely.com/remote-jobs.rss").content)
    for it in root.iter("item"):
        title = html.unescape(it.findtext("title", ""))
        company, _, role = title.partition(": ")
        desc = BeautifulSoup(html.unescape(it.findtext("description", "")), "html.parser").get_text(" ")
        try:
            date = datetime.strptime(it.findtext("pubDate", ""), "%a, %d %b %Y %H:%M:%S %z").date().isoformat()
        except ValueError:
            date = ""
        yield Job("weworkremotely", company, role or title, it.findtext("region", "") or "Remote",
                  True, offers_relocation(desc), "", date, it.findtext("link", ""), desc=desc)


def hn_hiring(c: Client, **_) -> Iterator[Job]:
    """Last 3 'Ask HN: Who is hiring?' threads via the official Algolia HN API.
    Top-level comments are 'Company | Role | Location | REMOTE | ...'."""
    api = "https://hn.algolia.com/api/v1"
    hits = c.get(f"{api}/search_by_date", params={"query": "Ask HN: Who is hiring?", "tags": "story,author_whoishiring", "hitsPerPage": 8}).json()["hits"]
    stories = [h for h in hits if "who is hiring" in h["title"].lower()][:3]
    for story in stories:
        for page in range(0, 10):
            data = c.get(f"{api}/search", params={"tags": f"comment,story_{story['objectID']}", "hitsPerPage": 1000, "page": page}).json()
            for h in data["hits"]:
                if str(h.get("parent_id")) != str(story["objectID"]) or not h.get("comment_text"):
                    continue
                text = BeautifulSoup(h["comment_text"], "html.parser").get_text("\n")
                head = [p.strip() for p in text.split("\n", 1)[0].split("|")]
                yield Job("hn_hiring", head[0], head[1] if len(head) > 1 else "", head[2] if len(head) > 2 else "",
                          is_remote(text[:400]), offers_relocation(text), "", h["created_at"][:10],
                          f"https://news.ycombinator.com/item?id={h['objectID']}", desc=text)
            if page + 1 >= data["nbPages"]:
                break


def _txt(h: str) -> str:
    return BeautifulSoup(h or "", "html.parser").get_text(" ")


def _iso(ts) -> str:
    """epoch seconds or ISO string -> YYYY-MM-DD; missing/zero -> "" (not 1970-01-01)."""
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts).date().isoformat() if ts > 0 else ""
    return str(ts or "")[:10]


def arbeitnow(c: Client, pages: int = 3) -> Iterator[Job]:
    """Public API; strong on relocation / visa-sponsorship roles (Europe-heavy)."""
    url = "https://arbeitnow.com/api/job-board-api"
    for _ in range(pages):
        d = c.get(url).json()
        for j in d.get("data", []):
            text = _txt(j.get("description", ""))
            yield Job("arbeitnow", j.get("company_name", ""), j.get("title", ""), j.get("location", ""),
                      bool(j.get("remote")), offers_relocation(text, " ".join(j.get("tags", []))),
                      "", _iso(j.get("created_at")), j.get("url", ""), j.get("tags", []), desc=text)
        url = (d.get("links") or {}).get("next")
        if not url:
            break


def himalayas(c: Client, pages: int = 3) -> Iterator[Job]:
    """Public API with cursor pagination."""
    cursor = None
    for _ in range(min(max(pages, 1) * 8, 40)):  # API returns only 20 jobs per page
        d = c.get("https://himalayas.app/jobs/api", params={"limit": 100, **({"cursor": cursor} if cursor else {})}).json()
        for j in d.get("jobs", []):
            sal = f"{j.get('currency', '$')}{j['minSalary']:,}-{j['maxSalary']:,}" if j.get("minSalary") and j.get("maxSalary") else ""
            yield Job("himalayas", j.get("companyName", ""), j.get("title", ""),
                      ", ".join(j.get("locationRestrictions") or []) or "Worldwide", True,
                      offers_relocation(_txt(j.get("description", ""))), sal, _iso(j.get("pubDate")),
                      j.get("applicationLink") or j.get("guid", ""), j.get("categories", []) or [],
                      desc=_txt(j.get("description", "")))
        cursor = d.get("nextCursor")
        if not cursor:
            break


def jobicy(c: Client, **_) -> Iterator[Job]:
    """Public API. Jobicy asks for credit + link-back if you republish."""
    for j in c.get("https://jobicy.com/api/v2/remote-jobs", params={"count": 100}).json().get("jobs", []):
        geo = j.get("jobGeo", "")
        yield Job("jobicy", j.get("companyName", ""), j.get("jobTitle", ""), geo, True,
                  offers_relocation(_txt(j.get("jobDescription", ""))),
                  f"{j.get('salaryMin', '')}-{j.get('salaryMax', '')} {j.get('salaryCurrency', '')}".strip("- "),
                  _iso(j.get("pubDate")), j.get("url", ""), j.get("jobIndustry", []) or [],
                  desc=_txt(j.get("jobDescription", "")))


def workingnomads(c: Client, **_) -> Iterator[Job]:
    for j in c.get("https://www.workingnomads.com/api/exposed_jobs/").json():
        yield Job("workingnomads", j.get("company_name", ""), j.get("title", ""), j.get("location", "") or "Remote",
                  True, offers_relocation(_txt(j.get("description", ""))), "", _iso(j.get("pub_date")),
                  j.get("url", ""), [t for t in (j.get("tags") or "").split(",") if t],
                  desc=_txt(j.get("description", "")))


# ---- per-company career boards (public ATS APIs): used for the funded companies ----

ATS_BOARDS = ["greenhouse", "lever", "ashby", "workable", "recruitee", "rippling"]


def _slugs(company: str, website: str = "") -> list[str]:
    """Candidate board names. The company's own domain (serval.com -> serval) is the best guess."""
    out = []
    if website:
        host = urlparse(website if "//" in website else "//" + website).hostname or ""
        parts = host.removeprefix("www.").split(".")
        if parts and parts[0]:
            out.append(parts[0])
    base = re.sub(r"[^a-z0-9 -]", "", company.lower()).strip()
    out += [base.replace(" ", ""), base.replace(" ", "-"), base.split(" ")[0] if base else ""]
    stripped = re.sub(r"\b(ai|labs|hq|inc|technologies|app|io)\b", "", base).strip()
    out += [stripped.replace(" ", ""), stripped.replace(" ", "-")]
    return list(dict.fromkeys(x for x in out if len(x) > 1))


def _first_ok(c: Client, url: str):
    try:
        return c.get(url).json()
    except Exception:
        return None


def prepdb_website(c: Client, startup_url: str) -> str:
    """Company website from a prepdb.fyi startup page (first external, non-social link)."""
    try:
        soup = BeautifulSoup(c.get(startup_url).text, "html.parser")
    except Exception:
        return ""
    # compare real hostnames, not substrings ("dropbox.com" contains "x.com"!)
    skip_domains = ("prepdb.fyi", "cloudfront.net", "linkedin.com", "discord.gg", "discord.com", "crunchbase.com",
                    "startups.gallery", "twitter.com", "x.com", "github.com", "youtube.com", "facebook.com",
                    "instagram.com", "medium.com")
    for a in soup.find_all("a", href=True):
        h = a["href"]
        host = (urlparse(h).hostname or "").lower() if h.startswith("http") else ""
        if not host:
            continue
        if any(host == d or host.endswith("." + d) for d in skip_domains) or host.startswith("jobs."):
            continue  # social links and VC portfolio job boards (jobs.bvp.com ...) aren't the company site
        return h
    return ""


_BOARD_LINKS = {
    "greenhouse": re.compile(r"(?:job-)?boards\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]{2,})", re.I),
    "lever": re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]{2,})", re.I),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]{2,})", re.I),
    "workable": re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]{2,})", re.I),
    "recruitee": re.compile(r"([a-z0-9-]{2,})\.recruitee\.com", re.I),
    "rippling": re.compile(r"ats\.rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]{2,})/jobs", re.I),
}
_NOT_SLUGS = {"embed", "api", "v1", "www", "static", "assets", "careers", "jobs", "widget"}


def discover_boards(c: Client, website: str, boards: list[str]) -> list[tuple[str, str]]:
    """Read the company's own homepage / careers page and pull out the exact job-board link
    (e.g. jobs.ashbyhq.com/whop). Far more reliable than guessing names."""
    if not website:
        return []
    base = website.rstrip("/")
    found: list[tuple[str, str]] = []
    for path in ("/careers", "/jobs", ""):
        try:
            text = c.get(base + path).text
        except Exception:
            continue
        for board in boards:
            for m in _BOARD_LINKS[board].finditer(text):
                slug = m.group(1).strip("/")
                if slug.lower() not in _NOT_SLUGS and (board, slug) not in found:
                    found.append((board, slug))
        if found:
            break
    return found


def _board_jobs(c: Client, board: str, slug: str, company: str) -> list[Job]:
    """Fetch one specific career board. Empty list when the board doesn't exist or has no jobs."""
    out: list[Job] = []
    if board == "greenhouse":
        d = _first_ok(c, f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true")
        for j in (d or {}).get("jobs", []) if isinstance(d, dict) else []:
            loc = (j.get("location") or {}).get("name", "")
            text = _txt(html.unescape(j.get("content", "")))
            out.append(Job("greenhouse", company, j["title"], loc, is_remote(loc, j["title"]) or remote_in_text(text),
                           offers_relocation(text), "", _iso(j.get("updated_at")), j.get("absolute_url", ""), desc=text))
    elif board == "lever":
        d = _first_ok(c, f"https://api.lever.co/v0/postings/{slug}?mode=json")
        for j in d if isinstance(d, list) else []:
            loc = (j.get("categories") or {}).get("location", "") or ""
            text = j.get("descriptionPlain", "")
            out.append(Job("lever", company, j["text"], loc,
                           j.get("workplaceType") == "remote" or is_remote(loc)
                           or (j.get("workplaceType") not in ("hybrid", "on-site") and remote_in_text(text)),
                           offers_relocation(text), "", _iso((j.get("createdAt") or 0) / 1000), j.get("hostedUrl", ""), desc=text))
    elif board == "ashby":
        d = _first_ok(c, f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
        for j in (d or {}).get("jobs", []) if isinstance(d, dict) else []:
            loc = j.get("location", "")
            out.append(Job("ashby", company, j["title"], loc,
                           bool(j.get("isRemote")) or j.get("workplaceType") == "Remote" or is_remote(loc)
                           or (j.get("workplaceType") not in ("Hybrid", "OnSite") and remote_in_text(j.get("descriptionPlain", ""))),
                           offers_relocation(j.get("descriptionPlain", "")), "", _iso(j.get("publishedAt")),
                           j.get("jobUrl", ""), desc=j.get("descriptionPlain", "")))
    elif board == "workable":
        d = _first_ok(c, f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true")
        for j in (d or {}).get("jobs", []) if isinstance(d, dict) else []:
            loc = ", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x)
            text = _txt(j.get("description", ""))
            out.append(Job("workable", company, j.get("title", ""), loc, bool(j.get("telecommuting")) or is_remote(loc) or remote_in_text(text),
                           offers_relocation(text), "", _iso(j.get("published_on")), j.get("url") or j.get("shortlink", ""), desc=text))
    elif board == "recruitee":
        d = _first_ok(c, f"https://{slug}.recruitee.com/api/offers/")
        for j in (d or {}).get("offers", []) if isinstance(d, dict) else []:
            loc = j.get("location", "") or ""
            text = _txt(j.get("description", ""))
            out.append(Job("recruitee", company, j.get("title", ""), loc, bool(j.get("remote")) or is_remote(loc) or remote_in_text(text),
                           offers_relocation(text), "", _iso(j.get("published_at")), j.get("careers_url", ""), desc=text))
    elif board == "rippling":
        d = _first_ok(c, f"https://ats.rippling.com/api/v2/board/{slug}/jobs")
        for j in (d or {}).get("items", []) if isinstance(d, dict) else []:
            locs = j.get("locations") or []
            loc = ", ".join(x.get("name", "") for x in locs if x.get("name"))
            kinds = {str(x.get("workplaceType", "")).upper() for x in locs}
            out.append(Job("rippling", company, j.get("name", "").strip(), loc,
                           "REMOTE" in kinds or is_remote(loc), False, "", "", j.get("url", "")))
    return out


def ats_jobs(c: Client, company: str, website: str = "", boards: list[str] | None = None,
             site: Client | None = None) -> Iterator[Job]:
    """1) follow the job-board link on the company's own site, 2) otherwise guess board names.
    `site` is the client used for company websites (defaults to `c`)."""
    boards = boards if boards is not None else ATS_BOARDS
    for board, slug in discover_boards(site or c, website, boards):
        got = _board_jobs(c, board, slug, company)
        if got:
            yield from got
            return
    for slug in _slugs(company, website):
        for board in boards:
            got = _board_jobs(c, board, slug, company)
            if got:
                yield from got
                return


# ---- user-supplied job feeds (RSS / Atom) ----


def _feed_date(s: str) -> str:
    s = (s or "").strip()
    try:
        return parsedate_to_datetime(s).date().isoformat()  # RSS: 'Mon, 02 Nov 2026 10:00:00 +0000'
    except (TypeError, ValueError):
        return s[:10]  # Atom: '2026-11-02T10:00:00Z'


def custom_feed(c: Client, url: str) -> Iterator[Job]:
    """Generic RSS/Atom job feed added by the user. Company is parsed from the title
    ('Role at Company', 'Company: Role') or the feed's author field, else the site name."""
    # Follow redirects by hand so every hop is re-checked (a public URL can't bounce us to localhost)
    hop = url
    for _ in range(5):
        if not safe_url(hop):
            raise ValueError("URL must be a public http(s) address")
        r = c.get(hop, allow_redirects=False)
        if not getattr(r, "is_redirect", False):
            break
        hop = urljoin(hop, r.headers.get("Location", ""))
    else:
        raise ValueError("too many redirects")
    root = ET.fromstring(r.content)
    host = urlparse(url).netloc.removeprefix("www.")

    def child(el, *names):
        for ch in el:
            if ch.tag.split("}")[-1] in names:
                return ch
        return None

    def t(el, *names):
        ch = child(el, *names)
        return html.unescape((ch.text or "") if ch is not None else "").strip()

    for it in root.iter():
        if it.tag.split("}")[-1] not in ("item", "entry"):
            continue
        title = t(it, "title")
        link_el = child(it, "link")
        link = ((link_el.get("href") or link_el.text or "") if link_el is not None else "").strip()
        desc = _txt(t(it, "description", "summary", "content", "encoded"))
        loc = t(it, "location", "region")
        author = t(it, "creator", "author", "company")
        if " at " in title:
            role, _, company = title.rpartition(" at ")
        elif ": " in title:
            company, _, role = title.partition(": ")
        else:
            role, company = title, author or host
        yield Job(f"feed:{host}", company.strip(), role.strip(), loc, is_remote(title, desc[:500], loc),
                  offers_relocation(desc), "", _feed_date(t(it, "pubDate", "published", "updated")), link, desc=desc)


FUNDING_SOURCES = {"prepdb": prepdb_funding, "techcrunch": techcrunch_funding}
JOB_SOURCES = {"prepdb_jobs": prepdb_jobs, "remoteok": remoteok, "weworkremotely": weworkremotely,
               "hn_hiring": hn_hiring, "arbeitnow": arbeitnow, "himalayas": himalayas,
               "jobicy": jobicy, "workingnomads": workingnomads}


SOURCE_INFO = {
    "funding": {
        "prepdb": ("prepdb.fyi", "Latest funding rounds table (amount, round, lead investor)"),
        "techcrunch": ("TechCrunch Venture", "Funding news RSS; company/amount parsed from headlines (approximate)"),
    },
    "jobs": {
        "prepdb_jobs": ("prepdb.fyi jobs", "New-grad / early-career roles at funded startups (US/Canada)"),
        "remoteok": ("RemoteOK", "Remote jobs, public API"),
        "weworkremotely": ("We Work Remotely", "Remote jobs, RSS"),
        "hn_hiring": ("HN: Who is hiring", "Last 3 monthly threads, official HN API"),
        "arbeitnow": ("Arbeitnow", "Europe-heavy, many relocation / visa-sponsored roles"),
        "himalayas": ("Himalayas", "Remote jobs, public API (scans many pages)"),
        "jobicy": ("Jobicy", "Remote jobs, public API"),
        "workingnomads": ("Working Nomads", "Remote jobs, public API"),
    },
    "ats": {
        "greenhouse": ("Greenhouse boards", "Each funded company's own Greenhouse job board"),
        "lever": ("Lever boards", "Each funded company's own Lever job board"),
        "ashby": ("Ashby boards", "Each funded company's own Ashby job board"),
        "workable": ("Workable boards", "Each funded company's own Workable job board"),
        "recruitee": ("Recruitee boards", "Each funded company's own Recruitee job board"),
        "rippling": ("Rippling boards", "Each funded company's own Rippling job board"),
    },
}
