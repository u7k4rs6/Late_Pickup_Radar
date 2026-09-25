-- Load the three raw sources into the DuckDB raw schema with NO transformation (PRD 4.3).
-- raw.weather only unnests Open-Meteo's parallel hourly arrays into rows; values and the
-- UTC time strings are kept exactly as returned.
CREATE SCHEMA IF NOT EXISTS raw;

CREATE OR REPLACE TABLE raw.trips AS
SELECT * FROM read_parquet({trips_path});

CREATE OR REPLACE TABLE raw.zones AS
SELECT * FROM read_csv({zones_path}, header = true);
