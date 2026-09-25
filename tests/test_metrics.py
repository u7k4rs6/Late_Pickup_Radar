"""Model + metrics on a tiny hand-computed fixture, and the SQL-vs-YAML exclusion contract."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta

import duckdb
import pandas as pd
import pytest
from test_validate import base_row

from pipeline import metrics, model, profile, report, validate
from pipeline.config import load_config
from pipeline.db import SQL_DIR

CFG = load_config()
RULES = validate.load_rules()
BOUNDS = ("2026-07-01", "2026-08-01")
T12 = datetime(2026, 7, 15, 12, 0, 7)  # Wednesday; local hour 12 is rainy in the fixture
T14 = datetime(2026, 7, 15, 14, 0, 7)  # dry hour


def m(n: float) -> timedelta:
    return timedelta(minutes=n)


def trip(t0: datetime, wait: float, zone: int, on_scene: datetime | None = None) -> dict:
    return base_row(
        request_datetime=t0,
        on_scene_datetime=on_scene if on_scene is not None else t0 + m(wait - 1),
        pickup_datetime=t0 + m(wait),
        dropoff_datetime=t0 + m(wait + 15),
        PULocationID=zone,
    )


FIXTURE = [
    trip(T12, 2, 161),  # a
    trip(T12, 5, 161),  # b
    trip(T12, 12, 161),  # c  late at 8 and 10
    trip(T12, 20, 161, on_scene=T12 + m(20)),  # d  late at 8/10/15; on_scene == pickup (R13)
    trip(T12, 15, 161, on_scene=T12 - m(3)),  # e  pre-arranged (R12): out of wait metrics
    trip(T14, 3, 50),  # f
    trip(T14, 4, 50),  # g
]


def weather_rows() -> pd.DataFrame:
    """Open-Meteo shape; the row labelled 17:00 UTC (= 13:00 EDT) covers 12:00-13:00 EDT."""
    labels = [datetime(2026, 7, 15, h) for h in range(16, 21)]  # UTC 16..20 = EDT 12..16
    return pd.DataFrame(
        {
            "timezone": "GMT",
            "grid_latitude": 40.8,
            "grid_longitude": -74.0,
            "time_utc": [t.strftime("%Y-%m-%dT%H:%M") for t in labels],
            "precipitation": [0.0, 3.0, 0.0, 0.0, 0.0],
            "temperature_2m": [20.0, 21.0, 22.0, 23.0, 24.0],
            "weather_code": [0, 61, 0, 0, 0],
        }
    )


def build(tmp_path, min_cell_trips: int = 1):
    cfg = {**CFG, "incentives": {**CFG["incentives"], "min_cell_trips": min_cell_trips}}
    con = duckdb.connect()
    df = pd.DataFrame(FIXTURE)
    df["PULocationID"] = df["PULocationID"].astype("int32")
    df["DOLocationID"] = df["DOLocationID"].astype("int32")
    con.execute("CREATE SCHEMA raw")
    con.execute(
        "CREATE TABLE raw.trips AS SELECT *, (row_number() OVER () - 1)::BIGINT file_row_number "
        "FROM df"
    )
    con.execute(
        "CREATE TABLE raw.zones AS SELECT i AS LocationID, "
        "CASE WHEN i = 264 THEN 'Unknown' WHEN i = 265 THEN 'N/A' WHEN i <= 100 THEN 'Queens' "
        "ELSE 'Manhattan' END AS Borough, 'Z' || i AS Zone, 'S' AS service_zone "
        "FROM range(1, 266) t(i)"
    )
    con.register("weather_df", weather_rows())
    con.execute("CREATE TABLE raw.weather AS SELECT * FROM weather_df")
    profile.build_stage(con, RULES, None)
    validate.validate(con, "2026-07", cfg, RULES, tmp_path, BOUNDS)
    model.build_model(con, "2026-07", cfg, RULES)
    res = metrics.compute_metrics(con, cfg, tmp_path)
    return con, cfg, res


def value(res, metric, grain="month", dims=""):
    row = res.metrics[
        (res.metrics["metric"] == metric)
        & (res.metrics["grain"] == grain)
        & (res.metrics["dimensions"] == dims)
    ]
    assert len(row) == 1, (metric, grain, dims)
    return row["value"].iloc[0], int(row["n"].iloc[0])


def test_kpi_at_three_thresholds(tmp_path):
    _, _, res = build(tmp_path)
    # wait-eligible: a b c d f g (e is pre-arranged). Late >8: c d; >10: c d; >15: d.
    assert value(res, "M1_late_rate_10") == (pytest.approx(2 / 6, abs=1e-6), 6)
    assert value(res, "M1_late_rate_8")[0] == pytest.approx(2 / 6, abs=1e-6)
    assert value(res, "M1_late_rate_15")[0] == pytest.approx(1 / 6, abs=1e-6)
    kept = value(res, "M1_late_rate_10", dims="variant=pre-arranged kept as measured")
    assert kept == (pytest.approx(3 / 7, abs=1e-6), 7)


def test_p90_wait(tmp_path):
    _, _, res = build(tmp_path)
    # waits [2, 3, 4, 5, 12, 20]: position 0.9 * 5 = 4.5 -> 12 + 0.5 * (20 - 12) = 16
    assert value(res, "M2_p90_wait_minutes") == (pytest.approx(16.0), 6)


def test_dwell_only_where_arrival_captured(tmp_path):
    _, _, res = build(tmp_path)
    # captured: a b c f g (1 min each) and e (18 min); d has on_scene == pickup -> excluded
    assert value(res, "M3_median_dwell_minutes", "company", "company=HV0003") == (1.0, 6)
    assert value(res, "M3_dwell_coverage", "company", "company=HV0003")[0] == pytest.approx(
        6 / 7, abs=1e-6
    )


def test_trust_lines(tmp_path):
    _, _, res = build(tmp_path)
    assert value(res, "M5_trusted_row_share")[0] == 1.0
    assert value(res, "M5_wait_eligible_share")[0] == pytest.approx(6 / 7, abs=1e-6)
    assert value(res, "M5_dwell_coverage")[0] == pytest.approx(6 / 7, abs=1e-6)


def test_rain_joins_the_request_hour_with_preceding_hour_precipitation(tmp_path):
    con, _, res = build(tmp_path)
    hours = dict(
        con.execute(
            "SELECT hour(hour_key), precipitation_mm FROM model.dim_hour "
            "WHERE hour_date = DATE '2026-07-15' AND weather_available"
        ).fetchall()
    )
    assert hours[12] == 3.0 and hours[13] == 0.0  # row labelled 17:00 UTC covers 12-13 EDT
    rainy, n_rainy = value(res, "M4_late_rate_rainy", "month", "threshold_mm=0.5")
    dry, n_dry = value(res, "M4_late_rate_dry", "month", "threshold_mm=0.5")
    assert (rainy, n_rainy) == (0.5, 4) and (dry, n_dry) == (0.0, 2)
    assert value(res, "M4_rain_minus_dry_late_rate", "month", "threshold_mm=0.5")[0] == 0.5
    assert value(res, "M4_wet_hours", "month", "threshold_mm=0.5")[0] == 1


def test_no_weather_marks_m4_unavailable(tmp_path):
    con, cfg, _ = build(tmp_path)
    con.execute("DELETE FROM raw.weather")
    model.build_model(con, "2026-07", cfg, RULES)
    res = metrics.compute_metrics(con, cfg, tmp_path)
    m4 = res.metrics[res.metrics["metric"].str.startswith("M4")]
    assert len(m4) == len(metrics.M4_METRICS)
    assert m4["value"].isna().all() and m4["note"].str.startswith("UNAVAILABLE").all()


def test_event_table_has_no_fake_arrivals(tmp_path):
    con, _, _ = build(tmp_path)
    per_trip = dict(
        con.execute("SELECT trip_key, count(*) FROM model.fact_trip_event GROUP BY 1").fetchall()
    )
    assert per_trip[3] == 3  # d: on_scene == pickup -> no on_scene event
    assert all(v == 4 for k, v in per_trip.items() if k != 3)
    sources = con.execute(
        "SELECT DISTINCT event_type, event_source FROM model.fact_trip_event ORDER BY 1"
    ).fetchall()
    assert ("on_scene", "on_scene_datetime") in sources


def test_cell_ranking_is_excess_late_trips(tmp_path):
    _, _, res = build(tmp_path)
    cells = res.cells.set_index("pu_zone_id")
    city = 2 / 6
    assert cells.loc[161, "excess_late_trips"] == pytest.approx((0.5 - city) * 4, abs=1e-5)
    assert cells.loc[50, "excess_late_trips"] == pytest.approx((0.0 - city) * 2, abs=1e-5)
    assert list(res.cells["pu_zone_id"]) == [161, 50]


def test_cell_floor_is_applied(tmp_path):
    _, _, res = build(tmp_path, min_cell_trips=3)
    assert list(res.cells["pu_zone_id"]) == [161]  # zone 50 has 2 trips


def test_metrics_csv_is_byte_identical_across_runs(tmp_path):
    hashes = []
    for i in range(2):
        out = tmp_path / str(i)
        out.mkdir()
        _, _, res = build(out)
        hashes.append(hashlib.sha256(res.metrics_path.read_bytes()).hexdigest())
    assert hashes[0] == hashes[1]


def test_report_renders(tmp_path):
    con, cfg, res = build(tmp_path)
    ctx = {
        "scope": "fixture",
        "git_sha": "test",
        "completeness": {
            "trips": {
                "footer_rows": 7,
                "days_with_rows": 1,
                "expected_days": 31,
                "hours_with_rows": 2,
                "expected_hours": 744,
            }
        },
        "trust": {"trusted_row_share": 1.0, "wait_eligible_share": 6 / 7},
    }
    rep = report.write_report(con, "2026-07", cfg, RULES, res, ctx, tmp_path)
    text = rep.evidence_path.read_text()
    assert "Evidence table" in text and "Top 20" in text and "Known" in text
    assert len(rep.chart_paths) == 3


FAMILY_FLAGS = {"r14_sensitivity": {"flag_r14"}}


def expected_flags(families: list[str]) -> set[str]:
    out: set[str] = set()
    for f in families:
        out |= FAMILY_FLAGS.get(f) or set(validate.excluding_flags(RULES, f))
    return out


@pytest.mark.parametrize("sql_file", ["07_metrics.sql", "08_incentive_cells.sql", "09_report.sql"])
def test_sql_exclusions_match_validation_rules(sql_file):
    """Every '-- families: x, y' line uses exactly the flags the YAML says x and y exclude."""
    tagged = 0
    for line in (SQL_DIR / sql_file).read_text().splitlines():
        match = re.search(r"-- families: (.+)$", line)
        if not match or line.lstrip().startswith("--"):
            continue
        tagged += 1
        families = [f.strip() for f in match.group(1).split(",")]
        used = set(re.findall(r"flag_r\d+", line.split("--")[0]))
        want = set() if families == ["none"] else expected_flags(families)
        assert used == want, f"{sql_file}: {line.strip()}"
    assert tagged > 0
