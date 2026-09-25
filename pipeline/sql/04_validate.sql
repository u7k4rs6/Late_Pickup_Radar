-- Apply pipeline/validation_rules.yml to stage.trips (PRD 5.2). The rule expressions are
-- generated from the YAML by pipeline/validate.py; the fully rendered SQL for the last run
-- is shown in docs/validation_rules.md.
--   quarantine.trips: every REJECTed row, its primary rule_id, every REJECT rule it matched,
--                     and its FLAG columns. Never deleted.
--   clean.trips:      every other row, with one flag_<id> boolean per FLAG rule, plus
--                     flag_r05_survivor on the kept copy of an exact duplicate.
CREATE SCHEMA IF NOT EXISTS quarantine;
CREATE SCHEMA IF NOT EXISTS clean;

CREATE OR REPLACE TEMP TABLE checked AS
SELECT
    *,
{hit_columns}
FROM stage.trips;

CREATE OR REPLACE TABLE quarantine.trips AS
SELECT
    {primary_rule} AS rule_id,
    {reject_list}  AS reject_rules,
    * EXCLUDE ({all_hits}),
{flag_columns}
FROM checked
WHERE {any_reject}
ORDER BY trip_id;

CREATE OR REPLACE TABLE clean.trips AS
SELECT
    * EXCLUDE ({all_hits}),
{flag_columns},
    dup_count > 1 AS flag_r05_survivor
FROM checked
WHERE NOT ({any_reject})
ORDER BY trip_id;

DROP TABLE checked;
