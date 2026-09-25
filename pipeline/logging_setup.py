"""Console (rich) + plain log file logging, and stage banners with elapsed time."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

STAGES = ("ingest", "load_raw", "profile", "validate", "model", "metrics", "report")
LOGGER_NAME = "pipeline"

console = Console(highlight=False)
log = logging.getLogger(LOGGER_NAME)


def setup_logging(logs_dir: Path, month: str, *, quiet: bool) -> Path:
    """Log to console and to logs/run_<month>_<timestamp>.log. Returns the log path."""
    global console
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"run_{month}_{datetime.now():%Y%m%dT%H%M%S}.log"
    log.handlers.clear()
    log.setLevel(logging.INFO)
    log.propagate = False

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    log.addHandler(file_handler)

    if quiet:
        console = Console(quiet=True)
        plain = logging.StreamHandler()
        plain.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        log.addHandler(plain)
    else:
        console = Console(highlight=False)
        log.addHandler(RichHandler(console=console, show_path=False, show_time=False, markup=False))
    return log_path


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Banner '━━ 1/7 INGEST ━━' on entry; elapsed time on exit (also written to the log file)."""
    idx = STAGES.index(name) + 1
    title = f"{idx}/{len(STAGES)} {name.upper()}"
    console.rule(f"[bold cyan]{title}", characters="━", style="cyan")
    log.info("stage start: %s", title)
    t0 = time.perf_counter()
    try:
        yield
    except Exception:
        log.error("stage failed: %s after %.1fs", title, time.perf_counter() - t0)
        raise
    elapsed = time.perf_counter() - t0
    log.info("stage end: %s (%.1fs)", title, elapsed)
    console.print(f"[dim]{name} done in {elapsed:.1f}s[/dim]")


def rows_line(name: str, rows_in: int, rows_out: int) -> None:
    log.info("%s rows: %s -> %s", name, f"{rows_in:,}", f"{rows_out:,}")
