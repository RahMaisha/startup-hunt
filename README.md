# Startup Hunt

Finds **recently funded startups** and the **remote-friendly jobs** they are hiring for, filtered by
funding round, years of experience and skills. Every job returned is remote-friendly (hybrid / on-site
roles are always dropped). Runs on your own computer: a web page at http://127.0.0.1:8765, or the command line.

## Websites it scrapes

You can switch each one on or off in the **Sources** panel on the page, or with `--funding`, `--jobs`
and `--ats` on the command line. Print this list any time with `python main.py --list-sources`.

### Funding (who just raised money)
| Key | Website | What it gives | How |
|---|---|---|---|
| `prepdb` | [prepdb.fyi](https://prepdb.fyi) | Latest rounds: company, amount, round, date, lead investor, company website | HTML pages |
| `techcrunch` | [TechCrunch](https://techcrunch.com/category/venture/) (venture + startups) | Funding news; company and amount read from headlines (approximate) | RSS |

### Job boards (remote jobs from many companies)
| Key | Website | What it gives | How |
|---|---|---|---|
| `prepdb_jobs` | [prepdb.fyi/jobs](https://prepdb.fyi/jobs) | New-grad / early-career roles at funded startups (US/Canada) | HTML pages |
| `remoteok` | [RemoteOK](https://remoteok.com) | Remote jobs | public API |
| `weworkremotely` | [We Work Remotely](https://weworkremotely.com) | Remote jobs | RSS |
| `hn_hiring` | [Hacker News "Who is hiring"](https://news.ycombinator.com/submitted?id=whoishiring) | Last 3 monthly threads | official HN API |
| `arbeitnow` | [Arbeitnow](https://www.arbeitnow.com) | Europe-heavy; many relocation / visa-sponsored roles | public API |
| `himalayas` | [Himalayas](https://himalayas.app/jobs) | Remote jobs | public API |
| `jobicy` | [Jobicy](https://jobicy.com) | Remote jobs | public API |
| `workingnomads` | [Working Nomads](https://www.workingnomads.com) | Remote jobs | public API |

### Company career boards (looked up for each funded company — most of the matches come from here)
| Key | Platform | Example board |
|---|---|---|
| `greenhouse` | [Greenhouse](https://www.greenhouse.com) | `boards.greenhouse.io/<company>` |
| `lever` | [Lever](https://www.lever.co) | `jobs.lever.co/<company>` |
| `ashby` | [Ashby](https://www.ashbyhq.com) | `jobs.ashbyhq.com/<company>` |
| `workable` | [Workable](https://www.workable.com) | `apply.workable.com/<company>` |
| `recruitee` | [Recruitee](https://recruitee.com) | `<company>.recruitee.com` |
| `rippling` | [Rippling](https://www.rippling.com) | `ats.rippling.com/<company>/jobs` |

### Also read
- **Each funded company's own website** (`/careers`, `/jobs`, home page) to find its exact job-board link.
- **prepdb.fyi startup pages** to get each company's website.
- **Any RSS / Atom job feed you add** (page: *Add your own*; CLI: `--feed URL`).

### Links only (not scraped)
Every company card has *Look manually* links to **LinkedIn** (remote jobs search), **Google** and **Indeed**.

### Deliberately not used
| Website | Why |
|---|---|
| Remotive | its robots.txt disallows its API |
| Crunchbase, LinkedIn, Wellfound | need a login / their terms forbid scraping |

## Quick start

You need [Python 3.10+](https://www.python.org/downloads/). On Windows tick **"Add Python to PATH"** when installing.

```bash
git clone https://github.com/RahMaisha/startup-hunt.git
cd startup-hunt
pip install -r requirements.txt
python server.py
```

Your browser opens http://127.0.0.1:8765. Pick filters, press **Find startups** (1-3 min, live data),
expand companies to see jobs, search the results, **Download CSV**. Stop it with **Ctrl+C** in the terminal.

No git? On GitHub use **Code -> Download ZIP**, unzip, open a terminal in the folder, run the last two commands.

## Commands

> On macOS / Linux use `python3` and `pip3` instead of `python` and `pip`.

### Setup
| Command | What it does |
|---|---|
| `git clone https://github.com/RahMaisha/startup-hunt.git` | download the project |
| `cd startup-hunt` | go into the folder |
| `python -m venv .venv` | *(optional)* create a private Python environment |
| `.venv\Scripts\activate` (Windows) / `source .venv/bin/activate` (mac/Linux) | *(optional)* turn it on |
| `pip install -r requirements.txt` | install the 3 libraries it needs |
| `git pull` | update to the latest version later |

### Web page
| Command | What it does |
|---|---|
| `python server.py` | start the page and open the browser |
| `python server.py --no-browser` | start without opening a browser tab |
| **Ctrl+C** | stop it |

### Command line (results saved to `output/` as CSV + JSON)
| Command | What it does |
|---|---|
| `python main.py --list-sources` | show every website it can scrape |
| `python main.py` | defaults: rounds of $20M+ in the last 90 days, all sources |
| `python main.py --min-amount 0 --days 30` | every round in the last 30 days, any size |
| `python main.py --experience 0 1-2 --skills python react` | new-grad / junior jobs mentioning Python or React |
| `python main.py --experience 4+ --all-skills --skills python aws` | 4+ years, must mention **both** skills |
| `python main.py --rounds "series a" "series b" --investor sequoia a16z` | only those rounds / lead investors |
| `python main.py --role engineer --exclude-role manager director` | title filters |
| `python main.py --relocation-only --location europe --job-days 14` | relocation/visa roles in Europe, posted in 2 weeks |
| `python main.py --funding prepdb --jobs remoteok himalayas --ats ashby greenhouse` | use only these sources |
| `python main.py --track Anthropic Ramp` | also check these companies, funded or not |
| `python main.py --feed https://example.com/jobs.rss` | add your own job feed |
| `python main.py --only-matches` | list only companies with at least one matching job |
| `python main.py --no-cache` | ignore the 6-hour cache, fetch everything fresh |
| `python main.py --out results` | save files to `results/` instead of `output/` |
| `python main.py --help` | every option |

### Maintenance
| Command | What it does |
|---|---|
| `python -m unittest discover -s tests -v` | run the tests (offline) |
| delete the `.cache` folder | clear cached career-board lookups |

### All filters
| Group | Option | Meaning |
|---|---|---|
| funding | `--min-amount / --max-amount` | round size, USD millions (`0` = include rounds with no listed amount) |
| funding | `--days` | funded within N days |
| funding | `--rounds`, `--investor`, `--company` | substring match, any-of |
| jobs | `--experience 0 1-2 2-3 4+` | years of experience; ranges overlap (a "2+ yrs" job fits `1-2` and `4+`) |
| jobs | `--keep-unknown-exp` | keep jobs that don't state experience (dropped by default when `--experience` is set) |
| jobs | `--skills`, `--all-skills` | job mentions any (or all) of the skills |
| jobs | `--role`, `--exclude-role`, `--location`, `--job-days` | title / location / recency |
| jobs | `--relocation-only` | also mentions relocation or visa sponsorship |
| jobs | `--allow-non-remote` | include non-remote jobs (off by default) |
| jobs | `--no-ats` | skip company career boards (faster, far fewer matches) |

## Sharing with friends

- **Easiest:** share this repo; each friend follows the Quick start and runs it on their own computer.
  The repo must be public (or add them as collaborators on GitHub).
- **Hosting on a server** is possible but not free on most platforms any more. Any host that runs a normal
  Python process works: set `APP_PASSWORD` (required — the server refuses to start publicly without it),
  `ALLOWED_HOSTS=your-app.example.com`, and run `python server.py --no-browser`; the platform's `PORT` is
  picked up automatically. `render.yaml` is included for Render.
- Not suitable: Vercel and Cloudflare Workers (short request limits; a run makes thousands of requests).

## Speed and freshness
Career-board lookups (including "no board here" answers) are cached in `./.cache` for 6 hours, which roughly
halves repeat runs. Use **Fresh data** under Advanced on the page, or `--no-cache`, to bypass it.

## What to expect (no-filter run, last 90 days, all rounds)
About 94 funded companies; a career board is found for ~3 in 4; ~47 have remote-friendly jobs (~640 job links).
The rest are mostly seed-stage companies with no public jobs page, or companies hiring in-office only. The
"Where jobs were filtered out" line on the results page shows which filter removes what.

## Notes and limits
- Honours each site's `robots.txt` and waits at least 0.5 s between requests. RemoteOK and Jobicy ask for
  credit + a link back if you republish their data.
- Remote, experience, skills and relocation are **guessed from job text**; spot-check before applying.
  "Senior"/"Staff" titles with no stated years count as 5+/8+.
- If prepdb.fyi redesigns its pages, that source shows "failed" until the selectors in `hunt/sources.py` are updated.
- Add a new source: write a function in `hunt/sources.py` that yields `Funding` or `Job`, then register it in
  `FUNDING_SOURCES` / `JOB_SOURCES` and `SOURCE_INFO`.
- Quality report: [QA_REPORT.md](QA_REPORT.md).
