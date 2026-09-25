"""Stage 6: M1-M5 (PRD 6.3) into outputs/<month>/metrics.csv, plus the ranked incentive cells.

metrics.csv is long format (metric, grain, dimensions, value, n, note), sorted explicitly and
rounded, so two runs on the same inputs produce byte-identical files (idempotency test).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from pipeline import db
from pipeline.errors import StageCheckFailed
from pipeline.logging_setup import log

COLUMNS = ["metric", "grain", "dimensions", "value", "n", "note"]
M4_METRICS = (
    "M4_rain_minus_dry_late_rate",
    "M4_rain_minus_dry_late_rate_hour_adjusted",
    "M4_late_rate_rainy",
    "M4_late_rate_dry",
    "M4_wet_hours",
)
DECIMALS = 6
CELL_COLUMNS = [
    "list",
    "rank",
    "overall_rank",
    "is_airport",
    "borough",
    "zone",
    "pu_zone_id",
    "dow",
    "hour",
    "n",
    "late_trips",
    "late_rate",
    "city_late_rate",
    "late_rate_excess",
    "excess_late_trips",
    "p90_wait_minutes",
    "median_request_to_arrival_minutes",
    "zone_median_request_to_arrival_minutes",
    "median_dwell_minutes",
]
ROUNDED_CELL_COLUMNS = CELL_COLUMNS[CELL_COLUMNS.index("late_rate") :]


@dataclass
class MetricsResult:
    metrics: pd.DataFrame
    cells: pd.DataFrame
    kpi: float
    weather_available: bool
    metrics_path: Path
    cells_path: Path


def sql_params(cfg: dict[str, Any]) -> dict[str, object]:
    kpi = cfg["kpi"]
    low, high = sorted(kpi["sensitivity_minutes"])
    return {
        "late_low": low,
        "late_main": kpi["late_minutes"],
        "late_high": high,
        "rain_mm": cfg["thresholds"]["rain_mm_per_hour"],
        "rain_mm_high": cfg["thresholds"]["rain_sensitivity_mm_per_hour"],
        "min_cell_trips": cfg["incentives"]["min_cell_trips"],
    }


def compute_metrics(
    con: duckdb.DuckDBPyConnection, cfg: dict[str, Any], out_dir: Path
) -> MetricsResult:
    params = sql_params(cfg)
    db.run_file(con, "08_incentive_cells.sql", **params)
    frames = [con.execute(q).df() for q in db.named_queries("07_metrics.sql", **params).values()]
    metrics = pd.concat([f[COLUMNS] for f in frames if len(f)], ignore_index=True)

    weather = bool(
        con.execute("SELECT bool_or(weather_available) FROM model.dim_hour").fetchone()[0]
    )
    if not weather:
        metrics = metrics[~metrics["metric"].isin(M4_METRICS)]
        unavailable = pd.DataFrame(
            [
                {
                    "metric": m,
                    "grain": "month",
                    "dimensions": "",
                    "value": None,
                    "n": 0,
                    "note": "UNAVAILABLE (no weather data: --no-weather or source failed)",
                }
                for m in M4_METRICS
            ]
        )
        metrics = pd.concat([metrics, unavailable], ignore_index=True)
        log.warning("weather unavailable: M4 metrics marked UNAVAILABLE")

    metrics["value"] = pd.to_numeric(metrics["value"]).round(DECIMALS)
    metrics["n"] = metrics["n"].astype("int64")
    metrics["note"] = metrics["note"].fillna("")
    metrics = metrics.sort_values(["metric", "grain", "dimensions"], kind="mergesort")
    metrics = metrics.reset_index(drop=True)

    main = f"M1_late_rate_{cfg['kpi']['late_minutes']}"
    headline = metrics[
        (metrics["metric"] == main) & (metrics["grain"] == "month") & (metrics["dimensions"] == "")
    ]
    if metrics.empty or headline.empty:
        raise StageCheckFailed("metrics are empty or the headline KPI is missing")
    kpi = float(headline["value"].iloc[0])
    if not 0 <= kpi <= 1:
        raise StageCheckFailed(f"KPI {kpi} is outside [0, 1]")
    missing = {"M1", "M2", "M3", "M4", "M5"} - {m[:2] for m in metrics["metric"]}
    if missing:
        raise StageCheckFailed(f"metrics.csv is missing {sorted(missing)}")

    cells = con.execute(
        f"SELECT {', '.join(CELL_COLUMNS)} FROM model.incentive_cells WHERE eligible "
        "ORDER BY list DESC, rank"
    ).df()
    for c in ROUNDED_CELL_COLUMNS:
        cells[c] = cells[c].round(DECIMALS)

    out_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = out_dir / "metrics.csv"
    metrics.to_csv(metrics_path, index=False, float_format=f"%.{DECIMALS}f", lineterminator="\n")
    cells_path = out_dir / "incentive_cells.csv"
    cells.to_csv(cells_path, index=False, float_format=f"%.{DECIMALS}f", lineterminator="\n")
    log.info(
        "metrics.csv %d rows (M1-M5); %d eligible cells (n >= %d); KPI %.3f%%",
        len(metrics),
        len(cells),
        cfg["incentives"]["min_cell_trips"],
        100 * kpi,
    )
    return MetricsResult(metrics, cells, kpi, weather, metrics_path, cells_path)
