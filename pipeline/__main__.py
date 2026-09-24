"""CLI entry point: ``python -m pipeline {run,show} ...``."""

from __future__ import annotations

import argparse
import re
import sys

EXIT_OK = 0
EXIT_SOURCE_UNAVAILABLE = 2
EXIT_VALIDATION_FAILED = 3
EXIT_INTERNAL = 4

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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(f"'{args.command}' is not implemented yet (scaffold, phase 0).", file=sys.stderr)
    return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
