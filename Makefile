# Late Pickup Radar: task runner.
PYTHON ?= python3
VENV   := .venv
PY     := $(VENV)/bin/python
MONTH  ?=
RULE   ?= R03

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

# Targets below are implemented in later phases (see docs/PRD.md Section 10).
# Cut data/sample/ from the real month (needs a full `make run MONTH=2026-07` first).
sample:
	$(PY) -m pipeline sample

# The whole pipeline on the committed sample, no network.
run-offline:
	$(PY) -m pipeline run --month 2026-07 --offline

demo demo-rerun demo-fail demo-show demo-reset:
	@echo "make $@: not implemented yet (phase 8)" && exit 1
