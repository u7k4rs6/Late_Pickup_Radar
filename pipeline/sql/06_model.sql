-- Star schema `model` (PRD 6.2), built from clean.trips and the raw reference tables.
-- Every FLAG column travels into model.fact_trip, so each metric's exclusion is a WHERE clause
-- on a named flag (see 07_metrics.sql). Nothing is imputed.
CREATE SCHEMA IF NOT EXISTS model;

-- ---------------------------------------------------------------- dim_zone (S2)
CREATE OR REPLACE TABLE model.dim_zone AS
SELECT
    LocationID                              AS zone_id,
    Borough                                 AS borough,
    Zone                                    AS zone,
    service_zone,
    LocationID IN {unknown_zone_ids}        AS is_unknown,   -- 264 Unknown, 265 Outside of NYC
    'taxi_zone_lookup.csv'                  AS source
FROM raw.zones
ORDER BY zone_id;

-- ---------------------------------------------------------------- dim_company (S4)
CREATE OR REPLACE TABLE model.dim_company AS
SELECT company_id, company_name, {companies_source} AS source
FROM (VALUES {company_values}) AS c(company_id, company_name)
ORDER BY company_id;

-- ---------------------------------------------------------------- dim_base
-- Base IDs as they appear in the trip file. Names are not available (assumptions.md F7).
CREATE OR REPLACE TABLE model.dim_base AS
SELECT base_id, NULL::VARCHAR AS base_name, 'trip file (names unavailable)' AS source
FROM (
    SELECT dispatching_base_num AS base_id FROM clean.trips
    UNION
    SELECT originating_base_num FROM clean.trips WHERE originating_base_num IS NOT NULL
)
ORDER BY base_id;

-- ---------------------------------------------------------------- dim_hour (S3)
-- One row per real local hour of the month, keyed on the naive local wall-clock hour (TLC's
-- timestamps are naive local). Open-Meteo precipitation and weather_code at time T summarise the
-- PRECEDING hour, so hour H takes them from the row labelled H + 1h; temperature is
-- instantaneous and is taken at H. In a November fall-back hour the two real hours share one
-- wall-clock label and their values are averaged (TLC timestamps cannot tell them apart either).
CREATE OR REPLACE TABLE model.dim_hour AS
WITH hours AS (
    SELECT DISTINCT timezone({local_tz}, timezone('UTC', ts)) AS hour_key
    FROM generate_series({utc_start}, {utc_end} - INTERVAL 1 HOUR, INTERVAL 1 HOUR) AS g(ts)
),
w AS (
    SELECT
        strptime(time_utc, '%Y-%m-%dT%H:%M')                       AS label_utc,
        precipitation,
        temperature_2m,
        weather_code
    FROM raw.weather
),
interval_values AS (      -- precipitation / weather_code describe [label - 1h, label)
    SELECT
        timezone({local_tz}, timezone('UTC', label_utc - INTERVAL 1 HOUR)) AS hour_key,
        avg(precipitation)                                           AS precipitation_mm,
        max(weather_code)                                            AS weather_code
    FROM w
    GROUP BY 1
),
instant_values AS (       -- temperature is an instant reading at the label
    SELECT
        timezone({local_tz}, timezone('UTC', label_utc))             AS hour_key,
        avg(temperature_2m)                                          AS temperature_c
    FROM w
    GROUP BY 1
)
SELECT
    h.hour_key,
    CAST(h.hour_key AS DATE)                                         AS hour_date,
    isodow(h.hour_key)                                               AS dow,          -- 1 = Monday
    dayname(h.hour_key)                                              AS dow_name,
    hour(h.hour_key)                                                 AS hour_of_day,
    iv.precipitation_mm,
    t.temperature_c,
    iv.weather_code,
    iv.precipitation_mm >= {rain_mm}                                 AS is_rainy,     -- NULL if no weather
    iv.precipitation_mm >= {rain_mm_high}                            AS is_rainy_moderate,
    iv.precipitation_mm IS NOT NULL                                  AS weather_available
FROM hours h
LEFT JOIN interval_values iv USING (hour_key)
LEFT JOIN instant_values t USING (hour_key)
ORDER BY h.hour_key;

-- ---------------------------------------------------------------- fact_trip
CREATE OR REPLACE TABLE model.fact_trip AS
SELECT
    trip_id                                                    AS trip_key,  -- raw file_row_number
    hvfhs_license_num                                          AS company_id,
    dispatching_base_num                                       AS base_id,
    originating_base_num                                       AS originating_base_id,
    PULocationID                                               AS pu_zone_id,
    DOLocationID                                               AS do_zone_id,
    request_datetime                                           AS request_ts,
    on_scene_datetime                                          AS on_scene_ts,
    pickup_datetime                                            AS pickup_ts,
    dropoff_datetime                                           AS dropoff_ts,
    date_trunc('hour', request_datetime)                       AS request_hour_key,
    isodow(request_datetime)                                   AS request_dow,
    hour(request_datetime)                                     AS request_hour,
    wait_minutes,
    dwell_minutes,
    trip_minutes,                                              -- from trip_time (F15)
    timestamp_trip_minutes,
    trip_miles,
    driver_pay,
    base_passenger_fare,
    wait_minutes > {late_low}                                  AS is_late_{late_low},
    wait_minutes > {late_main}                                 AS is_late_{late_main},
    wait_minutes > {late_high}                                 AS is_late_{late_high},
    shared_request_flag = 'Y'                                  AS shared_request,
    wav_request_flag = 'Y'                                     AS wav_request,
    wav_match_flag = 'Y'                                       AS wav_match,
    access_a_ride_flag = 'Y'                                   AS access_a_ride,
    {flag_columns},
    flag_r05_survivor
FROM clean.trips;

-- ---------------------------------------------------------------- fact_trip_event
-- Long form of the workflow: requested -> on_scene -> picked_up -> dropped_off. The on_scene
-- event exists only where arrival was captured (on_scene < pickup); for R09/R13 rows it is
-- absent, not faked. event_source names the raw column each timestamp came from.
CREATE OR REPLACE TABLE model.fact_trip_event AS
SELECT trip_key, 1 AS event_order, 'requested' AS event_type, request_ts AS event_ts,
       'request_datetime' AS event_source
FROM model.fact_trip
UNION ALL
SELECT trip_key, 2, 'on_scene', on_scene_ts, 'on_scene_datetime'
FROM model.fact_trip
WHERE NOT flag_r09 AND NOT flag_r13
UNION ALL
SELECT trip_key, 3, 'picked_up', pickup_ts, 'pickup_datetime'
FROM model.fact_trip
UNION ALL
SELECT trip_key, 4, 'dropped_off', dropoff_ts, 'dropoff_datetime'
FROM model.fact_trip;
