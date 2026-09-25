# Late Pickup Radar

Monthly pipeline from NYC TLC high-volume FHV (ride-hail) trip records to a ranked list of
zone × hour-of-week cells for driver-supply incentives. Spec: [docs/PRD.md](docs/PRD.md).

_Full README (PRD Section 11) is written in phase 7._

## 1. Problem
## 2. Users / stakeholders
## 3. Project KPI
## 4. Why Track B with HVFHV data
## 5. Source overview
## 6. Workflow & data model
## 7. Validation approach
## 8. Metrics
## 9. Results
## 10. Known / Unknown / Assumption / Limitation
## 11. Pipeline
## 12. Setup / run instructions

**Requirements (measured, not estimated).** Python 3.11+, about 6 GB of free RAM, and about 6 GB of
free disk (511 MB download, ~4 GB DuckDB warehouse). With the default `config.yml`
(`duckdb.threads: 2`, `duckdb.memory_limit: 4GB`), a full-month run of 2026-07 (20,921,249 trips)
took **4:15 wall time with a peak process RSS of 5.3 GB** on a 12-core, 10 GB laptop (with the
trip file already downloaded; the first download adds about a minute). `memory_limit` caps
DuckDB's buffer pool, not the whole process: on a smaller machine, lower `memory_limit` and DuckDB
spills to `data/warehouse/tmp` and runs slower. The sample run (`make demo`) needs a fraction of
that.

_Full copy-paste instructions are written in phase 7._
## 13. One FDE judgement call
## 14. Repo map
