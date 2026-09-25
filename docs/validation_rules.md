# Validation rules

_Generated from `pipeline/validation_rules.yml` by the validate stage. Do not edit by hand._

| rule_id | severity | rule | condition | excluded from | rationale |
|---|---|---|---|---|---|
| R05 | REJECT | exact duplicate row | `dup_rank > 1` | all metrics | A byte-identical second copy of a trip is a double submission by the base. The first copy (lowest file_row_number) stays in clean.trips with flag_r05_survivor = true. |
| R04 | REJECT | pickup outside target month | `pickup_datetime < <month start> OR pickup_datetime >= <next month start>` | all metrics | Belongs to another month's file; keeping it would double count across months (quarantined as out_of_period). |
| R02 | REJECT | dropoff not after pickup | `dropoff_datetime <= pickup_datetime` | all metrics | A zero or negative trip cannot be a completed trip. |
| R01 | REJECT | pickup before request, not pre-arranged | `wait_minutes < 0 AND NOT (on_scene_datetime < request_datetime)` | all metrics | A negative wait with no sign of pre-arrangement is a clock or entry error. |
| R03 | REJECT | implausibly long wait | `wait_minutes > 180` | all metrics | A wait over 3 hours is not an on-demand pickup wait; it is a data artefact. |
| R06 | REJECT | implausible implied speed | `speed_mph > 65` | all metrics | An average speed over 65 mph for a whole trip is physically implausible in and around NYC. speed_mph = trip_miles / (trip_time / 3600); NULL when trip_time = 0 (29 rows), so a zero duration never divides. |
| R07 | FLAG | trip_time disagrees with timestamps | `abs(duration_gap_seconds) > 120` | trip_duration | Two sources of trip duration disagree by more than 2 minutes. We do not pick a winner: the rows are left out of trip-duration metrics. Neither field enters the KPI. |
| R08 | FLAG | unknown pickup zone | `PULocationID IN (264, 265)` | zone | 264 = Unknown, 265 = Outside of NYC. Kept in the city-wide KPI, excluded from zone-level metrics and from the incentive ranking. |
| R09 | FLAG | on_scene missing | `on_scene_datetime IS NULL` | dwell | Dwell cannot be computed; it is never imputed. |
| R10 | FLAG | zero miles with non-trivial duration | `trip_miles = 0 AND trip_time > 60` | speed | Likely GPS failure; excluded from speed metrics only. |
| R11 | FLAG | negative pay or fare | `driver_pay < 0 OR base_passenger_fare < 0` | pay | Refund or adjustment rows; excluded from pay metrics only. |
| R12 | FLAG | pre-arranged pickup (on_scene before request) | `on_scene_datetime < request_datetime` | wait | The driver was at the pickup before the request timestamp, so request_datetime is not the rider's on-demand ask (for reservations it is the booked pickup time). The row is a real completed trip, so it is kept for volume, but request-to-pickup is not a rider wait: excluded from wait-based metrics (the KPI and p90 wait). This is an assumption: the file has no scheduling field. |
| R13 | FLAG | dwell not captured (on_scene at or after pickup) | `on_scene_datetime >= pickup_datetime` | dwell | on_scene equal to pickup means arrival was not captured and pickup was written in its place: dwell there is zero by construction, not zero in reality. on_scene after pickup is an impossible order. Dwell is computed only where on_scene < pickup, with coverage reported beside it. |
| R14 | FLAG | request on a whole minute (possible reservation) | `second(request_datetime) = 0` | none (sensitivity only) | Reservation request times are booked on whole minutes. On-demand requests land on second 0 by chance 1 time in 60, so dropping ALL whole-minute rows removes most reservations while removing on-demand trips at random (no bias). Not used to exclude rows from the headline KPI; used for a KPI sensitivity line. |
| A01 | ASSUME | wait is request to pickup | n/a | n/a | request_datetime is the rider's ask, so wait = pickup - request (not pickup - on_scene): the rider experiences the whole wait. Where request_datetime is evidently not the ask (R12), the row is excluded from wait metrics instead. |
| A02 | ASSUME | only completed trips | n/a | n/a | Every row is a completed trip; cancellations and unfulfilled requests are not in the data, so the KPI is conditional on a trip happening (a Known Unknown). |
| A03 | ASSUME | one weather point for the city | n/a | n/a | Open-Meteo at Central Park (grid cell over New Jersey) represents the whole city. |

## Evidence and PRD changes

- **R05** 2026-07: 0 exact duplicates. The PRD business key has 20 collisions (20 pairs), but every pair has different request times and fares: they are pooled riders sharing one vehicle, not duplicates. Kept as a guard; business-key collisions are reported, never rejected.
  - _PRD change:_ Business-key collisions are NOT treated as duplicates (they are shared rides).
- **R04** 2026-07: 0 rows. TLC partitions files by pickup time (footer pickup range is exactly the month). Kept as a guard for re-published or future months.
- **R02** 2026-07: 2 rows.
- **R01** 2026-07: 246,534 rows (1.18%) have pickup before request, but 246,243 of them (99.9%) also have on_scene before request: the request timestamp was stamped after the driver arrived, the signature of a pre-arranged ride (see R12). Only 291 rows are negative with no such signal; those are rejected.
  - _PRD change:_ PRD R01 rejected every negative wait. That would quarantine 1.18% of trips, including 55% of Lyft WAV trips, for a reason that is not a data error. Negative waits explained by pre-arrangement move to FLAG R12; the residual stays REJECT. One rule for all segments, not a WAV split: the pattern is ~1.1% in every non-WAV segment of both companies.
- **R03** 2026-07 tail: p99.9 = 29.7 min, p99.99 = 52.3 min, p99.999 = 91.7 min. The log-binned distribution decays smoothly to ~180 min. Above 180 min there are 44 rows: 41 are Lyft, requested between Jun 29 and Jul 7 and picked up up to 2 days later (34 of them > 1,000 min), a submission artefact; 3 are Uber. Between 60 and 180 min the tail is continuous and mostly Uber reservations (65% have a whole-minute request time, see R14), which R14 handles. Cap stays at 180: it cuts exactly the detached artefact cluster.
  - _PRD change:_ PRD suggested deriving the cap from p99.9. p99.9 is 29.7 min and sits inside the smooth body: it would reject ~21,000 genuinely long waits, which are exactly what the KPI measures. The cap is set at the break in the tail instead (180 min, same number as the PRD placeholder).
- **R06** 2026-07: p99 = 36.8, p99.9 = 46.6, p99.99 = 54.0 mph. Counts per 5-mph bin fall ~5x per bin from 50 to 65 mph (6,441 / 1,219 / 190), then scatter: 42 rows above 65 mph. Timestamp-based speed would flag 661 rows, but those have a median dropoff - pickup of 20 seconds against a median trip_time of 1,305 s: the dropoff timestamp is broken there, not the car fast. So speed uses trip_time, as the PRD formula does.
- **R07** 2026-07: 323,291 rows (1.55%); median difference 0 s, p99 absolute difference 161 s.
  - _PRD change:_ PRD said "we trust timestamps". The R06 profile shows dropoff timestamps 20 s after pickup on trips with a 20-minute trip_time, so neither source is trusted blindly.
- **R08** 2026-07: 1,245 rows (all 265, none 264). Dropoff in 264/265 is far more common (978,358 rows, mostly trips leaving NYC) but the decision is about pickup zones.
- **R09** 2026-07: 0 rows. The data dictionary (2025-03-18) says on_scene is WAV-only, but it is populated on every row. Kept as a guard.
- **R10** 2026-07: 1,733 rows (of 1,987 with trip_miles = 0).
- **R11** 2026-07: 4,331 rows (4,326 negative fare, 5 negative driver pay).
- **R12** 2026-07: 366,752 rows (1.75%). 246,243 have a negative wait; 120,509 have wait >= 0 but a median dwell of 11 min. They go to airports far more often (29.7% and 47.3% of dropoffs vs 4.4% for other trips), are longer (median 8.7 vs 2.9 miles) and peak at 04:00-05:00 (4.5% of pickups vs 0.3% in the evening). 98.3% of Uber's negative-wait rows have a whole-minute request time vs 1.7% by chance.
  - _PRD change:_ PRD R12 was informational ("noted, not fixed"). Now it removes rows from wait-based metrics, and absorbs the negative waits PRD R01 would have rejected.
- **R13** 2026-07: on_scene = pickup on 6.60% of Uber trips vs 0.14% of Lyft; on_scene > pickup on 0.003% of Uber trips. Where on_scene < pickup, median dwell is 0.77 min for both companies.
  - _PRD change:_ New rule (not in PRD).
- **R14** 2026-07 Uber: whole-minute requests are 2.3% of 0-10 min waits (chance = 1.7%), 34.4% of 30-60 min waits, 65.5% of 60-180 min waits, 98.3% of negative waits. Lyft does not show the pattern outside negative waits (44.3%).
  - _PRD change:_ New rule (not in PRD).

## Rendered SQL (last run)

```sql
-- Apply pipeline/validation_rules.yml to stage.trips (PRD 5.2). The rule expressions are
-- generated from the YAML by pipeline/validate.py; the fully rendered SQL for the last run
-- is shown in docs/validation_rules.md.
--   quarantine.trips: every REJECTed row, its primary rule_id, every REJECT rule it matched,
--                     and its FLAG columns. Never deleted.
--   clean.trips:      every other row, with one flag_<id> boolean per FLAG rule, plus
--                     flag_r05_survivor on the kept copy of an exact duplicate.
CREATE SCHEMA IF NOT EXISTS quarantine;
CREATE SCHEMA IF NOT EXISTS clean;

CREATE OR REPLACE TEMP TABLE checked AS
SELECT
    *,
    coalesce((dup_rank > 1), false) AS hit_r05,  -- R05 REJECT: exact duplicate row
    coalesce((pickup_datetime < TIMESTAMP '2026-07-01' OR pickup_datetime >= TIMESTAMP '2026-08-01'), false) AS hit_r04,  -- R04 REJECT: pickup outside target month
    coalesce((dropoff_datetime <= pickup_datetime), false) AS hit_r02,  -- R02 REJECT: dropoff not after pickup
    coalesce((wait_minutes < 0 AND NOT (on_scene_datetime < request_datetime)), false) AS hit_r01,  -- R01 REJECT: pickup before request, not pre-arranged
    coalesce((wait_minutes > 180), false) AS hit_r03,  -- R03 REJECT: implausibly long wait
    coalesce((speed_mph > 65), false) AS hit_r06,  -- R06 REJECT: implausible implied speed
    coalesce((abs(duration_gap_seconds) > 120), false) AS hit_r07,  -- R07 FLAG: trip_time disagrees with timestamps
    coalesce((PULocationID IN (264, 265)), false) AS hit_r08,  -- R08 FLAG: unknown pickup zone
    coalesce((on_scene_datetime IS NULL), false) AS hit_r09,  -- R09 FLAG: on_scene missing
    coalesce((trip_miles = 0 AND trip_time > 60), false) AS hit_r10,  -- R10 FLAG: zero miles with non-trivial duration
    coalesce((driver_pay < 0 OR base_passenger_fare < 0), false) AS hit_r11,  -- R11 FLAG: negative pay or fare
    coalesce((on_scene_datetime < request_datetime), false) AS hit_r12,  -- R12 FLAG: pre-arranged pickup (on_scene before request)
    coalesce((on_scene_datetime >= pickup_datetime), false) AS hit_r13,  -- R13 FLAG: dwell not captured (on_scene at or after pickup)
    coalesce((second(request_datetime) = 0), false) AS hit_r14  -- R14 FLAG: request on a whole minute (possible reservation)
FROM stage.trips;

CREATE OR REPLACE TABLE quarantine.trips AS
SELECT
    CASE WHEN hit_r05 THEN 'R05' WHEN hit_r04 THEN 'R04' WHEN hit_r02 THEN 'R02' WHEN hit_r01 THEN 'R01' WHEN hit_r03 THEN 'R03' WHEN hit_r06 THEN 'R06' END AS rule_id,
    list_filter([CASE WHEN hit_r05 THEN 'R05' END, CASE WHEN hit_r04 THEN 'R04' END, CASE WHEN hit_r02 THEN 'R02' END, CASE WHEN hit_r01 THEN 'R01' END, CASE WHEN hit_r03 THEN 'R03' END, CASE WHEN hit_r06 THEN 'R06' END], x -> x IS NOT NULL)  AS reject_rules,
    * EXCLUDE (hit_r05, hit_r04, hit_r02, hit_r01, hit_r03, hit_r06, hit_r07, hit_r08, hit_r09, hit_r10, hit_r11, hit_r12, hit_r13, hit_r14),
    hit_r07 AS flag_r07,
    hit_r08 AS flag_r08,
    hit_r09 AS flag_r09,
    hit_r10 AS flag_r10,
    hit_r11 AS flag_r11,
    hit_r12 AS flag_r12,
    hit_r13 AS flag_r13,
    hit_r14 AS flag_r14
FROM checked
WHERE hit_r05 OR hit_r04 OR hit_r02 OR hit_r01 OR hit_r03 OR hit_r06
ORDER BY trip_id;

CREATE OR REPLACE TABLE clean.trips AS
SELECT
    * EXCLUDE (hit_r05, hit_r04, hit_r02, hit_r01, hit_r03, hit_r06, hit_r07, hit_r08, hit_r09, hit_r10, hit_r11, hit_r12, hit_r13, hit_r14),
    hit_r07 AS flag_r07,
    hit_r08 AS flag_r08,
    hit_r09 AS flag_r09,
    hit_r10 AS flag_r10,
    hit_r11 AS flag_r11,
    hit_r12 AS flag_r12,
    hit_r13 AS flag_r13,
    hit_r14 AS flag_r14,
    dup_count > 1 AS flag_r05_survivor
FROM checked
WHERE NOT (hit_r05 OR hit_r04 OR hit_r02 OR hit_r01 OR hit_r03 OR hit_r06)
ORDER BY trip_id;

DROP TABLE checked;
```
