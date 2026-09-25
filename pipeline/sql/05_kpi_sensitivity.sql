-- Late-pickup rate (KPI M1 preview) under the validation choices, for validation_report.md.
-- {wait_ok} is the generated "row may enter wait metrics" condition (no FLAG with
-- excludes: [wait]). Late = wait_minutes > threshold.

-- name: kpi_variants
WITH base AS (
    SELECT wait_minutes, 'a headline: clean, excluding pre-arranged (R12)' AS variant
    FROM clean.trips WHERE {wait_ok}
    UNION ALL
    SELECT wait_minutes, 'b if R03 rejects were kept'
    FROM clean.trips WHERE {wait_ok}
    UNION ALL
    SELECT wait_minutes, 'b if R03 rejects were kept'
    FROM quarantine.trips WHERE rule_id = 'R03' AND len(reject_rules) = 1 AND {wait_ok}
    UNION ALL
    SELECT wait_minutes, 'c if pre-arranged (R12) rows were kept as measured'
    FROM clean.trips
    UNION ALL
    SELECT wait_minutes, 'd also excluding whole-minute requests (R14)'
    FROM clean.trips WHERE {wait_ok} AND NOT flag_r14
)
SELECT
    variant,
    count(*)                                                      AS n,
    round(100 * avg((wait_minutes > {late_low})::int), 5)         AS late_rate_{late_low}_pct,
    round(100 * avg((wait_minutes > {late_main})::int), 5)        AS late_rate_{late_main}_pct,
    round(100 * avg((wait_minutes > {late_high})::int), 5)        AS late_rate_{late_high}_pct
FROM base
GROUP BY variant
ORDER BY variant;

-- name: kpi_by_segment
SELECT
    hvfhs_license_num                                             AS company,
    wav_request_flag                                              AS wav_request,
    count(*)                                                      AS clean_rows,
    count(*) FILTER (WHERE {wait_ok})                             AS rows_in_wait_metrics,
    round(100 * avg(({wait_ok})::int), 2)                         AS wait_coverage_pct,
    round(100 * avg((wait_minutes > {late_main})::int) FILTER (WHERE {wait_ok}), 3) AS late_rate_{late_main}_pct
FROM clean.trips
GROUP BY ALL
ORDER BY company, wav_request;

-- name: r14_check
-- Are whole-minute requests (R14) a hidden block of reservations inside the KPI?
-- Promotion bar (agreed in review): R14 rows > 2x the late rate of the rest.
SELECT
    count(*) FILTER (WHERE flag_r14)                                         AS whole_minute_rows,
    round(count(*) / 60.0)                                                   AS expected_by_chance,
    count(*) FILTER (WHERE flag_r14) - round(count(*) / 60.0)                AS excess_over_chance,
    round(100 * avg((wait_minutes > {late_main})::int) FILTER (WHERE flag_r14), 3)     AS late_rate_r14_pct,
    round(100 * avg((wait_minutes > {late_main})::int) FILTER (WHERE NOT flag_r14), 3) AS late_rate_rest_pct,
    round(avg((wait_minutes > {late_main})::int) FILTER (WHERE flag_r14)
        / avg((wait_minutes > {late_main})::int) FILTER (WHERE NOT flag_r14), 2)       AS ratio
FROM clean.trips
WHERE {wait_ok};
