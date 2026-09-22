# core/access_rules.py
import threading
import time

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES
from core.rules_provider import PublishedState, get_published_state, with_provider_lock
from core.rules_v2.accessors import AccessRulesAccessor

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
    provider_generation: int = 0


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


def _derive_access_snapshot(published: PublishedState) -> Snapshot:
    snapshot_v2 = published.snapshot
    indexes = published.indexes
    meta = snapshot_v2.meta
    accessor = AccessRulesAccessor(snapshot_v2, indexes)

    access_map: Dict[Tuple[Any, int], int] = {}
    for rule in snapshot_v2.access_rules:
        if not rule.enabled:
            continue

        role = snapshot_v2.roles.get(rule.role_key)
        if not role or not role.enabled:
            continue

        raw_chat = str(rule.chat_id).strip()
        chat_key: Any = "private" if raw_chat.lower() == "private" else int(float(raw_chat))
        access_map[(chat_key, int(float(str(rule.user_id))))] = int(role.role_level)

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

    return Snapshot(
        access_map=access_map,
        commands_map=commands_map,
        stat_key=published.stat_key,
        loaded_at_ts=time.time(),
        source=f"snapshot_v2:{meta.ruleset_version}",
        accessor=accessor,
        provider_generation=published.generation,
    )


class AccessRules:
    def __init__(self, rules_env_path: str | None = None):
        self._snap: Optional[Snapshot] = None
        self._snap_epoch: int = 0
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        with self._lock:
            self._snap_epoch += 1
            self._snap = None

    def get_snapshot(self, force_sync: bool = False) -> Snapshot:
        with self._lock:
            start_epoch = self._snap_epoch
        published = get_published_state(force_sync=force_sync)

        def _reuse(current: PublishedState | None) -> Snapshot | None:
            with self._lock:
                if (
                    self._snap is not None
                    and self._snap.provider_generation == published.generation
                    and self._snap_epoch == start_epoch
                    and current is not None
                    and current.generation == published.generation
                ):
                    return self._snap
            return None

        reused = with_provider_lock(_reuse)
        if reused is not None:
            return reused

        derived = _derive_access_snapshot(published)

        def _cas(current: PublishedState | None) -> Snapshot:
            with self._lock:
                if self._snap_epoch != start_epoch:
                    return derived
                if current is None or current.generation != published.generation:
                    return derived
                self._snap = derived
                logger.info(
                    f"🔐 AccessRules loaded from snapshot_v2: access={len(derived.access_map)} "
                    f"commands={len(derived.commands_map)} version={published.snapshot.meta.ruleset_version}"
                )
                return derived

        return with_provider_lock(_cas)
