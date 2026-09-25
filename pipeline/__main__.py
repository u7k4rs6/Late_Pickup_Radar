"""CLI entry point: ``python -m pipeline {run,show} ...``."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from pipeline import __version__
from pipeline.errors import EXIT_INTERNAL, EXIT_OK, PipelineError

SHOW_TARGETS = ("quarantine", "flags", "metrics", "manifest", "top-cells")


def month_arg(value: str) -> str:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise argparse.ArgumentTypeError(f"expected YYYY-MM, got {value!r}")
    return value


def fraction_arg(value: str) -> float:
    f = float(value)
    if not 0 < f <= 1:
        raise argparse.ArgumentTypeError(f"sample fraction must be in (0, 1], got {value}")
    return f


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline",
        description="Late Pickup Radar: monthly late-pickup KPI from NYC TLC HVFHV trip data.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser(
        "run", help="ingest -> load_raw -> profile -> validate -> model -> metrics -> report"
    )
    run.add_argument("--month", required=True, type=month_arg, help="target month, YYYY-MM")
    sample = run.add_mutually_exclusive_group()
    sample.add_argument(
        "--sample",
        type=fraction_arg,
        metavar="FRACTION",
        help="deterministic sample fraction of the full month, e.g. 0.02",
    )
    sample.add_argument(
        "--sample-file",
        metavar="PATH",
        help="run on a pre-built sample Parquet instead of downloading",
    )
    run.add_argument(
        "--no-weather",
        action="store_true",
        help="skip the weather API; weather metrics become UNAVAILABLE",
    )
    run.add_argument("--force", action="store_true", help="re-download even if cached")
    run.add_argument("--quiet", action="store_true", help="plain console output (CI)")

    show = sub.add_parser("show", help="read-only inspection of a finished run")
    show.add_argument("target", choices=SHOW_TARGETS)
    show.add_argument("--month", required=True, type=month_arg)
    show.add_argument("--rule", help="rule id filter, e.g. R03")
    show.add_argument("--n", type=int, default=5, help="rows to print")
    return parser


def run(args: argparse.Namespace) -> int:
    import duckdb

    from pipeline import ingest as ing
    from pipeline.config import ROOT, load_config, resolve
    from pipeline.logging_setup import log, rows_line, setup_logging, stage
    from pipeline.manifest import git_sha, repo_relative, sha256_file, utc_now, write_json_atomic
    from pipeline.metrics import compute_metrics
    from pipeline.model import build_model
    from pipeline.profile import profile as profile_stage
    from pipeline.report import write_report
    from pipeline.validate import load_rules, validate

    cfg = load_config()
    sample_file = Path(args.sample_file).resolve() if args.sample_file else None
    if sample_file:
        tag, out_name = f"{args.month}-sample-file", "demo"
    elif args.sample is not None:
        tag = out_name = f"{args.month}-sample"
    else:
        tag = out_name = args.month
    out_dir = resolve(cfg, "outputs_dir") / out_name
    full_run = sample_file is None and args.sample is None
    log_path = setup_logging(resolve(cfg, "logs_dir"), args.month, quiet=args.quiet)

    t0 = time.perf_counter()
    manifest: dict = {
        "pipeline_version": __version__,
        "git_sha": git_sha(),
        "started_utc": utc_now(),
        "parameters": {k: v for k, v in vars(args).items() if k != "func"},
        "log_file": repo_relative(log_path),
        "stages": {},
        "status": "running",
    }
    current = "ingest"
    try:
        with stage("ingest"):
            result = ing.ingest(
                args.month,
                cfg,
                resolve(cfg, "raw_dir"),
                force=args.force,
                no_weather=args.no_weather,
                sample_file=sample_file,
                quiet=args.quiet,
            )
            footer = result.footer_rows
            rows_line("ingest", footer, footer)
            manifest["sources"] = result.sources
            manifest["completeness"] = result.completeness
            manifest["stages"]["ingest"] = {"rows_in": footer, "rows_out": footer}

        current = "load_raw"
        with stage("load_raw"):
            counts = ing.load_raw(result, resolve(cfg, "warehouse_dir"), tag)
            rows_line("load_raw", footer, counts["trips"])
            manifest["stages"]["load_raw"] = {"rows_in": footer, "rows_out": counts["trips"]}
            manifest["raw_tables"] = counts

        manifest["reconciliation"] = {
            "parquet_footer_rows": footer,
            "raw_trips_rows": counts["trips"],
            "aggregate_report": result.completeness["expected_counts"],
        }

        rules = load_rules()
        bounds = (str(ing.month_start(args.month)), str(ing.next_month_start(args.month)))
        con = duckdb.connect(counts_db_path(cfg, tag))
        try:
            current = "profile"
            with stage("profile"):
                prof = profile_stage(con, args.month, cfg, rules, out_dir, bounds, args.sample)
                rows_line("profile", prof.rows_in, prof.rows_out)
                manifest["stages"]["profile"] = {
                    "rows_in": prof.rows_in,
                    "rows_out": prof.rows_out,
                    "sample_modulus": prof.sample_modulus,
                }

            current = "validate"
            with stage("validate"):
                docs = ROOT / "docs" / "validation_rules.md" if full_run else None
                val = validate(con, args.month, cfg, rules, out_dir, bounds, docs_path=docs)
                rows_line("validate", val.rows_in, val.rows_clean)
                manifest["stages"]["validate"] = {
                    "rows_in": val.rows_in,
                    "rows_out": val.rows_clean,
                    "rows_quarantined": val.rows_quarantined,
                }
                manifest["rule_counts"] = val.rule_counts
                manifest["trusted_row_share"] = val.trusted_share
                manifest["reconciliation"]["clean_plus_quarantined"] = (
                    val.rows_clean + val.rows_quarantined
                )
                manifest["trust"] = val.trust

            current = "model"
            with stage("model"):
                mod = build_model(con, args.month, cfg, rules)
                rows_line("model", mod.rows_in, mod.fact_trips)
                manifest["stages"]["model"] = {
                    "rows_in": mod.rows_in,
                    "rows_out": mod.fact_trips,
                    "fact_trip_event_rows": mod.fact_events,
                    "dim_hour_rows": mod.hours,
                    "hours_with_weather": mod.hours_with_weather,
                    "wet_hours": mod.wet_hours,
                }

            current = "metrics"
            with stage("metrics"):
                met = compute_metrics(con, cfg, out_dir)
                rows_line("metrics", mod.fact_trips, len(met.metrics))
                manifest["stages"]["metrics"] = {
                    "rows_in": mod.fact_trips,
                    "rows_out": len(met.metrics),
                    "eligible_cells": len(met.cells),
                    "metrics_csv_sha256": sha256_file(met.metrics_path),
                }
                manifest["kpi"] = {
                    "late_rate": met.kpi,
                    "late_minutes": cfg["kpi"]["late_minutes"],
                    "weather_available": met.weather_available,
                }

            current = "report"
            with stage("report"):
                ctx = {
                    "scope": (
                        f"deterministic sample (--sample {args.sample})"
                        if args.sample is not None
                        else f"sample file {repo_relative(sample_file)}"
                        if sample_file
                        else "full month"
                    ),
                    "git_sha": manifest["git_sha"],
                    "completeness": result.completeness,
                    "trust": val.trust,
                }
                rep = write_report(con, args.month, cfg, rules, met, ctx, out_dir)
                rows_line("report", len(met.metrics), len(met.metrics))
                manifest["stages"]["report"] = {
                    "rows_in": len(met.metrics),
                    "rows_out": len(met.metrics),
                    "evidence": repo_relative(rep.evidence_path),
                    "charts": [repo_relative(p) for p in rep.chart_paths],
                }
        finally:
            con.close()
        manifest["status"] = "success"
        code = EXIT_OK
    except PipelineError as exc:
        log.error("%s", exc)
        manifest["status"] = f"failed_at:{current}"
        manifest["error"] = str(exc)
        code = exc.exit_code
    except Exception as exc:  # internal error: record it, exit 4
        log.exception("internal error in %s", current)
        manifest["status"] = f"failed_at:{current}"
        manifest["error"] = repr(exc)
        code = EXIT_INTERNAL

    manifest["wall_time_seconds"] = round(time.perf_counter() - t0, 1)
    manifest["finished_utc"] = utc_now()
    write_json_atomic(out_dir / "run_manifest.json", manifest)
    log.info(
        "run manifest: %s (status %s, exit %d)",
        out_dir / "run_manifest.json",
        manifest["status"],
        code,
    )
    return code


def counts_db_path(cfg: dict, tag: str) -> str:
    from pipeline.config import resolve

    return str(resolve(cfg, "warehouse_dir") / f"{tag}.duckdb")


def show(args: argparse.Namespace) -> int:
    from pipeline.config import load_config, resolve

    if args.target != "manifest":
        print(f"show {args.target} is implemented in a later phase.", file=sys.stderr)
        return EXIT_INTERNAL
    path = resolve(load_config(), "outputs_dir") / args.month / "run_manifest.json"
    if not path.exists():
        print(f"no run manifest at {path}; run the pipeline first.", file=sys.stderr)
        return EXIT_INTERNAL
    print(json.dumps(json.loads(path.read_text()), indent=2))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(args) if args.command == "run" else show(args)


if __name__ == "__main__":
    sys.exit(main())
