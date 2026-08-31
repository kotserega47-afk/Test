# core/lock_status.py
"""Read-only PID lock inspection for observation-only /status (Phase 1a)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Dict, Literal, Tuple, Union

LockField = Union[float, Literal["none", "unknown"]]
PidField = Union[int, Literal["none", "unknown"]]


def _state_dir() -> Path:
    return Path(os.getenv("STATE_DIR", "/data/state"))


KNOWN_JOB_TYPES: Tuple[str, ...] = (
    "wallet",
    "hourly",
    "rate",
    "download",
    "raccoon_wallet",
    "raccoon_hourly",
    "raccoon_daily_conversion",
    "wallet_editor_registry_refresh",
    "wallet_editor_auto_enable",
)


def _safe_job_name(job_type: str) -> str:
    return "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in (job_type or ""))


def _lock_path(job_type: str) -> Path:
    return _state_dir() / "locks" / f"{_safe_job_name(job_type)}.lock"


def _parse_lock_pid(raw: str) -> PidField:
    text = (raw or "").strip()
    if not text:
        return "unknown"
    try:
        pid = int(text)
        return pid if pid > 0 else "unknown"
    except ValueError:
        pass
    try:
        data = json.loads(text)
        pid = int(data["pid"])
        return pid if pid > 0 else "unknown"
    except Exception:
        return "unknown"


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
            raw = path.read_text(encoding="utf-8")
            pid = _parse_lock_pid(raw)
            if pid != "unknown":
                return age, pid
        except Exception:
            pass
        try:
            meta = path.with_name(path.name + ".meta")
            if meta.exists():
                return age, _parse_lock_pid(meta.read_text(encoding="utf-8"))
        except Exception:
            pass
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
