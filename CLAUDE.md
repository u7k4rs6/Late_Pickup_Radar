Project name: Late Pickup Radar (repo: late-pickup-radar).

# Project rules for Claude Code

Goal: a trustworthy, repeatable path from NYC TLC HVFHV data to a monthly
"where to incentivise drivers" decision. Grading = source reasoning,
retrieval, validation, workflow+metrics, pipeline dependability (20% each).

## Non-negotiables
- NEVER guess a URL, column name, row count or threshold. Verify against the
  real source, then record it in docs/source_map.md or docs/assumptions.md.
- NEVER silently fix data. Rows either pass, are FLAGGED (kept, marked) or
  are REJECTED (quarantined with rule_id). Every threshold has a written
  rationale.
- NEVER write into data/raw/ except from pipeline/ingest.py. Raw is immutable.
- NEVER commit data/raw or data/warehouse. data/sample/ is committed.
- No magic numbers in code: thresholds live in config.yml / validation_rules.yml.
- Every stage logs rows in / rows out; the run manifest must reconcile.
- Prefer SQL in pipeline/sql/*.sql for all transforms; Python orchestrates.
- Keep it small. No web UI, no ML, no extra datasets beyond the source map.

## Working style
- Follow docs/PRD.md phases in order; commit per phase ("phase N: ...").
- Before finishing a phase, run: make lint && make test.
- When a real-data finding contradicts the PRD (e.g. a column is missing,
  a threshold is wrong), do NOT silently adapt: change the rule, log the
  reason in docs/assumptions.md, and mention it in the commit message.
- Write docs as you go; README is a deliverable, not an afterthought.
- Sample-first development: build and test on data/sample, then run full.

## Stack
Python 3.11+, DuckDB, pyarrow, pandas, requests, pyyaml, matplotlib, rich,
pytest, ruff. argparse CLI. GitHub Actions (no network in CI).
