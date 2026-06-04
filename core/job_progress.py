# core/job_progress.py
"""In-memory job stage progress for Job Health Guard (observe-only)."""
from __future__ import annotations

import threading
import time
from typing import Dict, Optional, Tuple

_lock = threading.Lock()
_progress: Dict[str, Tuple[str, float]] = {}


def record_progress(job_type: str, stage: str) -> None:
    """Record the latest stage marker for a running job (best-effort, never raises)."""
    jt = (job_type or "").strip()
    st = (stage or "").strip()
    if not jt or not st:
        return
    try:
        ts = time.time()
        with _lock:
            _progress[jt] = (st, ts)
    except Exception:
        return


def get_progress(job_type: str) -> Optional[Tuple[str, float]]:
    """Return (stage, recorded_ts) or None if no progress recorded."""
    jt = (job_type or "").strip()
    if not jt:
        return None
    try:
        with _lock:
            return _progress.get(jt)
    except Exception:
        return None


def clear_progress(job_type: str) -> None:
    """Test helper / optional explicit clear."""
    jt = (job_type or "").strip()
    if not jt:
        return
    with _lock:
        _progress.pop(jt, None)


def _reset_job_progress_for_tests() -> None:
    with _lock:
        _progress.clear()
