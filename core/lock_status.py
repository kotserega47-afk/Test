# core/lock_status.py
"""Read-only PID lock inspection for observation-only /status (Phase 1a)."""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict, Literal, Tuple, Union

LockField = Union[float, Literal["none", "unknown"]]
PidField = Union[int, Literal["none", "unknown"]]

_DEFAULT_STATE_DIR = "/config/state"
_STATE_DIR_ENV_KEYS = ("STATE_DIR", "CONFIG_STATE_DIR", "DROPBOX_STATE_DIR")

KNOWN_JOB_TYPES: Tuple[str, ...] = ("wallet", "hourly", "rate", "download")


def _state_dir() -> Path:
    for k in _STATE_DIR_ENV_KEYS:
        v = os.getenv(k, "").strip()
        if v:
            return Path(v)
    return Path(_DEFAULT_STATE_DIR)


def _safe_job_name(job_type: str) -> str:
    return "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in (job_type or ""))


def _lock_path(job_type: str) -> Path:
    return _state_dir() / "locks" / f"{_safe_job_name(job_type)}.lock"


def _read_lock_fields(job_type: str) -> Tuple[LockField, PidField]:
    try:
        path = _lock_path(job_type)
        if not path.exists():
            return "none", "none"

        age: LockField = "unknown"
        try:
            age = round(time.time() - path.stat().st_mtime, 1)
        except Exception:
            age = "unknown"

        try:
            raw = path.read_text(encoding="utf-8").strip()
            pid = int(raw)
            if pid <= 0:
                return age, "unknown"
            return age, pid
        except Exception:
            return age, "unknown"
    except Exception:
        return "unknown", "unknown"


def get_lock_status_for_job_types(
    job_types: Tuple[str, ...] = KNOWN_JOB_TYPES,
) -> Dict[str, Dict[str, LockField | PidField]]:
    out: Dict[str, Dict[str, LockField | PidField]] = {}
    for jt in job_types:
        age, pid = _read_lock_fields(jt)
        out[jt] = {"lock_age_sec": age, "lock_pid": pid}
    return out
