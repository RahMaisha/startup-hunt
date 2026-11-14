# Startup Hunt — Requirements analysis & SQA report

Date: 2026-11-15 · Scope: `hunt/`, `main.py`, `server.py`, `static/index.html`

## 1. Requirements traceability

| # | Requirement (from the user) | Status | Where / evidence |
|---|---|---|---|
| R1 | Extract recently funded startups ("big loans") from prepdb.fyi | Met | `sources.prepdb_funding`; live run: 94 rounds / 90 days |
| R2 | Find companies supporting remote jobs or relocation | Met | remote flag (always on) + `--relocation-only` / UI checkbox |
| R3 | Same for other popular sites | Met | 2 funding feeds, 8 job boards, 6 career-board systems (`SOURCE_INFO`) |
| R4 | "Add more" sources | Met | Arbeitnow, Himalayas, Jobicy, Working Nomads, HN; Greenhouse/Lever/Ashby/Workable/Recruitee/Rippling |
| R5 | Filters | Met | round size, days, round type, investor, company, role, location, recency |
| R6 | Experience 0 / 1-2 / 2-3 / 4+, skills, all remote-friendly | Met | `parse_experience`, `exp_bucket_matches`, `skill_in`; remote stage always applied |
| R7 | Front screen | Met | `server.py` + `static/index.html` |
| R8 | CSV download | Met | `/api/csv/<id>` + `output/*.csv` |
| R9 | More jobs / more links | Met | per-company career boards (≈640 jobs on a no-filter run) + "Look manually" links |
| R10 | Show sources on screen; add / unselect | Met | Sources panel, custom feeds, tracked companies, per-source row counts |
| R11 | Arrow to hide sections | Met | collapsible Sources & Filters, state remembered |
| R12 | Light and dark theme | Met | Light / Auto / Dark switch, applied before first paint |
| R13 | Proper data with no filters | Met | no-filter run: 94 companies, 47 hiring remote, 637 jobs, 0 bad links |
| R14 | Find gaps & improve | Met | board discovery from company sites, cache, remote-in-text, funnel |

Implicit requirements checked: politeness (robots.txt + ≥0.5 s delay, enforced server-side), local-only UI,
no crash on one bad source, results reproducible as files.

## 2. Defects found and fixed

| ID | Sev. | Defect (reproduced before fixing) | Fix | Regression test |
|---|---|---|---|---|
| D1 | High | CLI `--min-amount 0` (or any 0) silently became the $20M default (`0 == False`) | `main.build_params` uses `is not None` | `test_cli_keeps_zero_values` |
| D2 | High | Job link with `javascript:` scheme from a feed would run script when clicked | `safe_href` on every Job + `safeHref` in the page | `test_unsafe_urls_dropped` |
| D3 | High | CSV formula injection (`=HYPERLINK(...)` titles executed in Excel) | `csv_safe` in both CSV writers | `test_empty_run_overwrites_old_files_and_csv_is_formula_safe` |
| D4 | High | A `null` location/title from any API crashed the **whole** source | `Job.__post_init__` normalises fields | `test_null_fields_dont_crash` |
| D5 | Med | Website detection used substrings: `dropbox.com` matched `x.com` → wrong/no website | host-based domain matching | `test_website_host_matching` |
| D6 | Med | Names like "AI Labs" normalised to `""` and matched every blank-company job | `norm` never returns empty | `test_norm_never_empty` |
| D7 | Med | Empty run left the **previous** run's CSV/JSON in `output/` | files always overwritten | same as D3 |
| D8 | Med | Same job listed twice (job board copy + career board) | `dedupe` (career board wins) + funnel step | `test_dedupe_prefers_career_board`; live: 7 removed |
| D9 | Med | "Founded 10 years ago" parsed as 10 years experience | same-sentence context, `N+`/range fallback only | `test_experience_prefers_context` |
| D10 | Med | No input validation: `pages=9999`, `delay=0`, `"abc"` numbers, bad buckets crashed or hammered sites | `clean_params` validates + clamps; 400 with message | `test_clean_params_clamps_and_validates`, `test_body_limits_and_validation` |
| D11 | Med | DNS-rebinding: any site resolving to 127.0.0.1 could drive the local API | Host header allow-list → 403 | `test_dns_rebinding_host_rejected`; live 403 |
| D12 | Med | Custom feed could redirect to a private/localhost address (SSRF) | redirects followed manually, every hop re-checked | `test_redirect_to_private_address_blocked` |
| D13 | Low | Unbounded request body; finished runs kept in memory forever | 64 KB cap (413); keep last 5 runs | `test_old_runs_pruned` |
| D14 | Low | Missing Lever timestamp shown as 1970-01-01 | `_iso` returns "" | `test_missing_timestamp_is_blank_not_1970` |
| D15 | Low | `/?anything` returned 404; no security headers / clickjacking protection | path parsing; CSP, nosniff, frame-ancestors | `test_page_has_security_headers` |
| D16 | Low | UI stuck on "Running…" if the server stopped mid-run | error handling in `poll()` | manual |
| D17 | Low | Theme flashed on load; `--list-sources` omitted career boards; one malformed prepdb row failed the source | early theme script; list fix; per-row guard | manual |
| D18 | Low | Cache folder grew forever (66 MB) | expired entries pruned each run | manual (verified file removed) |

## 3. Verification

- Unit/regression suite: **28 tests, all pass** (`python -m unittest discover -s tests -v`, offline).
- Live CLI run (`--min-amount 0`, all sources): 94 companies, 47 with remote jobs, 637 jobs; 0 non-http links,
  0 1970 dates, 0 duplicate URLs; 14 rounds without a listed amount now included.
- Live server: page loads under the new CSP with **no console errors**; full UI run rendered results;
  CSV download 200 with correct headers (49 rows × 17 cols); spoofed Host → 403.

## 4. Residual risks (not defects; inherent to the approach)

- Remote / experience / skills are inferred from text — expect some misclassification; spot-check before applying.
- ~1 in 4 funded companies has no discoverable public board (mostly seed-stage) → "Look manually" links.
- TechCrunch amounts are parsed from headlines (approximate).
- HTML selectors for prepdb.fyi break if the site is redesigned; the source then reports "failed" rather than crashing.
- Custom-feed DNS check is resolve-then-fetch (a fast DNS-rebinding feed host could still race it); acceptable for a local tool.
- No version control yet — changes can't be rolled back.
