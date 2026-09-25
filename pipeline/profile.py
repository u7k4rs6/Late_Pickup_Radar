"""Stage 3: build stage.trips (optionally a deterministic sample) and write profile.md.

The profile drives the validation thresholds, not the other way round: every number quoted in
pipeline/validation_rules.yml comes from a table in outputs/<month>/profile.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from pipeline import db  # noqa: E402
from pipeline.logging_setup import log  # noqa: E402
from pipeline.markdown import table  # noqa: E402

DERIVED = (
    "trip_id",
    "wait_minutes",
    "dwell_minutes",
    "trip_minutes",
    "duration_gap_seconds",
    "speed_mph",
    "dup_rank",
    "dup_count",
)
LINEAGE = ("file_row_number",)

SECTIONS = (
    ("wait_percentiles", "Wait minutes (pickup - request): percentiles, overall and by company"),
    ("wait_tail", "Wait tail"),
    ("negative_wait_by_segment", "Negative wait and on_scene before request, by company x WAV"),
    ("negative_wait_vs_on_scene", "Negative wait vs on_scene before request (overlap)"),
    ("prearranged_signature", "Do on_scene-before-request rows look like reservations?"),
    ("whole_minute_request_by_wait", "Whole-minute request times by wait band (chance = 1.7%)"),
    ("prearranged_by_hour", "Pre-arrangement signals by pickup hour"),
    ("on_scene_vs_pickup", "on_scene vs pickup, by company"),
    ("dwell_where_measurable", "Dwell minutes (pickup - on_scene) where on_scene < pickup"),
    ("implied_speed", "Implied speed, mph (trip_miles / trip_time); trip_time = 0 -> NULL"),
    ("implied_speed_bins", "Implied speed, 5-mph bins from 40 mph"),
    (
        "timestamp_speed_check",
        "Speed from timestamps instead of trip_time (why R06 uses trip_time)",
    ),
    ("duration_agreement", "trip_time vs dropoff - pickup (R07)"),
    ("duplicates", "Duplicates: exact row and business key"),
    ("other_counts", "Zero miles, negative pay/fare, unknown zones, out-of-period rows"),
)

# Chart style is fixed so screenshots match between takes (PRD 15.4).
CHART_SIZE = (9, 4.5)
CHART_DPI = 120
SERIES_BLUE = "#2a78d6"
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_MUTED = "#52514e"


@dataclass
class ProfileResult:
    rows_in: int
    rows_out: int
    sample_modulus: int | None
    profile_path: Path
    chart_paths: list[Path]


def sample_modulus(fraction: float | None) -> int | None:
    return None if fraction is None else max(1, round(1 / fraction))


def sample_predicate(business_key: list[str], modulus: int | None) -> db.Fragment:
    if modulus is None or modulus == 1:
        return db.Fragment("TRUE")
    key = ", ".join(business_key)
    return db.Fragment(f"md5_number(concat_ws('|', {key})) % {modulus} = 0")


def build_stage(
    con: duckdb.DuckDBPyConnection, rules: dict[str, Any], modulus: int | None
) -> tuple[int, int]:
    raw_cols = [r[0] for r in con.execute("DESCRIBE raw.trips").fetchall() if r[0] not in LINEAGE]
    db.run_file(
        con,
        "02_stage_trips.sql",
        raw_columns=db.Fragment(", ".join(raw_cols)),
        sample_predicate=sample_predicate(rules["business_key"], modulus),
    )
    rows_in = con.execute("SELECT count(*) FROM raw.trips").fetchone()[0]
    rows_out = con.execute("SELECT count(*) FROM stage.trips").fetchone()[0]
    return rows_in, rows_out


def column_profile(con: duckdb.DuckDBPyConnection) -> str:
    cols = [r[0] for r in con.execute("DESCRIBE stage.trips").fetchall() if r[0] not in DERIVED]
    summary = con.execute(
        f"SELECT column_name, column_type, null_percentage, min, max "
        f"FROM (SUMMARIZE SELECT {', '.join(cols)} FROM stage.trips)"
    ).df()
    # Exact distinct counts: SUMMARIZE's HyperLogLog approx_unique said 1 for a 2-value column.
    exact = con.execute(
        "SELECT " + ", ".join(f"count(DISTINCT {c})" for c in cols) + " FROM stage.trips"
    ).fetchone()
    summary.insert(3, "distinct", list(exact))
    top = []
    for c in cols:
        rows = con.execute(
            f"SELECT {c}::VARCHAR v, count(*) n FROM stage.trips GROUP BY 1 "
            f"ORDER BY n DESC, v NULLS FIRST LIMIT 5"
        ).fetchall()
        top.append("; ".join(f"{v} ({n:,})" for v, n in rows))
    summary["top_5_values"] = top
    summary = summary.rename(columns={"null_percentage": "null_%"})
    return table(summary)


def wait_chart(
    con: duckdb.DuckDBPyConnection,
    queries: dict[str, str],
    path: Path,
    cfg: dict[str, Any],
    rules: dict[str, Any],
) -> Path:
    hist = con.execute(queries["wait_log_histogram"]).df()
    counts = dict(zip(hist["bin_k"].astype(int), hist["n"].astype(int), strict=True))
    ks = range(min(counts), max(counts) + 1)  # every bin, empty ones as 0 (not stretched)
    edges = [10 ** (k / 20) for k in ks] + [10 ** ((max(counts) + 1) / 20)]
    values = [counts.get(k, 0) for k in ks]

    fig, ax = plt.subplots(figsize=CHART_SIZE, dpi=CHART_DPI)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.stairs(values, edges, fill=True, color=SERIES_BLUE, linewidth=0)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_ylim(bottom=0.8)
    kpi = cfg["kpi"]
    thresholds = sorted([kpi["late_minutes"], *kpi["sensitivity_minutes"]])
    cap = next(r for r in rules["rules"] if r["id"] == "R03")["params"]["max_wait_minutes"]
    top = ax.get_ylim()[1]
    for m in thresholds:
        ax.axvline(m, color=INK_MUTED, linewidth=1, linestyle=":")
    ax.text(
        thresholds[0] / 1.05,  # left of the first line, so it never meets the R03 label
        top,
        "late thresholds " + " / ".join(map(str, thresholds)) + " min",
        color=INK_MUTED,
        fontsize=8,
        va="top",
        ha="right",
    )
    ax.axvline(cap, color=INK_MUTED, linewidth=1, linestyle="--")
    ax.text(cap * 1.08, top, f"R03 cap {cap} min", color=INK_MUTED, fontsize=8, va="top")
    ax.set_xlabel("wait = pickup - request (minutes, log scale)", color=INK)
    ax.set_ylabel("trips per 0.05-decade bin (log scale)", color=INK)
    ax.set_title(
        "Wait distribution, positive waits only (negative waits: see R01/R12)",
        color=INK,
        fontsize=11,
        loc="left",
    )
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors=INK_MUTED)
    ax.grid(axis="y", color="#e6e5e1", linewidth=0.6)
    ax.set_axisbelow(True)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, facecolor=SURFACE, metadata={"Software": None})
    plt.close(fig)
    return path


def profile(
    con: duckdb.DuckDBPyConnection,
    month: str,
    cfg: dict[str, Any],
    rules: dict[str, Any],
    out_dir: Path,
    month_bounds: tuple[str, str],
    sample_fraction: float | None = None,
) -> ProfileResult:
    modulus = sample_modulus(sample_fraction)
    rows_in, rows_out = build_stage(con, rules, modulus)
    if modulus:
        log.info(
            "sample: md5(business key) %% %d = 0 -> %s of %s rows",
            modulus,
            f"{rows_out:,}",
            f"{rows_in:,}",
        )

    queries = db.named_queries(
        "03_profile.sql",
        business_key=db.Fragment(", ".join(rules["business_key"])),
        month_start=db.Fragment(f"TIMESTAMP '{month_bounds[0]}'"),
        month_end=db.Fragment(f"TIMESTAMP '{month_bounds[1]}'"),
    )
    scope = (
        f"deterministic sample, md5(business key) % {modulus} = 0: {rows_out:,} of {rows_in:,} rows"
        if modulus
        else f"full month: {rows_out:,} rows"
    )
    parts = [
        f"# Profile: {month}",
        "",
        f"Scope: {scope}. Generated by `pipeline/profile.py` from `pipeline/sql/03_profile.sql`.",
        "Times are TLC's naive local timestamps. Durations in minutes unless the column says so.",
        "",
        "## Charts",
        "",
        "![wait distribution](charts/wait_log_histogram.png)",
        "",
        "Timestamps have one-second resolution, so waits under ~0.1 min fall into separate "
        "1 s / 2 s / 3 s bins (the spikes on the left). Empty bins are drawn empty.",
        "",
    ]
    for key, title in SECTIONS:
        parts += [f"## {title}", "", table(con.execute(queries[key]).df()), ""]
    parts += ["## Every column", "", column_profile(con), ""]

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "profile.md"
    path.write_text("\n".join(parts))
    chart = wait_chart(con, queries, out_dir / "charts" / "wait_log_histogram.png", cfg, rules)
    log.info("profile written: %s (+ %s)", path.name, chart.name)
    return ProfileResult(rows_in, rows_out, modulus, path, [chart])
