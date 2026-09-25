"""Demo helpers behind `make demo-rerun` and `make demo-fail` (PRD 15.1).

python -m pipeline.demo rerun   # run the demo again; print whether metrics.csv is identical
python -m pipeline.demo fail    # dead trip URL into a throwaway dir: exit 2, nothing partial
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import yaml
from rich.console import Console

from pipeline.__main__ import main
from pipeline.config import TRIPS_URL_ENV, load_config, resolve

MONTH = "2026-07"
DEAD_URL = "https://d37ci6vzurychx.cloudfront.net/trip-data/fhvhv_tripdata_2099-01.parquet"
console = Console(highlight=False)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rerun() -> int:
    metrics = resolve(load_config(), "outputs_dir") / "demo" / "metrics.csv"
    if not metrics.exists():
        console.print("no previous demo run: running `make demo` first")
        if main(["run", "--month", MONTH, "--offline"]) != 0:
            return 1
    before = _sha(metrics)
    code = main(["run", "--month", MONTH, "--offline"])
    after = _sha(metrics)
    if code == 0 and before == after:
        console.print(
            f"[bold green]outputs identical: {after}[/bold green] "
            "(metrics.csv sha256, previous run vs this run)"
        )
        return 0
    console.print(f"[bold red]outputs DIFFER[/bold red]: {before} -> {after} (exit {code})")
    return 1


def fail() -> int:
    """Everything goes to a temporary directory, so the real outputs/ and data/ are untouched."""
    with tempfile.TemporaryDirectory(prefix="demo-fail-") as tmp:
        cfg = load_config()
        for key in ("raw_dir", "warehouse_dir", "outputs_dir", "logs_dir"):
            cfg["paths"][key] = str(Path(tmp) / key)
        cfg["paths"]["staging_dir"] = str(Path(tmp) / "outputs_dir" / ".staging")
        cfg["duckdb"]["temp_directory"] = str(Path(tmp) / "duckdb_tmp")
        cfg["sources"]["trips_url_template"] = DEAD_URL
        config = Path(tmp) / "config.yml"
        config.write_text(yaml.safe_dump(cfg))
        os.environ.pop(TRIPS_URL_ENV, None)
        console.print(f"[bold]source[/bold] trips = {DEAD_URL}  (outputs to {tmp})")

        code = main(["run", "--month", MONTH, "--force", "--config", str(config)])

        out = Path(tmp) / "outputs_dir" / MONTH
        published = [p for p in out.rglob("*") if p.is_file() and "failed" not in p.parts]
        partial = list(Path(tmp).rglob("*.part")) + list(Path(tmp).rglob("*.duckdb.tmp"))
        staging = [p for p in (Path(tmp) / "outputs_dir" / ".staging").glob(f"{MONTH}-*")]
        failed = sorted((out / "failed").glob("*.json"))
        ok = code == 2 and not published and not partial and not staging and len(failed) == 1
        if failed:
            m = json.loads(failed[0].read_text())
            console.print(
                f"[bold]failed-run manifest[/bold] status={m['status']} exit_code={m['exit_code']}"
            )
        if ok:
            console.print(
                "[bold green]no partial outputs written[/bold green] "
                "(0 published files, 0 .part files, 0 staging dirs; exit code 2)"
            )
        else:
            console.print(
                f"[bold red]unexpected[/bold red]: exit {code}, published {published}, "
                f"partial {partial}, staging {staging}"
            )
        return code if ok else 1


if __name__ == "__main__":
    sys.exit({"rerun": rerun, "fail": fail}[sys.argv[1]]())
