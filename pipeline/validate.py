"""Stage 4: apply validation_rules.yml, quarantine rejects, flag the rest, reconcile.

NEVER silently fixes data: every row leaving the clean set is in quarantine.trips with a
rule_id, and every count here is written to validation_report.md and the run manifest.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import yaml
from rich.table import Table

from pipeline import db, logging_setup
from pipeline.errors import StageCheckFailed, ValidationFailed
from pipeline.logging_setup import FILE_ONLY, log
from pipeline.markdown import table

RULES_PATH = Path(__file__).resolve().parent / "validation_rules.yml"
SEVERITIES = ("REJECT", "FLAG", "ASSUME")


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    rules = yaml.safe_load(path.read_text())
    seen = set()
    for r in rules["rules"]:
        if r["severity"] not in SEVERITIES[:2]:
            raise ValueError(f"{r['id']}: severity must be REJECT or FLAG in `rules`")
        if r["id"] in seen:
            raise ValueError(f"duplicate rule id {r['id']}")
        seen.add(r["id"])
    return rules


def _param(value: object) -> object:
    if isinstance(value, list):
        return db.Fragment("(" + ", ".join(str(v) for v in value) + ")")
    return value


def condition(rule: dict[str, Any], month_bounds: tuple[str, str]) -> str:
    params = {k: _param(v) for k, v in (rule.get("params") or {}).items()}
    params["month_start"] = db.Fragment(f"TIMESTAMP '{month_bounds[0]}'")
    params["month_end"] = db.Fragment(f"TIMESTAMP '{month_bounds[1]}'")
    return db.render_text(rule["when"], **params)


def hit(rule_id: str) -> str:
    return f"hit_{rule_id.lower()}"


def flag(rule_id: str) -> str:
    return f"flag_{rule_id.lower()}"


def excluding_flags(rules: dict[str, Any], family: str) -> list[str]:
    """flag_<id> columns whose rule excludes rows from a metric family (wait, dwell, zone...)."""
    return [flag(r["id"]) for r in rules["rules"] if family in (r.get("excludes") or [])]


def eligible(rules: dict[str, Any], family: str) -> db.Fragment:
    """Condition for a clean row to enter metrics of `family`."""
    ex = excluding_flags(rules, family)
    return db.Fragment(" AND ".join(f"NOT {c}" for c in ex) if ex else "TRUE")


def wait_ok(rules: dict[str, Any]) -> db.Fragment:
    """Condition for a clean row to enter wait-based metrics (KPI, p90 wait)."""
    return eligible(rules, "wait")


def render_validate_sql(rules: dict[str, Any], month_bounds: tuple[str, str]) -> str:
    rs = rules["rules"]
    rejects = [r for r in rs if r["severity"] == "REJECT"]
    flags = [r for r in rs if r["severity"] == "FLAG"]
    # The comma goes before each trailing comment, or the comment would swallow it.
    hit_columns = "\n".join(
        f"    coalesce(({condition(r, month_bounds)}), false) AS {hit(r['id'])}"
        + ("," if i < len(rs) - 1 else "")
        + f"  -- {r['id']} {r['severity']}: {r['name']}"
        for i, r in enumerate(rs)
    )
    primary = "CASE " + " ".join(f"WHEN {hit(r['id'])} THEN '{r['id']}'" for r in rejects) + " END"
    reject_list = (
        "list_filter(["
        + ", ".join(f"CASE WHEN {hit(r['id'])} THEN '{r['id']}' END" for r in rejects)
        + "], x -> x IS NOT NULL)"
    )
    return db.render(
        "04_validate.sql",
        hit_columns=db.Fragment(hit_columns),
        primary_rule=db.Fragment(primary),
        reject_list=db.Fragment(reject_list),
        all_hits=db.Fragment(", ".join(hit(r["id"]) for r in rs)),
        flag_columns=db.Fragment(
            ",\n".join(f"    {hit(r['id'])} AS {flag(r['id'])}" for r in flags)
        ),
        any_reject=db.Fragment(" OR ".join(hit(r["id"]) for r in rejects)),
    )


@dataclass
class ValidationResult:
    rows_in: int
    rows_clean: int
    rows_quarantined: int
    trusted_share: float
    trust: dict[str, Any]
    rule_counts: list[dict[str, Any]]
    report_path: Path


def _counts(con: duckdb.DuckDBPyConnection, rules: dict[str, Any], rows_in: int) -> list[dict]:
    primary = dict(
        con.execute("SELECT rule_id, count(*) FROM quarantine.trips GROUP BY 1").fetchall()
    )
    matched = dict(
        con.execute(
            "SELECT r, count(*) FROM (SELECT unnest(reject_rules) r FROM quarantine.trips) "
            "GROUP BY 1"
        ).fetchall()
    )
    out = []
    for r in rules["rules"]:
        rid = r["id"]
        if r["severity"] == "REJECT":
            n = primary.get(rid, 0)
            also = matched.get(rid, 0)
        else:
            n = con.execute(f"SELECT count(*) FROM clean.trips WHERE {flag(rid)}").fetchone()[0]
            also = None
        out.append(
            {
                "rule_id": rid,
                "severity": r["severity"],
                "name": r["name"],
                "rows": n,
                "pct_of_rows_in": round(100 * n / rows_in, 4) if rows_in else 0.0,
                "rows_matching_incl_overlaps": also,
                "excludes": ", ".join(r.get("excludes") or []) or None,
            }
        )
    survivors = con.execute("SELECT count(*) FROM clean.trips WHERE flag_r05_survivor").fetchone()[
        0
    ]
    out.append(
        {
            "rule_id": "R05s",
            "severity": "FLAG",
            "name": "survivor of an exact duplicate (kept copy)",
            "rows": survivors,
            "pct_of_rows_in": round(100 * survivors / rows_in, 4) if rows_in else 0.0,
            "rows_matching_incl_overlaps": None,
            "excludes": None,
        }
    )
    return out


def _console_table(counts: list[dict], trust: dict[str, Any], floor: float) -> None:
    t = Table(show_edge=False, header_style="bold")
    for col, justify in (
        ("rule_id", "left"),
        ("severity", "left"),
        ("rows", "right"),
        ("%", "right"),
        ("rule", "left"),
    ):
        t.add_column(col, justify=justify)
    for c in counts:
        style = "red" if c["severity"] == "REJECT" else "yellow"
        t.add_row(
            c["rule_id"],
            f"[{style}]{c['severity']}[/{style}]",
            f"{c['rows']:,}",
            f"{c['pct_of_rows_in']:.3f}",
            c["name"],
        )
    logging_setup.console.print(t)
    trusted = trust["trusted_row_share"]
    mark = "[green]✔[/green]" if trusted >= floor else "[red]✘[/red]"
    logging_setup.console.print(
        f"trusted-row share   [bold]{trusted:.3%}[/bold] (floor {floor:.0%}) {mark}\n"
        f"wait-eligible share [bold]{trust['wait_eligible_share']:.3%}[/bold] (rows in the KPI)\n"
        f"dwell coverage      [bold]{trust['dwell_coverage']:.3%}[/bold] (on_scene < pickup)"
    )


def validate(
    con: duckdb.DuckDBPyConnection,
    month: str,
    cfg: dict[str, Any],
    rules: dict[str, Any],
    out_dir: Path,
    month_bounds: tuple[str, str],
    docs_path: Path | None = None,
) -> ValidationResult:
    sql = render_validate_sql(rules, month_bounds)
    con.execute(sql)

    rows_in = con.execute("SELECT count(*) FROM stage.trips").fetchone()[0]
    rows_clean = con.execute("SELECT count(*) FROM clean.trips").fetchone()[0]
    rows_q = con.execute("SELECT count(*) FROM quarantine.trips").fetchone()[0]
    if rows_clean + rows_q != rows_in:
        raise StageCheckFailed(
            f"reconciliation failed: clean {rows_clean:,} + quarantined {rows_q:,} != "
            f"rows in {rows_in:,}"
        )
    log.info(
        "reconciliation: clean %s + quarantined %s == rows in %s ✔",
        f"{rows_clean:,}",
        f"{rows_q:,}",
        f"{rows_in:,}",
    )
    trust = trust_shares(con, rules, rows_in, rows_clean)
    trusted = trust["trusted_row_share"]
    floor = cfg["thresholds"]["trust_floor"]
    counts = _counts(con, rules, rows_in)
    for c in counts:
        log.info(
            "%-4s %-6s %10s rows (%.4f%%) %s",
            c["rule_id"],
            c["severity"],
            f"{c['rows']:,}",
            c["pct_of_rows_in"],
            c["name"],
            extra=FILE_ONLY,
        )
    _console_table(counts, trust, floor)
    log.info(
        "trusted-row share %.3f%%; wait-eligible share %.3f%%; dwell coverage %.3f%%",
        100 * trusted,
        100 * trust["wait_eligible_share"],
        100 * trust["dwell_coverage"],
        extra=FILE_ONLY,
    )

    report = _report(con, month, cfg, rules, counts, rows_in, rows_clean, rows_q, trust)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "validation_report.md"
    path.write_text(report)
    if docs_path is not None:
        docs_path.write_text(render_rules_doc(rules, sql))
    log.info("validation report written: %s", path.name)

    if trusted < floor:
        raise ValidationFailed(
            f"trusted-row share {trusted:.3%} is below the floor {floor:.0%}: "
            "metrics will not be published"
        )
    return ValidationResult(rows_in, rows_clean, rows_q, trusted, trust, counts, path)


def trust_shares(con, rules: dict[str, Any], rows_in: int, rows_clean: int) -> dict[str, Any]:
    """M5 as three lines: how much data stands behind each number (all over rows in)."""
    wait_n = con.execute(f"SELECT count(*) FROM clean.trips WHERE {wait_ok(rules)}").fetchone()[0]
    dwell_n = con.execute(
        f"SELECT count(*) FROM clean.trips WHERE {eligible(rules, 'dwell')}"
    ).fetchone()[0]
    share = (lambda k: k / rows_in) if rows_in else (lambda k: 0.0)
    return {
        "rows_in": rows_in,
        "trusted_rows": rows_clean,
        "trusted_row_share": share(rows_clean),
        "wait_eligible_rows": wait_n,
        "wait_eligible_share": share(wait_n),
        "dwell_rows": dwell_n,
        "dwell_coverage": share(dwell_n),
    }


def _kpi_queries(cfg: dict[str, Any], rules: dict[str, Any]) -> dict[str, str]:
    low, high = sorted(cfg["kpi"]["sensitivity_minutes"])
    return db.named_queries(
        "05_kpi_sensitivity.sql",
        wait_ok=wait_ok(rules),
        late_low=low,
        late_main=cfg["kpi"]["late_minutes"],
        late_high=high,
    )


def _report(con, month, cfg, rules, counts, rows_in, rows_clean, rows_q, trust) -> str:
    import pandas as pd

    q = _kpi_queries(cfg, rules)
    variants = con.execute(q["kpi_variants"]).df()
    segments = con.execute(q["kpi_by_segment"]).df()
    r14 = con.execute(q["r14_check"]).df()
    r14_row = r14.to_dict("records")[0] if len(r14) else {}
    ratio = r14_row.get("ratio")
    bar = cfg["kpi"]["r14_promotion_ratio"]
    if ratio is None or ratio != ratio:  # NaN when there are no rows
        r14_text = "No wait-eligible rows: the R14 check cannot be computed."
    else:
        verdict = (
            "above the promotion bar: R14 should be excluded from wait metrics like R12"
            if ratio > bar
            else "below the promotion bar, so R14 stays a sensitivity line"
        )
        r14_text = (
            f"Whole-minute rows are late {ratio:.2f}x as often as the rest (bar: {bar}x), "
            f"{verdict}. Excess over the 1-in-60 chance level: "
            f"{int(r14_row['excess_over_chance']):,} rows."
        )
    main = cfg["kpi"]["late_minutes"]
    col = f"late_rate_{main}_pct"
    v = {r["variant"][0]: r for r in variants.to_dict("records")}

    def rate(key: str) -> str:
        return f"{v[key][col]:.3f}%" if key in v else "n/a (no rows)"

    def n(key: str) -> str:
        return f"{int(v[key]['n']):,}" if key in v else "0"

    shift = f"{v['b'][col] - v['a'][col]:+.5f} points" if "a" in v and "b" in v else "n/a"
    r03 = next(c for c in counts if c["rule_id"] == "R03")
    wait_flagged = sum(
        c["rows"] for c in counts if c["severity"] == "FLAG" and "wait" in (c["excludes"] or "")
    )
    floor = cfg["thresholds"]["trust_floor"]

    kpi_text = (
        f"The headline late-pickup rate (wait > {main} min) is **{rate('a')}** over {n('a')} "
        f"trips that can enter wait metrics. R03 removed {r03['rows']:,} trips "
        f"({r03['pct_of_rows_in']:.4f}% of rows in); if they were kept, the rate would be "
        f"**{rate('b')}** ({shift}). {wait_flagged:,} clean trips carry a FLAG that excludes them "
        f"from wait metrics (R12, pre-arranged); counting their request-to-pickup time as a wait "
        f"would give **{rate('c')}**. Also dropping every whole-minute request (R14: most "
        f"remaining reservations plus a random 1/60 of on-demand trips) gives **{rate('d')}**; "
        f"the gap between that and the headline bounds the effect of reservations R12 does not "
        f"catch."
    )
    count_df = pd.DataFrame(counts)
    return "\n".join(
        [
            f"# Validation report: {month}",
            "",
            "Rules: `pipeline/validation_rules.yml` (rendered in `docs/validation_rules.md`). "
            "REJECT rows are filed under the first matching rule in file order; "
            "`rows_matching_incl_overlaps` counts every REJECT rule a quarantined row matched. "
            "FLAG counts are over clean rows.",
            "",
            "## Row flow",
            "",
            table(
                pd.DataFrame(
                    [
                        {"step": "rows in (stage.trips)", "rows": rows_in},
                        {"step": "rejected -> quarantine.trips", "rows": rows_q},
                        {"step": "rows out (clean.trips)", "rows": rows_clean},
                    ]
                )
            ),
            "",
            f"Reconciliation: clean {rows_clean:,} + quarantined {rows_q:,} = "
            f"{rows_clean + rows_q:,} == rows in {rows_in:,} ✔",
            "",
            "## How much data stands behind each number (M5)",
            "",
            table(
                pd.DataFrame(
                    [
                        {
                            "line": "trusted-row share (not quarantined)",
                            "rows": trust["trusted_rows"],
                            "share_of_rows_in_pct": round(100 * trust["trusted_row_share"], 4),
                            "note": f"floor {floor:.0%}; hard fail below it",
                        },
                        {
                            "line": "wait-eligible share (rows in the KPI and p90 wait)",
                            "rows": trust["wait_eligible_rows"],
                            "share_of_rows_in_pct": round(100 * trust["wait_eligible_share"], 4),
                            "note": "clean and not excluded from wait metrics",
                        },
                        {
                            "line": "dwell coverage (on_scene < pickup)",
                            "rows": trust["dwell_rows"],
                            "share_of_rows_in_pct": round(100 * trust["dwell_coverage"], 4),
                            "note": "clean and not excluded from dwell metrics",
                        },
                    ]
                )
            ),
            "",
            "## Per rule",
            "",
            table(count_df),
            "",
            "## What this means for the KPI",
            "",
            kpi_text,
            "",
            table(variants),
            "",
            "### Whole-minute requests (R14): promote or keep as sensitivity?",
            "",
            r14_text,
            "",
            table(r14),
            "",
            "### By company and WAV request",
            "",
            "WAV is reported as its own line: pre-arranged rows are much more common among Lyft "
            "WAV trips, so wait coverage is shown beside the rate.",
            "",
            table(segments),
            "",
        ]
    )


def render_rules_doc(rules: dict[str, Any], sql: str) -> str:
    lines = [
        "# Validation rules",
        "",
        "_Generated from `pipeline/validation_rules.yml` by the validate stage. Do not edit by "
        "hand._",
        "",
        "| rule_id | severity | rule | condition | excluded from | rationale |",
        "|---|---|---|---|---|---|",
    ]
    for r in rules["rules"]:
        params = r.get("params") or {}
        cond = r["when"].format(
            **{k: str(tuple(v)) if isinstance(v, list) else v for k, v in params.items()},
            month_start="<month start>",
            month_end="<next month start>",
        )
        if r["severity"] == "REJECT":
            excluded = "all metrics"
        else:
            excluded = ", ".join(r.get("excludes") or []) or "none (sensitivity only)"
        lines.append(
            f"| {r['id']} | {r['severity']} | {r['name']} | `{cond}` | {excluded} | "
            f"{' '.join(r['rationale'].split())} |"
        )
    for a in rules.get("assumptions", []):
        lines.append(
            f"| {a['id']} | ASSUME | {a['name']} | n/a | n/a | {' '.join(a['statement'].split())} |"
        )
    lines += ["", "## Evidence and PRD changes", ""]
    for r in rules["rules"]:
        lines.append(f"- **{r['id']}** {' '.join(r['evidence'].split())}")
        if r.get("prd_change"):
            lines.append(f"  - _PRD change:_ {' '.join(r['prd_change'].split())}")
    lines += ["", "## Rendered SQL (last run)", "", "```sql", sql.strip(), "```", ""]
    return "\n".join(lines)
