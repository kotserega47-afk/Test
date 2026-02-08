# core/access_guard.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Tuple

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

from core.access_rules import AccessRules

icon, name = LOG_PROFILES["MAIN"]
logger = get_logger(name, icon)


@dataclass(frozen=True)
class AccessContext:
    chat_type: str
    chat_id: int
    user_id: int


def _chat_key(ctx: AccessContext) -> Any:
    return "private" if ctx.chat_type == "private" else int(ctx.chat_id)


def check_access(rules: AccessRules, ctx: AccessContext, command: str) -> Tuple[bool, str, Dict[str, Any]]:
    cmd = (command or "").strip().lstrip("/").lower()
    details: Dict[str, Any] = {
        "command": cmd,
        "chat_type": ctx.chat_type,
        "chat_id": ctx.chat_id,
        "user_id": ctx.user_id,
    }

    try:
        snap = rules.get_snapshot()
    except ValueError as e:
        # rules.xlsx скачался, но нарушен контракт (колонки/значения)
        msg = str(e)
        logger.warning(f"🔐 guard: rules_invalid command={cmd} chat={ctx.chat_id} user={ctx.user_id} err={msg}")
        return False, "rules_invalid", {**details, "err": msg}
    except Exception as e:
        # инфраструктура (Dropbox/скачивание/битый файл)
        logger.warning(f"🔐 guard: rules_not_ready command={cmd} chat={ctx.chat_id} user={ctx.user_id} err={e}")
        return False, "rules_not_ready", details

    rule = snap.commands_map.get(cmd)
    if rule is None:
        logger.warning(f"🔐 guard: unknown_command command={cmd} chat={ctx.chat_id} user={ctx.user_id}")
        return False, "unknown_command", details

    is_private = ctx.chat_type == "private"
    if is_private and not rule.allow_private:
        logger.info(f"🔐 guard: command_not_allowed_here(private) command={cmd} user={ctx.user_id}")
        return False, "command_not_allowed_here", {**details, "where": "private"}

    if (not is_private) and not rule.allow_groups:
        logger.info(f"🔐 guard: command_not_allowed_here(group) command={cmd} chat={ctx.chat_id} user={ctx.user_id}")
        return False, "command_not_allowed_here", {**details, "where": "group"}

    chat_key = _chat_key(ctx)
    level = snap.access_map.get((chat_key, ctx.user_id))
    if level is None:
        logger.info(f"🔐 guard: no_access_rule command={cmd} chat_key={chat_key} user={ctx.user_id}")
        return False, "no_access_rule", {**details, "level": 0, "required": rule.required_level}

    details["level"] = int(level)
    details["required"] = int(rule.required_level)

    if int(level) < int(rule.required_level):
        logger.info(
            f"🔐 guard: insufficient_level command={cmd} chat_key={chat_key} user={ctx.user_id} "
            f"level={level} required={rule.required_level}"
        )
        return False, "insufficient_level", details

    return True, "ok", details


def deny_message(reason: str, details: Dict[str, Any]) -> str:
    if reason == "unknown_command":
        return "❌ Команда не разрешена правилами (нет записи в commands)."
    if reason == "command_not_allowed_here":
        where = details.get("where")
        return "❌ Команда запрещена в личке." if where == "private" else "❌ Команда запрещена в группе."
    if reason == "no_access_rule":
        return "❌ Нет доступа (нет записи в access)."
    if reason == "insufficient_level":
        return f"❌ Недостаточно прав (level {details.get('level', 0)} < {details.get('required', 999)})."
    if reason == "rules_not_ready":
        return "❌ Правила доступа временно недоступны (rules.xlsx обновляется/недоступен)."
    if reason == "rules_invalid":
        err = details.get("err", "")
        # не спамим огромным текстом, но даём суть
        short = err if len(err) <= 220 else (err[:220] + "…")
        return f"❌ rules.xlsx сломан: {short}"
    return "❌ Нет доступа."

