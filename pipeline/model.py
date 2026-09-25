"""Stage 5: build the `model` star schema (PRD 6.2) from clean.trips via sql/06_model.sql."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, timedelta
from typing import Any

import duckdb

from pipeline import db
from pipeline.errors import StageCheckFailed
from pipeline.ingest import local_hours
from pipeline.logging_setup import log
from pipeline.validate import flag

FACT_KEYS = ("trip_key", "company_id", "pu_zone_id", "request_ts", "pickup_ts", "request_hour_key")


@dataclass
class ModelResult:
    rows_in: int
    fact_trips: int
    fact_events: int
    hours: int
    hours_with_weather: int
    wet_hours: int


def _ts(value) -> db.Fragment:
    return db.Fragment(f"TIMESTAMP '{value:%Y-%m-%d %H:%M:%S}'")


def model_params(month: str, cfg: dict[str, Any], rules: dict[str, Any]) -> dict[str, object]:
    kpi = cfg["kpi"]
    low, high = sorted(kpi["sensitivity_minutes"])
    ref = cfg["reference"]
    hours = local_hours(month, cfg["weather"]["local_timezone"])
    r08 = next(r for r in rules["rules"] if r["id"] == "R08")
    flags = [r for r in rules["rules"] if r["severity"] == "FLAG"]
    return {
        "unknown_zone_ids": db.Fragment(
            "(" + ", ".join(map(str, r08["params"]["unknown_zone_ids"])) + ")"
        ),
        "companies_source": ref["companies_source"],
        "company_values": db.Fragment(
            ", ".join(
                f"({db.sql_literal(k)}, {db.sql_literal(v)})"
                for k, v in sorted(ref["companies"].items())
            )
        ),
        "local_tz": cfg["weather"]["local_timezone"],
        "utc_start": _ts(hours[0].astimezone(UTC)),
        "utc_end": _ts(hours[-1].astimezone(UTC) + timedelta(hours=1)),  # exclusive, in UTC
        "rain_mm": cfg["thresholds"]["rain_mm_per_hour"],
        "rain_mm_high": cfg["thresholds"]["rain_sensitivity_mm_per_hour"],
        "late_low": low,
        "late_main": kpi["late_minutes"],
        "late_high": high,
        "flag_columns": db.Fragment(",\n    ".join(flag(r["id"]) for r in flags)),
    }


def build_model(
    con: duckdb.DuckDBPyConnection, month: str, cfg: dict[str, Any], rules: dict[str, Any]
) -> ModelResult:
    db.run_file(con, "06_model.sql", **model_params(month, cfg, rules))

    one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    rows_in = one("SELECT count(*) FROM clean.trips")
    trips = one("SELECT count(*) FROM model.fact_trip")
    events = one("SELECT count(*) FROM model.fact_trip_event")
    arrivals = one("SELECT count(*) FROM model.fact_trip WHERE NOT flag_r09 AND NOT flag_r13")
    hours = one("SELECT count(*) FROM model.dim_hour")
    with_weather = one("SELECT count(*) FROM model.dim_hour WHERE weather_available")
    wet = one("SELECT count(*) FROM model.dim_hour WHERE is_rainy")

    if trips != rows_in:
        raise StageCheckFailed(f"fact_trip has {trips:,} rows, clean.trips has {rows_in:,}")
    nulls = one(
        "SELECT count(*) FROM model.fact_trip WHERE "
        + " OR ".join(f"{k} IS NULL" for k in FACT_KEYS)
    )
    if nulls:
        raise StageCheckFailed(f"{nulls:,} fact_trip rows have a NULL key ({', '.join(FACT_KEYS)})")
    if events != 3 * trips + arrivals:
        raise StageCheckFailed(
            f"fact_trip_event has {events:,} rows, expected 3 x {trips:,} + {arrivals:,} arrivals"
        )
    expected_hours = len(local_hours(month, cfg["weather"]["local_timezone"]))
    if hours not in (expected_hours, expected_hours - 1):  # fall-back hour shares one label
        raise StageCheckFailed(f"dim_hour has {hours} rows, expected {expected_hours}")

    unknown_company = one(
        "SELECT count(*) FROM model.fact_trip f "
        "LEFT JOIN model.dim_company c USING (company_id) WHERE c.company_id IS NULL"
    )
    if unknown_company:
        raise StageCheckFailed(f"{unknown_company:,} trips have a license not in dim_company")

    log.info(
        "fact_trip %s rows; fact_trip_event %s rows (3 x trips + %s captured arrivals) ✔",
        f"{trips:,}",
        f"{events:,}",
        f"{arrivals:,}",
    )
    log.info(
        "dim_hour %d hours, %d with weather, %d wet (>= %s mm)",
        hours,
        with_weather,
        wet,
        cfg["thresholds"]["rain_mm_per_hour"],
    )
    return ModelResult(rows_in, trips, events, hours, with_weather, wet)
