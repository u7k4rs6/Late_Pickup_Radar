-- --no-weather: raw.weather exists with the Open-Meteo shape but zero rows, so the source is
-- visibly absent (weather metrics become UNAVAILABLE) rather than silently missing.
CREATE OR REPLACE TABLE raw.weather (
    timezone        VARCHAR,
    grid_latitude   DOUBLE,
    grid_longitude  DOUBLE,
    time_utc        VARCHAR,
    precipitation   DOUBLE,
    temperature_2m  DOUBLE,
    weather_code    BIGINT
);
