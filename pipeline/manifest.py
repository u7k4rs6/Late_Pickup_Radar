"""Checksums, atomic JSON writes, and the run manifest."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.config import ROOT

HASH_CHUNK = 1 << 20
CLEAN_CHECK_PATHS = (".", ":!outputs", ":!docs/validation_rules.md")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True, default=str) + "\n")
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def repo_relative(path: str | Path) -> str:
    """Paths in committed manifests are repo-relative so they don't leak a local home dir."""
    p = Path(path).resolve()
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
        )
        dirty = subprocess.run(
            # Code/config changes only: the pipeline's own outputs never make its version dirty.
            ["git", "status", "--porcelain", "--untracked-files=no", "--", *CLEAN_CHECK_PATHS],
            cwd=ROOT,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
