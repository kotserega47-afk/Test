# core/event_log.py
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

_EVENTS_DIR = Path("/tmp/events")
_EVENTS_DIR.mkdir(parents=True, exist_ok=True)


def _events_path(day_ymd: str) -> Path:
    return _EVENTS_DIR / f"events_{day_ymd}.jsonl"


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
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")