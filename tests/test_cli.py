import subprocess
import sys

import pytest

from pipeline.__main__ import build_parser


def test_help_prints():
    out = subprocess.run(
        [sys.executable, "-m", "pipeline", "--help"], capture_output=True, text=True, check=True
    )
    assert "run" in out.stdout and "show" in out.stdout


def test_sample_flags_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        build_parser().parse_args(
            ["run", "--month", "2026-07", "--sample", "0.02", "--sample-file", "x.parquet"]
        )


@pytest.mark.parametrize("bad", ["2026-13", "26-07", "2026-7"])
def test_month_validation(bad):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--month", bad])
