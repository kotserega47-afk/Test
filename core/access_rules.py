# core/access_rules.py
import time

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from core.rules_provider import get_snapshot_v2
from core.rules_v2.accessors import AccessRulesAccessor
from core.rules_v2.indexes import build_indexes

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
    access_map: Dict[Tuple[Any, int], int]
    commands_map: Dict[str, CommandRule]
    stat_key: Tuple[float, int]
    loaded_at_ts: float
    source: str
    accessor: AccessRulesAccessor

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


class AccessRules:
    def __init__(self, rules_env_path: str | None = None):
        self._snap: Optional[Snapshot] = None

    def invalidate(self) -> None:
        self._snap = None

    def get_snapshot(self, force_sync: bool = False) -> Snapshot:
        snapshot_v2 = get_snapshot_v2(force_sync=force_sync)
        indexes = build_rules_indexes(snapshot_v2)

        meta = snapshot_v2.meta
        stat_key = (float(meta.updated_at.timestamp()), len(snapshot_v2.partners))

        if self._snap is not None and self._snap.stat_key == stat_key:
            return self._snap

        accessor = AccessRulesAccessor(snapshot_v2, indexes)

        access_map: Dict[Tuple[Any, int], int] = {}
        for rule in snapshot_v2.access_rules:
            if not rule.enabled:
                continue

            role = snapshot_v2.roles.get(rule.role_key)
            if not role or not role.enabled:
                continue

            chat_key: Any = "private" if str(rule.chat_id).strip().lower() == "private" else int(rule.chat_id)
            access_map[(chat_key, int(rule.user_id))] = int(role.role_level)

        commands_map: Dict[str, CommandRule] = {}
        for command in snapshot_v2.commands.values():
            if not command.enabled:
                continue

            policy = indexes.command_policy_by_command_key.get(command.command_key)
            if not policy or not policy.enabled:
                continue

            role = snapshot_v2.roles.get(policy.min_role_key)
            if not role or not role.enabled:
                continue

            cmd = str(command.command_text).strip().lstrip("/").lower()
            if not cmd:
                continue

            commands_map[cmd] = CommandRule(
                required_level=int(role.role_level),
                allow_private=bool(policy.allow_private),
                allow_groups=bool(policy.allow_groups),
                enabled=True,
            )

        snap = Snapshot(
            access_map=access_map,
            commands_map=commands_map,
            stat_key=stat_key,
            loaded_at_ts=time.time(),
            source=f"snapshot_v2:{meta.ruleset_version}",
            accessor=accessor,
        )
        self._snap = snap

        logger.info(
            f"🔐 AccessRules loaded from snapshot_v2: access={len(access_map)} "
            f"commands={len(commands_map)} version={meta.ruleset_version}"
        )
        return snap

