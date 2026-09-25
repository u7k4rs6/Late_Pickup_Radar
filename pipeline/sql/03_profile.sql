-- Targeted profiles over stage.trips (PRD 5.1). Each '-- name:' block is one table in
-- outputs/<month>/profile.md. Every ORDER BY is explicit so the report is deterministic.

-- name: wait_percentiles
SELECT
    coalesce(hvfhs_license_num, 'ALL')                        AS company,
    count(*)                                                  AS n,
    round(quantile_cont(wait_minutes, 0.01), 2)               AS p1,
    round(quantile_cont(wait_minutes, 0.05), 2)               AS p5,
    round(quantile_cont(wait_minutes, 0.25), 2)               AS p25,
    round(quantile_cont(wait_minutes, 0.50), 2)               AS p50,
    round(quantile_cont(wait_minutes, 0.75), 2)               AS p75,
    round(quantile_cont(wait_minutes, 0.90), 2)               AS p90,
    round(quantile_cont(wait_minutes, 0.99), 2)               AS p99,
    round(quantile_cont(wait_minutes, 0.999), 2)              AS p99_9,
    round(max(wait_minutes), 1)                               AS max,
    round(min(wait_minutes), 1)                               AS min
FROM stage.trips
GROUP BY ROLLUP (hvfhs_license_num)
ORDER BY hvfhs_license_num NULLS FIRST;

-- name: wait_tail
SELECT
    round(quantile_cont(wait_minutes, 0.9999), 1)             AS p99_99,
    round(quantile_cont(wait_minutes, 0.99999), 1)            AS p99_999,
    count(*) FILTER (WHERE wait_minutes > 60)                 AS over_60,
    count(*) FILTER (WHERE wait_minutes > 120)                AS over_120,
    count(*) FILTER (WHERE wait_minutes > 180)                AS over_180,
    count(*) FILTER (WHERE wait_minutes > 180 AND hvfhs_license_num = 'HV0005') AS over_180_lyft,
    count(*) FILTER (WHERE wait_minutes > 300)                AS over_300,
    count(*) FILTER (WHERE wait_minutes > 1000)               AS over_1000
FROM stage.trips;

-- name: wait_log_histogram
-- 0.05-decade bins of positive waits (for the log-x chart). Bin k covers
-- [10^(k/20), 10^((k+1)/20)) minutes; empty bins are absent here and filled with 0 in Python.
SELECT
    floor(log10(wait_minutes) * 20)::INTEGER                  AS bin_k,
    count(*)                                                  AS n
FROM stage.trips
WHERE wait_minutes > 0
GROUP BY bin_k
ORDER BY bin_k;

-- name: negative_wait_by_segment
-- R01 (negative wait) and R12 (on_scene < request) by company x WAV requested x WAV matched.
SELECT
    hvfhs_license_num                                         AS company,
    wav_request_flag                                          AS wav_request,
    wav_match_flag                                            AS wav_match,
    count(*)                                                  AS n,
    count(*) FILTER (WHERE wait_minutes < 0)                  AS negative_wait,
    round(100 * avg((wait_minutes < 0)::int), 2)              AS pct_negative,
    count(*) FILTER (WHERE on_scene_datetime < request_datetime) AS on_scene_before_request,
    round(100 * avg((on_scene_datetime < request_datetime)::int), 2) AS pct_on_scene_before_request,
    count(*) FILTER (WHERE wait_minutes < 0 AND on_scene_datetime < request_datetime) AS both_signals,
    round(median(wait_minutes) FILTER (WHERE wait_minutes < 0), 2) AS median_negative_wait
FROM stage.trips
GROUP BY ALL
ORDER BY company, wav_request, wav_match;

-- name: negative_wait_vs_on_scene
SELECT
    wait_minutes < 0                                          AS negative_wait,
    on_scene_datetime < request_datetime                      AS on_scene_before_request,
    count(*)                                                  AS n
FROM stage.trips
GROUP BY ALL
ORDER BY negative_wait, on_scene_before_request;

-- name: prearranged_signature
-- Do on_scene-before-request rows look like reservations? Compared with all other trips.
SELECT
    CASE
        WHEN wait_minutes < 0 AND on_scene_datetime < request_datetime THEN 'a negative wait + on_scene < request'
        WHEN on_scene_datetime < request_datetime THEN 'b wait >= 0 + on_scene < request'
        WHEN wait_minutes < 0 THEN 'c negative wait only'
        ELSE 'd other trips'
    END                                                       AS grp,
    count(*)                                                  AS n,
    round(100 * avg((d.Zone ILIKE '%airport%')::int), 1)      AS pct_dropoff_airport,
    round(median(trip_miles), 2)                              AS median_miles,
    round(median(dwell_minutes), 2)                           AS median_dwell,
    round(100 * avg((second(request_datetime) = 0)::int), 1)  AS pct_whole_minute_request
FROM stage.trips t
LEFT JOIN raw.zones d ON d.LocationID = t.DOLocationID
GROUP BY grp
ORDER BY grp;

-- name: prearranged_by_hour
SELECT
    hour(pickup_datetime)                                     AS pickup_hour,
    count(*)                                                  AS n,
    round(100 * avg((on_scene_datetime < request_datetime)::int), 2) AS pct_on_scene_before_request,
    round(100 * avg((wait_minutes < 0)::int), 2)              AS pct_negative_wait
FROM stage.trips
GROUP BY pickup_hour
ORDER BY pickup_hour;

-- name: whole_minute_request_by_wait
-- Reservations are booked on whole minutes; chance level is 1/60 = 1.7%.
SELECT
    hvfhs_license_num                                         AS company,
    CASE
        WHEN wait_minutes < 0 THEN 'a <0'
        WHEN wait_minutes < 10 THEN 'b 0-10'
        WHEN wait_minutes < 20 THEN 'c 10-20'
        WHEN wait_minutes < 30 THEN 'd 20-30'
        WHEN wait_minutes < 60 THEN 'e 30-60'
        WHEN wait_minutes < 180 THEN 'f 60-180'
        ELSE 'g 180+'
    END                                                       AS wait_band,
    count(*)                                                  AS n,
    round(100 * avg((second(request_datetime) = 0)::int), 1)  AS pct_whole_minute_request,
    round(100 * avg((on_scene_datetime < request_datetime)::int), 1) AS pct_on_scene_before_request
FROM stage.trips
GROUP BY ALL
ORDER BY company, wait_band;

-- name: on_scene_vs_pickup
SELECT
    hvfhs_license_num                                         AS company,
    count(*)                                                  AS n,
    round(100 * avg((on_scene_datetime IS NULL)::int), 3)     AS pct_on_scene_null,
    round(100 * avg((on_scene_datetime = pickup_datetime)::int), 2) AS pct_on_scene_eq_pickup,
    round(100 * avg((on_scene_datetime > pickup_datetime)::int), 3) AS pct_on_scene_after_pickup,
    round(100 * avg((on_scene_datetime < pickup_datetime)::int), 2) AS pct_dwell_measurable
FROM stage.trips
GROUP BY company
ORDER BY company;

-- name: dwell_where_measurable
-- Dwell (pickup - on_scene) only where on_scene < pickup.
SELECT
    hvfhs_license_num                                         AS company,
    count(*)                                                  AS n,
    round(quantile_cont(dwell_minutes, 0.25), 2)              AS p25,
    round(quantile_cont(dwell_minutes, 0.50), 2)              AS p50,
    round(quantile_cont(dwell_minutes, 0.75), 2)              AS p75,
    round(quantile_cont(dwell_minutes, 0.90), 2)              AS p90,
    round(quantile_cont(dwell_minutes, 0.99), 2)              AS p99,
    round(max(dwell_minutes), 1)                              AS max
FROM stage.trips
WHERE on_scene_datetime < pickup_datetime
GROUP BY company
ORDER BY company;

-- name: implied_speed
-- speed_mph = trip_miles / (trip_time / 3600); NULL when trip_time = 0.
SELECT
    count(*) FILTER (WHERE trip_time = 0)                     AS trip_time_zero,
    round(quantile_cont(speed_mph, 0.50), 1)                  AS p50,
    round(quantile_cont(speed_mph, 0.90), 1)                  AS p90,
    round(quantile_cont(speed_mph, 0.99), 1)                  AS p99,
    round(quantile_cont(speed_mph, 0.999), 1)                 AS p99_9,
    round(quantile_cont(speed_mph, 0.9999), 1)                AS p99_99,
    round(max(speed_mph), 0)                                  AS max,
    count(*) FILTER (WHERE speed_mph > 55)                    AS over_55,
    count(*) FILTER (WHERE speed_mph > 65)                    AS over_65,
    count(*) FILTER (WHERE speed_mph > 100)                   AS over_100
FROM stage.trips;

-- name: implied_speed_bins
SELECT floor(speed_mph / 5) * 5 AS mph_bin_lo, count(*) AS n
FROM stage.trips
WHERE speed_mph >= 40 AND speed_mph < 100
GROUP BY mph_bin_lo
ORDER BY mph_bin_lo;

-- name: timestamp_speed_check
-- Why speed uses trip_time: rows that look fast by timestamps have broken dropoff times.
SELECT
    count(*)                                                  AS n_over_65_by_timestamps,
    round(median(epoch(dropoff_datetime - pickup_datetime)), 0) AS median_timestamp_seconds,
    round(median(trip_time), 0)                               AS median_trip_time_seconds
FROM stage.trips
WHERE timestamp_trip_minutes > 0 AND trip_miles / (timestamp_trip_minutes / 60.0) > 65;

-- name: duration_agreement
SELECT
    count(*) FILTER (WHERE abs(duration_gap_seconds) > 120)   AS gap_over_120s,
    round(100 * avg((abs(duration_gap_seconds) > 120)::int), 3) AS pct_gap_over_120s,
    round(median(duration_gap_seconds), 1)                    AS median_gap_seconds,
    round(quantile_cont(abs(duration_gap_seconds), 0.99), 1)  AS p99_abs_gap_seconds
FROM stage.trips;

-- name: duplicates
SELECT
    (SELECT count(*) FROM stage.trips WHERE dup_rank > 1)     AS exact_duplicate_extra_rows,
    (SELECT count(*) FROM stage.trips WHERE dup_count > 1)    AS rows_in_exact_duplicate_groups,
    (SELECT count(*) FROM (SELECT count(*) c FROM stage.trips GROUP BY {business_key} HAVING c > 1))
                                                              AS business_key_groups_with_collision,
    (SELECT coalesce(sum(c) - count(*), 0) FROM (SELECT count(*) c FROM stage.trips GROUP BY {business_key} HAVING c > 1))
                                                              AS business_key_extra_rows,
    (SELECT count(*) FROM (SELECT count(DISTINCT request_datetime) k FROM stage.trips GROUP BY {business_key} HAVING count(*) > 1) WHERE k > 1)
                                                              AS collision_groups_with_distinct_requests;

-- name: other_counts
SELECT
    count(*)                                                  AS n,
    count(*) FILTER (WHERE trip_miles = 0)                    AS trip_miles_zero,
    count(*) FILTER (WHERE trip_miles = 0 AND trip_time > 60) AS miles_zero_time_over_60s,
    count(*) FILTER (WHERE driver_pay < 0)                    AS driver_pay_negative,
    count(*) FILTER (WHERE base_passenger_fare < 0)           AS fare_negative,
    count(*) FILTER (WHERE PULocationID = 264)                AS pickup_zone_264,
    count(*) FILTER (WHERE PULocationID = 265)                AS pickup_zone_265,
    round(100 * avg((PULocationID IN (264, 265))::int), 3)    AS pct_pickup_unknown,
    count(*) FILTER (WHERE DOLocationID IN (264, 265))        AS dropoff_zone_264_265,
    count(*) FILTER (WHERE dropoff_datetime <= pickup_datetime) AS dropoff_not_after_pickup,
    count(*) FILTER (WHERE pickup_datetime < {month_start} OR pickup_datetime >= {month_end})
                                                              AS pickup_outside_month
FROM stage.trips;
