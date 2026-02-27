# core/job_state.py
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional


# -----------------------------------------------------------------------------
# Dropbox-backed runtime state
# -----------------------------------------------------------------------------
# Contract:
#   - single state.json in <STATE_DIR>/state.json
#   - fingerprints stored under: state["fingerprints"][job_type] = "<hash>"
#   - no /tmp usage
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


def _state_path() -> Path:
    d = _state_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "state.json"


def _read_state_file() -> Dict[str, Any]:
    p = _state_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def _atomic_write_json(p: Path, obj: Dict[str, Any]) -> None:
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def load_state(job_type: str) -> Dict[str, Any]:
    """
    Backward-compatible API.
    Returns per-job state dict (a view of state.json).
    """
    jt = (job_type or "").strip()
    if not jt:
        return {}
    with _lock:
        st = _read_state_file()
        jobs = st.get("jobs") or {}
        if isinstance(jobs, dict) and isinstance(jobs.get(jt), dict):
            return dict(jobs.get(jt) or {})
        return {}


def save_state(job_type: str, state: Dict[str, Any]) -> None:
    """
    Backward-compatible API.
    Persists per-job state dict into state.json under state["jobs"][job_type].
    """
    jt = (job_type or "").strip()
    if not jt:
        return
    state = state or {}
    if not isinstance(state, dict):
        raise TypeError("save_state: state must be a dict")

    p = _state_path()
    with _lock:
        root = _read_state_file()
        if not isinstance(root, dict):
            root = {}

        jobs = root.get("jobs")
        if not isinstance(jobs, dict):
            jobs = {}
            root["jobs"] = jobs

        jobs[jt] = dict(state)
        _atomic_write_json(p, root)


def get_last_fingerprint(job_type: str) -> Optional[str]:
    # Prefer canonical location: root["fingerprints"][job_type]
    jt = (job_type or "").strip()
    if not jt:
        return None
    with _lock:
        root = _read_state_file()

        fps = root.get("fingerprints")
        if isinstance(fps, dict) and isinstance(fps.get(jt), str):
            return fps.get(jt)  # type: ignore[return-value]

        # backward compatibility fallback: jobs[jt]["fingerprint"]
        jobs = root.get("jobs")
        if isinstance(jobs, dict):
            j = jobs.get(jt)
            if isinstance(j, dict):
                v = j.get("fingerprint")
                return v if isinstance(v, str) else None
        return None


def set_last_fingerprint(job_type: str, fp: str) -> None:
    jt = (job_type or "").strip()
    if not jt:
        return
    fp = str(fp or "").strip()
    if not fp:
        return

    p = _state_path()
    with _lock:
        root = _read_state_file()
        if not isinstance(root, dict):
            root = {}

        fps = root.get("fingerprints")
        if not isinstance(fps, dict):
            fps = {}
            root["fingerprints"] = fps

        fps[jt] = fp

        # also mirror into jobs[jt]["fingerprint"] for older readers
        jobs = root.get("jobs")
        if not isinstance(jobs, dict):
            jobs = {}
            root["jobs"] = jobs
        j = jobs.get(jt)
        if not isinstance(j, dict):
            j = {}
            jobs[jt] = j
        j["fingerprint"] = fp

        _atomic_write_json(p, root)
