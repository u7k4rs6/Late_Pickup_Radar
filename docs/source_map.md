# Source map

All values below were observed on **2026-09-24/25 (UTC 2026-09-24 ~21:00)** during phase 1
verification. Nothing here is from memory. Where a value can only be established by the full
download (SHA-256 of the trip file, full-file profiles), it is marked _phase 2_ and is written
by `pipeline/ingest.py` into `data/raw/<month>/manifest.json`.

## Summary table

| # | Business question | Information needed | Source system | Retrieval mode | Owner (real-world) | Grain | Known gaps / risks (verified) |
|---|---|---|---|---|---|---|---|
| S1 | How long do riders wait, where, when? | request / on_scene / pickup / dropoff timestamps; PU/DO zone; company; base; shared & WAV flags; pay | NYC TLC HVFHV trip records, monthly Parquet on TLC CloudFront | File (HTTP, Parquet) | NYC TLC (submitted by HVFHS licensees) | One row per trip | Only HV0003/HV0005 present; `on_scene_datetime` has **0 nulls** in 2026-07 (PRD expected nulls); Lyft WAV trips show `on_scene < request` on ~57% of rows in row group 0; `trip_miles` min −0.0 and `trip_time` min 0; `originating_base_num` 27% null; timestamps are naive (no tz) |
| S2 | What is zone 132? Which borough? | Zone ID → borough, zone, service zone | TLC Taxi Zone Lookup CSV | File (CSV) | NYC TLC | One row per zone (1–265) | 264 = `Unknown`/`N/A`; 265 = borough `N/A`, zone `Outside of NYC` |
| S3 | Does weather explain late pickups? | Hourly precipitation, temperature, weather code | Open-Meteo Historical Weather API (archive) | API (JSON) | Open-Meteo (reanalysis) | One row per hour | Model grid, not a station; requested point snaps to a grid cell in **New Jersey**; local-time output is **not DST-aware** (see S3) |
| S4 | Which company is `HV0003`? | License → company name | TLC HVFHV data dictionary (PDF) | Reference doc | NYC TLC | One row per license | Dictionary list is "as of September 2019"; base names not available from the SODA dataset checked (see S4) |
| S5 | Is the file complete? | Expected trips for the month | TLC monthly aggregate report CSV | File (CSV) | NYC TLC | Month × license class | Aggregate is trips/day rounded; used as a sanity band, not a hard check |

## S1: TLC HVFHV trip records (Parquet)

- **Discovery:** URLs taken from the links on the TLC trip record page
  `https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page` (HTTP 200). The page links
  `fhvhv_tripdata_YYYY-MM.parquet` up to **2026-07**.
- **URL template:** `https://d37ci6vzurychx.cloudfront.net/trip-data/fhvhv_tripdata_{YYYY-MM}.parquet`

HEAD results (newest first, as agreed: try 08, then 07, then 06):

| Month | HTTP | Content-Length | Last-Modified | ETag |
|---|---|---|---|---|
| 2026-08 | **403** (`application/xml`, `Error from cloudfront`) | n/a | n/a | n/a |
| 2026-07 | **200** | **511,176,663** (487.5 MiB) | Thu, 17 Sep 2026 14:43:53 GMT | `"0075ffa5d0ecb2946a4b4e8f2d7158a5-30"` |
| 2026-06 | 200 | 506,800,012 | Thu, 17 Sep 2026 14:43:53 GMT | `"f984f6804c41a6f827d155442d807d96-30"` |

Notes:
- An unpublished month returns **403, not 404** (CloudFront in front of S3). Ingest must map
  403 and 404 to exit code 2 ("source unavailable").
- 2026-06 and 2026-07 carry the **same Last-Modified timestamp**, so TLC appears to re-upload
  earlier months in batches. The ETag (multipart, 30 parts) is recorded so a silent re-publish
  is detectable. SHA-256 is computed on download in phase 2.

**Parquet footer (read remotely with HTTP range requests; full file not downloaded yet):**
- Rows: **20,921,249**. Row groups: 20. Writer: `parquet-cpp-arrow version 21.0.0`.
- SHA-256: _phase 2_ (recorded in `data/raw/2026-07/manifest.json`).

Observed schema (25 columns). The names match the PRD's expected list exactly, and `cbd_congestion_fee` is present.
Min / max / null counts come from the footer's row-group statistics:

| # | Column | Arrow type | Min | Max | Nulls |
|---|---|---|---|---|---|
| 0 | hvfhs_license_num | large_string | HV0003 | HV0005 | 0 |
| 1 | dispatching_base_num | large_string | B03404 | B03406 | 0 |
| 2 | originating_base_num | large_string | B00887 | B03406 | 5,682,699 |
| 3 | request_datetime | timestamp[us] | 2026-06-29 14:40:33 | 2026-08-01 00:30:00 | 0 |
| 4 | on_scene_datetime | timestamp[us] | 2026-06-30 23:09:00 | 2026-07-31 23:59:58 | **0** |
| 5 | pickup_datetime | timestamp[us] | 2026-07-01 00:00:00 | 2026-07-31 23:59:59 | 0 |
| 6 | dropoff_datetime | timestamp[us] | 2026-07-01 00:02:11 | 2026-08-01 02:04:28 | 0 |
| 7 | PULocationID | int32 | 1 | 265 | 0 |
| 8 | DOLocationID | int32 | 1 | 265 | 0 |
| 9 | trip_miles | double | -0.0 | 1431.47 | 0 |
| 10 | trip_time | int64 (seconds) | 0 | 49,332 | 0 |
| 11 | base_passenger_fare | double | -225.21 | 1418.32 | 0 |
| 12 | tolls | double | -0.0 | 136.31 | 0 |
| 13 | bcf | double | -0.0 | 37.29 | 0 |
| 14 | sales_tax | double | -0.0 | 119.34 | 0 |
| 15 | congestion_surcharge | double | -0.0 | 2.75 | 0 |
| 16 | airport_fee | double | -0.0 | 9.0 | 0 |
| 17 | tips | double | -0.0 | 307.39 | 0 |
| 18 | driver_pay | double | -18.2 | 1199.35 | 0 |
| 19 | shared_request_flag | large_string | N | Y | 0 |
| 20 | shared_match_flag | large_string | N | Y | 0 |
| 21 | access_a_ride_flag | large_string | N | Y | 0 |
| 22 | wav_request_flag | large_string | N | Y | 0 |
| 23 | wav_match_flag | large_string | N | Y | 0 |
| 24 | cbd_congestion_fee | double | -0.0 | 3.0 | 0 |

Exploratory peek. These are **not pipeline figures**: they come from row group 0 only (1,048,576 rows),
fetched by range request, and are recorded because they bear on PRD rules:

| license | wav_request | rows | % on_scene null | % on_scene = pickup | % on_scene < request | median dwell (min) | median wait (min) |
|---|---|---|---|---|---|---|---|
| HV0003 | N | 725,717 | 0.0 | 6.63 | 2.39 | 0.72 | 4.18 |
| HV0003 | Y | 2,623 | 0.0 | 1.72 | 4.65 | 2.80 | 7.60 |
| HV0005 | N | 318,910 | 0.0 | 0.14 | 2.04 | 0.78 | 4.48 |
| HV0005 | Y | 1,326 | 0.0 | 0.53 | **57.16** | 1.95 | **−2.97** |

**Completeness (plan, executed in phase 2):**
- bytes written == Content-Length.
- footer readable, and row count 20,921,249 > 0.
- pickup min/max cover the month; the footer already shows 2026-07-01 00:00:00 to 2026-07-31 23:59:59.
- every day and every hour has rows.
- the S5 band agrees.

## S2: TLC Taxi Zone Lookup (CSV)

- **URL:** `https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv` (linked from the TLC trip record page)
- HTTP **200**, Content-Length **12,331**, Last-Modified Thu, 22 Feb 2024 21:33:00 GMT, ETag `"c6064b7c144c716450641f769659d178"`
- SHA-256 `1a99e105092230f8620f301edcca7f80d3080642ff404d28ed957d3fa222c8ed`
- 266 lines = header + **265 zones** (LocationID 1–265)
- Schema: `LocationID` (int), `Borough` (str), `Zone` (str), `service_zone` (str)
- Special rows: `264,"Unknown","N/A","N/A"` and `265,"N/A","Outside of NYC","N/A"`. Borough is
  `Unknown` for 264 but `N/A` for 265, so `dim_zone.is_unknown` must be keyed on ID, not borough text.

## S3: Open-Meteo Historical Weather (JSON API)

- **Endpoint:** `https://archive-api.open-meteo.com/v1/archive`
- **Request tested:** `latitude=40.7580&longitude=-73.9855&start_date=2026-07-01&end_date=2026-07-31&hourly=precipitation,temperature_2m,weather_code&timezone=America/New_York`
- HTTP **200**, `application/json; charset=utf-8`, 23,608 bytes, server Date Thu, 24 Sep 2026 21:16:54 GMT
- Response: `timezone=America/New_York`, `timezone_abbreviation=GMT-4`, `utc_offset_seconds=-14400`,
  units `precipitation: mm`, `temperature_2m: °C`, `weather_code: wmo code`
- **744 hourly rows** (2026-07-01T00:00 to 2026-07-31T23:00), 0 nulls in all three variables
- July 2026: precipitation max 14.8 mm/h; **98 hours ≥ 0.5 mm**, 172 hours > 0; temperature 16.1 to 38.4 °C; weather codes 0 to 65

**Surprise 1: grid snapping.** The API returns the grid cell it used, not the requested point:

| Requested point | Returned cell (lat, lon) | Cell location |
|---|---|---|
| Midtown 40.7580, −73.9855 | 40.8084, −74.0199 | New Jersey (west of the Hudson) |
| Central Park 40.7829, −73.9654 | 40.8084, −74.0199 | New Jersey (same cell) |
| Lower Manhattan 40.7128, −74.0060 | 40.7381, −74.0425 | New Jersey (Hoboken / Jersey City) |
| Queens 40.7306, −73.9352 | 40.7381, −73.9149 | Queens, NYC |

`cell_selection=nearest` and `=land` don't return a Manhattan cell either.

**Surprise 2: DST.** With `timezone=America/New_York`, March 2026 returns **744** rows and includes
`2026-03-08T02:00`, a local time that does not exist. November 2025 returns 720 rows with no repeated
01:00. The API emits a fixed 24-rows-per-day local grid, **not** real local hours.
Requesting `timezone=GMT` and converting to America/New_York gives **743** local hours for March 2026, which is correct.

## S4: HVFHV data dictionary (license → company)

- **URL:** `https://www.nyc.gov/assets/tlc/downloads/pdf/data_dictionary_trip_records_hvfhs.pdf`
  (linked from the TLC trip record page). HTTP **200**, 117,347 bytes, 1 page, dated **March 18, 2025**.
- SHA-256 `5458c35b4cb989c69f1211fa9bfe7462d6ba157569de4aa0480e764b2037c41a`
- License map, "as of September 2019": **HV0002 Juno, HV0003 Uber, HV0004 Via, HV0005 Lyft**.
  Only HV0003 and HV0005 appear in 2026-07.
- The field list matches the Parquet schema one-to-one (25 fields, including `cbd_congestion_fee`,
  "starting Jan. 5, 2025").
- **Definition that matters:** `on_scene_datetime` = "Date/time when driver arrived at the pick-up
  location (**Accessible Vehicles-only**)". The observed data has it populated on 100% of rows. See the
  finding in `assumptions.md`.
- Optional base-name lookup: NYC Open Data "FHV Base Aggregate Report" (SODA `2v9c-2k7f`) answers
  HTTP 200, but a query for `base_license_number in ('B03404','B03406')` returns `[]`. The HVFHV
  dispatch bases are not in that dataset, so base names are **not available**; `dim_base` would carry IDs only.

## S5: Completeness reference

- **URL:** `https://www.nyc.gov/assets/tlc/downloads/csv/data_reports_monthly.csv` (linked from
  `https://www.nyc.gov/site/tlc/about/aggregated-reports.page`). HTTP **200**, 79,774 bytes.
- Row for `2026-07, FHV - High Volume`: **674,877 trips per day**; also 5,355 shared trips per day.
- 674,877 × 31 = **20,921,187** expected, against **20,921,249** Parquet rows. Difference +62 (0.0003%); the gap
  is consistent with rounding to whole trips per day. Proposed sanity band: ±1%, WARN outside it, not a hard fail.

## Target month and justification

**2026-07.** It is the newest month TLC has published: the trip record page links it, HEAD returns 200,
and 2026-08 returns 403. The footer covers exactly 2026-07-01 00:00:00 to 2026-07-31 23:59:59 on
`pickup_datetime`, and S5 matches within 0.0003%.
**Limitation:** July contains the July 4 holiday (Saturday in 2026), with atypical demand around it.
The zone × hour-of-week ranking averages over ~4.4 weeks, so one holiday weekend is diluted but
not removed. Stated in KUAL.
