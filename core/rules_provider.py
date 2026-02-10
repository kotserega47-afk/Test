from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from integrations.dropbox_watcher import download_file


@dataclass(frozen=True)
class RulesSnapshot:
    local_path: str                 # /tmp/rules_cache/rules.xlsx
    rules_version: str              # sha256 of file bytes
    stat_key: Tuple[float, int]     # (mtime, size)
    loaded_at_ts: float
    source: str                     # dropbox path used


_CACHE_PATH = Path("/tmp/rules_cache/rules.xlsx")
_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)

_last_sync_ts: float = 0.0
_last_snap: Optional[RulesSnapshot] = None


def _dropbox_rules_file_path() -> str:
    p = (os.getenv("RULES_XLSX_PATH") or "").strip()
    if not p:
        raise RuntimeError("RULES_XLSX_PATH пуст — ожидаю dropbox папку или путь к rules.xlsx")
    return p if p.lower().endswith(".xlsx") else p.rstrip("/") + "/rules.xlsx"


def _stat_key(path: Path) -> Tuple[float, int]:
    st = path.stat()
    return (st.st_mtime, st.st_size)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def _wait_file_stable(path: Path, checks: int = 3, interval_sec: float = 0.25, timeout_sec: float = 8.0) -> None:
    start = time.time()
    last = None
    stable = 0
    while True:
        st = path.stat()
        cur = (st.st_size, st.st_mtime)
        if cur == last:
            stable += 1
        else:
            stable = 0
            last = cur
        if stable >= checks:
            return
        if time.time() - start > timeout_sec:
            raise TimeoutError(f"rules cache not stable: {path}")
        time.sleep(interval_sec)

def get_rules_snapshot(*, force_sync: bool = False) -> RulesSnapshot:
    global _last_sync_ts, _last_snap

    ttl = float(os.getenv("RULES_SYNC_MIN_INTERVAL_SEC", "60"))
    now = time.time()

    if (not force_sync) and _last_snap is not None and _CACHE_PATH.exists() and (now - _last_sync_ts) < ttl:
        try:
            if _stat_key(_CACHE_PATH) == _last_snap.stat_key:
                return _last_snap
        except Exception:
            pass

    src = _dropbox_rules_file_path()

    try:
        ok = download_file(src, str(_CACHE_PATH))
        if not ok:
            raise RuntimeError(f"Не удалось скачать rules.xlsx из Dropbox: {src}")

        _wait_file_stable(_CACHE_PATH)

        _last_sync_ts = now
        key = _stat_key(_CACHE_PATH)
        ver = _sha256_file(_CACHE_PATH)

        _last_snap = RulesSnapshot(
            local_path=str(_CACHE_PATH),
            rules_version=ver,
            stat_key=key,
            loaded_at_ts=now,
            source=src,
        )
        return _last_snap

    except Exception as e:
        if _last_snap is not None and Path(_last_snap.local_path).exists():
            return _last_snap
        raise RuntimeError(f"rules.xlsx not ready: {e}") from e
