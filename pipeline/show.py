"""`python -m pipeline show ...`: read-only inspection of a finished run (used in the demo)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from rich.console import Console
from rich.table import Table

from pipeline.config import resolve
from pipeline.validate import flag, load_rules

console = Console(highlight=False)
CONTEXT_COLUMNS = [
    "trip_id",
    "hvfhs_license_num",
    "PULocationID",
    "request_datetime",
    "on_scene_datetime",
    "pickup_datetime",
    "wait_minutes",
]


def _warehouse(cfg: dict[str, Any], month: str, scope: str) -> Path:
    tag = f"{month}-sample-file" if scope == "demo" else month
    return resolve(cfg, "warehouse_dir") / f"{tag}.duckdb"


def _outputs(cfg: dict[str, Any], month: str, scope: str) -> Path:
    return resolve(cfg, "outputs_dir") / ("demo" if scope == "demo" else month)


def show_rule(cfg: dict[str, Any], month: str, rule_id: str, n: int, scope: str) -> int:
    rules = load_rules()
    rule = next((r for r in rules["rules"] if r["id"] == rule_id.upper()), None)
    if rule is None:
        console.print(
            f"[red]unknown rule {rule_id}[/red]; rules: "
            + ", ".join(r["id"] for r in rules["rules"])
        )
        return 4
    path = _warehouse(cfg, month, scope)
    if not path.exists():
        console.print(f"[red]no warehouse at {path}[/red]: run the pipeline first")
        return 4
    con = duckdb.connect(str(path), read_only=True)
    stage_cols = [r[0] for r in con.execute("DESCRIBE stage.trips").fetchall()]
    used = [c for c in stage_cols if re.search(rf"\b{re.escape(c)}\b", rule["when"])]
    cols = list(dict.fromkeys(CONTEXT_COLUMNS + used))
    if rule["severity"] == "REJECT":
        table, where = "quarantine.trips", f"list_contains(reject_rules, '{rule['id']}')"
        total_sql = f"SELECT count(*) FROM {table} WHERE {where}"
    else:
        table, where = "clean.trips", flag(rule["id"])
        total_sql = f"SELECT count(*) FROM {table} WHERE {where}"
    total = con.execute(total_sql).fetchone()[0]
    rows = con.execute(
        f"SELECT {', '.join(cols)} FROM {table} WHERE {where} ORDER BY trip_id LIMIT {int(n)}"
    ).df()
    con.close()

    sev = "red" if rule["severity"] == "REJECT" else "yellow"
    console.rule(f"[bold]{rule['id']}[/bold] [{sev}]{rule['severity']}[/{sev}]: {rule['name']}")
    params = {
        k: str(tuple(v)) if isinstance(v, list) else v
        for k, v in (rule.get("params") or {}).items()
    }
    cond = rule["when"].format(**params, month_start="<month start>", month_end="<next month>")
    console.print(f"[bold]condition[/bold]  {cond}")
    excl = (
        "all metrics (quarantined)"
        if rule["severity"] == "REJECT"
        else (", ".join(rule.get("excludes") or []) or "none (sensitivity only)")
    )
    console.print(f"[bold]excluded from[/bold]  {excl}")
    console.print(f"[bold]why[/bold]  {' '.join(rule['rationale'].split())}")
    console.print(f"[bold]evidence[/bold]  {' '.join(rule['evidence'].split())}")
    where_from = "demo sample run" if scope == "demo" else f"full-month run {month}"
    t = Table(
        title=f"{min(n, total)} of {total:,} real rows ({where_from}; rule columns highlighted)",
        show_edge=False,
        header_style="bold",
    )
    for c in cols:
        t.add_column(
            c,
            style="bold yellow" if c in used else None,
            justify="right" if c in ("trip_id", "PULocationID", "wait_minutes") else "left",
        )
    for r in rows.itertuples(index=False):
        t.add_row(*[_fmt(v) for v in r])
    console.print(t)
    return 0


def _fmt(v: object) -> str:
    if isinstance(v, pd.Timestamp):
        return v.strftime("%m-%d %H:%M:%S")
    if isinstance(v, float):
        return f"{v:.2f}"
    return "" if v is None else str(v)


def show_top_cells(cfg: dict[str, Any], month: str) -> int:
    from pipeline.report import print_top_cells

    path = resolve(cfg, "outputs_dir") / month / "incentive_cells.csv"
    if not path.exists():
        console.print(f"[red]no {path}[/red]: run the full month first")
        return 4
    import pipeline.logging_setup as ls

    ls.console = console
    print_top_cells(pd.read_csv(path), cfg, f"from committed full-month run {month}")
    return 0


def show_metrics(cfg: dict[str, Any], month: str, scope: str) -> int:
    path = _outputs(cfg, month, scope) / "metrics.csv"
    if not path.exists():
        console.print(f"[red]no {path}[/red]")
        return 4
    df = pd.read_csv(path, keep_default_na=False)
    df = df[df["grain"] == "month"]
    t = Table(title=f"metrics.csv, month grain ({path})", show_edge=False, header_style="bold")
    for c in ("metric", "dimensions", "value", "n", "note"):
        t.add_column(c, justify="right" if c in ("value", "n") else "left")
    for r in df.itertuples(index=False):
        t.add_row(r.metric, r.dimensions, str(r.value), f"{int(r.n):,}", r.note)
    console.print(t)
    return 0


def show_manifest(cfg: dict[str, Any], month: str, scope: str) -> int:
    path = _outputs(cfg, month, scope) / "run_manifest.json"
    if not path.exists():
        console.print(f"[red]no run manifest at {path}[/red]; run the pipeline first")
        return 4
    print(json.dumps(json.loads(path.read_text()), indent=2))
    return 0
