-- The decision table: pickup zone x day-of-week x hour-of-day cells, on the request timestamp.
-- Ranked by excess late trips = (cell late rate - citywide late rate) x n, eligible only when
-- n >= {min_cell_trips}. Rate alone rewards tiny cells; volume alone rewards Midtown.
-- Two lists with the SAME rule, partitioned on zone type (dim_zone.is_airport, from the zone
-- lookup), never on the numbers:
--   neighbourhood: the driver-incentive decision
--   airport:       escalate to airport ops (staging-lot throughput, Port Authority dispatch)
-- overall_rank is the naive single ranking, kept so the split stays auditable.
-- The WHERE / FILTER clauses name the flags they exclude; tests/test_metrics.py checks them
-- against the `excludes` lists in validation_rules.yml.
CREATE OR REPLACE TABLE model.incentive_cells AS
WITH city AS (
    SELECT avg(is_late_{late_main}::INTEGER) AS city_late_rate
    FROM model.fact_trip
    WHERE NOT flag_r12  -- families: wait
),
trips AS (
    SELECT f.*, z.is_airport
    FROM model.fact_trip f
    JOIN model.dim_zone z ON z.zone_id = f.pu_zone_id
    WHERE NOT f.flag_r12 AND NOT f.flag_r08  -- families: wait, zone
),
zone_base AS (    -- the zone's own request-to-arrival median over the whole month
    SELECT
        pu_zone_id,
        median(request_to_arrival_minutes)
            FILTER (WHERE NOT flag_r09 AND NOT flag_r13)  -- families: dwell
                                                      AS zone_median_request_to_arrival_minutes
    FROM trips
    GROUP BY pu_zone_id
),
cells AS (
    SELECT
        pu_zone_id,
        request_dow                                   AS dow,
        request_hour                                  AS hour,
        any_value(is_airport)                         AS is_airport,
        count(*)                                      AS n,
        sum(is_late_{late_main}::INTEGER)             AS late_trips,
        avg(is_late_{late_main}::INTEGER)             AS late_rate,
        quantile_cont(wait_minutes, 0.9)              AS p90_wait_minutes,
        median(request_to_arrival_minutes)
            FILTER (WHERE NOT flag_r09 AND NOT flag_r13)  -- families: dwell
                                                      AS median_request_to_arrival_minutes,
        median(dwell_minutes)
            FILTER (WHERE NOT flag_r09 AND NOT flag_r13)  -- families: dwell
                                                      AS median_dwell_minutes
    FROM trips
    GROUP BY pu_zone_id, request_dow, request_hour
),
scored AS (
    SELECT
        c.*,
        CASE WHEN c.is_airport THEN 'airport' ELSE 'neighbourhood' END AS list,
        z.borough,
        z.zone,
        b.zone_median_request_to_arrival_minutes,
        city.city_late_rate,
        c.late_rate - city.city_late_rate               AS late_rate_excess,
        (c.late_rate - city.city_late_rate) * c.n       AS excess_late_trips,
        c.n >= {min_cell_trips}                         AS eligible
    FROM cells c
    JOIN model.dim_zone z ON z.zone_id = c.pu_zone_id
    JOIN zone_base b USING (pu_zone_id)
    CROSS JOIN city
)
SELECT
    CASE WHEN eligible THEN row_number() OVER (
        PARTITION BY eligible, list ORDER BY excess_late_trips DESC, pu_zone_id, dow, hour
    ) END                                               AS rank,
    CASE WHEN eligible THEN row_number() OVER (
        PARTITION BY eligible ORDER BY excess_late_trips DESC, pu_zone_id, dow, hour
    ) END                                               AS overall_rank,
    *
FROM scored
ORDER BY list DESC, rank NULLS LAST, pu_zone_id, dow, hour;
