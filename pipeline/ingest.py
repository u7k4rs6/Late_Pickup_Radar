"""Stages 1-2: ingest (files + API, with manifests and completeness checks) and load_raw (SQL).

Only this module writes into data/raw/. Every downloaded file gets a sibling
`<file>.manifest.json` (URL, HTTP status, headers, Content-Length, bytes written, SHA-256,
download time). A cached file is reused only if its manifest URL matches the current URL and
its SHA-256 still matches the manifest.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pyarrow.parquet as pq
import requests
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from pipeline import db, logging_setup
from pipeline.errors import CompletenessError, SourceUnavailable, StageCheckFailed
from pipeline.logging_setup import log
from pipeline.manifest import read_json, repo_relative, sha256_file, utc_now, write_json_atomic

NOT_PUBLISHED_STATUSES = (403, 404)  # CloudFront/S3 answers 403 for a missing object
RECORDED_HEADERS = ("Content-Length", "Content-Type", "ETag", "Last-Modified", "Date")


class IncompleteDownload(Exception):
    """Transient: the stream ended early or broke; the download is retried."""


# --------------------------------------------------------------------------- month helpers


def month_start(month: str) -> date:
    y, m = map(int, month.split("-"))
    return date(y, m, 1)


def next_month_start(month: str) -> date:
    d = month_start(month)
    return date(d.year + d.month // 12, d.month % 12 + 1, 1)


def local_hours(month: str, tz: str) -> list[datetime]:
    """Every real local hour of the month (DST-aware), as tz-aware datetimes.

    March has one hour fewer and November one hour more than days * 24 in America/New_York.
    """
    zone = ZoneInfo(tz)
    start = datetime.combine(month_start(month), datetime.min.time(), zone).astimezone(UTC)
    end = datetime.combine(next_month_start(month), datetime.min.time(), zone).astimezone(UTC)
    n = int((end - start).total_seconds() // 3600)
    return [(start + timedelta(hours=i)).astimezone(zone) for i in range(n)]


def wall_clock_hours(month: str, tz: str) -> list[datetime]:
    """Distinct naive wall-clock hour labels, the way TLC's naive timestamps bucket."""
    return sorted({h.replace(tzinfo=None) for h in local_hours(month, tz)})


# --------------------------------------------------------------------------- downloads


def _progress(quiet: bool) -> Progress:
    return Progress(
        TextColumn("  {task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=logging_setup.console,
        disable=quiet,
        transient=True,
    )


def fetch(
    name: str,
    url: str,
    dest: Path,
    dl: dict[str, Any],
    *,
    force: bool = False,
    quiet: bool = True,
    check: Callable[[Path], None] | None = None,
) -> dict[str, Any]:
    """Stream `url` to `dest` atomically (.part then rename), with retries and a manifest.

    `check` validates the .part file before it is renamed into place (e.g. JSON parses), so a
    malformed response never becomes a cached raw file.
    """
    manifest_path = dest.with_name(dest.name + ".manifest.json")
    if dest.exists() and manifest_path.exists() and not force:
        cached = read_json(manifest_path)
        if cached.get("url") == url and sha256_file(dest) == cached.get("sha256"):
            log.info("%s: SKIP (cached, checksum ok) %s", name, dest.name)
            return {**cached, "cached": True}
        log.warning("%s: cache invalid (url or checksum changed); re-downloading", name)

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    attempts, backoff = dl["attempts"], dl["backoff_seconds"]
    last_error: Exception | None = None

    for attempt in range(1, attempts + 1):
        try:
            manifest = _stream_once(name, url, part, dl, quiet)
            if check is not None:
                check(part)
            os.replace(part, dest)
            write_json_atomic(manifest_path, manifest)
            log.info(
                "%s: downloaded %s bytes, sha256 %s…",
                name,
                f"{manifest['bytes_written']:,}",
                manifest["sha256"][:12],
            )
            return {**manifest, "cached": False}
        except SourceUnavailable:
            part.unlink(missing_ok=True)
            raise
        except (requests.RequestException, IncompleteDownload) as exc:
            part.unlink(missing_ok=True)
            last_error = exc
            log.warning("%s: attempt %d/%d failed: %s", name, attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(backoff * 2 ** (attempt - 1))

    raise SourceUnavailable(f"{name}: download failed after {attempts} attempts: {last_error}")


def _stream_once(name: str, url: str, part: Path, dl: dict[str, Any], quiet: bool) -> dict:
    started = utc_now()
    # identity encoding: bytes on disk must equal Content-Length and the server's object.
    headers = {"Accept-Encoding": "identity", "User-Agent": dl["user_agent"]}
    with requests.get(url, stream=True, timeout=dl["timeout_seconds"], headers=headers) as r:
        if r.status_code in NOT_PUBLISHED_STATUSES:
            raise SourceUnavailable(
                f"{name}: HTTP {r.status_code} for {url} (not published or not accessible)"
            )
        if r.status_code != 200:
            raise IncompleteDownload(f"HTTP {r.status_code}")
        length = r.headers.get("Content-Length")
        expected = int(length) if length is not None else None
        digest = hashlib.sha256()
        written = 0
        with part.open("wb") as f, _progress(quiet) as progress:
            task = progress.add_task(name, total=expected)
            for chunk in r.iter_content(chunk_size=dl["chunk_bytes"]):
                f.write(chunk)
                digest.update(chunk)
                written += len(chunk)
                progress.update(task, advance=len(chunk))
        if expected is not None and written != expected:
            raise IncompleteDownload(f"short read: {written} of {expected} bytes")
        return {
            "source": name,
            "url": url,
            "http_status": r.status_code,
            "headers": {h: r.headers[h] for h in RECORDED_HEADERS if h in r.headers},
            "content_length": expected,
            "bytes_written": written,
            "sha256": digest.hexdigest(),
            "download_started_utc": started,
            "download_finished_utc": utc_now(),
        }


# --------------------------------------------------------------------------- completeness


def check_trips(path: Path, month: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """PRD 4.1: footer readable, rows > 0, every day and every hour of the month present."""
    try:
        meta = pq.read_metadata(path)
    except Exception as exc:  # pyarrow raises several types for a corrupt footer
        raise CompletenessError(f"trips: Parquet footer unreadable: {exc}") from exc
    if meta.num_rows <= 0:
        raise CompletenessError("trips: Parquet file has 0 rows")
    schema = check_schema(meta.schema.to_arrow_schema().names, cfg)

    tz = cfg["weather"]["local_timezone"]
    con = db.connect(cfg)
    rows = con.execute(db.render("00_trips_completeness.sql", trips_path=path)).fetchall()
    con.close()
    counts = {hour: n for hour, n in rows}
    expected = wall_clock_hours(month, tz)
    expected_set = set(expected)

    zero_hours = [h for h in expected if h not in counts]
    outside = sum(n for h, n in counts.items() if h not in expected_set)
    in_month = [counts[h] for h in expected if h in counts]
    median = statistics.median(in_month) if in_month else 0
    floor = cfg["completeness"]["low_hour_share"] * median
    thin = [(h, counts[h]) for h in expected if h in counts and counts[h] < floor]
    days = {h.date() for h in expected}
    days_with_rows = {h.date() for h in counts if h in expected_set}

    result = {
        "schema": schema,
        "footer_rows": meta.num_rows,
        "row_groups": meta.num_row_groups,
        "created_by": meta.created_by,
        "pickup_min_hour": str(min(counts)) if counts else None,
        "pickup_max_hour": str(max(counts)) if counts else None,
        "expected_hours": len(expected),
        "hours_with_rows": len(expected) - len(zero_hours),
        "zero_hours": [str(h) for h in zero_hours],
        "thin_hours": [{"hour": str(h), "rows": n} for h, n in thin],
        "median_rows_per_hour": median,
        "expected_days": len(days),
        "days_with_rows": len(days_with_rows),
        "rows_outside_month": outside,
    }
    log.info(
        "trips completeness: %s rows; %d/%d days; %d/%d hours; median %s rows/hour",
        f"{meta.num_rows:,}",
        len(days_with_rows),
        len(days),
        result["hours_with_rows"],
        len(expected),
        f"{median:,.0f}",
    )
    for h, n in thin:
        log.warning(
            "trips: thin hour %s has %d rows (< %.0f%% of median)", h, n, floor / median * 100
        )
    if outside:
        log.warning("trips: %s rows have pickup outside %s (R04 will quarantine)", outside, month)
    if zero_hours or days_with_rows != days:
        raise CompletenessError(
            f"trips: {len(zero_hours)} hour(s) of {month} have no rows, first: {zero_hours[:3]}"
        )
    return result


def check_schema(columns: list[str], cfg: dict[str, Any]) -> dict[str, Any]:
    """Missing expected column = schema drift = exit 2; an extra column is only a WARN."""
    expected = cfg["sources"]["trips_expected_columns"]
    missing = [c for c in expected if c not in columns]
    extra = [c for c in columns if c not in expected]
    if missing:
        raise CompletenessError(f"trips: schema drift, missing column(s) {missing}")
    if extra:
        log.warning("trips: new column(s) not in the source map, ignored by rules: %s", extra)
    return {"columns": len(columns), "missing": missing, "extra": extra}


def local_source(name: str, path: Path) -> dict[str, Any]:
    """Manifest entry for a local (offline) source file used by --sample-file runs."""
    if not path.exists():
        raise SourceUnavailable(f"{name}: local sample source {path} does not exist")
    return {
        "source": name,
        "url": None,
        "local_file": repo_relative(path),
        "bytes_written": path.stat().st_size,
        "sha256": sha256_file(path),
        "cached": True,
    }


def check_zones(path: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames
        ids = [int(r["LocationID"]) for r in reader]
    n = cfg["completeness"]["zone_count"]
    if sorted(ids) != list(range(1, n + 1)):
        raise CompletenessError(f"zones: expected LocationID 1..{n} once each, got {len(ids)} rows")
    log.info("zones completeness: %d zones, IDs 1..%d unique", len(ids), n)
    return {"rows": len(ids), "header": header}


def check_weather_payload(payload: dict[str, Any], month: str, cfg: dict[str, Any]) -> dict:
    """Hourly rows == real local hours of the month; contiguous; response tz asserted."""
    w = cfg["weather"]
    if payload.get("timezone") != w["request_timezone"]:
        raise CompletenessError(
            f"weather: response timezone {payload.get('timezone')!r}, "
            f"expected {w['request_timezone']!r}"
        )
    hourly = payload["hourly"]
    times = [datetime.fromisoformat(t).replace(tzinfo=UTC) for t in hourly["time"]]
    expected = [h.astimezone(UTC) for h in local_hours(month, w["local_timezone"])]
    index = {t: i for i, t in enumerate(times)}
    missing = [t for t in expected if t not in index]
    if missing:
        raise CompletenessError(
            f"weather: {len(missing)} of {len(expected)} local hours missing, first {missing[0]}"
        )
    in_month = [index[t] for t in expected]
    if in_month != list(range(in_month[0], in_month[0] + len(in_month))):
        raise CompletenessError("weather: hourly timestamps are not contiguous")
    nulls = {v: sum(hourly[v][i] is None for i in in_month) for v in w["hourly"]}
    for var, n in nulls.items():
        if n:
            log.warning("weather: %s has %d null hour(s) in %s (kept, not filled)", var, n, month)
    log.info(
        "weather completeness: %d/%d local hours (%s), contiguous; grid cell %.4f, %.4f",
        len(in_month),
        len(expected),
        w["local_timezone"],
        payload["latitude"],
        payload["longitude"],
    )
    return {
        "returned_hours": len(times),
        "expected_local_hours": len(expected),
        "local_hours_present": len(in_month),
        "response_timezone": payload["timezone"],
        "grid_latitude": payload["latitude"],
        "grid_longitude": payload["longitude"],
        "nulls_in_month": nulls,
    }


def weather_url(month: str, cfg: dict[str, Any]) -> str:
    w = cfg["weather"]
    hours = local_hours(month, w["local_timezone"])
    params = {
        "latitude": w["latitude"],
        "longitude": w["longitude"],
        "start_date": hours[0].astimezone(UTC).date().isoformat(),
        "end_date": hours[-1].astimezone(UTC).date().isoformat(),
        "hourly": ",".join(w["hourly"]),
        "timezone": w["request_timezone"],
    }
    return f"{cfg['sources']['weather_url']}?{urlencode(params)}"


def _check_json(path: Path) -> None:
    try:
        payload = json.loads(path.read_text())
        payload["hourly"]["time"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise SourceUnavailable(f"weather: malformed JSON response: {exc}") from exc


def expected_trips(path: Path, month: str, footer_rows: int, cfg: dict[str, Any]) -> dict:
    """S5 sanity band from TLC's monthly aggregate report. Never a hard check."""
    license_class = cfg["sources"]["expected_counts_license_class"]
    with path.open(newline="", encoding="utf-8-sig") as f:
        row = next(
            (
                r
                for r in csv.DictReader(f)
                if r["Month/Year"] == month and r["License Class"] == license_class
            ),
            None,
        )
    if row is None:
        log.warning(
            "aggregate report has no %s row for %s; sanity band skipped", license_class, month
        )
        return {"status": "unavailable"}
    per_day = int(row["Trips Per Day"].replace(",", ""))
    days = (next_month_start(month) - month_start(month)).days
    expected = per_day * days
    delta = footer_rows - expected
    share = abs(delta) / expected
    band = cfg["completeness"]["aggregate_band"]
    status = "within_band" if share <= band else "outside_band"
    log_fn = log.info if status == "within_band" else log.warning
    log_fn(
        "aggregate sanity band: %s/day x %d days = %s expected; parquet %s; "
        "delta %+d (%.4f%%, band %.0f%%)",
        f"{per_day:,}",
        days,
        f"{expected:,}",
        f"{footer_rows:,}",
        delta,
        share * 100,
        band * 100,
    )
    return {
        "status": status,
        "trips_per_day": per_day,
        "days": days,
        "expected_trips": expected,
        "parquet_rows": footer_rows,
        "delta": delta,
        "delta_share": share,
        "band": band,
    }


# --------------------------------------------------------------------------- stages


@dataclass
class IngestResult:
    month: str
    trips_path: Path
    zones_path: Path
    weather_path: Path | None
    sources: dict[str, Any] = field(default_factory=dict)
    completeness: dict[str, Any] = field(default_factory=dict)

    @property
    def footer_rows(self) -> int:
        return self.completeness["trips"]["footer_rows"]


def ingest(
    month: str,
    cfg: dict[str, Any],
    raw_dir: Path,
    *,
    force: bool = False,
    no_weather: bool = False,
    sample_file: Path | None = None,
    quiet: bool = True,
) -> IngestResult:
    src, dl = cfg["sources"], cfg["download"]
    month_dir = raw_dir / month
    sources: dict[str, Any] = {}

    counts_path: Path | None = None
    if sample_file is not None:
        # Offline: every source is a local file; nothing is downloaded or written to data/raw.
        from pipeline.config import resolve_path

        trips_path = sample_file
        sources["trips"] = local_source("trips", sample_file)
        zones_path = resolve_path(cfg["sample"]["zones_file"])
        sources["zones"] = local_source("zones", zones_path)
        weather_path: Path | None = None
        if no_weather:
            log.warning("weather: skipped (--no-weather); weather metrics will be UNAVAILABLE")
            sources["weather"] = {"status": "UNAVAILABLE", "reason": "--no-weather"}
        else:
            weather_path = resolve_path(cfg["sample"]["weather_file"])
            sources["weather"] = local_source("weather", weather_path)
            _check_json(weather_path)
        sources["expected_counts"] = {"status": "not applicable (sample file)"}
        log.info("sample run: trips, zones and weather from local files (no network)")
    else:
        url = src["trips_url_template"].format(month=month)
        trips_path = month_dir / f"fhvhv_tripdata_{month}.parquet"
        sources["trips"] = fetch("trips", url, trips_path, dl, force=force, quiet=quiet)

        zones_path = month_dir / "taxi_zone_lookup.csv"
        sources["zones"] = fetch(
            "zones", src["zones_url"], zones_path, dl, force=force, quiet=quiet
        )

        weather_path = None
        if no_weather:
            log.warning("weather: skipped (--no-weather); weather metrics will be UNAVAILABLE")
            sources["weather"] = {"status": "UNAVAILABLE", "reason": "--no-weather"}
        else:
            weather_path = month_dir / "weather_openmeteo.json"
            sources["weather"] = fetch(
                "weather",
                weather_url(month, cfg),
                weather_path,
                dl,
                force=force,
                quiet=quiet,
                check=_check_json,
            )

        # S5 is a sanity band only: if TLC's site refuses the request, WARN and carry on.
        counts_path = month_dir / "data_reports_monthly.csv"
        try:
            sources["expected_counts"] = fetch(
                "expected_counts",
                src["expected_counts_url"],
                counts_path,
                dl,
                force=force,
                quiet=quiet,
            )
        except SourceUnavailable as exc:
            log.warning("expected_counts unavailable, sanity band skipped: %s", exc)
            sources["expected_counts"] = {"status": "unavailable", "error": str(exc)}
            counts_path = None

    completeness: dict[str, Any] = {"trips": check_trips(trips_path, month, cfg)}
    completeness["zones"] = check_zones(zones_path, cfg)
    if weather_path is not None:
        completeness["weather"] = check_weather_payload(
            json.loads(weather_path.read_text()), month, cfg
        )
    completeness["expected_counts"] = (
        expected_trips(counts_path, month, completeness["trips"]["footer_rows"], cfg)
        if counts_path is not None
        else {"status": "unavailable"}
    )
    return IngestResult(month, trips_path, zones_path, weather_path, sources, completeness)


def load_raw(
    result: IngestResult, warehouse_dir: Path, db_name: str, cfg: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build data/warehouse/<db_name>.duckdb with raw.* tables; swap it in atomically."""
    warehouse_dir.mkdir(parents=True, exist_ok=True)
    final = warehouse_dir / f"{db_name}.duckdb"
    tmp = warehouse_dir / f"{db_name}.duckdb.tmp"
    tmp.unlink(missing_ok=True)
    con = db.connect(cfg, tmp)
    try:
        db.run_file(
            con, "01_load_raw.sql", trips_path=result.trips_path, zones_path=result.zones_path
        )
        if result.weather_path is not None:
            db.run_file(con, "01b_load_raw_weather.sql", weather_path=result.weather_path)
        else:
            db.run_file(con, "01c_empty_weather.sql")
        counts = {
            t: con.execute(f"SELECT count(*) FROM raw.{t}").fetchone()[0]
            for t in ("trips", "zones", "weather")
        }
    finally:
        con.close()

    if counts["trips"] != result.footer_rows:
        tmp.unlink(missing_ok=True)
        raise StageCheckFailed(
            f"raw.trips has {counts['trips']:,} rows but the Parquet footer says "
            f"{result.footer_rows:,}"
        )
    os.replace(tmp, final)
    log.info(
        "raw.trips %s rows == Parquet footer %s rows ✔",
        f"{counts['trips']:,}",
        f"{result.footer_rows:,}",
    )
    return {**counts, "database": repo_relative(final)}
