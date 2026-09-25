from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
import requests

from pipeline import ingest
from pipeline.config import load_config
from pipeline.errors import CompletenessError, SourceUnavailable, StageCheckFailed

CFG = load_config()
DL = {**CFG["download"], "backoff_seconds": 0}
TZ = CFG["weather"]["local_timezone"]


# ------------------------------------------------------------------ DST-aware hour counting


@pytest.mark.parametrize(
    ("month", "hours"),
    [("2026-07", 744), ("2026-03", 743), ("2026-11", 721), ("2026-02", 672)],
)
def test_local_hours_are_real_local_hours(month, hours):
    assert len(ingest.local_hours(month, TZ)) == hours


def _weather_payload(times: list[str], tz: str = "GMT") -> dict:
    n = len(times)
    return {
        "latitude": 40.808434,
        "longitude": -74.0199,
        "timezone": tz,
        "hourly": {
            "time": times,
            "precipitation": [0.0] * n,
            "temperature_2m": [20.0] * n,
            "weather_code": [0] * n,
        },
    }


def _utc_hours(start: datetime, n: int) -> list[str]:
    return [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(n)]


@pytest.mark.parametrize("month", ["2026-03", "2026-11", "2026-07"])
def test_weather_check_accepts_utc_response_covering_every_local_hour(month):
    hours = ingest.local_hours(month, TZ)
    first = hours[0].astimezone(UTC).replace(hour=0)  # API returns whole UTC days
    last = hours[-1].astimezone(UTC).replace(hour=23)
    n = int((last - first).total_seconds() // 3600) + 1
    out = ingest.check_weather_payload(_weather_payload(_utc_hours(first, n)), month, CFG)
    assert out["local_hours_present"] == len(hours)


def test_weather_check_rejects_days_times_24_local_grid_in_march():
    """What Open-Meteo returns with timezone=America/New_York: 744 rows incl. a fake 02:00."""
    local_grid = _utc_hours(datetime(2026, 3, 1), 31 * 24)
    with pytest.raises(CompletenessError):
        ingest.check_weather_payload(_weather_payload(local_grid, TZ), "2026-03", CFG)


def test_weather_check_rejects_a_missing_hour():
    hours = ingest.local_hours("2026-07", TZ)
    times = _utc_hours(hours[0].astimezone(UTC), len(hours))
    del times[100]
    with pytest.raises(CompletenessError, match="missing"):
        ingest.check_weather_payload(_weather_payload(times), "2026-07", CFG)


# ------------------------------------------------------------------ downloads


class FakeResponse:
    def __init__(self, status=200, chunks=(b"abc", b"def"), fail_after=None, length=None):
        self.status_code = status
        self._chunks = list(chunks)
        self._fail_after = fail_after
        total = sum(len(c) for c in self._chunks)
        self.headers = {"Content-Length": str(length if length is not None else total)}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size):
        for i, chunk in enumerate(self._chunks):
            if self._fail_after is not None and i == self._fail_after:
                raise requests.ConnectionError("connection reset mid-stream")
            yield chunk


def _patch_get(monkeypatch, responses):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return responses[min(len(calls), len(responses)) - 1]

    monkeypatch.setattr(ingest.requests, "get", fake_get)
    return calls


def test_mid_stream_connection_error_leaves_no_partial_file(tmp_path, monkeypatch):
    dest = tmp_path / "raw" / "trips.parquet"
    calls = _patch_get(monkeypatch, [FakeResponse(chunks=[b"x" * 10] * 5, fail_after=2)])
    with pytest.raises(SourceUnavailable, match="after 3 attempts"):
        ingest.fetch("trips", "https://example.test/t.parquet", dest, DL)
    assert len(calls) == DL["attempts"]
    assert list(dest.parent.iterdir()) == []  # no .part, no file, no manifest


def test_short_read_is_retried_then_succeeds(tmp_path, monkeypatch):
    dest = tmp_path / "f.bin"
    _patch_get(monkeypatch, [FakeResponse(length=999), FakeResponse()])
    man = ingest.fetch("f", "https://example.test/f", dest, DL)
    assert dest.read_bytes() == b"abcdef"
    assert man["bytes_written"] == man["content_length"] == 6
    assert not dest.with_name("f.bin.part").exists()


@pytest.mark.parametrize("status", [403, 404])
def test_unpublished_month_fails_fast_without_retry(tmp_path, monkeypatch, status):
    calls = _patch_get(monkeypatch, [FakeResponse(status=status)])
    with pytest.raises(SourceUnavailable, match=f"HTTP {status}") as exc:
        ingest.fetch("trips", "https://example.test/x", tmp_path / "x", DL)
    assert exc.value.exit_code == 2
    assert len(calls) == 1


def test_cached_file_is_skipped_by_checksum(tmp_path, monkeypatch):
    dest = tmp_path / "f.bin"
    calls = _patch_get(monkeypatch, [FakeResponse()])
    ingest.fetch("f", "https://example.test/f", dest, DL)
    again = ingest.fetch("f", "https://example.test/f", dest, DL)
    assert again["cached"] is True and len(calls) == 1
    ingest.fetch("f", "https://example.test/f", dest, DL, force=True)
    assert len(calls) == 2


def test_tampered_cache_is_redownloaded(tmp_path, monkeypatch):
    dest = tmp_path / "f.bin"
    calls = _patch_get(monkeypatch, [FakeResponse()])
    ingest.fetch("f", "https://example.test/f", dest, DL)
    dest.write_bytes(b"tampered")
    ingest.fetch("f", "https://example.test/f", dest, DL)
    assert len(calls) == 2 and dest.read_bytes() == b"abcdef"


def test_malformed_json_never_becomes_a_cached_file(tmp_path, monkeypatch):
    dest = tmp_path / "weather.json"
    _patch_get(monkeypatch, [FakeResponse(chunks=[b"{not json"])])
    with pytest.raises(SourceUnavailable, match="malformed JSON"):
        ingest.fetch("weather", "https://example.test/w", dest, DL, check=ingest._check_json)
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------------------ completeness + load_raw


def _trips_parquet(path: Path, month: str, drop_hour: int | None = None) -> Path:
    hours = ingest.wall_clock_hours(month, TZ)
    rows = [h + timedelta(minutes=5) for i, h in enumerate(hours) if i != drop_hour]
    table = pa.table(
        {
            "request_datetime": pa.array(
                [t - timedelta(minutes=4) for t in rows], pa.timestamp("us")
            ),
            "pickup_datetime": pa.array(rows, pa.timestamp("us")),
        }
    )
    pq.write_table(table, path)
    return path


def test_trips_check_passes_with_every_hour_present(tmp_path):
    out = ingest.check_trips(_trips_parquet(tmp_path / "t.parquet", "2026-07"), "2026-07", CFG)
    assert out["footer_rows"] == 744 and out["zero_hours"] == []


def test_trips_check_uses_dst_wall_clock_hours_in_march(tmp_path):
    out = ingest.check_trips(_trips_parquet(tmp_path / "t.parquet", "2026-03"), "2026-03", CFG)
    assert out["expected_hours"] == 743


def test_trips_check_fails_on_a_zero_hour(tmp_path):
    path = _trips_parquet(tmp_path / "t.parquet", "2026-07", drop_hour=200)
    with pytest.raises(CompletenessError, match="1 hour"):
        ingest.check_trips(path, "2026-07", CFG)


def test_trips_check_fails_on_unreadable_footer(tmp_path):
    bad = tmp_path / "bad.parquet"
    bad.write_bytes(b"not a parquet file")
    with pytest.raises(CompletenessError, match="footer"):
        ingest.check_trips(bad, "2026-07", CFG)


def _zones_csv(path: Path, n: int = 265) -> Path:
    lines = ['"LocationID","Borough","Zone","service_zone"']
    lines += [f'{i},"B","Z{i}","S"' for i in range(1, n + 1)]
    path.write_text("\n".join(lines) + "\n")
    return path


def test_zone_check_requires_all_265_ids(tmp_path):
    assert ingest.check_zones(_zones_csv(tmp_path / "z.csv"), CFG)["rows"] == 265
    with pytest.raises(CompletenessError):
        ingest.check_zones(_zones_csv(tmp_path / "z2.csv", n=264), CFG)


def test_load_raw_reconciles_row_counts(tmp_path):
    trips = _trips_parquet(tmp_path / "t.parquet", "2026-07")
    hours = ingest.local_hours("2026-07", TZ)
    weather = tmp_path / "w.json"
    weather.write_text(
        json.dumps(_weather_payload(_utc_hours(hours[0].astimezone(UTC), len(hours))))
    )
    result = ingest.IngestResult(
        "2026-07",
        trips,
        _zones_csv(tmp_path / "z.csv"),
        weather,
        completeness={"trips": {"footer_rows": 744}},
    )
    counts = ingest.load_raw(result, tmp_path / "wh", "2026-07")
    assert (counts["trips"], counts["zones"], counts["weather"]) == (744, 265, 744)
    assert not (tmp_path / "wh" / "2026-07.duckdb.tmp").exists()

    result.completeness["trips"]["footer_rows"] = 745
    with pytest.raises(StageCheckFailed):
        ingest.load_raw(result, tmp_path / "wh", "2026-07")


def test_aggregate_band_is_recorded_not_enforced(tmp_path):
    csv_path = tmp_path / "agg.csv"
    csv_path.write_text(
        'Month/Year,License Class,Trips Per Day\n2026-07,FHV - High Volume,"674,877"\n'
    )
    ok = ingest.expected_trips(csv_path, "2026-07", 20_921_249, CFG)
    assert (ok["expected_trips"], ok["delta"], ok["status"]) == (20_921_187, 62, "within_band")
    off = ingest.expected_trips(csv_path, "2026-07", 10_000_000, CFG)
    assert off["status"] == "outside_band"  # a WARN, never an exception
