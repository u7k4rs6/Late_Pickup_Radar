# Data model

Built in DuckDB by `pipeline/sql/06_model.sql` from `clean.trips` (rows that passed every
REJECT rule) and the raw reference tables. Diagrams: [`workflow.mmd`](workflow.mmd) and
[`erd.mmd`](erd.mmd).

## Workflow (events and states)

```
[requested] --wait--> [on_scene] --dwell--> [picked_up] --in_trip--> [dropped_off]
```

| Element | What it is | Source column | Trust note |
|---|---|---|---|
| requested | rider's ask | `request_datetime` | For pre-arranged rides (on_scene before request, R12) it is a booking time, not the ask |
| on_scene | driver arrives | `on_scene_datetime` | Only where on_scene < pickup. For 6.6% of Uber trips it equals pickup: not captured (R13) |
| picked_up | rider in car | `pickup_datetime` | Anchors the month (TLC partitions files by pickup) |
| dropped_off | trip ends | `dropoff_datetime` | Occasionally broken (R02, R07); duration comes from `trip_time` instead |
| wait | request to pickup | derived | The KPI's duration (A01) |
| dwell | on_scene to pickup | derived | Only where arrival was captured |
| trip | pickup to dropoff | `trip_time` | Trusted over the timestamps (F15) |

- **Interventions (things ops or the rider can change):** zone x hour supply, which is the
  incentive lever; `shared_request_flag`; `wav_request_flag`; the company.
- **Exogenous factor:** precipitation in the request hour.
- **Outcomes:** `is_late_{8,10,15}`, wait, trip duration.

## Star schema (`model.*`)

| Table | Grain | Built from | Notes |
|---|---|---|---|
| `fact_trip` | one row per clean trip | `clean.trips` | Every FLAG column travels in, so each metric's exclusion is a WHERE clause on a named flag |
| `fact_trip_event` | one row per event | `fact_trip` | 4 rows where on_scene < pickup, 3 otherwise; `event_source` names the raw column. A missing arrival is absent, not faked |
| `dim_zone` | zone | `raw.zones` | `is_unknown` for 264/265, keyed on ID (borough text differs between them); `is_airport` from `service_zone` (Airports / EWR = zones 1, 132, 138) |
| `dim_company` | license | config (data dictionary) | `source = data_dictionary_2025-03-18` |
| `dim_base` | base ID | `clean.trips` | Names not available (F7) |
| `dim_hour` | local hour of the month | `raw.weather` | Precipitation for hour H comes from the row labelled H+1 (Open-Meteo sums the preceding hour); `is_rainy` = >= 0.5 mm |

Time in state is a lag over `fact_trip_event`:

```sql
SELECT trip_key, event_type,
       epoch(event_ts - lag(event_ts) OVER (PARTITION BY trip_key ORDER BY event_order)) / 60
           AS minutes_since_previous_event
FROM model.fact_trip_event;
```

## Metric families and the flags that exclude rows from them

| Family | Used by | Excluded when |
|---|---|---|
| wait | M1 (KPI), M2, M4, incentive cells | `flag_r12` (pre-arranged) |
| zone | zone / borough grains, incentive cells | `flag_r08` (pickup zone 264/265) |
| dwell | M3 | `flag_r09`, `flag_r13` (arrival not captured) |
| trip_duration | (none of M1-M5) | `flag_r07` |
| timestamp_duration | (none of M1-M5) | `flag_r02` |

`tests/test_metrics.py` checks that every WHERE clause in `pipeline/sql/07-09` uses exactly these
flags.

## Metrics M1-M5

Definitions and SQL are in `pipeline/sql/07_metrics.sql` and `08_incentive_cells.sql`.
Output: `outputs/<month>/metrics.csv` (long format: metric, grain, dimensions, value, n, note).

| # | Metric | Definition | Grains |
|---|---|---|---|
| M1 (KPI) | Late-pickup rate | `avg(is_late_10)` over wait-eligible clean trips; also at 8 and 15 min | month; company x WAV; borough x hour; zone x dow x hour |
| M2 | p90 wait | `quantile_cont(wait_minutes, 0.9)`, same rows | month; zone x dow x hour |
| M3 | Median dwell | `median(dwell_minutes)` where arrival was captured, with coverage beside it | company; zone |
| M4 | Rain sensitivity | late rate in rainy request hours minus dry; raw and within hour of day; at 0.5 and 2.5 mm | month; borough |
| M5 | Data trust | trusted-row share, wait-eligible share, dwell coverage (all over rows in); per-rule reject share | month; rule; company x WAV |

**Decision table:** `model.incentive_cells` (exported as `outputs/<month>/incentive_cells.csv`),
one row per pickup zone x request day-of-week x request hour. It is ranked by excess late trips =
(cell late rate - citywide late rate) x n, for cells with n >= 200
(`config.yml incentives.min_cell_trips`). The same rule ranks two lists split on
`dim_zone.is_airport`:
- `list = neighbourhood`: the driver-incentive decision.
- `list = airport`: escalate to airport ops.

`overall_rank` keeps the naive single ranking auditable; its top 20 in 2026-07 are all airports.
Each cell also carries its median request-to-arrival and dwell, and the zone's month median
request-to-arrival.
