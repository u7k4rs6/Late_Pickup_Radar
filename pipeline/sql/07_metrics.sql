-- Metrics M1-M5 (PRD 6.3) in long format: metric, grain, dimensions, value, n, note.
-- Reporting grains only (month, company, company x WAV, borough, borough x hour, rainy/dry,
-- rule). Cell-level numbers (zone x dow x hour) live in incentive_cells.csv only.
-- Every exclusion is an explicit WHERE on a named flag column, tagged "-- families: ...";
-- tests/test_metrics.py checks each tag against the `excludes` lists in validation_rules.yml.
--   wait  -> NOT flag_r12                  (pre-arranged: request is not the rider's ask)
--   zone  -> NOT flag_r08                  (pickup zone 264/265)
--   dwell -> NOT flag_r09 AND NOT flag_r13 (arrival not captured)

-- name: m1_month
-- M1 (KPI) late-pickup rate at the three thresholds, month grain.
SELECT metric, 'month' AS grain, '' AS dimensions, value, n, NULL AS note
FROM (
    SELECT
        avg(is_late_{late_low}::INTEGER)   AS M1_late_rate_{late_low},
        avg(is_late_{late_main}::INTEGER)  AS M1_late_rate_{late_main},
        avg(is_late_{late_high}::INTEGER)  AS M1_late_rate_{late_high},
        count(*)                           AS n
    FROM model.fact_trip
    WHERE NOT flag_r12  -- families: wait
) UNPIVOT (value FOR metric IN (M1_late_rate_{late_low}, M1_late_rate_{late_main}, M1_late_rate_{late_high}));

-- name: m1_month_sensitivity
-- The same KPI with the R12 exclusion undone, and with whole-minute requests (R14) also dropped.
SELECT 'M1_late_rate_{late_main}' AS metric, 'month' AS grain,
       'variant=pre-arranged kept as measured' AS dimensions,
       avg(is_late_{late_main}::INTEGER) AS value, count(*) AS n,
       'sensitivity: R12 exclusion undone' AS note
FROM model.fact_trip  -- families: none
UNION ALL
SELECT 'M1_late_rate_{late_main}', 'month', 'variant=whole-minute requests dropped',
       avg(is_late_{late_main}::INTEGER), count(*), 'sensitivity: R14 rows also excluded'
FROM model.fact_trip
WHERE NOT flag_r12 AND NOT flag_r14;  -- families: wait, r14_sensitivity

-- name: m1_company_wav
-- The KPI by company x WAV request, with wait coverage (share of clean rows that can enter
-- wait metrics) beside it as an M5 line: Lyft WAV coverage is low (pre-arranged rides).
SELECT 'M1_late_rate_{late_main}' AS metric, 'company x wav_request' AS grain,
       'company=' || company_id || '|wav_request=' || wav_request AS dimensions,
       avg(is_late_{late_main}::INTEGER) AS value, count(*) AS n, NULL AS note
FROM model.fact_trip
WHERE NOT flag_r12  -- families: wait
GROUP BY company_id, wav_request
UNION ALL
SELECT 'M5_wait_coverage', 'company x wav_request',
       'company=' || company_id || '|wav_request=' || wav_request,
       avg((NOT flag_r12)::INTEGER), count(*), 'share of clean rows that enter wait metrics'
FROM model.fact_trip  -- families: none
GROUP BY company_id, wav_request;

-- name: m1_borough_hour
-- Heatmap input: late rate by pickup borough x request hour-of-day.
SELECT 'M1_late_rate_{late_main}' AS metric, 'borough x hour_of_day' AS grain,
       'borough=' || z.borough || '|hour=' || lpad(f.request_hour::VARCHAR, 2, '0') AS dimensions,
       avg(f.is_late_{late_main}::INTEGER) AS value, count(*) AS n, NULL AS note
FROM model.fact_trip f
JOIN model.dim_zone z ON z.zone_id = f.pu_zone_id
WHERE NOT f.flag_r12 AND NOT f.flag_r08  -- families: wait, zone
GROUP BY z.borough, f.request_hour;

-- name: m2_month
SELECT 'M2_p90_wait_minutes' AS metric, 'month' AS grain, '' AS dimensions,
       quantile_cont(wait_minutes, 0.9) AS value, count(*) AS n, NULL AS note
FROM model.fact_trip
WHERE NOT flag_r12;  -- families: wait

-- name: m3_dwell
-- Median on-scene dwell where arrival was captured, by company, plus the coverage it rests
-- on (captured / clean rows).
SELECT 'M3_median_dwell_minutes' AS metric, 'company' AS grain, 'company=' || company_id AS dimensions,
       median(dwell_minutes) AS value, count(*) AS n, NULL AS note
FROM model.fact_trip
WHERE NOT flag_r09 AND NOT flag_r13  -- families: dwell
GROUP BY company_id
UNION ALL
SELECT 'M3_dwell_coverage', 'company', 'company=' || company_id,
       avg((NOT flag_r09 AND NOT flag_r13)::INTEGER), count(*), 'share of clean rows with on_scene < pickup'
FROM model.fact_trip  -- families: none
GROUP BY company_id;

-- name: m4_rain
-- Rain sensitivity of the late rate. Weather joins on the REQUEST hour. Raw difference
-- (PRD definition) plus an hour-of-day-adjusted difference: within each hour of day, rainy minus
-- dry, weighted by rainy trips, because wet hours cluster on a few days (Jul 5-6 holiday weekend).
WITH t AS (
    SELECT f.is_late_{late_main}::INTEGER AS late, f.request_hour, z.borough,
           h.is_rainy, h.is_rainy_moderate
    FROM model.fact_trip f
    JOIN model.dim_hour h ON h.hour_key = f.request_hour_key
    JOIN model.dim_zone z ON z.zone_id = f.pu_zone_id
    WHERE NOT f.flag_r12 AND h.weather_available  -- families: wait
),
long AS (
    SELECT late, request_hour, borough, 'threshold_mm={rain_mm}' AS thr, is_rainy AS rainy FROM t
    UNION ALL
    SELECT late, request_hour, borough, 'threshold_mm={rain_mm_high}', is_rainy_moderate FROM t
),
scoped AS (
    SELECT late, request_hour, thr, rainy, 'all' AS borough FROM long
    UNION ALL
    SELECT late, request_hour, thr, rainy, borough FROM long WHERE borough NOT IN ('Unknown', 'N/A')
),
by_hour AS (
    SELECT thr, borough, request_hour,
           avg(late) FILTER (WHERE rainy)      AS rate_rainy,
           avg(late) FILTER (WHERE NOT rainy)  AS rate_dry,
           count(*) FILTER (WHERE rainy)       AS n_rainy
    FROM scoped GROUP BY ALL
),
adjusted AS (
    SELECT thr, borough,
           sum((rate_rainy - rate_dry) * n_rainy) / sum(n_rainy) AS diff_adj,
           sum(n_rainy) AS n_rainy
    FROM by_hour WHERE rate_rainy IS NOT NULL AND rate_dry IS NOT NULL
    GROUP BY ALL
),
raw_rates AS (
    SELECT thr, borough,
           avg(late) FILTER (WHERE rainy)      AS rate_rainy,
           avg(late) FILTER (WHERE NOT rainy)  AS rate_dry,
           count(*) FILTER (WHERE rainy)       AS n_rainy,
           count(*) FILTER (WHERE NOT rainy)   AS n_dry
    FROM scoped GROUP BY ALL
)
SELECT 'M4_rain_minus_dry_late_rate' AS metric,
       CASE WHEN r.borough = 'all' THEN 'month' ELSE 'borough' END AS grain,
       r.thr || CASE WHEN r.borough = 'all' THEN '' ELSE '|borough=' || r.borough END AS dimensions,
       r.rate_rainy - r.rate_dry AS value, r.n_rainy + r.n_dry AS n, 'raw difference' AS note
FROM raw_rates r
UNION ALL
SELECT 'M4_rain_minus_dry_late_rate_hour_adjusted',
       CASE WHEN a.borough = 'all' THEN 'month' ELSE 'borough' END,
       a.thr || CASE WHEN a.borough = 'all' THEN '' ELSE '|borough=' || a.borough END,
       a.diff_adj, a.n_rainy, 'within hour of day, weighted by rainy trips'
FROM adjusted a
UNION ALL
SELECT 'M4_late_rate_rainy', CASE WHEN borough = 'all' THEN 'month' ELSE 'borough' END,
       thr || CASE WHEN borough = 'all' THEN '' ELSE '|borough=' || borough END,
       rate_rainy, n_rainy, NULL
FROM raw_rates
UNION ALL
SELECT 'M4_late_rate_dry', CASE WHEN borough = 'all' THEN 'month' ELSE 'borough' END,
       thr || CASE WHEN borough = 'all' THEN '' ELSE '|borough=' || borough END,
       rate_dry, n_dry, NULL
FROM raw_rates;

-- name: m4_wet_hours
SELECT 'M4_wet_hours' AS metric, 'month' AS grain, 'threshold_mm={rain_mm}' AS dimensions,
       count(*) FILTER (WHERE is_rainy) AS value, count(*) AS n, 'hours of the month at or above threshold' AS note
FROM model.dim_hour WHERE weather_available
UNION ALL
SELECT 'M4_wet_hours', 'month', 'threshold_mm={rain_mm_high}',
       count(*) FILTER (WHERE is_rainy_moderate), count(*), 'hours of the month at or above threshold'
FROM model.dim_hour WHERE weather_available;

-- name: m5_trust
-- Data trust: three lines over rows in, then the share of rows each rule touched.
WITH rows_in AS (SELECT count(*) AS n FROM stage.trips)
SELECT 'M5_trusted_row_share' AS metric, 'month' AS grain, '' AS dimensions,
       (SELECT count(*) FROM model.fact_trip) / n AS value, n, 'not quarantined' AS note
FROM rows_in
UNION ALL
SELECT 'M5_wait_eligible_share', 'month', '',
       (SELECT count(*) FROM model.fact_trip WHERE NOT flag_r12) / n, n,  -- families: wait
       'rows that enter the KPI'
FROM rows_in
UNION ALL
SELECT 'M5_dwell_coverage', 'month', '',
       (SELECT count(*) FROM model.fact_trip WHERE NOT flag_r09 AND NOT flag_r13) / n, n,  -- families: dwell
       'rows with on_scene < pickup'
FROM rows_in
UNION ALL
SELECT 'M5_rule_share', 'rule', 'rule_id=' || rule_id || '|severity=REJECT',
       count(*) / any_value(r.n), count(*), 'primary reject rule'
FROM quarantine.trips CROSS JOIN rows_in r
GROUP BY rule_id;
