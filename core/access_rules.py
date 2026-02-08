# core/access_rules.py
from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from integrations.dropbox_watcher import download_file

icon, name = LOG_PROFILES["MAIN"]
logger = get_logger(name, icon)


@dataclass(frozen=True)
class CommandRule:
    required_level: int
    allow_private: bool
    allow_groups: bool
    enabled: bool = True


@dataclass
class Snapshot:
    access_map: Dict[Tuple[Any, int], int]          # (chat_key, user_id) -> level
    commands_map: Dict[str, CommandRule]            # command -> rule
    stat_key: Tuple[float, int]                     # (mtime, size) of local cache file
    loaded_at_ts: float
    source: str                                     # dropbox path used


def _as_bool01(v: Any) -> bool:
    try:
        return int(v) == 1
    except Exception:
        return bool(v)


def _norm_command(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if s.startswith("/"):
        s = s[1:]
    return s.lower()


def _norm_chat_id(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if s.lower() == "private":
            return "private"
        try:
            return int(float(s))
        except Exception:
            return s
    try:
        return int(v)
    except Exception:
        return v


def _require_cols(df: pd.DataFrame, sheet: str, cols: Tuple[str, ...]) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"[{sheet}] missing columns: {missing}")


def _stat_key(path: Path) -> Tuple[float, int]:
    st = path.stat()
    return (st.st_mtime, st.st_size)


def _wait_file_stable(path: Path, checks: int = 2, interval_sec: float = 0.2, timeout_sec: float = 6.0) -> None:
    """
    Ждём стабильность локального кеш-файла (после скачивания из Dropbox).
    """
    start = time.time()
    last: Optional[Tuple[int, float]] = None
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


class AccessRules:
    """
    SOURCE: env RULES_XLSX_PATH
      - если заканчивается на .xlsx -> считаем это dropbox_path к файлу
      - иначе считаем это dropbox-папка, файл = <dir>/rules.xlsx
    CACHE: /tmp/rules_cache/rules.xlsx
    """

    def __init__(self, rules_env_path: str):
        self.rules_env_path = (rules_env_path or "").strip()
        self.cache_path = Path("/tmp/rules_cache/rules.xlsx")
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        self._snap: Optional[Snapshot] = None
        self._last_sync_ts: float = 0.0
        self._min_sync_interval_sec: float = float(os.getenv("RULES_SYNC_MIN_INTERVAL_SEC", "15"))

    def invalidate(self) -> None:
        self._snap = None
        self._last_sync_ts = 0.0

    def _dropbox_rules_file_path(self) -> str:
        p = self.rules_env_path
        if not p:
            raise RuntimeError("RULES_XLSX_PATH пуст — ожидаю dropbox папку или путь к rules.xlsx")
        if p.lower().endswith(".xlsx"):
            return p
        return p.rstrip("/") + "/rules.xlsx"

    def _maybe_sync_from_dropbox(self, force: bool = False) -> str:
        now = time.time()
        if (not force) and (now - self._last_sync_ts) < self._min_sync_interval_sec and self.cache_path.exists():
            return self._dropbox_rules_file_path()

        dropbox_path = self._dropbox_rules_file_path()
        ok = download_file(dropbox_path, str(self.cache_path))
        if not ok:
            raise RuntimeError(f"Не удалось скачать rules.xlsx из Dropbox: {dropbox_path}")

        _wait_file_stable(self.cache_path)
        self._last_sync_ts = now
        return dropbox_path

    def get_snapshot(self, force_sync: bool = False) -> Snapshot:
        src = self._maybe_sync_from_dropbox(force=force_sync)

        key = _stat_key(self.cache_path)
        if self._snap is not None and self._snap.stat_key == key:
            return self._snap

        df_access = pd.read_excel(self.cache_path, sheet_name="access")
        df_cmds = pd.read_excel(self.cache_path, sheet_name="commands")

        _require_cols(df_access, "access", ("chat_id", "user_id", "level"))
        _require_cols(df_cmds, "commands", ("command", "required_level", "allow_private", "allow_groups"))

        if "enabled" not in df_access.columns:
            df_access["enabled"] = 1
        if "enabled" not in df_cmds.columns:
            df_cmds["enabled"] = 1

        access_map: Dict[Tuple[Any, int], int] = {}
        for _, r in df_access.iterrows():
            if not _as_bool01(r.get("enabled", 1)):
                continue
            chat_key = _norm_chat_id(r.get("chat_id"))
            if chat_key is None:
                continue
            try:
                user_id = int(float(r.get("user_id")))
                level = int(float(r.get("level")))
            except Exception:
                continue
            access_map[(chat_key, user_id)] = level

        commands_map: Dict[str, CommandRule] = {}
        for _, r in df_cmds.iterrows():
            if not _as_bool01(r.get("enabled", 1)):
                continue
            cmd = _norm_command(r.get("command"))
            if not cmd:
                continue
            try:
                required_level = int(float(r.get("required_level")))
            except Exception:
                required_level = 999
            commands_map[cmd] = CommandRule(
                required_level=required_level,
                allow_private=_as_bool01(r.get("allow_private", 0)),
                allow_groups=_as_bool01(r.get("allow_groups", 0)),
                enabled=True,
            )

        snap = Snapshot(
            access_map=access_map,
            commands_map=commands_map,
            stat_key=key,
            loaded_at_ts=time.time(),
            source=src,
        )
        self._snap = snap
        logger.info(f"🔐 AccessRules loaded: access={len(access_map)} commands={len(commands_map)} src={src} stat={key}")
        return snap
