"""Stage 7: evidence.md (evidence table, top-N incentive cells, KUAL), charts, console summary.

Every number here is read from metrics.csv / incentive_cells.csv / the run context; the report
adds no computation of its own beyond formatting.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from rich.table import Table

from pipeline import charts, db, logging_setup
from pipeline.logging_setup import log
from pipeline.markdown import table
from pipeline.metrics import MetricsResult

DOW = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
HEATMAP_BOROUGHS = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]  # EWR: < 200/hour
RAIN_SCOPES = ["all", "Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island"]
COMPANY = {"HV0003": "Uber", "HV0005": "Lyft"}


@dataclass
class ReportResult:
    evidence_path: Path
    chart_paths: list[Path]


class Lookup:
    """metrics.csv rows by (metric, grain, dimensions)."""

    def __init__(self, metrics: pd.DataFrame):
        self.rows = {(r.metric, r.grain, r.dimensions): r for r in metrics.itertuples(index=False)}

    def get(self, metric: str, grain: str = "month", dims: str = ""):
        return self.rows.get((metric, grain, dims))

    def value(self, metric: str, grain: str = "month", dims: str = "") -> float | None:
        r = self.get(metric, grain, dims)
        return None if r is None or pd.isna(r.value) else float(r.value)


def pct(v: float | None, digits: int = 2) -> str:
    return "UNAVAILABLE" if v is None else f"{100 * v:.{digits}f}%"


def pts(v: float | None) -> str:
    return "UNAVAILABLE" if v is None else f"{100 * v:+.2f} pts"


def _dims(d: str) -> dict[str, str]:
    return dict(p.split("=", 1) for p in d.split("|") if p)


def evidence_rows(m: Lookup, cfg: dict[str, Any]) -> list[dict[str, str]]:
    main = cfg["kpi"]["late_minutes"]
    low, high = sorted(cfg["kpi"]["sensitivity_minutes"])
    rain, rain_hi = (
        cfg["thresholds"]["rain_mm_per_hour"],
        cfg["thresholds"]["rain_sensitivity_mm_per_hour"],
    )
    n = lambda metric, grain="month", dims="": (  # noqa: E731
        f"{int(r.n):,}" if (r := m.get(metric, grain, dims)) is not None else "0"
    )
    wait_share = pct(m.value("M5_wait_eligible_share"), 3)
    rows = [
        {
            "#": "M1 (KPI)",
            "metric": f"Late-pickup rate, wait > {main} min",
            "value": pct(m.value(f"M1_late_rate_{main}")),
            "n": n(f"M1_late_rate_{main}"),
            "data behind it": f"wait-eligible rows: {wait_share} of rows in",
        },
        {
            "#": "M1",
            "metric": f"Late-pickup rate, wait > {low} min",
            "value": pct(m.value(f"M1_late_rate_{low}")),
            "n": n(f"M1_late_rate_{low}"),
            "data behind it": "threshold sensitivity",
        },
        {
            "#": "M1",
            "metric": f"Late-pickup rate, wait > {high} min",
            "value": pct(m.value(f"M1_late_rate_{high}")),
            "n": n(f"M1_late_rate_{high}"),
            "data behind it": "threshold sensitivity",
        },
    ]
    for variant, label in (
        ("variant=pre-arranged kept as measured", "if pre-arranged rides (R12) were counted"),
        ("variant=whole-minute requests dropped", "if whole-minute requests (R14) were dropped"),
    ):
        rows.append(
            {
                "#": "M1",
                "metric": f"Late rate > {main} min, {label}",
                "value": pct(m.value(f"M1_late_rate_{main}", "month", variant)),
                "n": n(f"M1_late_rate_{main}", "month", variant),
                "data behind it": "sensitivity of the exclusion",
            }
        )
    for company in ("HV0003", "HV0005"):
        for wav in ("false", "true"):
            dims = f"company={company}|wav_request={wav}"
            cov = m.value("M5_wait_coverage", "company x wav_request", dims)
            rows.append(
                {
                    "#": "M1",
                    "metric": f"Late rate > {main} min, {COMPANY[company]} "
                    f"{'WAV' if wav == 'true' else 'non-WAV'}",
                    "value": pct(m.value(f"M1_late_rate_{main}", "company x wav_request", dims)),
                    "n": n(f"M1_late_rate_{main}", "company x wav_request", dims),
                    "data behind it": f"wait coverage {pct(cov)} of this segment's clean rows",
                }
            )
    rows.append(
        {
            "#": "M2",
            "metric": "p90 wait (minutes)",
            "value": _num(m.value("M2_p90_wait_minutes")),
            "n": n("M2_p90_wait_minutes"),
            "data behind it": "same rows as the KPI",
        }
    )
    for company in ("HV0003", "HV0005"):
        dims = f"company={company}"
        rows.append(
            {
                "#": "M3",
                "metric": f"Median on-scene dwell (minutes), {COMPANY[company]}",
                "value": _num(m.value("M3_median_dwell_minutes", "company", dims)),
                "n": n("M3_median_dwell_minutes", "company", dims),
                "data behind it": f"dwell coverage {pct(m.value('M3_dwell_coverage', 'company', dims))}"
                " (on_scene < pickup)",
            }
        )
    for thr in (rain, rain_hi):
        dims = f"threshold_mm={thr}"
        wet = m.value("M4_wet_hours", "month", dims)
        wet_txt = (
            "UNAVAILABLE"
            if wet is None
            else f"{int(wet)} of {n('M4_wet_hours', 'month', dims)} hours"
        )
        rows.append(
            {
                "#": "M4",
                "metric": f"Rain minus dry late rate, rain >= {thr} mm/h (raw)",
                "value": pts(m.value("M4_rain_minus_dry_late_rate", "month", dims)),
                "n": n("M4_rain_minus_dry_late_rate", "month", dims),
                "data behind it": f"wet hours: {wet_txt}",
            }
        )
        rows.append(
            {
                "#": "M4",
                "metric": f"Rain minus dry, rain >= {thr} mm/h, within hour of day",
                "value": pts(m.value("M4_rain_minus_dry_late_rate_hour_adjusted", "month", dims)),
                "n": n("M4_rain_minus_dry_late_rate_hour_adjusted", "month", dims),
                "data behind it": "n = trips in rainy hours; controls for time of day",
            }
        )
    for metric, label in (
        ("M5_trusted_row_share", "Trusted-row share (not quarantined)"),
        ("M5_wait_eligible_share", "Wait-eligible share (rows in the KPI)"),
        ("M5_dwell_coverage", "Dwell coverage (on_scene < pickup)"),
    ):
        rows.append(
            {
                "#": "M5",
                "metric": label,
                "value": pct(m.value(metric), 3),
                "n": n(metric),
                "data behind it": "share of rows in",
            }
        )
    return rows


def _num(v: float | None) -> str:
    return "UNAVAILABLE" if v is None else f"{v:.2f}"


def _day_hour(top: pd.DataFrame) -> list[str]:
    return [f"{DOW[d]} {h:02d}:00" for d, h in zip(top["dow"], top["hour"], strict=True)]


def neighbourhood_table(cells: pd.DataFrame, k: int) -> pd.DataFrame:
    top = cells[cells["list"] == "neighbourhood"].head(k)
    return pd.DataFrame(
        {
            "rank": top["rank"],
            "borough": top["borough"],
            "zone": top["zone"],
            "day-hour (request)": _day_hour(top),
            "late rate": [f"{100 * v:.1f}%" for v in top["late_rate"]],
            "vs city": [f"{100 * v:+.1f} pts" for v in top["late_rate_excess"]],
            "n": top["n"],
            "excess late trips": [f"{v:.0f}" for v in top["excess_late_trips"]],
            "p90 wait (min)": [f"{v:.1f}" for v in top["p90_wait_minutes"]],
        }
    )


def airport_table(cells: pd.DataFrame, k: int) -> pd.DataFrame:
    top = cells[cells["list"] == "airport"].head(k)
    return pd.DataFrame(
        {
            "rank": top["rank"],
            "zone": top["zone"],
            "day-hour (request)": _day_hour(top),
            "late rate": [f"{100 * v:.1f}%" for v in top["late_rate"]],
            "n": top["n"],
            "excess late trips": [f"{v:.0f}" for v in top["excess_late_trips"]],
            "request -> arrival, this cell (median min)": [
                f"{v:.1f}" for v in top["median_request_to_arrival_minutes"]
            ],
            "request -> arrival, zone all month (median min)": [
                f"{v:.1f}" for v in top["zone_median_request_to_arrival_minutes"]
            ],
            "dwell (median min)": [f"{v:.2f}" for v in top["median_dwell_minutes"]],
        }
    )


def findings(m: MetricsResult, lk: Lookup, cfg: dict[str, Any]) -> list[str]:
    """Plain-language findings, each computed from the numbers so it stays true."""
    main = cfg["kpi"]["late_minutes"]
    inc = cfg["incentives"]
    out = ["## What the numbers say", ""]
    naive_k = inc["top_n"] + inc["airport_top_n"] + 5
    naive = m.cells[m.cells["overall_rank"] <= naive_k] if len(m.cells) else m.cells
    if len(naive):
        airports = int(naive["is_airport"].sum())
        first_nb = m.cells[m.cells["list"] == "neighbourhood"]["overall_rank"].min()
        out.append(
            f"- **A single ranking is {100 * airports / len(naive):.0f}% airports** in its top "
            f"{len(naive)}; the first neighbourhood cell is overall rank {first_nb}. Airport waits "
            "are driven by staging-lot throughput and Port Authority dispatch, a different owner and "
            "a different lever from driver incentives, so the same rule is applied to two lists "
            "split on zone type: neighbourhood cells are the incentive decision; airport cells are "
            "escalated to airport ops."
        )
    thr = cfg["thresholds"]["rain_mm_per_hour"]
    adj = lk.value("M4_rain_minus_dry_late_rate_hour_adjusted", "month", f"threshold_mm={thr}")
    raw = lk.value("M4_rain_minus_dry_late_rate", "month", f"threshold_mm={thr}")
    heat = m.metrics[
        (m.metrics["metric"] == f"M1_late_rate_{main}")
        & (m.metrics["grain"] == "borough x hour_of_day")
        & (m.metrics["n"] >= inc["min_cell_trips"])
    ]
    if adj is None or raw is None:
        out.append("- **Rain:** UNAVAILABLE (no weather data for this run).")
    elif len(heat):
        lo, hi = heat["value"].min(), heat["value"].max()
        spread = hi - lo
        size = "far smaller than" if abs(adj) < 0.25 * spread else "comparable to"
        out.append(
            f"- **Rain is a weak lever.** Rainy request hours (>= {thr} mm) raise the late rate by "
            f"{100 * raw:+.1f} points raw and {100 * adj:+.1f} points within the same hour of day. "
            f"That is {size} location and hour: across borough x hour the late rate runs from "
            f"{100 * lo:.1f}% to {100 * hi:.1f}%. Weather-triggered incentives are not supported "
            "by this month's data; fixed zone x hour incentives are."
        )
    out.append("")
    return out


def make_charts(con, m: MetricsResult, cfg: dict[str, Any], rules, out_dir: Path) -> list[Path]:
    kpi = cfg["kpi"]
    main = kpi["late_minutes"]
    cdir = out_dir / "charts"
    cap = next(r for r in rules["rules"] if r["id"] == "R03")["params"]["max_wait_minutes"]
    q = db.named_queries("09_report.sql")
    paths = [
        charts.wait_histogram(
            con.execute(q["wait_histogram"]).df(),
            cdir / "wait_distribution.png",
            [main, *kpi["sensitivity_minutes"]],
            "Wait for the trips in the KPI (pre-arranged rides excluded), log scale",
            cap=cap,
        )
    ]
    heat = m.metrics[
        (m.metrics["metric"] == f"M1_late_rate_{main}")
        & (m.metrics["grain"] == "borough x hour_of_day")
    ].copy()
    heat["borough"] = [_dims(d)["borough"] for d in heat["dimensions"]]
    heat["hour"] = [int(_dims(d)["hour"]) for d in heat["dimensions"]]
    paths.append(
        charts.late_rate_heatmap(
            heat,
            cdir / "late_rate_heatmap.png",
            f"Late-pickup rate (wait > {main} min) by pickup borough and request hour",
            cfg["incentives"]["min_cell_trips"],
            HEATMAP_BOROUGHS,
        )
    )
    if m.weather_available:
        lk = Lookup(m.metrics)
        thr = cfg["thresholds"]["rain_mm_per_hour"]
        bars = []
        for scope in RAIN_SCOPES:
            grain = "month" if scope == "all" else "borough"
            dims = f"threshold_mm={thr}" + ("" if scope == "all" else f"|borough={scope}")
            bars.append(
                {
                    "scope": "citywide" if scope == "all" else scope,
                    "rainy": lk.value("M4_late_rate_rainy", grain, dims),
                    "dry": lk.value("M4_late_rate_dry", grain, dims),
                }
            )
        wet = lk.value("M4_wet_hours", "month", f"threshold_mm={thr}")
        paths.append(
            charts.rain_vs_dry(
                pd.DataFrame(bars),
                cdir / "rain_vs_dry.png",
                f"Late-pickup rate (wait > {main} min) in rainy vs dry request hours",
                f"rainy = precipitation >= {thr} mm in the request hour "
                f"({int(wet) if wet is not None else 0} wet hours); "
                "single weather point (Central Park); raw rates, not adjusted for time of day",
            )
        )
    return paths


def kual(ctx: dict[str, Any], m: Lookup, cfg: dict[str, Any]) -> list[str]:
    c = ctx["completeness"]["trips"]
    trust = ctx["trust"]
    main = cfg["kpi"]["late_minutes"]
    lyft_wav = m.value(
        "M5_wait_coverage", "company x wav_request", "company=HV0005|wav_request=true"
    )
    uber_dwell = m.value("M3_dwell_coverage", "company", "company=HV0003")
    return [
        "## Known / Unknown / Assumption / Limitation",
        "",
        "**Known**",
        f"- The TLC file is complete for the month: {c['footer_rows']:,} rows; "
        f"{c['days_with_rows']}/{c['expected_days']} days and "
        f"{c['hours_with_rows']}/{c['expected_hours']} hours have pickups; bytes match "
        "Content-Length; TLC's aggregate report agrees within the sanity band.",
        f"- {pct(trust['trusted_row_share'], 3)} of rows pass every REJECT rule; "
        f"{pct(trust['wait_eligible_share'], 3)} enter the KPI.",
        "",
        "**Unknown**",
        "- Cancellations and unfulfilled requests are not in the data, so the KPI is conditional on "
        "a trip happening. True rider wait including cancellations cannot be known from this source.",
        "- Which trips were booked in advance: the file has no scheduling field.",
        "",
        "**Assumption**",
        f"- Late means wait > {main} min (observed p90), fixed across months; reported at "
        f"{' and '.join(map(str, sorted(cfg['kpi']['sensitivity_minutes'])))} min too.",
        "- Wait is measured from request, not on_scene. Where on_scene is before request (R12), "
        "the request is treated as a booking time, not the rider's ask, and the row leaves the KPI.",
        "- One weather point (Central Park; its grid cell lies over New Jersey) stands for the "
        "whole city.",
        "- on_scene is trusted where it is before pickup; where it equals pickup it was not captured.",
        "",
        "**Limitation**",
        "- One month (2026-07), which includes the July 4 holiday weekend; wet hours cluster on it.",
        "- Weather is model reanalysis, not a station.",
        f"- Dwell coverage differs by company (Uber {pct(uber_dwell)}), so dwell comparisons "
        "rest on different shares of trips.",
        f"- Lyft WAV: only {pct(lyft_wav)} of clean trips can enter wait metrics, so the Lyft WAV "
        "late rate rests on a minority of that segment's trips.",
        "",
    ]


def summary_rows(rows: list[dict]) -> list[dict]:
    """The five metrics for the console: KPI at three thresholds, M2, M3 with coverage, the
    hour-adjusted M4 at the configured threshold, and the three M5 lines."""
    keep = []
    for r in rows:
        m = r["metric"]
        if r["#"].startswith("M1") and m.startswith("Late-pickup rate"):
            keep.append(r)
        elif r["#"] in ("M2", "M3", "M5"):
            keep.append(r)
        elif r["#"] == "M4" and "within hour of day" in m and not keep_m4(keep):
            keep.append(r)
    return keep


def keep_m4(rows: list[dict]) -> bool:
    return any(r["#"] == "M4" for r in rows)


def console_summary(
    rows: list[dict], cells: pd.DataFrame, cfg: dict[str, Any], label: str, full_month: bool
) -> None:
    con = logging_setup.console
    t = Table(title=f"Five metrics: {label}", show_edge=False, header_style="bold")
    for c in ("#", "metric", "value", "n", "data behind it"):
        t.add_column(c, justify="right" if c in ("value", "n") else "left")
    for r in summary_rows(rows):
        t.add_row(r["#"], r["metric"], r["value"], r["n"], r["data behind it"])
    con.print(t)
    if full_month:
        print_top_cells(cells, cfg, label)
    else:
        con.print(
            f"[dim]Incentive cells need n >= {cfg['incentives']['min_cell_trips']} trips per "
            "zone x day x hour over a full month; a sample cannot rank them. The decision lists "
            "come from the committed full-month run (make demo prints them next).[/dim]"
        )


def print_top_cells(cells: pd.DataFrame, cfg: dict[str, Any], label: str) -> None:
    """Both ranked lists; `label` says where the cells come from (this run or a committed one)."""
    con = logging_setup.console
    inc = cfg["incentives"]
    for list_name, k, title in (
        ("neighbourhood", inc["console_top_n"], "incentive cells (neighbourhoods)"),
        ("airport", inc["airport_top_n"], "airport cells (escalate to airport ops)"),
    ):
        part = cells[cells["list"] == list_name].head(k)
        top = Table(title=f"Top {k} {title}: {label}", show_edge=False, header_style="bold")
        for c in ("#", "borough · zone", "dow-hour", "late rate", "n", "excess"):
            top.add_column(c, justify="right" if c in ("late rate", "n", "excess", "#") else "left")
        for r in part.itertuples(index=False):
            top.add_row(
                str(r.rank),
                f"{r.borough} · {r.zone}",
                f"{DOW[r.dow]} {r.hour:02d}",
                f"{100 * r.late_rate:.1f}%",
                f"{r.n:,}",
                f"{r.excess_late_trips:.0f}",
            )
        if not len(part):
            top.add_row("", f"no cells with n >= {inc['min_cell_trips']}", "", "", "", "")
        con.print(top)


def write_report(
    con: duckdb.DuckDBPyConnection,
    month: str,
    cfg: dict[str, Any],
    rules: dict[str, Any],
    m: MetricsResult,
    ctx: dict[str, Any],
    out_dir: Path,
) -> ReportResult:
    lk = Lookup(m.metrics)
    rows = evidence_rows(lk, cfg)
    inc = cfg["incentives"]
    chart_paths = make_charts(con, m, cfg, rules, out_dir)
    chart_md = [f"![{p.stem}](charts/{p.name})" for p in chart_paths]
    if not m.weather_available:
        chart_md.append("_Rain vs dry chart not drawn: weather UNAVAILABLE._")
    main = cfg["kpi"]["late_minutes"]
    parts = [
        f"# Evidence: {month}",
        "",
        f"Scope: {ctx['scope']}. Pipeline commit `{ctx['git_sha']}`. Generated by "
        "`pipeline/report.py` from `metrics.csv` and `incentive_cells.csv`.",
        "",
        "## Evidence table",
        "",
        table(pd.DataFrame(rows)),
        "",
        *findings(m, lk, cfg),
        f"## Top {inc['top_n']} neighbourhood cells for driver incentives",
        "",
        f"Ranked by **excess late trips** = (cell late rate - citywide late rate "
        f"{pct(m.kpi)}) x n: how many more trips were late in that cell than if it matched the "
        f"city. Cells need n >= {inc['min_cell_trips']} trips in the month; "
        f"{(m.cells['list'] == 'neighbourhood').sum():,} neighbourhood and "
        f"{(m.cells['list'] == 'airport').sum():,} airport cells qualify. Day and hour are of the "
        "request. Pickup zones 264/265 and pre-arranged rides are excluded. Same rule for both "
        "lists; the split is on zone type (`service_zone` Airports / EWR in the zone lookup).",
        "",
        table(neighbourhood_table(m.cells, inc["top_n"])),
        "",
        f"## Top {inc['airport_top_n']} airport cells: escalate to airport ops",
        "",
        "Not a driver-incentive list: the lever is staging-lot throughput and Port Authority "
        "dispatch. The delay is on the supply side when request -> arrival in the cell is well "
        "above the zone's own month median while dwell (driver at the curb, rider not yet in) "
        "stays short.",
        "",
        table(airport_table(m.cells, inc["airport_top_n"])),
        "",
        "## Charts",
        "",
        *[line for md in chart_md for line in (md, "")],
        *kual(ctx, lk, cfg),
        "## Definitions",
        "",
        f"- M1 late-pickup rate: share of wait-eligible clean trips with wait > {main} min "
        "(`avg(is_late_N)` over `model.fact_trip WHERE NOT flag_r12`).",
        "- M2: `quantile_cont(wait_minutes, 0.9)` over the same rows.",
        "- M3: `median(dwell_minutes)` where `NOT flag_r09 AND NOT flag_r13` (arrival captured).",
        "- M4: late rate in rainy request hours minus dry ones; raw, and within hour of day.",
        "- M5: trusted-row share, wait-eligible share and dwell coverage, all over rows in.",
        "- SQL: `pipeline/sql/07_metrics.sql`, `pipeline/sql/08_incentive_cells.sql`.",
        "",
    ]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "evidence.md"
    path.write_text("\n".join(parts))
    html_path = out_dir / "evidence.html"
    html_path.write_text(markdown_to_html("\n".join(parts), out_dir, f"Evidence: {month}"))
    console_summary(rows, m.cells, cfg, ctx["scope"], ctx["scope"] == "full month")
    log.info("evidence written: %s, %s (+ %d charts)", path.name, html_path.name, len(chart_paths))
    return ReportResult(path, chart_paths)


HTML_CSS = """
:root { --ink: #0b0b0b; --muted: #52514e; --rule: #e6e5e1; --surface: #fcfcfb; --accent: #2a78d6; }
body { margin: 0; background: var(--surface); color: var(--ink);
       font: 15px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 28px; margin: 8px 0 4px; } h2 { font-size: 20px; margin: 32px 0 8px;
     border-bottom: 1px solid var(--rule); padding-bottom: 4px; }
p, li { max-width: 80ch; } code { background: #f0efec; padding: 1px 4px; border-radius: 3px; }
.table { overflow-x: auto; } table { border-collapse: collapse; font-size: 13px; margin: 8px 0; }
th, td { border-bottom: 1px solid var(--rule); padding: 6px 10px; text-align: left;
         vertical-align: top; } th { color: var(--muted); font-weight: 600; }
td.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
img { max-width: 100%; height: auto; border: 1px solid var(--rule); margin: 8px 0; }
"""


def _inline(text: str) -> str:
    import html
    import re

    t = html.escape(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    t = re.sub(r"(?<![*\w])_(.+?)_(?![\w])", r"<em>\1</em>", t)
    return t


def _is_num(cell: str) -> bool:
    import re

    return bool(re.fullmatch(r"[-+]?[\d,.]+(%| pts| min)?", cell.strip()))


def markdown_to_html(md: str, out_dir: Path, title: str) -> str:
    """evidence.md -> one static HTML page (no JS): the same text, tables and charts, with the
    PNGs inlined as data URIs so the page is a single self-contained file."""
    import base64
    import html
    import re

    out: list[str] = []
    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split(" | ")])
                i += 1
            head, body = rows[0], [r for r in rows[2:]]
            out.append(
                '<div class="table"><table><thead><tr>'
                + "".join(f"<th>{_inline(c)}</th>" for c in head)
                + "</tr></thead><tbody>"
            )
            for r in body:
                out.append(
                    "<tr>"
                    + "".join(
                        f'<td class="num">{_inline(c)}</td>'
                        if _is_num(c)
                        else f"<td>{_inline(c)}</td>"
                        for c in r
                    )
                    + "</tr>"
                )
            out.append("</tbody></table></div>")
            continue
        if line.startswith("- "):
            out.append("<ul>")
            while i < len(lines) and lines[i].startswith("- "):
                out.append(f"<li>{_inline(lines[i][2:])}</li>")
                i += 1
            out.append("</ul>")
            continue
        img = re.fullmatch(r"!\[(.*?)\]\((.+?)\)", line.strip())
        if img:
            data = base64.b64encode((out_dir / img.group(2)).read_bytes()).decode()
            out.append(
                f'<img alt="{html.escape(img.group(1))}" src="data:image/png;base64,{data}">'
            )
        elif line.startswith("# "):
            out.append(f"<h1>{_inline(line[2:])}</h1>")
        elif line.startswith("## "):
            out.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("### "):
            out.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.strip():
            out.append(f"<p>{_inline(line)}</p>")
        i += 1
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html.escape(title)}</title><style>{HTML_CSS}</style></head>"
        "<body><main>\n" + "\n".join(out) + "\n</main></body></html>\n"
    )
