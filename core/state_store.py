# core/state_store.py

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional

from integrations.dropbox_watcher import download_file, upload_file
from core.rules_provider import _dropbox_rules_file_path
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from core.event_log import append_event

icon, name = LOG_PROFILES["MAIN"]
log = get_logger(name, icon)

_LOCAL_TMP = Path("/tmp/state_store")
_LOCAL_TMP.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------
# Helpers
# --------------------------------------------------------

def _state_dropbox_path() -> str:
    rules_path = _dropbox_rules_file_path()
    base = rules_path.rsplit("/", 1)[0]
    return f"{base}/state/state.json"


def _local_path() -> Path:
    return _LOCAL_TMP / "state.json"


def _load_from_dropbox() -> Dict[str, Any]:
    db_path = _state_dropbox_path()
    lp = _local_path()

    status = download_file(db_path, str(lp))

    if status == "not_found":
        # первый запуск
        return {}

    if status == "error":
        log.warning("Failed to download state.json from Dropbox, using local state")
        if lp.exists():
            try:
                return json.loads(lp.read_text(encoding="utf-8")) or {}
            except Exception:
                return {}
        return {}

    # status == "ok"
    try:
        return json.loads(lp.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise RuntimeError("state.json corrupted") from e

def _save_to_dropbox(state: Dict[str, Any]) -> None:
    lp = _local_path()
    lp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    db_path = _state_dropbox_path()
    ok = upload_file(str(lp), db_path)
    if not ok:
        raise RuntimeError("Failed to upload state.json to Dropbox")


# --------------------------------------------------------
# Public API
# --------------------------------------------------------

def load_state() -> Dict[str, Any]:
    return _load_from_dropbox()


def state_get(job_type: str, key: str) -> Optional[Any]:
    st = _load_from_dropbox()
    return st.get("jobs", {}).get(job_type, {}).get(key)


def state_update(job_type: str, patch: Dict[str, Any]) -> None:
    last_err: Exception | None = None

    for attempt in range(3):
        st = _load_from_dropbox()
        st.setdefault("jobs", {})
        st["jobs"].setdefault(job_type, {})
        st["jobs"][job_type].update(patch)

        try:
            _save_to_dropbox(st)

            append_event(
                type="state_updated",
                job_type="meta",
                payload={"keys": ["last_update_ts", "last_update_by"]}
            )

            return

        except Exception as e:
            last_err = e
            time.sleep(0.2 * (attempt + 1))

    raise RuntimeError(f"state_update failed after 3 attempts: {last_err}") from last_err

def state_update_meta(actor: Dict[str, Any]) -> None:
    st = _load_from_dropbox()

    st.setdefault("meta", {})
    st["meta"]["last_update_ts"] = int(time.time())
    st["meta"]["last_update_by"] = actor

    _save_to_dropbox(st)