"""Tiny DataFrame -> GitHub markdown table renderer (no tabulate dependency)."""

from __future__ import annotations

import math

import pandas as pd


def fmt(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.0f}" if value.is_integer() and abs(value) >= 1000 else f"{value:g}"
    return str(value).replace("|", "\\|")


def table(df: pd.DataFrame) -> str:
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join(fmt(_py(v)) for v in row) + " |")
    return "\n".join(lines)


def _py(v: object) -> object:
    """numpy scalars -> python scalars so int formatting applies."""
    return v.item() if hasattr(v, "item") else v
