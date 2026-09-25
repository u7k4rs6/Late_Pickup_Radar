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
    from pipeline import ingest as ing
    from pipeline.config import load_config, resolve
    from pipeline.logging_setup import log, rows_line, setup_logging, stage
    from pipeline.manifest import git_sha, repo_relative, utc_now, write_json_atomic

    cfg = load_config()
    sample_file = Path(args.sample_file).resolve() if args.sample_file else None
    tag = f"{args.month}-sample" if sample_file else args.month
    out_dir = resolve(cfg, "outputs_dir") / ("demo" if sample_file else args.month)
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
        if args.sample is not None:
            log.warning("--sample %s is applied from the profile stage (phase 3)", args.sample)
        log.info("stages after load_raw are not implemented yet (phase 3+)")
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
