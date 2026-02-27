# core/event_log.py
from __future__ import annotations

import json
import os
import time
import threading
from pathlib import Path
from typing import Any, Dict, Optional


# -----------------------------------------------------------------------------
# Dropbox-backed runtime storage
# -----------------------------------------------------------------------------
# Contract:
#   - events are append-only JSONL in <STATE_DIR>/events/events_YYYY-MM-DD.jsonl
#   - no /tmp usage
#
# STATE_DIR defaults to /config/state (Railway persistent volume / Dropbox-synced)
# -----------------------------------------------------------------------------

_DEFAULT_STATE_DIR = "/config/state"
_STATE_DIR_ENV_KEYS = ("STATE_DIR", "CONFIG_STATE_DIR", "DROPBOX_STATE_DIR")

_lock = threading.Lock()


def _state_dir() -> Path:
    for k in _STATE_DIR_ENV_KEYS:
        v = os.getenv(k, "").strip()
        if v:
            return Path(v)
    return Path(_DEFAULT_STATE_DIR)


def _events_dir() -> Path:
    d = _state_dir() / "events"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _events_path(day_ymd: str) -> Path:
    return _events_dir() / f"events_{day_ymd}.jsonl"


def append_event(
    *,
    type: str,
    job_id: Optional[str] = None,
    job_type: Optional[str] = None,
    actor: Optional[Dict[str, Any]] = None,
    rules_version: Optional[str] = None,
    rules_source: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Append one event record to daily JSONL.
    Never raises on write errors (best effort), but callers should still treat
    rule validation errors etc. as fatal elsewhere.
    """
    ts = time.time()
    day = time.strftime("%Y-%m-%d", time.gmtime(ts))
    rec = {
        "ts": ts,
        "type": type,
        "job_id": job_id,
        "job_type": job_type,
        "actor": actor or {},
        "rules_version": rules_version,
        "rules_source": rules_source,
        "payload": payload or {},
        "pid": os.getpid(),
    }
    p = _events_path(day)
    line = json.dumps(rec, ensure_ascii=False) + "\n"

    # in-process lock to avoid interleaving lines
    try:
        with _lock:
            with p.open("a", encoding="utf-8") as f:
                f.write(line)
    except Exception:
        # best-effort; event log must not crash the platform
        return
