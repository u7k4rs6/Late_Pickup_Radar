"""Load config.yml and resolve paths. Thresholds and URLs live there, not in code."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
TRIPS_URL_ENV = "PIPELINE_TRIPS_URL"


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg = yaml.safe_load((path or ROOT / "config.yml").read_text())
    if override := os.environ.get(TRIPS_URL_ENV):
        cfg["sources"]["trips_url_template"] = override
    return cfg


def resolve(cfg: dict[str, Any], key: str) -> Path:
    """Path from cfg['paths'][key], relative to the repo root unless absolute."""
    p = Path(cfg["paths"][key])
    return p if p.is_absolute() else ROOT / p
