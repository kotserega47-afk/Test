# core/job_state.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

_STATE_DIR = Path("/tmp/job_state")
_STATE_DIR.mkdir(parents=True, exist_ok=True)


def _path(job_type: str) -> Path:
    safe = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in job_type)
    return _STATE_DIR / f"{safe}.json"


def load_state(job_type: str) -> Dict[str, Any]:
    p = _path(job_type)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def save_state(job_type: str, state: Dict[str, Any]) -> None:
    p = _path(job_type)
    p.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def get_last_fingerprint(job_type: str) -> Optional[str]:
    return load_state(job_type).get("fingerprint")


def set_last_fingerprint(job_type: str, fp: str) -> None:
    st = load_state(job_type)
    st["fingerprint"] = fp
    save_state(job_type, st)