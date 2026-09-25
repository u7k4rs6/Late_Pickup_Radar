"""Atomic outputs: stages write into a staging directory; only a fully successful run swaps it
into outputs/<name>/. A failed run leaves outputs/<name>/ untouched and records its manifest in
outputs/<name>/failed/run_manifest_<run_id>.json (git-ignored).
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.manifest import write_json_atomic

FAILED = "failed"


class RunInProgress(Exception):
    """Another live run holds the lock for this output name."""


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # exists, owned by someone else
        return True
    return True


def acquire_lock(staging_root: Path, name: str, final: Path, run_id: str) -> Path:
    """One run per output name. A lock whose process is dead means the previous run was
    hard-killed (no chance to clean up): its staging directories are swept and a failed-run
    manifest is recorded for it, so the history stays complete."""
    staging_root.mkdir(parents=True, exist_ok=True)
    lock = staging_root / f"{name}.lock"
    if lock.exists():
        held = json.loads(lock.read_text())
        if _pid_alive(int(held["pid"])):
            raise RunInProgress(
                f"another run of {name} is in progress (pid {held['pid']}, run {held['run_id']})"
            )
    for stale in sorted(staging_root.glob(f"{name}-*")):
        if stale.is_dir():
            old_id = stale.name[len(name) + 1 :]
            shutil.rmtree(stale)
            write_json_atomic(
                final / FAILED / f"run_manifest_{old_id}.json",
                {
                    "run_id": old_id,
                    "status": "killed",
                    "exit_code": None,
                    "error": "process was killed before it could clean up; staging swept "
                    f"by run {run_id}. Published outputs were not touched.",
                },
            )
    lock.write_text(json.dumps({"pid": os.getpid(), "run_id": run_id}))
    return lock


def release_lock(lock: Path | None) -> None:
    if lock is not None:
        lock.unlink(missing_ok=True)


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
