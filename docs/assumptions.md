# Assumptions, thresholds and KUAL

Every threshold in `config.yml` / `pipeline/validation_rules.yml` is justified here.
Every place where real data contradicted the PRD is logged under "PRD deviations".

## Thresholds

| Name | Value | Where | Rationale |
|---|---|---|---|
| `download.attempts` / `backoff_seconds` | 3 / 2 s (doubling) | config.yml | PRD 4.1; covers transient CDN errors without hiding an unpublished month |
| `completeness.low_hour_share` | 0.05 | config.yml | PRD 4.1: an hour below 5% of the median hour is a real-world gap worth a WARN, not a failure |
| `completeness.aggregate_band` | 0.01 | config.yml | F8: observed delta vs TLC aggregate is 0.0003%; 1% leaves room for rounding and late corrections |
## PRD deviations

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

## Known
## Unknown
## Assumption
## Limitation

- Target month 2026-07 includes the July 4 holiday (Saturday); zone x hour-of-week cells average
  over ~4.4 weeks, so the holiday weekend is diluted, not removed.
