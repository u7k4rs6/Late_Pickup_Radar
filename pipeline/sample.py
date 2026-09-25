"""`make sample`: the committed, deterministic sample in data/sample/ (PRD Section 9).

Built from the real month, never synthesised:
  1. every trip whose md5(business key) % modulus == 0 (the same hash as --sample), then
  2. for any rule that has real examples in the month but none in the hash sample, up to
     `append_per_rule` of those real rows (lowest file_row_number first).
Rules with no real examples in the month (2026-07: R04, R05, R09) get no rows here; the per-rule
synthetic tests in tests/test_validate.py cover them. Zones, weather and the aggregate report are
copied byte-for-byte from data/raw/<month>/ with manifests, so a sample run needs no network.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from pipeline import db, profile, validate
from pipeline.config import resolve, resolve_path
from pipeline.errors import CompletenessError
from pipeline.logging_setup import log
from pipeline.manifest import read_json, repo_relative, sha256_file, write_json_atomic


def manifest_path(path: Path) -> Path:
    return path.with_name(path.name + ".manifest.json")


def write_manifest(path: Path, **extra: Any) -> dict[str, Any]:
    """Checksum manifest next to a local source file (what --offline verifies)."""
    m = {
        "file": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),  # no timestamp: a rebuild of the same data is byte-identical
        **extra,
    }
    write_json_atomic(manifest_path(path), m)
    return m


def _hits_sql(rules: dict[str, Any]) -> str:
    """(trip_id, rule_id) for every rule a row triggered, from the last full validate run."""
    parts = ["SELECT trip_id, unnest(reject_rules) AS rule_id FROM quarantine.trips"]
    for r in rules["rules"]:
        if r["severity"] == "FLAG":
            col = validate.flag(r["id"])
            for table in ("clean.trips", "quarantine.trips"):
                parts.append(f"SELECT trip_id, '{r['id']}' FROM {table} WHERE {col}")
    return "\nUNION ALL\n".join(parts)


def rule_counts_on(trips: Path, cfg: dict[str, Any], rules: dict[str, Any], month: str):
    """Run the real stage + validate SQL on a Parquet file; return {rule_id: rows}."""
    from pipeline.ingest import month_start, next_month_start

    con = db.connect(cfg)
    con.execute("CREATE SCHEMA raw")
    con.execute(
        "CREATE TABLE raw.trips AS SELECT * FROM read_parquet("
        f"{db.sql_literal(trips)}, file_row_number = true)"
    )
    profile.build_stage(con, rules, None)
    with tempfile.TemporaryDirectory() as tmp:
        bounds = (str(month_start(month)), str(next_month_start(month)))
        cfg0 = {**cfg, "thresholds": {**cfg["thresholds"], "trust_floor": 0.0}}
        res = validate.validate(con, month, cfg0, rules, Path(tmp), bounds)
    con.close()
    return {c["rule_id"]: c["rows_matching_incl_overlaps"] or c["rows"] for c in res.rule_counts}


def make_sample(month: str, cfg: dict[str, Any]) -> dict[str, Any]:
    s = cfg["sample"]
    rules = validate.load_rules()
    raw_dir = resolve(cfg, "raw_dir") / month
    trips_raw = raw_dir / f"fhvhv_tripdata_{month}.parquet"
    warehouse = resolve(cfg, "warehouse_dir") / f"{month}.duckdb"
    for need in (trips_raw, warehouse):
        if not need.exists():
            raise CompletenessError(
                f"{repo_relative(need)} is missing: run `make run MONTH={month}` first "
                "(the sample is cut from the real, validated month)"
            )
    raw_manifest = read_json(manifest_path(trips_raw))
    if sha256_file(trips_raw) != raw_manifest["sha256"]:
        raise CompletenessError(f"{trips_raw.name} no longer matches its download manifest")
    log.info(
        "source: %s (sha256 %s… verified)", repo_relative(trips_raw), raw_manifest["sha256"][:12]
    )

    con = db.connect(cfg, warehouse)
    n_raw = con.execute("SELECT count(*) FROM raw.trips").fetchone()[0]
    n_stage = con.execute("SELECT count(*) FROM stage.trips").fetchone()[0]
    if n_stage != n_raw:
        raise CompletenessError(
            f"the warehouse holds a sampled run ({n_stage:,} of {n_raw:,} rows): "
            f"run a full `make run MONTH={month}` first"
        )

    pred = profile.sample_predicate(rules["business_key"], s["modulus"])
    con.execute(f"CREATE TEMP TABLE picked AS SELECT file_row_number FROM raw.trips WHERE {pred}")
    n_hash = con.execute("SELECT count(*) FROM picked").fetchone()[0]
    log.info(
        "hash sample: md5(business key) %% %d = 0 -> %s of %s rows",
        s["modulus"],
        f"{n_hash:,}",
        f"{n_raw:,}",
    )

    con.execute(f"CREATE TEMP TABLE hits AS {_hits_sql(rules)}")
    month_counts = dict(con.execute("SELECT rule_id, count(*) FROM hits GROUP BY 1").fetchall())
    sample_counts = dict(
        con.execute(
            "SELECT rule_id, count(*) FROM hits WHERE trip_id IN (SELECT file_row_number FROM picked) "
            "GROUP BY 1"
        ).fetchall()
    )
    appended: dict[str, list[int]] = {}
    for r in rules["rules"]:
        rid = r["id"]
        if month_counts.get(rid, 0) and not sample_counts.get(rid, 0):
            ids = [
                row[0]
                for row in con.execute(
                    "SELECT DISTINCT trip_id FROM hits WHERE rule_id = ? "
                    "AND trip_id NOT IN (SELECT file_row_number FROM picked) "
                    "ORDER BY trip_id LIMIT ?",
                    [rid, s["append_per_rule"]],
                ).fetchall()
            ]
            appended[rid] = ids
            con.executemany("INSERT INTO picked VALUES (?)", [[i] for i in ids])
            log.info(
                "%s: 0 of %s real rows in the hash sample -> appended %d real rows %s",
                rid,
                f"{month_counts[rid]:,}",
                len(ids),
                ids,
            )

    out = resolve_path(s["trips_file"])
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    con.execute("SET threads = 1")  # single-threaded COPY: byte-identical Parquet on every run
    con.execute(
        "COPY (SELECT * EXCLUDE (file_row_number) FROM raw.trips "
        "WHERE file_row_number IN (SELECT file_row_number FROM picked) "
        f"ORDER BY file_row_number) TO {db.sql_literal(tmp)} (FORMAT parquet, COMPRESSION zstd)"
    )
    n_out = con.execute(f"SELECT count(*) FROM read_parquet({db.sql_literal(tmp)})").fetchone()[0]
    con.close()
    tmp.replace(out)

    copies = {}
    for key, raw_name in (
        ("zones_file", "taxi_zone_lookup.csv"),
        ("weather_file", "weather_openmeteo.json"),
        ("expected_counts_file", "data_reports_monthly.csv"),
    ):
        src = raw_dir / raw_name
        dst = resolve_path(s[key])
        shutil.copyfile(src, dst)
        src_manifest = read_json(manifest_path(src))
        write_manifest(
            dst,
            source="copied byte-for-byte from " + repo_relative(src),
            source_url=src_manifest.get("url"),
            source_sha256=src_manifest["sha256"],
        )
        if sha256_file(dst) != src_manifest["sha256"]:
            raise CompletenessError(f"{dst.name} differs from {src.name} after copy")
        copies[key] = repo_relative(dst)

    in_sample = rule_counts_on(out, cfg, rules, month)
    info = {
        "month": month,
        "source_file": repo_relative(trips_raw),
        "source_sha256": raw_manifest["sha256"],
        "source_rows": n_raw,
        "method": f"md5(concat_ws('|', {', '.join(rules['business_key'])})) % {s['modulus']} = 0",
        "hash_sample_rows": n_hash,
        "appended_real_rows": appended,
        "rows": n_out,
        "rule_rows_in_month": {r["id"]: month_counts.get(r["id"], 0) for r in rules["rules"]},
        "rule_rows_in_sample": {r["id"]: in_sample.get(r["id"], 0) for r in rules["rules"]},
        "copied_sources": copies,
    }
    write_manifest(out, **info)
    readme = write_readme(out.parent, info, rules)
    log.info(
        "sample written: %s (%s rows) + %s", repo_relative(out), f"{n_out:,}", repo_relative(readme)
    )
    return info


def write_readme(directory: Path, info: dict[str, Any], rules: dict[str, Any]) -> Path:
    rows = []
    for r in rules["rules"]:
        rid = r["id"]
        in_month, in_sample = info["rule_rows_in_month"][rid], info["rule_rows_in_sample"][rid]
        if in_sample:
            how = "real rows in the sample" + (
                f" ({len(info['appended_real_rows'][rid])} appended)"
                if rid in info["appended_real_rows"]
                else ""
            )
        elif in_month:
            how = "real rows exist in the month but not in the sample"
        else:
            how = "no real example this month: synthetic test only"
        rows.append(
            f"| {rid} | {r['severity']} | {r['name']} | {in_month:,} | {in_sample:,} | {how} |"
        )
    text = f"""# data/sample: the committed sample

Generated by `make sample` (`pipeline/sample.py`); do not edit by hand. Used by `make demo`, the
test suite and CI, which never touch the network (`--offline` / `--sample-file`).

## How it was made

- Source: `{info["source_file"]}`, sha256 `{info["source_sha256"]}`, {info["source_rows"]:,} rows.
- Deterministic hash sample: `{info["method"]}` gives {info["hash_sample_rows"]:,} rows (the same
  hash as `--sample`, so business-key groups such as pooled riders stay together).
- Rules with real examples in the month but none in the hash sample got up to 5 of their real
  rows appended (lowest `file_row_number` first): {", ".join(f"{k} ({len(v)})" for k, v in info["appended_real_rows"].items()) or "none"}.
- **No rows are fabricated.** Rules with no real example this month are covered only by the
  per-rule synthetic tests in `tests/test_validate.py`.
- Total: **{info["rows"]:,} rows**, same 25-column schema, rows in original file order.

## Which rules have real examples here

"rows in month" counts every row of the month that triggers the rule (including rows another
rule quarantined), from the last full validate run. "rows in sample" comes from running the real
stage and validate SQL on this file (REJECT counts include overlaps; FLAG counts are over the rows
that pass every REJECT rule).

| rule | severity | rule | rows in month | rows in sample | covered by |
|---|---|---|---|---|---|
{chr(10).join(rows)}

## Files

| file | what | checksum |
|---|---|---|
| `hvfhv_sample.parquet` | the sample | `hvfhv_sample.parquet.manifest.json` |
| `zones.csv` | TLC zone lookup, byte-for-byte copy of the raw download | `zones.csv.manifest.json` |
| `weather_sample.json` | raw Open-Meteo response for the month (GMT), byte-for-byte | `weather_sample.json.manifest.json` |
| `data_reports_monthly.csv` | TLC aggregate report, byte-for-byte | `data_reports_monthly.csv.manifest.json` |

Every file is verified against its manifest's sha256 before an offline run uses it.
"""
    path = directory / "README.md"
    path.write_text(text)
    return path
