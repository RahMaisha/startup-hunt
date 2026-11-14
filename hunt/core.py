"""Shared helpers: polite HTTP client (robots.txt aware), models, parsing."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
import time
import urllib.robotparser
from dataclasses import dataclass, field, asdict
from pathlib import Path
from urllib.parse import urlparse

import requests

USER_AGENT = "startup-hunt-scraper/1.0 (personal research; polite crawler)"


@dataclass
class Funding:
    source: str
    company: str
    date: str = ""
    round: str = ""
    amount_usd: float | None = None
    lead_investor: str = ""
    url: str = ""
    website: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class Job:
    source: str
    company: str
    title: str
    location: str = ""
    remote: bool = False
    relocation: bool = False
    salary: str = ""
    date: str = ""
    url: str = ""
    tags: list[str] = field(default_factory=list)
    desc: str = ""  # kept for filtering only; not exported
    exp_min: int | None = field(default=None, init=False)
    exp_max: int | None = field(default=None, init=False)
    skills: list[str] = field(default_factory=list, init=False)

    def __post_init__(self):
        # APIs often send null instead of "": normalise so one bad field can't crash a whole source
        for k in ("company", "title", "location", "salary", "date", "url"):
            v = getattr(self, k)
            setattr(self, k, "" if v is None else str(v).strip())
        self.tags = [str(t).strip() for t in (self.tags or []) if t is not None and str(t).strip()]
        self.url = safe_href(self.url)  # drop javascript:/data: links coming from untrusted feeds
        self.desc = str(self.desc or "")[:6000]
        self.exp_min, self.exp_max = parse_experience(self.title, self.desc)
        self.skills = detect_skills(self.title, self.desc, " ".join(self.tags))
        # "Hybrid"/"On-site" in the title or location means not remote-friendly
        # An explicit Hybrid/On-site in the TITLE always wins; in the location it only wins when the
        # location doesn't also say remote (e.g. "Remote (US)" stays remote).
        pat = r"hybrid|on-?site|in[- ]office"
        title, loc = self.title.lower(), self.location.lower()
        if self.remote and (re.search(pat, title) or (re.search(pat, loc) and "remote" not in loc)):
            self.remote = False

    @property
    def experience(self) -> str:
        if self.exp_min is None:
            return ""
        if self.exp_max is None or self.exp_max >= 50:
            return f"{self.exp_min}+"
        return str(self.exp_min) if self.exp_min == self.exp_max else f"{self.exp_min}-{self.exp_max}"

    def to_dict(self):
        d = asdict(self)
        for k in ("desc", "exp_min", "exp_max"):
            d.pop(k)
        d["tags"] = ", ".join(self.tags)
        d["skills"] = ", ".join(self.skills)
        d["experience_years"] = self.experience
        return d


class Client:
    """requests wrapper: robots.txt check, delay between calls, retries."""

    def __init__(self, delay: float = 1.0, respect_robots: bool = True,
                 cache_dir: str | None = None, ttl: int = 6 * 3600):
        self.s = requests.Session()
        self.s.headers["User-Agent"] = USER_AGENT
        self.delay = delay
        self.respect_robots = respect_robots
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last = 0.0
        # Optional on-disk cache of 200s and 404s (so repeated runs don't re-guess career boards)
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.ttl = ttl
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, url: str, kw: dict) -> Path:
        key = hashlib.sha1((url + json.dumps(kw.get("params") or {}, sort_keys=True)).encode()).hexdigest()
        return self.cache_dir / f"{key}.json"

    def _cache_read(self, url: str, kw: dict) -> requests.Response | None:
        try:
            path = self._cache_path(url, kw)
            if time.time() - path.stat().st_mtime > self.ttl:
                return None
            d = json.loads(path.read_text(encoding="utf-8"))
            r = requests.Response()
            r.status_code, r.url, r.encoding = d["status"], url, "utf-8"
            r._content = d["body"].encode("utf-8")
            return r
        except (OSError, ValueError, KeyError):
            return None

    def _cache_write(self, url: str, kw: dict, status: int, body: str = ""):
        try:
            self._cache_path(url, kw).write_text(json.dumps({"status": status, "body": body}), encoding="utf-8")
        except OSError:
            pass

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        host = "{0.scheme}://{0.netloc}".format(urlparse(url))
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.s.get(host + "/robots.txt", timeout=15)
                rp.parse(r.text.splitlines() if r.ok else [])
            except requests.RequestException:
                rp = None
            self._robots[host] = rp
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(USER_AGENT, url)

    def get(self, url: str, **kw) -> requests.Response:
        if self.cache_dir:
            hit = self._cache_read(url, kw)
            if hit is not None:
                hit.raise_for_status()  # cached 404 -> raises just like the live call would
                return hit
        if not self.allowed(url):
            raise PermissionError(f"robots.txt disallows {url}")
        wait = self.delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            try:
                r = self.s.get(url, timeout=30, **kw)
                self._last = time.time()
                if r.status_code in (429, 503):
                    time.sleep(5 * (attempt + 1))
                    continue
                r.raise_for_status()
                if self.cache_dir:
                    self._cache_write(url, kw, r.status_code, r.text)
                return r
            except requests.RequestException as e:
                status = getattr(e.response, "status_code", None)
                if attempt == 2 or (status and 400 <= status < 500 and status != 429):
                    if self.cache_dir and status in (404, 410):
                        self._cache_write(url, kw, status)
                    raise  # 404 etc. won't fix themselves; don't waste time retrying
                time.sleep(2 * (attempt + 1))
        raise RuntimeError("unreachable")


_AMOUNT = re.compile(r"\$\s?([\d,.]+)\s?(k|m|mm|million|b|bn|billion)?", re.I)
_MULT = {"k": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}


def parse_amount(text: str) -> float | None:
    """'$400.0M' -> 4e8, '$1.2 billion' -> 1.2e9."""
    m = _AMOUNT.search(text or "")
    if not m:
        return None
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    return n * _MULT.get((m.group(2) or "").lower(), 1)


_REMOTE = re.compile(r"\bremote|work from home|anywhere|distributed|wfh\b", re.I)
_RELO = re.compile(r"relocat|visa sponsor|sponsorship (?:available|provided)|will sponsor", re.I)


def is_remote(*texts: str) -> bool:
    return any(_REMOTE.search(t or "") for t in texts)


_REMOTE_POS = re.compile(
    r"(?:fully|100%|completely|entirely)[- ]remote|remote[- ]first|remote[- ]friendly|work(?:ing)? remotely|"
    r"work from anywhere|remote[- ]eligible|open to remote|remote within|can be (?:done )?remote|"
    r"(?:this|the) (?:role|position|job) is (?:fully |100% )?remote|(?:role|position|job) (?:is )?remote\b|"
    r"location:?\s*remote|remote \((?:us|usa|u\.s\.|eu|canada|uk|global|worldwide)", re.I)
_REMOTE_NEG = re.compile(
    r"not (?:a )?remote|no remote|non[- ]remote|not (?:eligible|available) for remote|"
    r"on-?site (?:only|required|position|role)|in[- ]office (?:only|required)|hybrid|"
    r"(?:five|5|four|4|three|3) days? (?:a|per) week in (?:the |our )?office", re.I)


def remote_in_text(text: str) -> bool:
    """Description says the role is remote ('remote-first', 'fully remote'...) and doesn't say hybrid/on-site.
    Conservative on purpose: a bare 'remote' mention (e.g. 'remote teams') is not enough."""
    t = text or ""
    return bool(_REMOTE_POS.search(t)) and not _REMOTE_NEG.search(t)


def offers_relocation(*texts: str) -> bool:
    return any(_RELO.search(t or "") for t in texts)


def norm(name: str) -> str:
    """Normalise company name for cross-source matching. Never returns "" for a non-empty name
    (otherwise 'AI Labs' and every job with a blank company would all match each other)."""
    base = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", (name or "").lower())).strip()
    n = re.sub(r"\s+", " ", re.sub(r"\b(inc|llc|ltd|corp|co|technologies|labs|ai|hq)\b", "", base)).strip()
    return n or base


def safe_href(url: str) -> str:
    """Only http(s) links are kept; anything else (javascript:, data:, relative junk) becomes ""."""
    u = (url or "").strip()
    return u if re.match(r"^https?://[^\s]+$", u, re.I) else ""


def csv_safe(v):
    """Stop spreadsheet formula injection: text cells starting with = + - @ (from scraped titles) get a quote."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


# ----------------------------------------------------------- experience / skills

_YRS = re.compile(r"(\d{1,2})\s*(\+)?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*)?\+?\s*(?:years?|yrs?)\b", re.I)
_ENTRY = re.compile(r"\b(intern(ship)?|new[- ]?grad|graduate|entry[- ]level|junior|jr\.?|early[- ]career|apprentice)\b", re.I)
_SENIOR = re.compile(r"\b(senior|sr\.?|lead|staff|principal|head of|director|vp)\b", re.I)


def parse_experience(title: str, desc: str) -> tuple[int | None, int | None]:
    """Best-effort (min_years, max_years); max=99 means open-ended. (None, None) when unknown.
    Prefers a "N years" that sits next to the word experience (so "founded 10 years ago" loses)."""
    d = desc or ""
    hits = [m for m in _YRS.finditer(d) if not re.match(r"\s*(?:ago|old)\b", d[m.end():], re.I)]

    def in_context(m) -> bool:  # look only within the same sentence as the match
        after = re.split(r"[.\n;]", d[m.end():m.end() + 60], maxsplit=1)[0]
        before = re.split(r"[.\n;]", d[max(0, m.start() - 40):m.start()])[-1]
        return bool(re.search(r"experience|exp\b|professional|industry", after, re.I)
                    or re.search(r"experience", before, re.I))

    ctx = [m for m in hits if in_context(m)]
    # without the word "experience" nearby, only trust requirement-shaped numbers ("3+ years", "2-4 years")
    shaped = [m for m in hits if m.group(2) or m.group(3)]
    m = (ctx or shaped or [None])[0] or _YRS.search(title or "")
    if m and int(m.group(1)) <= 25:
        lo = int(m.group(1))
        if m.group(3):
            return lo, int(m.group(3))
        src = d if m.string is d else (title or "")
        return (lo, 99) if m.group(2) or "plus" in src[m.end():m.end() + 6].lower() else (lo, lo)
    if _ENTRY.search(title or ""):
        return 0, 1
    if re.search(r"\b(staff|principal|head of|director|vp)\b", title or "", re.I):
        return 8, 99
    if _SENIOR.search(title or ""):
        return 5, 99
    return None, None


SKILLS = ["python", "javascript", "typescript", "react", "next.js", "node.js", "java", "go", "golang", "rust",
          "c++", "c#", "ruby", "rails", "php", "swift", "kotlin", "sql", "postgres", "mongodb", "redis",
          "aws", "gcp", "azure", "docker", "kubernetes", "terraform", "linux", "graphql", "kafka",
          "machine learning", "deep learning", "llm", "pytorch", "tensorflow", "pandas", "spark",
          "django", "flask", "fastapi", "vue", "angular", "flutter", "android", "ios",
          "figma", "product management", "sales", "marketing", "devops", "security", "data science"]


def skill_in(skill: str, text: str) -> bool:
    return re.search(r"(?<![\w+#.])" + re.escape(skill.lower()) + r"(?![\w+#])", text.lower()) is not None


def detect_skills(*texts: str) -> list[str]:
    t = " ".join(texts)
    return [s for s in SKILLS if skill_in(s, t)]


EXP_BUCKET = re.compile(r"^\s*\d{1,2}\s*(?:\+|[-–]\s*\d{1,2})?\s*$")


def exp_bucket_matches(job_min: int | None, job_max: int | None, bucket: str) -> bool:
    """bucket: '0', '1-2', '2-3', '4+', '3'. Overlap test against the job's range."""
    if job_min is None or not EXP_BUCKET.match(bucket or ""):
        return False
    b = bucket.strip().lower().replace("–", "-").replace(" ", "")
    if b.endswith("+"):
        lo, hi = int(b[:-1]), 99
    elif "-" in b:
        lo, hi = (int(x) for x in b.split("-", 1))
    else:
        lo = hi = int(b)
    return job_min <= hi and (job_max if job_max is not None else job_min) >= lo


def safe_url(url: str) -> bool:
    """http(s) only, and never loopback/private/link-local hosts (user-supplied feed URLs)."""
    try:
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            return False
        for info in socket.getaddrinfo(u.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False
        return True
    except (OSError, ValueError):
        return False
