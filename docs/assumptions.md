# Assumptions, thresholds and KUAL

Month: **2026-07** (20,921,249 HVFHV trips). Read the first two sections for the picture; the
thresholds and the dated decision log below them are the audit trail. Every threshold in
`config.yml` / `pipeline/validation_rules.yml` is justified here, and every place where real data
contradicted the PRD is logged, with the decision.

## Known / Unknown / Assumption / Limitation

**Known**
- The trip file is complete: 20,921,249 rows, 511,176,663 bytes equal to Content-Length, 31/31
  days and 744/744 hours with pickups, and within 62 rows (0.0003%) of TLC's own aggregate report.
- 99.998% of rows pass every REJECT rule. The 376 quarantined rows are 291 R01 (a negative wait
  with no pre-arrangement signal), 44 R03 (a wait over 180 min) and 41 R06 (over 65 mph).
- The late-pickup rate is **10.02%** at 10 min (17.53% at 8, 2.69% at 15); p90 wait is 10.02 min.
- Location and hour drive lateness; rain barely does. Late rates run from 3.4% to 37.8% across
  borough x hour, while a rainy hour adds +0.9 points raw and +1.1 within the same hour of day.
- The top neighbourhood cell is Williamsburg (North Side), Saturday 23:00: 33.5% late over 3,358
  trips, 788 more late trips than the city rate predicts. A single ranking would be 100% airports
  in its top 20.

**Unknown**
- Cancellations and unfulfilled requests are not in the file, so the KPI is conditional on a trip
  happening; the rider who gave up waiting is invisible.
- Which trips were booked in advance: there is no scheduling field. 366,740 clean trips (1.75%)
  carry the on_scene-before-request signature (R12); about 12,500 more whole-minute requests
  above chance (R14) may be reservations the signal misses.
- What happened on the 6.6% of Uber trips whose on_scene equals pickup: arrival was not captured,
  so their dwell (1,012,982 rows) cannot be known.
- The real wait for Lyft WAV riders: only 41.98% of those trips can enter wait metrics.

**Assumption**
- Late means wait > 10 min, the observed p90, and the threshold is fixed across months. Re-deriving
  it from each month's p90 would pin the KPI at ~10% forever.
- Wait is request to pickup. Where on_scene is before request (R12), the request is a booking time,
  not the rider's ask, and the row leaves wait metrics (the KPI would be 9.92% with them in).
- One weather point: Central Park, whose reanalysis grid cell lies over New Jersey. Precipitation for
  hour H comes from the row labelled H+1, because Open-Meteo sums the preceding hour. "Rainy" means
  >= 0.5 mm, which gives 98 wet hours.
- trip_time is trusted over the dropoff timestamp: the 661 trips that look faster than 65 mph by
  timestamps end 20 s after they start while trip_time says ~22 min.
- A decision cell needs n >= 200 trips (+/-4.2 points at a 10% rate). Airports are ranked
  separately, split on the zone lookup's service_zone, never on the numbers.

**Limitation**
- One month, which includes the July 4 holiday weekend; 35 of the 98 wet hours fall on Jul 5-6.
- Weather is model reanalysis at a single point, not a station network.
- Dwell coverage differs by company (Uber 93.40%, Lyft 99.86%), so dwell comparisons rest on
  different shares of trips.
- Base names are not available (only IDs), and the data dictionary (2025-03-18) is stale against
  the file (it calls on_scene WAV-only; it is populated on every row).
- A full-month run needs about 6 GB of free RAM (peak RSS 5.3 GB, 4:15 at 2 threads).

## Resolved decisions

Every item that was open during the build, with the decision taken. Details are in the log below.

| Item | Decision | Log |
|---|---|---|
| on_scene is never null, contradicting R09's premise | R09 kept as a guard; new FLAG R13 for on_scene >= pickup; dwell only where arrival was captured | F1, F14 |
| Lyft WAV negative waits: split R01 by WAV? | No split: the pattern is in every segment. R01 rejects only negative waits with no pre-arrangement signal (291); the rest are R12 | F2, F10 |
| Open-Meteo local output is not DST-aware | Request GMT, convert to America/New_York; tests assert 743 / 721 / 744 hours | F4 |
| Weather coordinate | Central Park; the NJ grid cell is a stated limitation | F5 |
| Scheduled rides | Option (a): FLAG R12 excludes them from wait metrics; KPI reported with them in and out | F11 |
| Promote R14 (whole-minute requests)? | No: 1.13x the late rate of the rest, below the 2x bar; stays a sensitivity line | F18 |
| R03 cap | 180 min, the break in the tail (not p99.9 = 29.7 min) | F13 |
| R02 (dropoff <= pickup) | FLAG, not REJECT: timestamp defects on real trips | F19 |
| Trip duration source | trip_time; PRD R07's "we trust timestamps" reversed | F15 |
| Business-key duplicates | Not duplicates: pooled riders. R05 stays exact-row only | F16 |
| Aggregate report delta | Sanity band (1%), never a hard check; both numbers in the manifest | F8 |
| Base names | Skipped; dim_company from the data dictionary | F7 |
| Airports dominate the ranking | Two lists, same rule, split on zone type; airports escalate to airport ops | F24, F25 |
| Demo top cells on a 52k sample | make demo prints the committed full-month cells, labelled as such | F25 |
| metrics.csv size | Reporting grains only (202 rows); cells only in incentive_cells.csv | F26 |
| M5 | Three lines: trusted 99.998%, wait-eligible 98.245%, dwell coverage 95.156% | F21 |
| Resource envelope | 2 threads, 4 GB DuckDB limit; measured 4:15 and 5.3 GB peak | Phase 5 |
| Hard-killed runs | A lock per output; the next run sweeps and records a killed run | Phase 5 |

## Thresholds

| Name | Value | Where | Rationale |
|---|---|---|---|
| `kpi.late_minutes` | 10 | config.yml | Observed 2026-07 wait over the 20,554,131 trips that enter wait metrics: **median 4.47, p75 6.78, p90 10.02 min**. 10 min is more than twice the median and sits at p90, so "late" means roughly the worst decile of waits, in a round number an ops lead and a rider both understand. **The threshold is fixed, not re-derived each month:** re-deriving it from p90 would pin the KPI at ~10% and it could never show improvement. Sensitivity: 17.53% late at 8 min, 10.02% at 10 min, 2.69% at 15 min. |
| `kpi.sensitivity_minutes` | 8, 15 | config.yml | PRD 1; brackets the headline threshold (8 min is about p82 and 15 min about p97 of the observed wait: 17.53% and 2.69% of waits exceed them) |
| `download.attempts` / `backoff_seconds` | 3 / 2 s (doubling) | config.yml | PRD 4.1; covers transient CDN errors without hiding an unpublished month |
| `completeness.low_hour_share` | 0.05 | config.yml | PRD 4.1: an hour below 5% of the median hour is a real-world gap worth a WARN, not a failure |
| `completeness.aggregate_band` | 0.01 | config.yml | F8: observed delta vs TLC aggregate is 0.0003%; 1% leaves room for rounding and late corrections |
| R03 `max_wait_minutes` | 180 | validation_rules.yml | F13: break in the log-binned tail; 41 of 44 rows above it are one Lyft artefact cluster |
| R06 `max_speed_mph` | 65 | validation_rules.yml | Counts fall ~5x per 5-mph bin from 50 to 65 mph (6,441 / 1,219 / 190), then scatter (42 rows above); p99.99 = 54 mph |
| R07 `max_gap_seconds` | 120 | validation_rules.yml | PRD value; median gap 0 s, p99 absolute gap 161 s, so 120 s isolates the disagreeing 1.55% |
| R10 `min_trip_seconds` | 60 | validation_rules.yml | PRD value; 1,733 of 1,987 zero-mile trips last longer than a minute |
| `thresholds.rain_mm_per_hour` | 0.5 | config.yml | F20: separates the 74 drizzle-level hours (0.1-0.4 mm) from rain; 98 wet hours in 2026-07 |
| `thresholds.rain_sensitivity_mm_per_hour` | 2.5 | config.yml | F20: "moderate rain"; 32 hours in 2026-07; robustness line for M4 |
| `incentives.min_cell_trips` | 200 | config.yml | F22: binomial SE 2.1 pts at a 10% rate; keeps 93.7% of zone-known wait-eligible trips |
| `kpi.r14_promotion_ratio` | 2.0 | config.yml | Agreed in review; observed ratio 1.13x (F18) |
| R12 / R13 / R14 | no threshold | validation_rules.yml | Strict inequalities / equality on the raw timestamps; F11, F14, F17 |
## Decision log (dated findings, oldest first)

### Findings from phase 1 (decisions recorded 2026-09-25 after review)

- **F1 `on_scene_datetime` is never null in 2026-07.** Footer statistics show 0 nulls across all
  20,921,249 rows. The PRD expected nulls for some bases (R09) and built the demo judgement call on
  "company asymmetry in on_scene reporting". The data dictionary (2025-03-18) says the field is
  "Accessible Vehicles-only", yet it is populated for every trip. Consequence: R09 will fire on 0
  rows; the asymmetry, if any, is in *how* it is filled (row group 0: `on_scene = pickup` on 6.6% of
  Uber non-WAV vs 0.1% of Lyft non-WAV), not in whether it is filled. To be profiled in phase 3.
  **Decision:** R09 stays as a guard. Phase 3 profiles `on_scene = pickup` by company. If one company
  writes pickup into on_scene when arrival is not captured, dwell on those rows is zero by
  construction, not zero in reality (the same trap as "processed is not credited"). Likely outcome:
  a FLAG rule "on_scene equals pickup", dwell computed only where on_scene < pickup, with coverage
  reported beside it.
- **F2 Lyft WAV trips have `on_scene < request` on ~57% of rows** (row group 0), with a negative
  median request-to-pickup wait (-2.97 min). R01 (pickup < request, REJECT) could quarantine a large
  share of Lyft WAV trips, which would bias WAV metrics. Candidate for phase 3 profiling alongside
  the scheduled-ride analysis.
  **Decision (provisional, to be confirmed by the phase 3 profile):** R01 must not silently delete a
  whole rider segment. Phase 3 cross-tabs negative wait by company x `wav_request_flag` x
  `wav_match_flag`. Provisional plan: R01 stays REJECT for non-WAV rows (a negative wait there is a
  data error); WAV rows with on_scene < request get a FLAG ("pre-arranged pickup") and are excluded
  from wait-based metrics only; WAV is reported as its own line in the evidence table. This is an
  assumption, not a verified fact: the file has no pre-arrangement field.
- **F3 R04 (pickup outside month) will likely fire on 0 rows.** Footer `pickup_datetime` range is
  exactly 2026-07-01 00:00:00 to 2026-07-31 23:59:59, so TLC partitions files by pickup time.
  **Decision:** R04 kept as a guard for future months or re-published files.
- **F4 Open-Meteo local-time output is not DST-aware.** With `timezone=America/New_York` a March
  month returns 744 rows including a non-existent 02:00. The approved "count real local hours" check
  would fail against the API itself unless weather is requested in GMT and converted.
  **Decision:** weather is requested with `timezone=GMT` and converted to America/New_York locally.
  The response is asserted to be `timezone=GMT` (this replaces the PRD's "assert America/New_York in
  the response"). Expected rows = real local hours of the month: 743 in March (31 x 24 - 1), 721 in November (30 x 24 + 1), 744
  in July. The API returns a few extra UTC hours either side; they stay in `raw.weather` untouched and
  the month is selected in the model.
- **F5 Open-Meteo grid cell for Manhattan points is in New Jersey** (40.8084, -74.0199 for both
  Midtown and Central Park). Adds to limitation A03.
  **Decision:** request point is Central Park (40.7829, -73.9654). The reanalysis grid cell that
  contains it lies over New Jersey, and one point represents the whole city. We still use it because
  rain is regional at the hourly scale we care about, and Central Park is the conventional NYC
  weather reference.
- **F6 Unpublished month returns HTTP 403, not 404.** Ingest maps both to exit code 2.
- **F7 Base names unavailable.** SODA dataset `2v9c-2k7f` has no rows for B03404/B03406; `dim_base`
  carries IDs only.
  **Decision:** skip the open-data lookup. `dim_company` = license -> {HV0003 Uber, HV0005 Lyft} from
  the data dictionary, `source = data_dictionary_2025-03-18`.
- **F8 Aggregate report delta.** TLC's monthly report implies 20,921,187 trips (674,877/day x 31)
  vs 20,921,249 Parquet rows (+62). **Decision:** used as a sanity band (WARN outside +/-1%), never a
  hard check; both numbers are recorded in the run manifest.
- **F9 nyc.gov blocks non-browser User-Agents (phase 2).** The first full run failed at ingest
  with exit 2 because `data_reports_monthly.csv` answered 403 to `python-requests`. The PRD treats
  S5 as a sanity band, so a fetch failure must not fail the run. **Change:** all requests send a
  browser-style UA that still names the project; S5 fetch failure is a WARN and the manifest
  records `expected_counts.status = unavailable`.

### Findings from phase 3 (full-month profile, 2026-07)

Every number below is from `outputs/2026-07/profile.md`. Rule-level evidence is in
`pipeline/validation_rules.yml` (rendered into `docs/validation_rules.md`).

- **F10 Negative waits are pre-arranged rides, not a WAV quirk.** 246,534 trips (1.18%) have
  pickup before request. 94% are non-WAV, at ~1.1% in every non-WAV segment of both companies;
  Lyft WAV is the extreme case (55%, 12,730 rows). 99.9% of negative-wait rows also have
  on_scene before request. **Change vs the provisional WAV split:** one rule for every segment.
  R01 REJECTs only negative waits with no pre-arrangement signal (291 rows); the rest are FLAG R12.
- **F11 on_scene before request is the reservation signature.** 366,752 rows (1.75%). Compared
  with other trips they go to airports far more often, are ~3x longer, peak at 04:00-05:00, and
  (for Uber) have whole-minute request times. R12 now excludes them from wait-based metrics
  instead of being informational. **Assumption:** the file has no scheduling field; "on_scene
  before request" is our proxy.
- **F12 No second mode in the positive waits.** On a log axis the positive waits form one smooth
  hump peaking near 4 min. Reservations show up as negative waits and as a whole-minute excess in
  the 30-180 min tail (Uber: 34% of 30-60 min waits, 65% of 60-180 min waits vs 1.7% by chance).
- **F13 R03 cap: 180 min, from the break in the tail, not from p99.9.** p99.9 = 29.7 min lies in
  the smooth body; capping there would reject ~21,000 genuine long waits. Above 180 min: 44 rows,
  41 of them a Lyft cluster requested Jun 29 to Jul 7 and picked up up to 2 days later.
- **F14 on_scene = pickup is company-specific.** Uber 6.60%, Lyft 0.14%. Dwell for those rows
  is zero by construction. New FLAG R13; dwell only where on_scene < pickup.
- **F15 "We trust timestamps" (PRD R07) fails on the fast trips.** Timestamp speed > 65 mph on
  661 rows whose median dropoff - pickup is 20 s against a median trip_time of 1,305 s. R06 uses
  trip_time (42 rows > 65 mph); R07 excludes disagreeing rows from trip-duration metrics only.
- **F16 The PRD business key is not unique: pooled riders share it.** 20 collision pairs, every
  one with different request times and fares. Exact duplicates: 0. R05 stays exact-row only.
- **F17 Whole-minute requests as a sensitivity (R14).** On-demand requests hit second 0 one time
  in 60, so dropping every whole-minute request removes reservations while dropping on-demand
  trips at random. Used for a KPI sensitivity line, not for the headline.

### Findings from phase 4 (model and metrics, 2026-07)

- **F18 R14 stays a sensitivity line (checked, not assumed).** Of 655,426 whole-minute requests,
  ~300,000 are already R12 and outside the KPI. Among the 20,554,131 wait-eligible rows: 355,098
  whole-minute requests vs 342,569 expected by chance (1/60), an excess of ~12,500 likely
  reservations. Their late rate is 11.30% vs 9.99% for the rest: **1.13x**, below the 2x
  promotion bar agreed in review (`config.yml kpi.r14_promotion_ratio`). Dropping every R14 row
  moves the KPI from 10.02% to 9.99%.
- **F19 R02 becomes a FLAG.** Its 2 rows are timestamp defects, not non-trips: one has trip_time
  163 s, 0.48 mi and a fare; the other trip_time 1 s, 0 mi and a fare. Their waits are valid. R02
  now excludes rows only from fields derived from the dropoff timestamp. PRD R02 amended.
- **F15 follow-up: trip_minutes comes from trip_time.** PRD R07's "we trust timestamps" is
  amended to the opposite; `timestamp_trip_minutes` is kept beside it for R02/R07.
- **F20 Weather alignment and the rain threshold.** Open-Meteo documents precipitation as the
  "sum of the preceding hour", so hour H takes precipitation from the row labelled H+1; temperature
  is instantaneous and is taken at H. July 2026 across its 744 local hours: 572 fully dry, 172 with
  any precipitation, 98 at >= 0.5 mm, 32 at >= 2.5 mm. The 74 hours at 0.1-0.4 mm are drizzle-level
  model output (values are stored in 0.1 mm steps), so **is_rainy = >= 0.5 mm**, with 2.5 mm
  ("moderate rain") as a robustness line. Wet hours cluster: 35 of the 98 fall on Jul 5-6, the
  holiday weekend, so M4 also reports a within-hour-of-day difference. Weather joins on the
  **request** hour; 2,144 wait-eligible trips were requested before Jul 1 (for Jul 1 pickups) and
  have no weather hour, so they are outside M4 only.
- **F21 M5 is three lines, all over rows in.** Trusted-row share 99.998% (376 rows quarantined:
  quoting 100.00% would hide them); wait-eligible share 98.245%; dwell coverage 95.156%.
  Per segment, wait coverage is reported beside the rate: Lyft WAV is 41.98%.
- **F22 Cell floor n >= 200 (`incentives.min_cell_trips`).** At the city late rate (~10%), a
  200-trip cell has a binomial standard error of 2.1 points, so a 95% interval is about +/-4.2
  points: a cell must be roughly 1.4x the city rate before it is distinguishable from it. Lower
  floors admit small cells whose rate is mostly noise. 28,360 cells qualify, holding
  19,257,926 of the 20,552,912 wait-eligible trips with a known pickup zone (93.7%).
- **F23 Cells are keyed on the request timestamp.** Day-of-week and hour come from the rider's ask,
  which is when supply is needed; the pickup can fall in the next hour.
- **F24 The airports dominate the ranking (resolved in F25).** All 20 of the top 20 cells are
  LaGuardia or JFK, 21:00-00:00. The delay there is on the supply side: at LaGuardia late at night
  the median request-to-arrival is 9.2 min (3.9 min in the daytime) while dwell stays under a minute
  (0.87). The first non-airport cell ranks 45th (Williamsburg, Saturday 23:00), then a
  Bushwick / East Williamsburg / Bronx late-night cluster. Airport pickups run through
  Port-Authority staging lots, so airport incentives may be a different lever. Whether to rank
  airports separately is a business decision; it is not made in the code.

- **F25 Two ranked lists, split on zone type (decided in review).** A naive single ranking is
  **100% airports** in its top 20; the first neighbourhood cell is overall rank 45. The client's
  lever at JFK/LGA is not "incentivise drivers to go there" but staging-lot throughput and Port
  Authority dispatch: a different owner and a different intervention. So the same rule (excess late
  trips, n >= 200) ranks two lists partitioned by `dim_zone.is_airport`, derived from the zone
  lookup's `service_zone` (Airports = 132 JFK, 138 LGA; EWR = 1 Newark), never from the numbers.
  Neighbourhood cells are the incentive decision (top 10); airport cells are escalated to airport
  ops (top 5), shown with the cell's request-to-arrival median beside the zone's month median
  (LGA Wed 23:00: 10.5 vs 4.5 min, dwell 0.92 min, so the delay is supply-side). `overall_rank`
  stays in incentive_cells.csv so the split is auditable. Demo beat: the first answer the data
  gives you is not the answer the ops lead can act on.
- **F26 metrics.csv holds reporting grains only** (202 rows): month, company, company x WAV,
  borough, borough x hour, rainy/dry, rule. Cell-level numbers live only in incentive_cells.csv.
- **F27 Rain is a weak lever (reported as a finding).** +0.9 points raw, +1.1 within hour of day,
  against a 3.4%-37.8% range across borough x hour. evidence.md states it in those words; the
  sentence is generated from the numbers, so it changes if a future month disagrees.

### Phase 5 hardening decisions

- **Atomic outputs.** Every stage writes into `outputs/.staging/<name>-<run_id>/`. Only when all
  seven stages succeed is it swapped into `outputs/<name>/` (the old directory is renamed aside
  first, then removed; its `failed/` history is carried over). A failed or interrupted run deletes
  its staging directory, leaves `outputs/<name>/` byte-identical, and writes
  `outputs/<name>/failed/run_manifest_<run_id>.json` (git-ignored: failed manifests are never
  committed). `docs/validation_rules.md` is rewritten only after a successful full-month publish.
- **Exit codes.** 0 success; 2 source unavailable, incomplete, or schema drift (a missing expected
  column); 3 trust floor (the message carries all three M5 lines); 4 internal error; 130
  interrupted. A new, unexpected column is a WARN, not a failure.
- **Offline sample runs.** `--sample-file` reads trips, zones and weather from local files
  (`config.yml sample`), so tests, CI and `make demo` never touch the network or `data/raw/`.
- **Resource envelope.** DuckDB `memory_limit: 4GB`, `threads: 4`, spill to
  `data/warehouse/tmp`. Measured on the 2026-07 full month: 2:27 wall time, **peak process RSS
  5.7 GB**. The DuckDB limit caps its buffer pool, not every allocation, so a machine with less
  than ~6 GB free should lower `memory_limit` (DuckDB then spills more and runs slower).
- **Hard kills (found in the phase 5 demos).** A killed process cannot clean up. When the
  session running a full-month demo died on 2026-09-25, the published outputs were intact (the
  swap had not happened), but a 64 KB staging directory was left and no failed manifest existed.
  Now each run takes `outputs/.staging/<name>.lock` (pid + run_id). A live lock blocks a second
  run of the same output (exit 4, nothing swept). A lock whose process is dead, or leftover staging
  directories with no lock, mean the previous run was killed: the next run sweeps them and records
  `failed/run_manifest_<old_run_id>.json` with `status: killed`.
- **Floor text is never rounded (found in the phase 5 demos).** The first trust-floor demo printed
  "below the floor 100%" for a 99.999% floor. Shares are now printed without rounding them into a
  different number (`validate.share_text`), for the same reason M5 says 99.998%, not 100.00%.
- **Default resource envelope: 2 threads, 4 GB (decided in review).** Verification run on
  2026-09-25 at commit 28872cd plus this config change: 4:15 wall time, peak RSS 5.33 GB (2:27 and
  5.73 GB at 4 threads). The metrics.csv sha256 was unchanged (774592203b9b5726), as were
  incentive_cells.csv and the charts; only run metadata differed, so the committed outputs
  (generated at a32f2ed) were kept. Halving the threads barely lowers the peak: most of it sits
  outside DuckDB's buffer-pool limit. The recording runs the sample, not the full month.
- **The warehouse is a working store, not an output.** A failed run can leave
  `data/warehouse/<month>.duckdb` with partly rebuilt `stage/clean/model` schemas; the next run
  rebuilds them from `raw.*`, and `raw.*` itself is swapped in atomically by load_raw.

### Phase 6 decisions (sample, offline, CI)

- **The sample is cut from the real month, never synthesised.** `md5(business key) % 400 = 0`
  gives 52,393 of 20,921,249 rows. For the four rules with real examples in the month but none in
  the hash sample, their real rows were appended: R01, R03 and R06 got 5 each, R02 got both of its
  2. The total is 52,410 rows. R04, R05 and R09 have no real example this month and get no rows;
  the per-rule synthetic tests cover them. `data/sample/README.md` lists every rule's month and
  sample counts.
- **Reproducible.** Two consecutive `make sample` builds produced byte-identical files, manifests
  included (the Parquet is written single-threaded; manifests carry no timestamps). `make sample`
  needs a full-month run first (it reads `data/raw/` and the validated warehouse).
- **Offline means offline.** `--offline`, implied by `--sample-file`, reads all four sources from
  `data/sample/`, each verified against its `.manifest.json` sha256 (a mismatch is exit 2) and
  logged as `SOURCE (local sample)`. `fetch()` raises `NetworkInOfflineMode` if called during an
  offline run. The suite also passes inside a Linux network namespace with no interfaces.
- **Speed.** The offline run on the committed sample takes 3.1 s wall at 392 MB peak RSS with
  default console output (budget: 30 s).

The judgement call built from these findings is written up in README section 13.
