"""Render and run the SQL files in pipeline/sql/ against DuckDB."""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pandas as pd

SQL_DIR = Path(__file__).resolve().parent / "sql"


class Fragment(str):
    """Trusted SQL text (column lists, generated rule expressions): inserted unquoted."""


def sql_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _format(value: object) -> str:
    if isinstance(value, Fragment):
        return str(value)
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return repr(value)
    return sql_literal(value)  # str / Path


def render_text(text: str, **params: object) -> str:
    """Substitute {name} placeholders: strings/paths are quoted, numbers and Fragments are not."""
    return text.format(**{k: _format(v) for k, v in params.items()})


def render(name: str, **params: object) -> str:
    return render_text((SQL_DIR / name).read_text(), **params)


def run_file(con: duckdb.DuckDBPyConnection, name: str, **params: object) -> None:
    con.execute(render(name, **params))


def named_queries(name: str, **params: object) -> dict[str, str]:
    """Split a SQL file on '-- name: <key>' markers into {key: rendered query}."""
    text = render(name, **params)
    parts = re.split(r"^-- name: (\S+)\s*$", text, flags=re.MULTILINE)
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts), 2)}


def connect(cfg: dict | None = None, path: str | Path | None = None) -> duckdb.DuckDBPyConnection:
    """DuckDB connection with the configured memory limit, threads and spill directory."""
    con = duckdb.connect(str(path) if path is not None else ":memory:")
    if cfg is not None and "duckdb" in cfg:
        from pipeline.config import resolve_path

        d = cfg["duckdb"]
        tmp = resolve_path(d["temp_directory"])
        tmp.mkdir(parents=True, exist_ok=True)
        con.execute(f"SET memory_limit = {sql_literal(d['memory_limit'])}")
        con.execute(f"SET threads = {int(d['threads'])}")
        con.execute(f"SET temp_directory = {sql_literal(tmp)}")
    return con


def query_df(con: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    return con.execute(sql).df()
