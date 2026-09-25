"""Each validation rule fires on a synthetic row built to trigger it, and only where intended."""

from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import pandas as pd
import pytest

from pipeline import profile, validate
from pipeline.config import load_config
from pipeline.errors import ValidationFailed

CFG = load_config()
RULES = validate.load_rules()
BOUNDS = ("2026-07-01", "2026-08-01")
T0 = datetime(2026, 7, 15, 12, 0, 7)


def base_row(**overrides) -> dict:
    """A clean, unremarkable trip: 5 min wait, 1 min dwell, 3 miles in 15 minutes."""
    row = {
        "hvfhs_license_num": "HV0003",
        "dispatching_base_num": "B03404",
        "originating_base_num": "B03404",
        "request_datetime": T0,
        "on_scene_datetime": T0 + timedelta(minutes=4),
        "pickup_datetime": T0 + timedelta(minutes=5),
        "dropoff_datetime": T0 + timedelta(minutes=20),
        "PULocationID": 161,
        "DOLocationID": 237,
        "trip_miles": 3.0,
        "trip_time": 900,
        "base_passenger_fare": 20.0,
        "tolls": 0.0,
        "bcf": 0.5,
        "sales_tax": 1.8,
        "congestion_surcharge": 2.75,
        "airport_fee": 0.0,
        "tips": 0.0,
        "driver_pay": 15.0,
        "shared_request_flag": "N",
        "shared_match_flag": "N",
        "access_a_ride_flag": "N",
        "wav_request_flag": "N",
        "wav_match_flag": "N",
        "cbd_congestion_fee": 0.0,
    }
    row.update(overrides)
    return row


def minutes(n: float) -> timedelta:
    return timedelta(minutes=n)


# Each case: (rule_id, row). Rows are built so they trigger the named rule.
CASES = {
    # pickup 1 min before request, and no pre-arrangement signal (on_scene not before request)
    "R01": base_row(request_datetime=T0 + minutes(6), on_scene_datetime=T0 + minutes(6)),
    "R02": base_row(dropoff_datetime=T0 + minutes(5)),
    "R03": base_row(
        pickup_datetime=T0 + minutes(200),
        on_scene_datetime=T0 + minutes(199),
        dropoff_datetime=T0 + minutes(215),
    ),
    "R04": base_row(
        request_datetime=datetime(2026, 8, 1, 0, 1),
        on_scene_datetime=datetime(2026, 8, 1, 0, 3),
        pickup_datetime=datetime(2026, 8, 1, 0, 4),
        dropoff_datetime=datetime(2026, 8, 1, 0, 20),
    ),
    "R06": base_row(trip_miles=30.0, trip_time=900),  # 120 mph by trip_time
    "R07": base_row(trip_time=1200),  # 20 min vs 15 min of timestamps
    "R08": base_row(PULocationID=265),
    "R09": base_row(on_scene_datetime=None),
    "R10": base_row(trip_miles=0.0),
    "R11": base_row(base_passenger_fare=-5.0),
    "R12": base_row(on_scene_datetime=T0 - minutes(3)),  # driver there before the request
    "R13": base_row(on_scene_datetime=T0 + minutes(5)),  # on_scene == pickup
    "R14": base_row(request_datetime=datetime(2026, 7, 15, 12, 0, 0)),
}


def run_rules(rows: list[dict], tmp_path, floor: float = 0.0) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    df = pd.DataFrame(rows)
    df["PULocationID"] = df["PULocationID"].astype("int32")
    df["DOLocationID"] = df["DOLocationID"].astype("int32")
    con.execute("CREATE SCHEMA raw")
    con.execute(
        "CREATE TABLE raw.trips AS SELECT *, (row_number() OVER () - 1)::BIGINT AS file_row_number "
        "FROM df"
    )
    con.execute(
        "CREATE TABLE raw.zones AS SELECT i AS LocationID, 'B' AS Borough, 'Z' || i AS Zone, "
        "'S' AS service_zone FROM range(1, 266) t(i)"
    )
    profile.build_stage(con, RULES, None)
    cfg = {**CFG, "thresholds": {**CFG["thresholds"], "trust_floor": floor}}
    validate.validate(con, "2026-07", cfg, RULES, tmp_path, BOUNDS)
    return con


def test_rule_file_is_well_formed():
    ids = [r["id"] for r in RULES["rules"]]
    assert len(ids) == len(set(ids))
    for r in RULES["rules"]:
        assert r["rationale"] and r["evidence"], r["id"]


def test_every_rule_has_a_triggering_case():
    assert set(CASES) == {r["id"] for r in RULES["rules"]} - {"R05"}


@pytest.mark.parametrize("rule_id", sorted(CASES))
def test_rule_triggers(rule_id, tmp_path):
    con = run_rules([base_row(), CASES[rule_id]], tmp_path)
    rule = next(r for r in RULES["rules"] if r["id"] == rule_id)
    if rule["severity"] == "REJECT":
        got = con.execute("SELECT rule_id, trip_id FROM quarantine.trips").fetchall()
        assert got == [(rule_id, 1)]
    else:
        col = validate.flag(rule_id)
        got = con.execute(f"SELECT trip_id FROM clean.trips WHERE {col} ORDER BY 1").fetchall()
        assert got == [(1,)]
        assert con.execute("SELECT count(*) FROM quarantine.trips").fetchone()[0] == 0


def test_clean_base_row_triggers_nothing(tmp_path):
    con = run_rules([base_row()], tmp_path)
    flags = [validate.flag(r["id"]) for r in RULES["rules"] if r["severity"] == "FLAG"]
    row = con.execute(f"SELECT {', '.join(flags)} FROM clean.trips").fetchone()
    assert row == tuple(False for _ in flags)


def test_exact_duplicate_quarantines_copy_and_flags_survivor(tmp_path):
    con = run_rules([base_row(), base_row(), base_row(PULocationID=100)], tmp_path)
    assert con.execute("SELECT rule_id, trip_id FROM quarantine.trips").fetchall() == [("R05", 1)]
    survivors = con.execute(
        "SELECT trip_id FROM clean.trips WHERE flag_r05_survivor ORDER BY 1"
    ).fetchall()
    assert survivors == [(0,)]


def test_pooled_riders_sharing_business_key_are_not_duplicates(tmp_path):
    rider_b = base_row(request_datetime=T0 + minutes(1), base_passenger_fare=17.0)
    con = run_rules([base_row(), rider_b], tmp_path)
    assert con.execute("SELECT count(*) FROM quarantine.trips").fetchone()[0] == 0


def test_negative_wait_with_prearrangement_is_flagged_not_rejected(tmp_path):
    prearranged = base_row(
        request_datetime=T0 + minutes(10),  # stamped after pickup and after on_scene
        on_scene_datetime=T0 + minutes(4),
    )
    con = run_rules([base_row(), prearranged], tmp_path)
    assert con.execute("SELECT count(*) FROM quarantine.trips").fetchone()[0] == 0
    assert con.execute("SELECT flag_r12 FROM clean.trips WHERE trip_id = 1").fetchone()[0]


def test_zero_trip_time_never_divides(tmp_path):
    con = run_rules([base_row(trip_time=0)], tmp_path)
    speed = con.execute("SELECT speed_mph FROM clean.trips").fetchone()[0]
    assert speed is None  # R06 cannot fire; R07 flags the duration disagreement instead
    assert con.execute("SELECT flag_r07 FROM clean.trips").fetchone()[0]


def test_first_reject_rule_wins_and_all_matches_are_recorded(tmp_path):
    both = base_row(  # 200-min wait (R03) and 120 mph by trip_time (R06)
        pickup_datetime=T0 + minutes(200),
        on_scene_datetime=T0 + minutes(199),
        dropoff_datetime=T0 + minutes(215),
        trip_miles=30.0,
    )
    con = run_rules([both], tmp_path)
    rule_id, rules = con.execute("SELECT rule_id, reject_rules FROM quarantine.trips").fetchone()
    assert rule_id == "R03" and rules == ["R03", "R06"]


def test_bad_dropoff_timestamp_is_kept_with_valid_wait(tmp_path):
    con = run_rules([base_row(dropoff_datetime=T0 + minutes(5))], tmp_path)
    row = con.execute("SELECT flag_r02, wait_minutes, trip_minutes FROM clean.trips").fetchone()
    assert row == (True, 5.0, 15.0)  # trip_minutes from trip_time, not the broken timestamp


def test_rows_reconcile(tmp_path):
    rows = [base_row(PULocationID=i % 263 + 1) for i in range(20)] + list(CASES.values())
    con = run_rules(rows, tmp_path)
    clean = con.execute("SELECT count(*) FROM clean.trips").fetchone()[0]
    quarantined = con.execute("SELECT count(*) FROM quarantine.trips").fetchone()[0]
    assert clean + quarantined == len(rows)


def test_trust_floor_refuses_to_publish(tmp_path):
    with pytest.raises(ValidationFailed) as exc:
        run_rules([base_row(), CASES["R03"]], tmp_path, floor=0.95)
    assert exc.value.exit_code == 3


def test_sample_is_deterministic_and_keeps_business_key_groups():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA raw")
    rows = pd.DataFrame(
        [base_row(PULocationID=i % 263 + 1, trip_miles=float(i)) for i in range(2000)]
    )
    rows["PULocationID"] = rows["PULocationID"].astype("int32")
    rows["DOLocationID"] = rows["DOLocationID"].astype("int32")
    con.execute(
        "CREATE TABLE raw.trips AS SELECT *, (row_number() OVER () - 1)::BIGINT file_row_number "
        "FROM rows"
    )
    picks = []
    for _ in range(2):
        profile.build_stage(con, RULES, 10)
        picks.append(con.execute("SELECT trip_id FROM stage.trips ORDER BY 1").fetchall())
    assert picks[0] == picks[1]
    assert 100 < len(picks[0]) < 300  # ~1/10 of 2,000
