-- The decision table: pickup zone x day-of-week x hour-of-day cells, on the request timestamp.
-- Ranked by excess late trips = (cell late rate - citywide late rate) x n, eligible only when
-- n >= {min_cell_trips}. Rate alone rewards tiny cells; volume alone rewards Midtown.
-- The WHERE clauses name the flags they exclude; tests/test_metrics.py checks them against
-- the `excludes` lists in validation_rules.yml.
CREATE OR REPLACE TABLE model.incentive_cells AS
WITH city AS (
    SELECT avg(is_late_{late_main}::INTEGER) AS city_late_rate
    FROM model.fact_trip
    WHERE NOT flag_r12  -- families: wait
),
cells AS (
    SELECT
        pu_zone_id,
        request_dow                                   AS dow,
        request_hour                                  AS hour,
        count(*)                                      AS n,
        sum(is_late_{late_main}::INTEGER)             AS late_trips,
        avg(is_late_{late_main}::INTEGER)             AS late_rate,
        quantile_cont(wait_minutes, 0.9)              AS p90_wait_minutes
    FROM model.fact_trip
    WHERE NOT flag_r12 AND NOT flag_r08  -- families: wait, zone
    GROUP BY ALL
),
scored AS (
    SELECT
        c.*,
        z.borough,
        z.zone,
        city.city_late_rate,
        c.late_rate - city.city_late_rate               AS late_rate_excess,
        (c.late_rate - city.city_late_rate) * c.n       AS excess_late_trips,
        c.n >= {min_cell_trips}                         AS eligible
    FROM cells c
    JOIN model.dim_zone z ON z.zone_id = c.pu_zone_id
    CROSS JOIN city
)
SELECT
    CASE WHEN eligible THEN row_number() OVER (
        PARTITION BY eligible ORDER BY excess_late_trips DESC, pu_zone_id, dow, hour
    ) END                                               AS rank,
    *
FROM scored
ORDER BY rank NULLS LAST, pu_zone_id, dow, hour;
