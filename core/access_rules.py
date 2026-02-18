# core/access_rules.py
from __future__ import annotations

import pandas as pd
import time

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from core.rules_provider import get_rules_snapshot

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

def _validate_access_df(df: pd.DataFrame) -> None:
    bad = df[~df["chat_id"].apply(
        lambda v: str(v).strip().lower() == "private"
        or str(v).strip().lstrip("-").replace(".", "", 1).isdigit()
    )]
    if not bad.empty:
        raise ValueError(f"[access] invalid chat_id values (examples): {bad['chat_id'].head(5).tolist()}")


def _validate_commands_df(df: pd.DataFrame) -> None:
    for c in ("allow_private", "allow_groups"):
        vals = set(df[c].dropna().astype(int).unique().tolist())
        if not vals.issubset({0, 1}):
            raise ValueError(f"[commands] {c} must be 0/1, got {sorted(vals)}")

class AccessRules:
    def __init__(self, rules_env_path: str | None = None):
        self._snap: Optional[Snapshot] = None

    def invalidate(self) -> None:
        self._snap = None

    def get_snapshot(self, force_sync: bool = False) -> Snapshot:
        # 1) Получаем общий snapshot rules.xlsx (Dropbox -> /tmp/rules_cache/rules.xlsx)
        #    Fail-safe уже внутри RulesProvider.
        rs = get_rules_snapshot(force_sync=force_sync)

        # 2) Если локальный cache-файл не изменился — возвращаем кэш, не перечитывая Excel.
        key = rs.stat_key
        if self._snap is not None and self._snap.stat_key == key:
            return self._snap

        # 3) Читаем Excel из локального cache-файла
        df_access = pd.read_excel(rs.local_path, sheet_name="access")
        df_cmds = pd.read_excel(rs.local_path, sheet_name="commands")

        # 4) Проверяем схему (колонки)
        _require_cols(df_access, "access", ("chat_id", "user_id", "level"))
        _require_cols(df_cmds, "commands", ("command", "required_level", "allow_private", "allow_groups"))

        # 5) Валидируем значения
        _validate_access_df(df_access)
        _validate_commands_df(df_cmds)

        # 6) enabled по умолчанию = 1
        if "enabled" not in df_access.columns:
            df_access["enabled"] = 1
        if "enabled" not in df_cmds.columns:
            df_cmds["enabled"] = 1

        # 7) Собираем access_map
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

        # 8) Собираем commands_map
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
                required_level = 999  # fail-safe

            commands_map[cmd] = CommandRule(
                required_level=required_level,
                allow_private=_as_bool01(r.get("allow_private", 0)),
                allow_groups=_as_bool01(r.get("allow_groups", 0)),
                enabled=True,
            )

        # 9) Сохраняем snapshot (atomically)
        snap = Snapshot(
            access_map=access_map,
            commands_map=commands_map,
            stat_key=key,
            loaded_at_ts=time.time(),
            source=rs.source,
        )
        self._snap = snap

        logger.info(
            f"🔐 AccessRules loaded: access={len(access_map)} commands={len(commands_map)} src={rs.source} stat={key}"
        )
        return snap

