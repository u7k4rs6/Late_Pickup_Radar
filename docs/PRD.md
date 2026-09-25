---
title: "PRD & Build Instructions for Claude Code"
subtitle: "FDE Data Foundations Assignment (Classes 4–8) — Track B, NYC TLC High-Volume FHV"
date: "September 2026"
---

# 0. How to use this document

This is the single source of truth for building the project. Claude Code should:

1. Read this whole document once before touching the repo.
2. Copy Section 12 (`CLAUDE.md`) into the repo root verbatim, then work phase by phase (Section 10).
3. Treat every **MUST** as an acceptance criterion and every **NEVER** as a hard stop.
4. When a fact about the data is unknown (a column name, a URL, a row count), **verify it against the real source and record it** — do not guess and do not hard-code from memory.

The grader is not measuring size or UI. They are measuring whether every step from client systems to a business decision is **explicit, defensible, and connected to the KPI**. Optimise for that.

---

# 1. The client and the business problem

**Client (fictional framing over real data):** the Operations team of a large NYC ride-hail base — think of them as a dispatch/marketplace team at one of the high-volume for-hire vehicle (HVFHV) companies.

**Leadership's statement:** *"Riders are complaining about long waits for pickup. We want to know where and when wait piles up, and whether it's worth putting driver incentives in specific zones next month."*

**The decision the output supports:** *Which zone × hour-of-week cells should receive driver-supply incentives next month?* (Rank by late-pickup rate and volume; the pipeline produces that ranked list every month.)

**Why this framing:** the raw TLC HVFHV data contains a **real four-event workflow** per trip — `request → on_scene → pickup → dropoff` — so the Track-A question ("where does delay accumulate?") can be answered on real messy data instead of synthetic data. This is a deliberate FDE choice and MUST be stated in the README.

**Project KPI (primary):**
> **Late-pickup rate** — share of completed trips where `pickup_datetime − request_datetime` exceeds **10 minutes**.

The 10-minute threshold is an **assumption**. It MUST be justified in `docs/assumptions.md` by (a) the observed wait distribution (median, p75, p90) and (b) a one-line rationale, and the KPI MUST also be reported at 8 and 15 minutes to show sensitivity.

---

# 2. Scope

**In scope**
- One target month of HVFHV trip data (the most recent complete month TLC has published — verify, do not assume).
- Three retrieval modes: **files** (Parquet + CSV), **API** (JSON), **SQL** (DuckDB).
- Profiling, business-rule validation with severities, quarantine of rejects (never silent fixes).
- A relational/event model, 5 metrics, evidence table, Known/Unknown/Assumption/Limitation (KUAL).
- A repeatable CLI pipeline with logging, manifests, rerun behaviour, failure handling, tests, CI on a sample.
- README, source map, two diagrams, demo script.

**Out of scope (say so in README)**
- Dashboards/UI beyond a Markdown evidence table and 2–3 PNG charts.
- Multi-month backfills (design for it, run one month).
- Yellow/green taxi data (no request timestamp → no wait workflow).
- Any modelling/ML. This is a data-foundations project.

---

# 3. Sources (Class 4 — Understand sources)

Deliverable: `docs/source_map.md` + a Mermaid diagram `docs/source_map.mmd`. Every row below MUST appear with real, verified values filled in.

| # | Business question | Information needed | Source system | Retrieval mode | Owner (real-world) | Grain | Known gaps / risks |
|---|---|---|---|---|---|---|---|
| S1 | How long do riders wait, where, when? | request, on_scene, pickup, dropoff timestamps; PU/DO zone IDs; base/company; flags (shared, WAV); pay | **NYC TLC HVFHV trip records** — monthly Parquet on TLC's CloudFront CDN (`fhvhv_tripdata_YYYY-MM.parquet`) | File (HTTP download, Parquet) | NYC TLC (data submitted by bases, published with ~2-month lag) | One row per trip | `on_scene_datetime` is null for some bases; unknown zones 264/265; duplicate submissions; timestamps outside the month; schema has changed over years (e.g. congestion fee columns added 2025) |
| S2 | What is zone 132? Which borough? | Zone ID → name, borough, service zone | **TLC Taxi Zone Lookup** CSV (+ optional shapefile) | File (CSV) | NYC TLC | One row per zone (1–265) | 264 = Unknown, 265 = Outside NYC — must be handled explicitly |
| S3 | Does weather explain late pickups? | Hourly precipitation, temperature for NYC for the month | **Open-Meteo Historical Weather API** (free, no key, JSON) at a Manhattan lat/lon | API (JSON) | Open-Meteo (reanalysis, not a station) | One row per hour | Model reanalysis, not observed; single point for all of NYC — a stated limitation |
| S4 | Which company is `HV0003`? | License number → company name; base number → base name | TLC HVFHV data dictionary (PDF) for license map; optionally **NYC Open Data SODA API** dataset of FHV bases | Reference doc + optional API | NYC TLC | One row per license / base | Dictionary is a PDF; base list is large — only needed for a dim table |
| S5 | Is the file complete? | Expected trip counts | TLC monthly aggregate reports / prior-month file | File | NYC TLC | Month | Aggregates may not exactly match trip-level files; use as a sanity band, not a hard check |

**MUST** in `source_map.md`: for each source record the exact URL used, the download timestamp, file size, SHA-256, schema (column list + dtypes as observed), and how completeness was established (Section 4).

**MUST** verify before coding: do a `HEAD` request on the Parquet URL and confirm 200 + `Content-Length`; open the data dictionary and confirm the column names actually present in the target month. Column names to expect (verify): `hvfhs_license_num, dispatching_base_num, originating_base_num, request_datetime, on_scene_datetime, pickup_datetime, dropoff_datetime, PULocationID, DOLocationID, trip_miles, trip_time, base_passenger_fare, tolls, bcf, sales_tax, congestion_surcharge, airport_fee, tips, driver_pay, shared_request_flag, shared_match_flag, access_a_ride_flag, wav_request_flag, wav_match_flag` and possibly `cbd_congestion_fee`.

---

# 4. Retrieval (Class 5 — Retrieve data)

Module: `pipeline/ingest.py`. Three modes, each with a completeness check and raw preservation.

## 4.1 Files (Parquet + CSV)
- Download to `data/raw/<month>/` with streaming + retries (3 attempts, exponential backoff), atomic write (`.part` then rename).
- Store `manifest.json` next to the file: URL, `Content-Length`, bytes written, SHA-256, download time, HTTP status.
- **Completeness check:** bytes written == `Content-Length`; Parquet footer readable; row count > 0; `min(pickup_datetime)` and `max(pickup_datetime)` cover the month; every day of the month has rows; hourly row counts have no zero-hours (log a WARN if any hour < 5% of median hour — that is a real-world gap, not a bug).
- **Rerun behaviour:** if the file exists and SHA-256 matches the manifest, skip download and log `SKIP (cached, checksum ok)`.

## 4.2 API (JSON)
- Call Open-Meteo's historical hourly endpoint for the month's date range at one NYC coordinate; request `precipitation`, `temperature_2m`, `weather_code`.
- Save the **raw JSON response unmodified** to `data/raw/<month>/weather_openmeteo.json` plus a manifest with the exact URL/params and response headers.
- **Completeness check:** number of hourly rows == the number of real local hours in the month in `America/New_York` (DST-aware: a March month has one hour fewer and a November month one hour more than `days_in_month × 24`); timestamps contiguous with no gaps; timezone recorded (request `timezone=America/New_York` and assert it in the response).
- **Failure handling:** on non-200 or malformed JSON, fail the stage with a clear message; the pipeline MUST still be able to run with `--no-weather` and mark weather metrics as `UNAVAILABLE` in the evidence table rather than crashing the whole run.

## 4.3 SQL (DuckDB)
- Load raw Parquet/CSV/JSON into a DuckDB database `data/warehouse/<month>.duckdb` as **raw schema** tables (`raw.trips`, `raw.zones`, `raw.weather`) with no transformation.
- All downstream steps (profiling, validation, modelling, metrics) are SQL executed via DuckDB, stored as `.sql` files under `pipeline/sql/` and rendered in the README so the grader can read them.
- **Completeness check:** `raw.trips` row count == Parquet row count from the manifest (assert equality; log both).

## 4.4 Raw preservation rules
- `data/raw/` is **never modified** after download. Nothing in the repo writes into it except `ingest`.
- `data/` is git-ignored **except** `data/sample/` (see Section 9), which holds a committed ~50k-row deterministic sample used by tests and CI.

---

# 5. Profiling & validation (Class 6)

Modules: `pipeline/profile.py`, `pipeline/validate.py`. Rules live in `pipeline/validation_rules.yml` and are rendered into `docs/validation_rules.md` automatically.

## 5.1 Profiling (output: `outputs/<month>/profile.md`)
For every column: dtype, null %, distinct count, min/max, 5 most common values. Plus targeted profiles:
- Distribution of `wait_minutes = pickup − request` (p1, p5, p25, p50, p75, p90, p99, max).
- Distribution of `dwell_minutes = pickup − on_scene` and % null `on_scene_datetime` **by company** (this asymmetry is a real finding — report it).
- Implied speed `trip_miles / (trip_time/3600)` distribution.
- Share of rows with `PULocationID` in (264, 265).
- Duplicate rate on the full row and on a business key (`hvfhs_license_num, dispatching_base_num, pickup_datetime, dropoff_datetime, PULocationID, DOLocationID, trip_miles`).
- Rows whose `pickup_datetime` falls outside the target month.

## 5.2 Validation rules (business-oriented, with severities)

Three severities, each with a defined consequence:

- **REJECT** — row moved to `quarantine.trips` with `rule_id`; excluded from all metrics. Never deleted.
- **FLAG** — row kept; boolean column `flag_<rule_id>` set; metrics reported with and without flagged rows where relevant.
- **ASSUME** — no row action; a documented interpretation the metrics depend on.

Initial rule set (MUST be tuned against the real profile, and every threshold MUST be justified in `assumptions.md`):

| rule_id | Rule | Severity | Business rationale |
|---|---|---|---|
| R01 | `pickup_datetime < request_datetime` | REJECT | Negative wait is impossible; indicates clock/entry error |
| R02 | `dropoff_datetime <= pickup_datetime` | FLAG | Dropoff timestamp defect: trip_time shows the trip happened, so the row is kept and excluded from dropoff-timestamp-derived fields only (amended after the 2026-07 profile; see assumptions.md F19) |
| R03 | `wait_minutes > 180` | REJECT | 3h+ "wait" is a data artefact (e.g. scheduled ride entered as on-demand); threshold justified from p99.9 |
| R04 | `pickup_datetime` outside target month | REJECT (quarantine as `out_of_period`) | Belongs to another month's file; would double count across months |
| R05 | Exact duplicate row | REJECT (keep first, quarantine the other copies, flag the kept copy as survivor, log count) | Double submission by base |
| R06 | Implied speed > 65 mph | REJECT | Physically implausible in NYC |
| R07 | `abs(trip_time − (dropoff−pickup) seconds) > 120` | FLAG | Two sources of duration disagree — report which we trust and why (we trust `trip_time`, not timestamps: in the 2026-07 profile, rows that look faster than 65 mph by timestamps have dropoff 20 s after pickup on trips whose trip_time is ~22 min; `trip_minutes` is derived from `trip_time`; amended, see assumptions.md F15) |
| R08 | `PULocationID in (264,265)` | FLAG | Unknown zone: kept in city-wide KPI, excluded from zone-level metrics |
| R09 | `on_scene_datetime IS NULL` | FLAG | Dwell cannot be computed; do not impute — dwell metrics computed only where present, and stated |
| R10 | `trip_miles = 0 and trip_time > 60` | FLAG | Likely GPS failure; excluded from speed metrics only |
| R11 | `driver_pay < 0` or `base_passenger_fare < 0` | FLAG | Refund/adjustment rows; excluded from pay metrics only |
| R12 | `on_scene_datetime < request_datetime` | FLAG | Driver "arrived" before request — likely a scheduled ride; noted, not fixed |
| A01 | `request_datetime` is the rider's ask; wait = request→pickup, not on_scene→pickup | ASSUME | The rider experiences the full wait |
| A02 | Trips are "completed" if they have a dropoff; cancellations are not in the data | ASSUME | The dataset only contains completed trips — cancellations are a **Known Unknown** |
| A03 | One weather point represents the whole city | ASSUME | Limitation, not a rule |

**NEVER** silently coerce, fill, or drop. Every row that leaves the clean set must be countable in `quarantine.trips` and in the run manifest.

## 5.3 Validation output
`outputs/<month>/validation_report.md`: rows in, rows rejected per rule, rows flagged per rule, rows out, and a one-paragraph "what this means for the KPI" (e.g. "R03 removed 0.4% of trips; if instead kept, late-pickup rate rises from 12.1% to 12.5%").

---

# 6. Workflow & data model (Class 7)

Deliverables: `docs/data_model.md`, `docs/workflow.mmd` (state diagram), `docs/erd.mmd` (entity diagram), and the SQL that builds the model.

## 6.1 Workflow (events and states)
```
[requested] --wait--> [on_scene] --dwell--> [picked_up] --in_trip--> [dropped_off]
```
- **Entities:** Trip, Zone, Company (license), Base, WeatherHour.
- **Events:** requested, on_scene, picked_up, dropped_off (each a timestamp on the trip).
- **States/durations:** `wait_minutes` (request→pickup), `dwell_minutes` (on_scene→pickup, when available), `trip_minutes` (pickup→dropoff).
- **Interactions / interventions (things the ops team or rider can change):** `shared_request_flag` (rider opted for shared), `wav_request_flag` (accessibility request), company (HV0003 vs HV0005), zone × hour supply (the incentive lever).
- **Exogenous factor:** precipitation in the request hour.
- **Outcomes:** `is_late` (wait > threshold), wait, trip duration.

## 6.2 Relational model (star schema in DuckDB, schema `model`)
- `model.fact_trip` — one row per clean trip: trip surrogate key, company_id, base_id, pu_zone_id, do_zone_id, request_ts, on_scene_ts, pickup_ts, dropoff_ts, request_hour_key (`YYYY-MM-DD HH`), wait_minutes, dwell_minutes, trip_minutes (from `trip_time`), trip_miles, driver_pay, is_late_8/10/15, shared_request, wav_request, plus all `flag_*` columns.
- `model.fact_trip_event` — long form: (trip_key, event_type, event_ts) — 3–4 rows per trip. Built because it is the honest representation of the workflow and makes "time in state" a simple lag.
- `model.dim_zone` — from S2; includes `is_unknown` flag.
- `model.dim_company` — license → name (from dictionary), with a `source` column.
- `model.dim_hour` — every hour of the month with weather joined from S3; `is_rainy` = precipitation ≥ 0.5 mm (assumption A-threshold, justify).

## 6.3 Metrics (5, all linked to the KPI)

| # | Metric | Definition (SQL-precise) | Grain | Why it matters for the decision |
|---|---|---|---|---|
| M1 **(KPI)** | Late-pickup rate | `sum(is_late_10)/count(*)` over clean trips | Month; zone × hour-of-week | Which cells to incentivise |
| M2 | p90 wait minutes | `quantile_cont(wait_minutes, 0.9)` | Zone × hour-of-week | Tail experience, not average |
| M3 | Median on-scene dwell | `median(dwell_minutes)` where on_scene present | Company; zone | Is delay driver-side (dwell) or supply-side (wait)? Separates two interventions |
| M4 | Rain sensitivity of late rate | `late_rate(is_rainy) − late_rate(not rainy)` | Month; borough | If rain drives lateness, incentives should be weather-triggered, not fixed |
| M5 | Trusted-row share (data trust) | `clean_rows / raw_rows` and per-rule reject share | Month | The KPI is only as good as this number; it belongs on the dashboard |

Output: `outputs/<month>/metrics.csv` (long format: metric, grain, dimension values, value, n) and `outputs/<month>/evidence.md` containing the evidence table, the top-20 zone × hour-of-week cells ranked for incentives, and the KUAL section. Also 2–3 PNG charts (wait distribution; late rate heatmap zone-borough × hour; rain vs dry).

---

# 7. Dependable pipeline (Class 8)

Entry point: `python -m pipeline run --month YYYY-MM [--sample 0.02 | --sample-file PATH] [--no-weather] [--force]`

Also: `python -m pipeline show {quarantine|flags|metrics|manifest|top-cells} --month M [--rule R03] [--n 5]` — read-only inspection commands used in the demo (Section 15).

Stages, in order, each a function with typed inputs/outputs and its own log section:

```
ingest → load_raw → profile → validate → model → metrics → report
```

**MUST-haves**
- **Logging:** Python `logging` to console and `logs/run_<month>_<timestamp>.log`; every stage logs start, end, elapsed, rows in/out.
- **Run manifest:** `outputs/<month>/run_manifest.json` — pipeline version (git SHA), parameters, source manifests (checksums), stage row counts, rule counts, wall time, and a `status` of `success | failed_at:<stage>`.
- **Checks between stages** (fail fast): raw row count == manifest; clean + quarantined == raw (duplicate copies are quarantined under R05; the kept copy stays clean and carries `flag_r05_survivor`); no nulls in fact keys; metrics non-empty; KPI between 0 and 1.
- **Rerun behaviour:** idempotent. Same month twice → identical outputs (assert in a test by comparing metric CSV hashes). Cached downloads skipped by checksum. `--force` re-downloads. Outputs written to a temp dir and swapped in atomically so a failed run never leaves half-written outputs.
- **Failure handling:** network retries with backoff; clear exit codes (0 ok, 2 source unavailable, 3 validation hard-fail, 4 internal); a validation hard-fail is defined as **trusted-row share (M5) below 95%** — the pipeline refuses to publish metrics and says why. This threshold is an assumption; document it.
- **Config:** `config.yml` holds thresholds (late minutes, speed cap, rain mm, trust floor), URLs, coordinate. No magic numbers in code.
- **Tests (`pytest`):** run entirely on `data/sample/` in < 60 s: each validation rule with a synthetic row that must trigger it; metric definitions on a tiny hand-computed fixture; idempotency; manifest schema.
- **CI:** GitHub Actions workflow runs `ruff` + `pytest` on push using the committed sample. No network in CI.
- **Scheduling note:** README explains how the same command runs monthly (cron / GitHub Action with `--month $(date -d 'last month' +%Y-%m)`), and what to do when TLC has not yet published (exit 2, retry next week).

---

# 8. Repository layout

```
late-pickup-radar/
├── README.md                  # Section 11
├── CLAUDE.md                  # Section 12
├── config.yml
├── pyproject.toml             # python>=3.11; duckdb, pyarrow, pandas, requests, pyyaml, matplotlib, rich, pytest, ruff
├── Makefile                   # make setup | run | test | sample | lint | demo | demo-rerun | demo-fail | demo-show
├── pipeline/
│   ├── __main__.py            # CLI (argparse)
│   ├── ingest.py  profile.py  validate.py  model.py  metrics.py  report.py  manifest.py  logging_setup.py
│   ├── validation_rules.yml
│   └── sql/                   # 01_load_raw.sql ... 05_metrics.sql (rendered into docs)
├── docs/
│   ├── source_map.md  source_map.mmd
│   ├── workflow.mmd  erd.mmd  data_model.md
│   ├── validation_rules.md    # generated
│   ├── assumptions.md         # every threshold + rationale + KUAL
│   └── demo_script.md
├── data/
│   ├── raw/        (gitignored)
│   ├── warehouse/  (gitignored)
│   └── sample/     (committed: hvfhv_sample.parquet ~50k rows, zones.csv, weather_sample.json)
├── outputs/<month>/  metrics.csv  evidence.md  evidence.html  profile.md  validation_report.md  run_manifest.json  charts/*.png   (full month COMMITTED)
├── outputs/demo/     (gitignored; produced by make demo)
├── notebooks/01_exploration.ipynb   # optional; must be runnable top-to-bottom on the sample
├── tests/
└── .github/workflows/ci.yml
```

---

# 9. Sample data for tests and CI

`make sample` produces `data/sample/hvfhv_sample.parquet` by taking a **deterministic** sample (`hash(trip business key) % 400 == 0`, ~50k rows) from the real month, **plus** it must deliberately retain rows that trigger every REJECT/FLAG rule (check counts > 0 per rule; if a rule has zero examples in the sample, append the real rows that trigger it). Record how the sample was made in `data/sample/README.md`. Commit it. The whole test suite and CI run on it; the full month runs locally.

---

# 10. Build phases for Claude Code

Work in this order. Commit at the end of each phase with a message `phase N: <what>`. Do not start a later phase until the earlier phase's acceptance checks pass.

| Phase | Do | Acceptance check |
|---|---|---|
| 0 | Scaffold repo (Section 8), `pyproject`, `Makefile`, `CLAUDE.md`, `.gitignore`, empty docs with headings | `make setup` works; `python -m pipeline --help` prints |
| 1 | **Verify sources**: HEAD the Parquet URL for the newest month; open the data dictionary; hit Open-Meteo; download zone CSV. Write `docs/source_map.md` with real values | Every URL returns 200; observed schema recorded; month chosen and justified |
| 2 | `ingest.py` + `load_raw.sql` with manifests, checksums, retries, completeness checks, cache/skip | `run --month M` populates `raw.*`; rerun logs SKIP; manifest has SHA-256 |
| 3 | `profile.py` → `profile.md`; tune `validation_rules.yml` thresholds against the real profile; `validate.py` with quarantine | `validation_report.md` shows every rule with counts; sum of rows reconciles |
| 4 | `model.py` (fact/dim SQL), `metrics.py` (M1–M5), `report.py` (evidence.md, charts, top-20 incentive cells) | `metrics.csv` has all 5 metrics; KPI at 8/10/15 min; evidence table renders |
| 5 | Harden: run manifest, stage checks, atomic outputs, exit codes, `--no-weather`, trust floor | Kill the network mid-download → clean failure, exit 2, no partial outputs |
| 6 | `make sample`; tests; CI; ruff clean | `pytest` green in < 60 s; CI green on GitHub |
| 7 | README (Section 11), diagrams rendered to PNG, `assumptions.md` with KUAL, committed full-month `outputs/`, `demo_script.md` | A stranger can clone, `make setup`, `make test`, `make run MONTH=…` and reproduce `evidence.md` |
| 8 | Demo mode (Section 15): `make demo`, `demo-rerun`, `demo-fail`, `demo-show`; pretty console; `evidence.html` | `make demo` finishes in < 30 s with readable stage banners and the top-10 table; `make demo-fail` exits 2 with no partial outputs; a dry run of the script fits in 4:30 |

---

# 11. README.md — required sections (in this order)

1. **Problem** — 3 sentences: client, leadership's problem, the decision the output supports.
2. **Users / stakeholders** — ops lead (decides incentives), dispatch analysts (consume evidence table), data owner (TLC as external source), riders (outcome).
3. **Project KPI** — late-pickup rate, definition, threshold, and why; sensitivity at 8/15.
4. **Why Track B with HVFHV data** — the "real workflow on real data" judgement.
5. **Source overview** — the table from Section 3 (short form) + link to `docs/source_map.md` + embedded Mermaid source map.
6. **Workflow & data model** — embedded Mermaid state diagram + ERD; link to `docs/data_model.md`.
7. **Validation approach** — severities, rule table (generated), what gets quarantined, link to latest `validation_report.md`.
8. **Metrics** — M1–M5 table with definitions.
9. **Results for `<month>`** — the evidence table (5 metrics), top-10 incentive cells, 2 charts inline.
10. **Known / Unknown / Assumption / Limitation** — four short lists (see below).
11. **Pipeline** — stage diagram, `make run`, rerun/caching, failure modes & exit codes, logs, manifest, tests, CI, monthly scheduling.
12. **Setup / run instructions** — copy-paste block: clone → `make setup` → `make test` → `make run MONTH=YYYY-MM` → open `outputs/<month>/evidence.md`.
13. **One FDE judgement call** — the one you'll present in the demo (Section 13).
14. **Repo map** — one line per directory.

**KUAL starter (Claude Code fills with real findings):**
- *Known:* TLC file is complete for the month (bytes, days, hourly coverage); N rows; X% pass all rules.
- *Unknown:* cancellations and unfulfilled requests are not in the data — the KPI is conditional on a trip happening; true rider wait including cancellations is unknowable from this source.
- *Assumption:* 10-minute threshold; wait measured from request; one weather point; on_scene trusted where present.
- *Limitation:* one month; reanalysis weather; company asymmetry in `on_scene` reporting limits dwell comparisons.

---

# 12. `CLAUDE.md` (copy into repo root)

```markdown
# Project rules for Claude Code

Goal: a trustworthy, repeatable path from NYC TLC HVFHV data to a monthly
"where to incentivise drivers" decision. Grading = source reasoning,
retrieval, validation, workflow+metrics, pipeline dependability (20% each).

## Non-negotiables
- NEVER guess a URL, column name, row count or threshold. Verify against the
  real source, then record it in docs/source_map.md or docs/assumptions.md.
- NEVER silently fix data. Rows either pass, are FLAGGED (kept, marked) or
  are REJECTED (quarantined with rule_id). Every threshold has a written
  rationale.
- NEVER write into data/raw/ except from pipeline/ingest.py. Raw is immutable.
- NEVER commit data/raw or data/warehouse. data/sample/ is committed.
- No magic numbers in code: thresholds live in config.yml / validation_rules.yml.
- Every stage logs rows in / rows out; the run manifest must reconcile.
- Prefer SQL in pipeline/sql/*.sql for all transforms; Python orchestrates.
- Keep it small. No web UI, no ML, no extra datasets beyond the source map.

## Working style
- Follow docs/PRD.md phases in order; commit per phase ("phase N: ...").
- Before finishing a phase, run: make lint && make test.
- When a real-data finding contradicts the PRD (e.g. a column is missing,
  a threshold is wrong), do NOT silently adapt: change the rule, log the
  reason in docs/assumptions.md, and mention it in the commit message.
- Write docs as you go; README is a deliverable, not an afterthought.
- Sample-first development: build and test on data/sample, then run full.

## Stack
Python 3.11+, DuckDB, pyarrow, pandas, requests, pyyaml, matplotlib, rich,
pytest, ruff. argparse CLI. GitHub Actions (no network in CI).
```

---

# 13. Demo script (3–5 minutes) — `docs/demo_script.md`

Target runtime **4:00–4:30**. Everything runs on the committed sample so it is fast and deterministic; the full-month results are shown from committed `outputs/`. Two terminal tabs open before recording: **T1** in the repo root, **T2** with `outputs/<month>/evidence.md` (or `evidence.html`) ready.

| t | Screen | Say (teleprompter) | Command / click |
|---|---|---|---|
| 0:00 | README top | "The client is an ops team at a NYC ride-hail base. Leadership's problem: riders wait too long for pickup. The decision this pipeline supports: which zone-and-hour cells get driver incentives next month. KPI: late-pickup rate — share of trips where request-to-pickup exceeds 10 minutes." | scroll README §1–3 |
| 0:35 | `docs/source_map.png` | "Three sources, three retrieval modes. TLC's monthly Parquet file, a weather API in JSON, and DuckDB SQL for everything downstream. I chose the ride-hail data over yellow taxi because it's the only public NYC dataset with a request timestamp — that turns a trip table into a workflow." | open image |
| 1:05 | T1 | "Here's the whole pipeline on a 50k-row sample." | `make demo` |
| 1:15 | T1 (running) | Narrate the banners as they appear: "Ingest — checksum recorded, completeness check: every hour of the month present. Validate — twelve rules, three severities. Nothing is fixed silently: rejects go to a quarantine table with a rule id." | (wait for finish, ~20 s) |
| 1:40 | T1 | "Trusted-row share is itself a metric. If it drops below 95%, the pipeline refuses to publish." | point at summary table |
| 1:50 | T1 | "Let's look at what got rejected under R03 — waits over three hours." | `make demo-show RULE=R03` |
| 2:10 | `docs/workflow.png` | "Four events per trip: request, on-scene, pickup, dropoff. Three durations: wait, dwell, trip. The interventions are the shared and WAV flags, the company, and zone-hour supply — the incentive lever." | open image |
| 2:35 | T2 evidence | "Five metrics for the full month. Late-pickup rate at 8, 10 and 15 minutes so the threshold is visibly an assumption. And the output the ops lead actually uses: the top cells ranked for incentives." | scroll evidence table → top-10 |
| 3:05 | T1 | "Run it again — same month, nothing re-downloaded, identical output hash." | `make demo-rerun` |
| 3:20 | T1 | "And when the source is broken — here I point it at a bad URL — it fails cleanly, exit code 2, no half-written outputs." | `make demo-fail` |
| 3:40 | README §13 | **Judgement call:** "I measure wait from *request*, not *on-scene*, and I refuse to impute missing on-scene times. On-scene is populated very differently by company — imputing it would manufacture a comparison the data can't support. So dwell is reported only where it exists, with coverage beside it." | scroll |
| 4:15 | README §10 KUAL | "Known, unknown, assumption, limitation — the biggest unknown: cancellations aren't in the data, so the KPI is conditional on a trip happening." | scroll |
| 4:25 | — | "This isn't 'I analysed a dataset'. It's a path from TLC's systems to an incentive decision, and it runs again next month." | stop |

**Recording checklist:** terminal font ≥ 16 pt, dark theme, window ~120 columns; run `make demo` once before recording to warm caches; close notifications; record at 1080p; keep the cursor still while narrating; do one full dry run and time it.

---

# 15. Demonstration-friendliness requirements

These exist so the video is smooth, short and convincing. They are cheap; do not skip them.

## 15.1 Make targets
| Target | Does | Must satisfy |
|---|---|---|
| `make demo` | `run --month <M> --sample-file data/sample/...` on the committed sample, into `outputs/demo/` | < 30 s wall time; readable output (15.2); ends with summary + top-10 table |
| `make demo-rerun` | Runs `make demo` again | Logs `SKIP (cached, checksum ok)` for sources; prints `outputs identical: <hash>` by comparing `metrics.csv` hash with the previous run |
| `make demo-fail` | Sets `PIPELINE_TRIPS_URL` to a non-existent URL and runs ingest with `--force` into a temp output dir | Exit code 2; message names the source and the HTTP status; asserts and prints `no partial outputs written` |
| `make demo-show RULE=R03` | `python -m pipeline show quarantine --rule R03 --n 5` | Prints 5 real rejected rows with the offending columns highlighted, plus the rule text and rationale from `validation_rules.yml` |
| `make demo-reset` | Deletes `outputs/demo/` and demo caches | Lets you re-record from clean |

## 15.2 Console output (use `rich`)
- One **banner per stage** (`━━ 2/7 VALIDATE ━━`) with elapsed time on completion.
- Per stage: `rows in → rows out` on one line.
- Validate stage: a table `rule_id | severity | rows | %` with REJECT in red, FLAG in yellow, and a final line `trusted-row share 97.8% (floor 95%) ✔`.
- Final summary table: KPI at 8/10/15 min, M2–M5, then the **top-10 incentive cells** (`borough · zone · dow-hour · late rate · n`).
- `--quiet` flag turns this off (CI uses it). Log file always gets the plain version.

## 15.3 Artefacts that must exist in the repo before recording
- `outputs/<month>/` for the real full month **committed** (metrics.csv, evidence.md, evidence.html, validation_report.md, profile.md, run_manifest.json, charts/*.png). The demo never depends on the 500 MB download.
- `evidence.html` — a single static page generated by `report.py` from the same data as `evidence.md`: evidence table, top-20 cells table, the three charts inline. No JS, no framework. It exists so the video shows one clean page instead of scrolling Markdown.
- Diagrams rendered to PNG (`docs/source_map.png`, `docs/workflow.png`, `docs/erd.png`) via `mmdc` (mermaid-cli) or, if unavailable, drawn once and committed. README embeds the PNGs and links the `.mmd` sources — GitHub's Mermaid rendering is not relied on during the demo.
- `docs/demo_script.md` — the table in Section 13 plus the exact commands.

## 15.4 Determinism
- The sample is fixed (Section 9); `make demo` output is byte-identical across runs except timestamps in logs.
- Sort every printed table explicitly; never rely on DuckDB output order.
- Chart generation uses a fixed style and figure size so screenshots match between takes.

# 14. Acceptance checklist (map to rubric)

| Rubric (20% each) | Evidence the grader must find in the repo |
|---|---|
| Source reasoning | `source_map.md` with question → info → source → owner → grain → gaps; verified URLs and schemas; the HVFHV-vs-yellow choice explained |
| Retrieval | Three modes in `ingest.py`; manifests with checksums; explicit completeness checks; `--force` and cache-skip behaviour; raw immutability |
| Validation | `profile.md`; `validation_rules.yml` with severities and rationales; quarantine table; `validation_report.md` with reconciling counts; assumptions recorded, not applied silently |
| Workflow + metrics | `workflow.mmd`, `erd.mmd`; `fact_trip_event` long table; `dim_*`; M1–M5 with SQL definitions; evidence table + top-N cells tied to the decision |
| Pipeline dependability | One CLI; logs; run manifest; inter-stage checks; idempotent rerun test; exit codes; trust floor; tests + CI on committed sample; monthly run note |

Add to the checklist: **Demo** — `make demo` < 30 s, `demo-rerun` shows SKIP + identical hash, `demo-fail` exits 2, `demo-show` prints real quarantined rows, diagrams as PNG, full-month outputs committed, dry run ≤ 4:30.

Final self-check before submission: a fresh clone on a clean machine reaches `evidence.md` by following only the README.
