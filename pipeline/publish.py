"""Atomic outputs: stages write into a staging directory; only a fully successful run swaps it
into outputs/<name>/. A failed run leaves outputs/<name>/ untouched and records its manifest in
outputs/<name>/failed/run_manifest_<run_id>.json (git-ignored).
"""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.manifest import write_json_atomic

FAILED = "failed"


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def make_staging(staging_root: Path, name: str, run_id: str) -> Path:
    path = staging_root / f"{name}-{run_id}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def publish(staging: Path, final: Path) -> None:
    """Swap staging into place. The old directory is renamed aside first, so at no point is a
    half-written outputs/<name>/ visible; its failed/ history is carried over."""
    final.parent.mkdir(parents=True, exist_ok=True)
    backup = final.with_name(f".{final.name}.previous")
    if backup.exists():
        shutil.rmtree(backup)
    if final.exists():
        os.replace(final, backup)
    os.replace(staging, final)
    old_failed = backup / FAILED
    if old_failed.exists():
        os.replace(old_failed, final / FAILED)
    if backup.exists():
        shutil.rmtree(backup)


def record_failure(
    staging: Path | None, final: Path, manifest: dict[str, Any], run_id: str
) -> Path:
    """Discard staged outputs and write the failed run's manifest beside (not over) outputs."""
    if staging is not None and staging.exists():
        shutil.rmtree(staging)
    path = final / FAILED / f"run_manifest_{run_id}.json"
    write_json_atomic(path, manifest)
    return path
