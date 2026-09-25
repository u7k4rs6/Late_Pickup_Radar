-- Open-Meteo response -> one row per returned hour (times are UTC, as requested).
CREATE OR REPLACE TABLE raw.weather AS
SELECT
    timezone,
    latitude  AS grid_latitude,
    longitude AS grid_longitude,
    unnest(hourly.time)           AS time_utc,
    unnest(hourly.precipitation)  AS precipitation,
    unnest(hourly.temperature_2m) AS temperature_2m,
    unnest(hourly.weather_code)   AS weather_code
FROM read_json({weather_path});
