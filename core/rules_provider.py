# core/state_provider.py
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from integrations.dropbox_watcher import download_file, upload_file


@dataclass(frozen=True)
class StateSnapshot:
    local_path: str
    stat_key: tuple[float, int]       # (mtime, size)
    loaded_at_ts: float
    source: str                       # dropbox path used


_CACHE_DIR = Path("/tmp/state_cache")
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

_CACHE_PATH = _CACHE_DIR / "state.json"

_last_sync_ts: float = 0.0
_last_snap: Optional[StateSnapshot] = None
_last_state: Optional[Dict[str, Any]] = None


def _rules_dropbox_path() -> str:
    """
    RULES_XLSX_PATH:
      - can be '/folder' or '/folder/rules.xlsx'
    Returns dropbox path to rules.xlsx.
    """
    p = (os.getenv("RULES_XLSX_PATH") or "").strip()
    if not p:
        raise RuntimeError("RULES_XLSX_PATH пуст — ожидаю dropbox папку или путь к rules.xlsx")
    return p if p.lower().endswith(".xlsx") else p.rstrip("/") + "/rules.xlsx"


def _state_dropbox_path() -> str:
    """
    Canonical:
      <rules_folder>/state/state.json
    """
    rules = _rules_dropbox_path()
    folder = rules[:-len("/rules.xlsx")] if rules.lower().endswith("/rules.xlsx") else rules.rsplit("/", 1)[0]
    return folder.rstrip("/") + "/state/state.json"


def _stat_key(path: Path) -> tuple[float, int]:
    st = path.stat()
    return (st.st_mtime, st.st_size)


def _load_local_json(path: Path) -> Dict[str, Any]:
    try:
        if not path.exists():
            return {}
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        return json.loads(raw) or {}
    except Exception:
        return {}


def _save_local_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_state_snapshot(*, force_sync: bool = False) -> StateSnapshot:
    """
    Fetches /state/state.json from Dropbox into /tmp/state_cache/state.json.
    Fail-safe:
      - if download fails but we have cached local -> return cached snapshot
      - else raise
    """
    global _last_sync_ts, _last_snap, _last_state

    ttl = float(os.getenv("STATE_SYNC_MIN_INTERVAL_SEC", "30"))
    now = time.time()

    if (not force_sync) and _last_snap is not None and _CACHE_PATH.exists() and (now - _last_sync_ts) < ttl:
        try:
            if _stat_key(_CACHE_PATH) == _last_snap.stat_key:
                return _last_snap
        except Exception:
            pass

    src = _state_dropbox_path()

    # download (if missing in dropbox -> we will bootstrap later on write)
    ok = download_file(src, str(_CACHE_PATH))
    if ok:
        _last_sync_ts = now
        key = _stat_key(_CACHE_PATH)
        _last_snap = StateSnapshot(local_path=str(_CACHE_PATH), stat_key=key, loaded_at_ts=now, source=src)
        _last_state = _load_local_json(_CACHE_PATH)
        return _last_snap

    # fail-safe fallback: if we already have local cached state
    if _last_snap is not None and Path(_last_snap.local_path).exists():
        return _last_snap
    if _CACHE_PATH.exists():
        key = _stat_key(_CACHE_PATH)
        _last_snap = StateSnapshot(local_path=str(_CACHE_PATH), stat_key=key, loaded_at_ts=now, source=src)
        _last_state = _load_local_json(_CACHE_PATH)
        return _last_snap

    raise RuntimeError(f"state.json not ready (download failed): {src}")


def load_state(*, force_sync: bool = False) -> Dict[str, Any]:
    global _last_state
    get_state_snapshot(force_sync=force_sync)
    if _last_state is None:
        _last_state = _load_local_json(_CACHE_PATH)
    return _last_state


def _ensure_shape(st: Dict[str, Any]) -> Dict[str, Any]:
    st = st or {}
    st.setdefault("meta", {})
    st.setdefault("jobs", {})
    return st


def get_job_state(job_type: str, *, force_sync: bool = False) -> Dict[str, Any]:
    st = _ensure_shape(load_state(force_sync=force_sync))
    return dict(st.get("jobs", {}).get(job_type, {}) or {})


def get_job_value(job_type: str, key: str, default: Any = None, *, force_sync: bool = False) -> Any:
    js = get_job_state(job_type, force_sync=force_sync)
    return js.get(key, default)


def update_job_state(
    job_type: str,
    patch: Dict[str, Any],
    *,
    actor: Optional[Dict[str, Any]] = None,
    force_sync: bool = True,
) -> Dict[str, Any]:
    """
    Atomic update:
      - download latest (force_sync)
      - apply patch
      - upload overwrite
      - update local cache
    If state.json doesn't exist in dropbox yet, we'll create it.
    """
    global _last_sync_ts, _last_snap, _last_state

    # always pull fresh before write (avoid lost updates)
    src = _state_dropbox_path()

    st = {}
    ok = download_file(src, str(_CACHE_PATH))
    if ok:
        st = _load_local_json(_CACHE_PATH)

    st = _ensure_shape(st)

    jobs = st.setdefault("jobs", {})
    cur = dict(jobs.get(job_type, {}) or {})
    cur.update(patch or {})
    jobs[job_type] = cur

    meta = st.setdefault("meta", {})
    meta["last_update_ts"] = int(time.time())
    if actor:
        meta["last_update_by"] = actor

    tmp = _CACHE_DIR / f"state_tmp_{int(time.time() * 1000)}.json"
    _save_local_json(tmp, st)

    up_ok = upload_file(str(tmp), src)
    try:
        tmp.unlink(missing_ok=True)
    except Exception:
        pass

    if not up_ok:
        raise RuntimeError(f"Failed to upload state.json to Dropbox: {src}")

    # refresh local cache from what we wrote
    _save_local_json(_CACHE_PATH, st)
    _last_sync_ts = time.time()
    _last_snap = StateSnapshot(local_path=str(_CACHE_PATH), stat_key=_stat_key(_CACHE_PATH), loaded_at_ts=_last_sync_ts, source=src)
    _last_state = st
    return st


def set_job_value(
    job_type: str,
    key: str,
    value: Any,
    *,
    actor: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return update_job_state(job_type, {key: value}, actor=actor, force_sync=True)