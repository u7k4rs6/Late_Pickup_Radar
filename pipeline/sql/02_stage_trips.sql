-- Stage: the rows this run works on, with derived durations. Runs at the profile stage.
-- {sample_predicate} is TRUE for a full run, or a deterministic hash on the business key for
-- --sample (md5 is stable across DuckDB versions, unlike hash()).
-- Nothing here changes a raw value; derived columns sit beside the raw ones.
CREATE SCHEMA IF NOT EXISTS stage;

CREATE OR REPLACE TABLE stage.trips AS
SELECT
    file_row_number                                            AS trip_id,
    * EXCLUDE (file_row_number),
    epoch(pickup_datetime - request_datetime) / 60.0           AS wait_minutes,
    epoch(pickup_datetime - on_scene_datetime) / 60.0          AS dwell_minutes,
    trip_time / 60.0                                           AS trip_minutes,  -- trusted source (F15)
    epoch(dropoff_datetime - pickup_datetime) / 60.0           AS timestamp_trip_minutes,
    trip_time - epoch(dropoff_datetime - pickup_datetime)      AS duration_gap_seconds,
    CASE WHEN trip_time > 0 THEN trip_miles / (trip_time / 3600.0) END AS speed_mph,
    row_number() OVER (PARTITION BY {raw_columns} ORDER BY file_row_number) AS dup_rank,
    count(*)     OVER (PARTITION BY {raw_columns})                          AS dup_count
FROM raw.trips
WHERE {sample_predicate};
