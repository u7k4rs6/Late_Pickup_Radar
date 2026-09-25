"""Render and run the SQL files in pipeline/sql/ against DuckDB."""

from __future__ import annotations

from pathlib import Path

import duckdb

SQL_DIR = Path(__file__).resolve().parent / "sql"


def sql_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def render(name: str, **params: str | Path) -> str:
    """Substitute {name} placeholders with quoted SQL string literals."""
    return (SQL_DIR / name).read_text().format(**{k: sql_literal(v) for k, v in params.items()})


def run_file(con: duckdb.DuckDBPyConnection, name: str, **params: str | Path) -> None:
    con.execute(render(name, **params))
