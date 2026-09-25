# Validation report: 2026-07

Rules: `pipeline/validation_rules.yml` (rendered in `docs/validation_rules.md`). REJECT rows are filed under the first matching rule in file order; `rows_matching_incl_overlaps` counts every REJECT rule a quarantined row matched. FLAG counts are over clean rows.

## Row flow

| step | rows |
|---|---|
| rows in (stage.trips) | 20,921,249 |
| rejected -> quarantine.trips | 378 |
| rows out (clean.trips) | 20,920,871 |

Reconciliation: clean 20,920,871 + quarantined 378 = 20,921,249 == rows in 20,921,249 ✔

Trusted-row share (M5): **100.00%** (floor 95%).

## Per rule

| rule_id | severity | name | rows | pct_of_rows_in | rows_matching_incl_overlaps | excludes |
|---|---|---|---|---|---|---|
| R05 | REJECT | exact duplicate row | 0 | 0 | 0 |  |
| R04 | REJECT | pickup outside target month | 0 | 0 | 0 |  |
| R02 | REJECT | dropoff not after pickup | 2 | 0 | 2 |  |
| R01 | REJECT | pickup before request, not pre-arranged | 291 | 0.0014 | 291 |  |
| R03 | REJECT | implausibly long wait | 44 | 0.0002 | 44 |  |
| R06 | REJECT | implausible implied speed | 41 | 0.0002 | 42 |  |
| R07 | FLAG | trip_time disagrees with timestamps | 322,986 | 1.5438 |  | trip_duration |
| R08 | FLAG | unknown pickup zone | 1,244 | 0.0059 |  | zone |
| R09 | FLAG | on_scene missing | 0 | 0 |  | dwell |
| R10 | FLAG | zero miles with non-trivial duration | 1,731 | 0.0083 |  | speed |
| R11 | FLAG | negative pay or fare | 4,330 | 0.0207 |  | pay |
| R12 | FLAG | pre-arranged pickup (on_scene before request) | 366,740 | 1.753 |  | wait |
| R13 | FLAG | dwell not captured (on_scene at or after pickup) | 1,012,982 | 4.8419 |  | dwell |
| R14 | FLAG | request on a whole minute (possible reservation) | 655,426 | 3.1328 |  |  |
| R05s | FLAG | survivor of an exact duplicate (kept copy) | 0 | 0 |  |  |

## What this means for the KPI

The headline late-pickup rate (wait > 10 min) is **10.016%** over 20,554,131 trips that can enter wait metrics. R03 removed 44 trips (0.0002% of rows in); if they were kept, the rate would be **10.016%** (+0.00020 points). 366,740 clean trips carry a FLAG that excludes them from wait metrics (R12, pre-arranged); counting their request-to-pickup time as a wait would give **9.924%**. Also dropping every whole-minute request (R14: most remaining reservations plus a random 1/60 of on-demand trips) gives **9.993%**; the gap between that and the headline bounds the effect of reservations R12 does not catch. Trusted-row share is 100.00% against a floor of 95%.

| variant | n | late_rate_8_pct | late_rate_10_pct | late_rate_15_pct |
|---|---|---|---|---|
| a headline: clean, excluding pre-arranged (R12) | 20,554,131 | 17.5292 | 10.0161 | 2.69492 |
| b if R03 rejects were kept | 20,554,175 | 17.5294 | 10.0163 | 2.69513 |
| c if pre-arranged (R12) rows were kept as measured | 20,920,871 | 17.3283 | 9.92366 | 2.70235 |
| d also excluding whole-minute requests (R14) | 20,199,033 | 17.5075 | 9.9935 | 2.6746 |

### By company and WAV request

WAV is reported as its own line: pre-arranged rows are much more common among Lyft WAV trips, so wait coverage is shown beside the rate.

| company | wav_request | clean_rows | rows_in_wait_metrics | wait_coverage_pct | late_rate_10_pct |
|---|---|---|---|---|---|
| HV0003 | N | 15,171,479 | 14,904,333 | 98.24 | 11.298 |
| HV0003 | Y | 43,573 | 41,228 | 94.62 | 27.438 |
| HV0005 | N | 5,682,643 | 5,598,841 | 98.53 | 6.434 |
| HV0005 | Y | 23,176 | 9,729 | 41.98 | 34.731 |
