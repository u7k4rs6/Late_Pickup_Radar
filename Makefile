# Late Pickup Radar: task runner.
PYTHON ?= python3
VENV   := .venv
PY     := $(VENV)/bin/python
MONTH  ?=

.PHONY: setup run run-offline test sample lint demo demo-rerun demo-fail demo-show demo-reset

setup:
	$(PYTHON) -m venv $(VENV)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"
	$(PY) -m pipeline --help > /dev/null && echo "setup ok"

run:
	@test -n "$(MONTH)" || (echo "usage: make run MONTH=YYYY-MM" && exit 1)
	$(PY) -m pipeline run --month $(MONTH)

test:
	$(PY) -m pytest -q

lint:
	$(PY) -m ruff check pipeline tests
	$(PY) -m ruff format --check pipeline tests

# ---- demo (PRD 15.1): all offline except demo-fail, which needs one HTTPS request ----
RULE ?= R12

# Sample run with rich output, then the committed full-month decision lists.
demo:
	$(PY) -m pipeline run --month 2026-07 --offline
	@$(PY) -m pipeline show top-cells --month 2026-07

# Same run again: sources verified by checksum, metrics.csv hash compared with the last run.
demo-rerun:
	@$(PY) -m pipeline.demo rerun

# A dead source URL into a throwaway directory: exit 2, nothing partial, real outputs untouched.
demo-fail:
	@$(PY) -m pipeline.demo fail

# Real rows behind one rule, from the last demo run (default R12).
demo-show:
	@$(PY) -m pipeline show rule --rule $(RULE) --n 5 --scope demo --month 2026-07

# Start the demo from clean (the committed full-month outputs are not touched).
demo-reset:
	rm -rf outputs/demo outputs/.staging/demo-* outputs/.staging/demo.lock
	rm -f data/warehouse/2026-07-sample-file.duckdb data/warehouse/2026-07-sample-file.duckdb.wal
	@echo "demo reset"
