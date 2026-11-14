# Startup Hunt

Finds **recently funded startups** and the **remote-friendly jobs** they are hiring for, filtered by
funding round, years of experience and skills. Every job returned is remote-friendly (hybrid / on-site
roles are always dropped).

## Run it on your computer (2 minutes)

You need [Python 3.10+](https://www.python.org/downloads/) (on Windows, tick "Add Python to PATH" when installing).

```bash
git clone https://github.com/RahMaisha/startup-hunt.git
cd startup-hunt
pip install -r requirements.txt
python server.py
```

Your browser opens http://127.0.0.1:8765. Pick filters, press **Find startups** (1-3 min, live data),
expand companies to see jobs, search the results, download a CSV. Locally there is no password and the
server is only reachable from your own computer.

No git? On the GitHub page use **Code -> Download ZIP**, unzip it, and run the last two commands inside the folder.

## Host it for free (Render)

Gives you a link like `https://startup-hunt.onrender.com` that friends can open, protected by a password.

1. Sign up at [render.com](https://render.com) with your GitHub account.
2. **New -> Blueprint**, pick this repository. Render reads `render.yaml`.
3. When asked for `APP_PASSWORD`, choose a password and share it with your friends.
4. **Apply**. After the build, open the URL Render shows; the browser asks for the password
   (any username works).

Every push to `main` redeploys automatically. On the free plan the app **sleeps after ~15 minutes
without visits**, so the first visit after a break takes about a minute to wake up; data scraped and the
cache don't survive restarts (each run fetches fresh data anyway). The server refuses to start publicly
without `APP_PASSWORD`, so strangers can't use your link to trigger scrapes.

Other hosts work the same way: set `PORT` (most platforms do it for you), `APP_PASSWORD`, and
`ALLOWED_HOSTS=your-app.example.com`, then run `python server.py --no-browser`.

## Command line

```bash
python main.py                                                   # >= $20M, last 90 days
python main.py --experience 0 1-2 --skills python react          # new-grad / junior, Python or React
python main.py --experience 4+ --all-skills --skills python aws  # 4+ yrs, needs BOTH skills
python main.py --rounds "series a" "series b" --investor sequoia a16z --role engineer
python main.py --relocation-only --location europe --job-days 14
python main.py --list-sources
```

Results are also written to `./output/` (CSV + JSON).

### Filters
| Group | Option | Meaning |
|---|---|---|
| funding | `--min-amount / --max-amount` | round size, USD millions |
| funding | `--days` | funded within N days |
| funding | `--rounds`, `--investor`, `--company` | substring match, any-of |
| jobs | `--experience 0 1-2 2-3 4+` | years of experience; ranges overlap-match (a "2+ yrs" job fits `1-2` and `4+`) |
| jobs | `--skills`, `--all-skills` | job text/tags mention any (or all) of the skills |
| jobs | `--role`, `--exclude-role`, `--location`, `--job-days` | title / location / recency |
| jobs | `--relocation-only` | also mentions relocation or visa sponsorship |
| jobs | `--keep-unknown-exp` | keep jobs whose experience can't be determined (dropped by default when `--experience` is set) |
| jobs | `--allow-non-remote` | include non-remote jobs (off by default) |
| jobs | `--no-ats` | skip per-company career-board lookups (faster, fewer matches) |
| jobs | `--no-cache` | ignore the 6-hour career-board cache |

## Sources
The **Sources** panel at the top of the web UI lists everything below with a checkbox each. Untick what you
don't want; after a run each source shows its row count (or "failed"). Choices are remembered in the browser.

| Type | Source | Method |
|---|---|---|
| funding | prepdb.fyi | HTML table, `/?page=N` |
| funding | TechCrunch venture | RSS (headline parsing, approximate) |
| jobs | prepdb.fyi/jobs | HTML cards |
| jobs | RemoteOK, Himalayas, Jobicy, Working Nomads, Arbeitnow | public JSON APIs (Himalayas: 20 jobs/page, so it scans 8x `--pages`) |
| jobs | We Work Remotely | RSS |
| jobs | HN "Who is hiring" | official Algolia HN API, last 3 monthly threads |
| jobs | **Greenhouse / Lever / Ashby / Workable / Recruitee / Rippling** | public career-board APIs, queried per company (biggest source of matches). The company's own site is read first to find its exact board link (e.g. `jobs.ashbyhq.com/whop`); only if that fails are board names guessed |

**Add your own** (UI, or `--feed` / `--track` on the CLI):
- *Job feed URL*: any public RSS/Atom feed of jobs. Company is read from titles like "Role at Company" or
  "Company: Role". Loopback / private-network URLs are rejected.
- *Also check these companies*: names looked up on every selected career board even if they aren't in the
  funding list (e.g. `--track Anthropic Ramp`).

Each company card also has **Look manually** links (website, LinkedIn remote search, Google, Indeed) for
companies where no job was found.

Add a source: write a function in `hunt/sources.py` yielding `Funding` or `Job`, then register it in
`FUNDING_SOURCES` / `JOB_SOURCES`.

## Speed and freshness
Career-board lookups (including "no board here" answers) are cached in `./.cache` for 6 hours, which roughly
halves repeat runs. Use **Fresh data** under Advanced in the UI, or `--no-cache`, to bypass it. Delete `./.cache`
to clear it.

## Tests
```bash
python -m unittest discover -s tests -v     # offline, no network
```
See [QA_REPORT.md](QA_REPORT.md) for the requirements traceability, defects fixed and residual risks.

## What to expect (measured on a no-filter run, last 90 days, all rounds)
About 94 funded companies; career boards found for ~3 in 4 of them; ~45 of those have remote-friendly jobs
(~640 job links). The rest are mostly seed-stage companies with no public jobs page, or companies hiring
in-office only. The "Where jobs were filtered out" line on the results page shows which filter removes what.

## Notes and limits
- Honours each site's `robots.txt` and waits `--delay` seconds between requests (Remotive was left out
  because its robots.txt disallows its API; Crunchbase / LinkedIn need logins). RemoteOK and Jobicy ask for
  credit + a link back if you republish their data.
- Experience, skills, remote and relocation are **keyword/regex guesses** from job text. Expect misses and
  some false positives; "Senior"/"Staff" titles with no stated years are treated as 5+/8+.
- Funded companies are matched to career boards by guessing the board name from the company name; ~2/3
  are found. Matching to the generic job boards is by normalised company name.
- prepdb's job cards use hashed CSS class names; if the site is redesigned, update the selectors in
  `prepdb_jobs` / `prepdb_funding`.
