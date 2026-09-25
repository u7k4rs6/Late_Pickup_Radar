-- Completeness check for the trip file (PRD 4.1), run directly on the downloaded Parquet.
-- Hourly pickup counts on the file's naive local wall clock; Python compares them to the
-- month's expected wall-clock hours (DST-aware) and flags zero or thin hours.
SELECT
    date_trunc('hour', pickup_datetime) AS pickup_hour,
    count(*)                            AS n_trips
FROM read_parquet({trips_path})
GROUP BY pickup_hour
ORDER BY pickup_hour;
