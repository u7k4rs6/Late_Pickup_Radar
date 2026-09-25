"""End-to-end runs of `python -m pipeline run` on an offline fixture month.

Each failure mode must exit with its code AND leave published outputs untouched:
  0 success (published atomically; rerun is byte-identical)
  2 source unavailable (network dies mid-download: no .part, nothing published)
  3 validation hard-fail (trust floor)
  4 internal error
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from fixtures import base_row, month_trips, write_parquet
from test_ingest import FakeResponse

from pipeline import ingest
from pipeline import metrics as metrics_module
from pipeline.__main__ import main
from pipeline.config import load_config

MONTH = "2026-07"
TZ = "America/New_York"


@pytest.fixture
def env(tmp_path):
    """Config with every path under tmp_path, plus offline trips/zones/weather files."""
    cfg = load_config()
    for key in ("raw_dir", "warehouse_dir", "outputs_dir", "logs_dir"):
        cfg["paths"][key] = str(tmp_path / key)
    cfg["paths"]["staging_dir"] = str(tmp_path / "outputs_dir" / ".staging")
    cfg["duckdb"]["temp_directory"] = str(tmp_path / "duckdb_tmp")
    cfg["download"]["backoff_seconds"] = 0

    zones = tmp_path / "zones.csv"
    lines = ['"LocationID","Borough","Zone","service_zone"']
    for i in range(1, 266):
        sz = "Airports" if i in (132, 138) else "EWR" if i == 1 else "Boro Zone"
        lines.append(f'{i},"{"Queens" if i > 100 else "Manhattan"}","Z{i}","{sz}"')
    zones.write_text("\n".join(lines) + "\n")

    hours = ingest.local_hours(MONTH, TZ)
    start = hours[0].astimezone(UTC).replace(hour=0, tzinfo=None)
    times = [start + timedelta(hours=h) for h in range(32 * 24)]
    weather = tmp_path / "weather.json"
    weather.write_text(
        json.dumps(
            {
                "latitude": 40.8,
                "longitude": -74.0,
                "timezone": "GMT",
                "hourly": {
                    "time": [t.strftime("%Y-%m-%dT%H:%M") for t in times],
                    "precipitation": [1.0 if t.hour % 7 == 0 else 0.0 for t in times],
                    "temperature_2m": [22.0] * len(times),
                    "weather_code": [0] * len(times),
                },
            }
        )
    )
    cfg["sample"] = {"zones_file": str(zones), "weather_file": str(weather)}

    rows = month_trips(ingest.wall_clock_hours(MONTH, TZ))
    t = datetime(2026, 7, 15, 12, 0, 7)
    rows.append(
        base_row(  # R03 reject: 200-minute wait
            request_datetime=t,
            on_scene_datetime=t + timedelta(minutes=199),
            pickup_datetime=t + timedelta(minutes=200),
            dropoff_datetime=t + timedelta(minutes=215),
        )
    )
    rows.append(base_row(request_datetime=t, on_scene_datetime=t - timedelta(minutes=3)))  # R12
    trips = write_parquet(rows, tmp_path / "sample.parquet")

    def config(**overrides) -> Path:
        c = json.loads(json.dumps(cfg))
        for dotted, value in overrides.items():
            section, key = dotted.split(".")
            c[section][key] = value
        path = tmp_path / f"config_{len(list(tmp_path.glob('config_*')))}.yml"
        path.write_text(yaml.safe_dump(c))
        return path

    return {
        "tmp": tmp_path,
        "trips": trips,
        "config": config,
        "outputs": tmp_path / "outputs_dir",
        "raw": tmp_path / "raw_dir",
    }


def run(env, config: Path, *extra: str) -> int:
    return main(
        [
            "run",
            "--month",
            MONTH,
            "--sample-file",
            str(env["trips"]),
            "--quiet",
            "--config",
            str(config),
            *extra,
        ]
    )


def snapshot(directory: Path) -> dict[str, str]:
    """sha256 of every published file, ignoring the git-ignored failed/ history."""
    return {
        str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(directory.rglob("*"))
        if p.is_file() and "failed" not in p.relative_to(directory).parts
    }


def staging_is_empty(env) -> bool:
    staging = env["outputs"] / ".staging"
    return not staging.exists() or not any(staging.iterdir())


def test_success_publishes_atomically_and_rerun_is_byte_identical(env):
    cfg = env["config"]()
    assert run(env, cfg) == 0
    demo = env["outputs"] / "demo"
    first = snapshot(demo)
    for f in (
        "metrics.csv",
        "incentive_cells.csv",
        "evidence.md",
        "validation_report.md",
        "profile.md",
        "run_manifest.json",
    ):
        assert f in first
    assert json.loads((demo / "run_manifest.json").read_text())["status"] == "success"
    assert staging_is_empty(env)

    assert run(env, cfg) == 0
    second = snapshot(demo)
    assert first["metrics.csv"] == second["metrics.csv"]
    assert first["incentive_cells.csv"] == second["incentive_cells.csv"]


def test_no_weather_marks_m4_unavailable_and_everything_else_runs(env):
    assert run(env, env["config"](), "--no-weather") == 0
    demo = env["outputs"] / "demo"
    evidence = (demo / "evidence.md").read_text()
    assert "Rain minus dry late rate" in evidence and "UNAVAILABLE" in evidence
    m4 = [line for line in (demo / "metrics.csv").read_text().splitlines() if line.startswith("M4")]
    assert m4 and all("UNAVAILABLE" in line for line in m4)
    assert "M1_late_rate_10" in (demo / "metrics.csv").read_text()


def test_trust_floor_exits_3_and_leaves_published_outputs_untouched(env):
    assert run(env, env["config"]()) == 0
    demo = env["outputs"] / "demo"
    before = snapshot(demo)

    assert run(env, env["config"](**{"thresholds.trust_floor": 1.0})) == 3
    assert snapshot(demo) == before
    [failed] = (demo / "failed").glob("run_manifest_*.json")
    m = json.loads(failed.read_text())
    assert m["status"] == "failed_at:validate" and m["exit_code"] == 3
    assert {"trusted_row_share", "wait_eligible_share", "dwell_coverage"} <= set(m["trust"])
    assert "wait-eligible share" in m["error"] and "dwell coverage" in m["error"]
    assert staging_is_empty(env)


def test_network_dies_mid_download_exits_2_with_nothing_partial(env, monkeypatch):
    calls = []

    def dying_get(url, **kwargs):
        calls.append(url)
        return FakeResponse(chunks=[b"x" * 1024] * 8, fail_after=3)

    monkeypatch.setattr(ingest.requests, "get", dying_get)
    cfg = env["config"](**{"sources.trips_url_template": "https://example.test/{month}.parquet"})
    code = main(["run", "--month", MONTH, "--quiet", "--config", str(cfg)])
    assert code == 2
    assert len(calls) == 3  # retried with backoff, then gave up
    raw_files = list(env["raw"].rglob("*")) if env["raw"].exists() else []
    assert not [p for p in raw_files if p.is_file()]  # no .part, no half file
    published = env["outputs"] / MONTH
    assert [p.name for p in published.iterdir()] == ["failed"]  # only the failure record
    m = json.loads(next((published / "failed").glob("*.json")).read_text())
    assert m["status"] == "failed_at:ingest" and m["exit_code"] == 2
    assert staging_is_empty(env)


def test_internal_error_exits_4_and_leaves_published_outputs_untouched(env, monkeypatch):
    cfg = env["config"]()
    assert run(env, cfg) == 0
    demo = env["outputs"] / "demo"
    before = snapshot(demo)

    def boom(*args, **kwargs):
        raise RuntimeError("simulated bug in metrics")

    monkeypatch.setattr(metrics_module, "compute_metrics", boom)
    assert run(env, cfg) == 4
    assert snapshot(demo) == before
    [failed] = (demo / "failed").glob("run_manifest_*.json")
    assert json.loads(failed.read_text())["status"] == "failed_at:metrics"
    assert staging_is_empty(env)


def test_failed_history_survives_a_later_successful_publish(env):
    assert run(env, env["config"](**{"thresholds.trust_floor": 1.0})) == 3
    assert run(env, env["config"]()) == 0
    demo = env["outputs"] / "demo"
    assert len(list((demo / "failed").glob("*.json"))) == 1
    assert (demo / "metrics.csv").exists()


def test_hard_killed_run_is_swept_and_recorded_by_the_next_run(env):
    import subprocess
    import sys

    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()  # a real pid that no longer exists, like a SIGKILLed run
    staging_root = env["outputs"] / ".staging"
    (staging_root / "demo-20260101T000000000000Z" / "charts").mkdir(parents=True)
    (staging_root / "demo.lock").write_text(json.dumps({"pid": dead.pid, "run_id": "old"}))

    assert run(env, env["config"]()) == 0
    assert staging_is_empty(env) or [p.name for p in staging_root.iterdir()] == []
    killed = json.loads(
        (
            env["outputs"] / "demo" / "failed" / "run_manifest_20260101T000000000000Z.json"
        ).read_text()
    )
    assert killed["status"] == "killed" and "not touched" in killed["error"]


def test_a_live_run_holding_the_lock_blocks_a_second_run(env):
    import os

    staging_root = env["outputs"] / ".staging"
    staging_root.mkdir(parents=True)
    (staging_root / "demo.lock").write_text(json.dumps({"pid": os.getpid(), "run_id": "live"}))
    (staging_root / "demo-live").mkdir()
    assert run(env, env["config"]()) == 4
    assert (staging_root / "demo-live").exists()  # the live run's staging is not swept
    assert not (env["outputs"] / "demo").exists()
