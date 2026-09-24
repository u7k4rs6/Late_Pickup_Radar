# Assumptions, thresholds and KUAL

Every threshold in `config.yml` / `pipeline/validation_rules.yml` is justified here.
Every place where real data contradicted the PRD is logged under "PRD deviations".

## Thresholds
## PRD deviations

### Open findings from phase 1 (reported, NOT yet adapted; awaiting decision)

- **F1 `on_scene_datetime` is never null in 2026-07.** Footer statistics show 0 nulls across all
  20,921,249 rows. The PRD expected nulls for some bases (R09) and built the demo judgement call on
  "company asymmetry in on_scene reporting". The data dictionary (2025-03-18) says the field is
  "Accessible Vehicles-only", yet it is populated for every trip. Consequence: R09 will fire on 0
  rows; the asymmetry, if any, is in *how* it is filled (row group 0: `on_scene = pickup` on 6.6% of
  Uber non-WAV vs 0.1% of Lyft non-WAV), not in whether it is filled. To be profiled in phase 3.
- **F2 Lyft WAV trips have `on_scene < request` on ~57% of rows** (row group 0), with a negative
  median request-to-pickup wait (-2.97 min). R01 (pickup < request, REJECT) could quarantine a large
  share of Lyft WAV trips, which would bias WAV metrics. Candidate for phase 3 profiling alongside
  the scheduled-ride analysis.
- **F3 R04 (pickup outside month) will likely fire on 0 rows.** Footer `pickup_datetime` range is
  exactly 2026-07-01 00:00:00 to 2026-07-31 23:59:59, so TLC partitions by pickup. Rule kept as a guard.
- **F4 Open-Meteo local-time output is not DST-aware.** With `timezone=America/New_York` a March
  month returns 744 rows including a non-existent 02:00. The approved "count real local hours" check
  would fail against the API itself unless weather is requested in GMT and converted.
- **F5 Open-Meteo grid cell for Manhattan points is in New Jersey** (40.8084, -74.0199 for both
  Midtown and Central Park). Adds to limitation A03.
- **F6 Unpublished month returns HTTP 403, not 404.** Ingest maps both to exit code 2.
- **F7 Base names unavailable.** SODA dataset `2v9c-2k7f` has no rows for B03404/B03406; `dim_base`
  carries IDs only.
## Known
## Unknown
## Assumption
## Limitation

- Target month 2026-07 includes the July 4 holiday (Saturday); zone x hour-of-week cells average
  over ~4.4 weeks, so the holiday weekend is diluted, not removed.
