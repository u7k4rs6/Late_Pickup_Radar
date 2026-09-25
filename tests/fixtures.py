"""Shared test fixtures: synthetic trips in the real 25-column 2026-07 schema."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

T0 = datetime(2026, 7, 15, 12, 0, 7)


def base_row(**overrides) -> dict:
    """A clean, unremarkable trip: 5 min wait, 1 min dwell, 3 miles in 15 minutes."""
    row = {
        "hvfhs_license_num": "HV0003",
        "dispatching_base_num": "B03404",
        "originating_base_num": "B03404",
        "request_datetime": T0,
        "on_scene_datetime": T0 + timedelta(minutes=4),
        "pickup_datetime": T0 + timedelta(minutes=5),
        "dropoff_datetime": T0 + timedelta(minutes=20),
        "PULocationID": 161,
        "DOLocationID": 237,
        "trip_miles": 3.0,
        "trip_time": 900,
        "base_passenger_fare": 20.0,
        "tolls": 0.0,
        "bcf": 0.5,
        "sales_tax": 1.8,
        "congestion_surcharge": 2.75,
        "airport_fee": 0.0,
        "tips": 0.0,
        "driver_pay": 15.0,
        "shared_request_flag": "N",
        "shared_match_flag": "N",
        "access_a_ride_flag": "N",
        "wav_request_flag": "N",
        "wav_match_flag": "N",
        "cbd_congestion_fee": 0.0,
    }
    row.update(overrides)
    return row


def trips_frame(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["PULocationID"] = df["PULocationID"].astype("int32")
    df["DOLocationID"] = df["DOLocationID"].astype("int32")
    return df


def month_trips(hours: list[datetime], drop_hour: int | None = None) -> list[dict]:
    """One clean trip per wall-clock hour (request at :05:07, 4 min wait, 15 min ride)."""
    rows = []
    for i, h in enumerate(hours):
        if i == drop_hour:
            continue
        t = h + timedelta(minutes=5, seconds=7)
        rows.append(
            base_row(
                request_datetime=t,
                on_scene_datetime=t + timedelta(minutes=3),
                pickup_datetime=t + timedelta(minutes=4),
                dropoff_datetime=t + timedelta(minutes=19),
                PULocationID=i % 263 + 1,
            )
        )
    return rows


def write_parquet(rows: list[dict], path: Path) -> Path:
    trips_frame(rows).to_parquet(path, index=False)
    return path
