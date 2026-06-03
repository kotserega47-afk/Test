"""Telegram delivery routes — shadow compare + Phase 3A runtime migration.

Phase 2: ENV vs rules shadow (default).
Phase 3A: ``platform_hourly_report`` may use Rules V2 when ``TELEGRAM_ROUTES_FROM_RULES_V2=1``.
Phase 3B: ``platform_wallet_download_report`` (wallet download text reports).
Phase 3C: ``conversion_wallet_editor`` (Conversion → Wallet Editor notifications).
Phase 3D: ``bakai_rate_current`` / ``bakai_rate_alert`` (Bakai rate monitor).
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Literal

from core.rules_v2.accessors import BaseRulesAccessor
from core.rules_v2.indexes import RulesIndexes
from core.rules_v2.constants import (
    ALLOWED_TELEGRAM_ROUTE_KEYS,
    LEGACY_ENV_TO_TELEGRAM_ROUTE_KEYS,
    TELEGRAM_CHAT_ID_EMERGENCY_ENV,
    TELEGRAM_ROUTES_SHEET,
)
from core.rules_v2.models import RulesSnapshotV2
from core.rules_v2.validators import validate_telegram_routes_dict

log = logging.getLogger(__name__)

ENV_TELEGRAM_ROUTES_FROM_RULES_V2 = "TELEGRAM_ROUTES_FROM_RULES_V2"
ROUTE_PLATFORM_HOURLY_REPORT = "platform_hourly_report"
ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT = "platform_wallet_download_report"
ROUTE_CONVERSION_WALLET_EDITOR = "conversion_wallet_editor"
ROUTE_BAKAI_RATE_CURRENT = "bakai_rate_current"
ROUTE_BAKAI_RATE_ALERT = "bakai_rate_alert"
ENV_PLATFORM_HOURLY_LEGACY = "TELEGRAM_CHAT_ID_HOURLY"
ENV_PLATFORM_WALLET_LEGACY = "TELEGRAM_CHAT_ID_WALLET"
ENV_CONVERSION_WALLET_EDITOR_LEGACY = "CONVERSION_WALLET_EDITOR"
ENV_BAKAI_RATE_CURRENT_LEGACY = "CURRENT_RATE_BAKAI_CHAT_ID"
ENV_BAKAI_RATE_ALERT_LEGACY = "NEW_RATE_BAKAI_CHAT_ID"

# Phase 3A–3D — routes that read Rules V2 when ``TELEGRAM_ROUTES_FROM_RULES_V2=1``.
MIGRATED_RUNTIME_ROUTES: frozenset[str] = frozenset({
    ROUTE_PLATFORM_HOURLY_REPORT,
    ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT,
    ROUTE_CONVERSION_WALLET_EDITOR,
    ROUTE_BAKAI_RATE_CURRENT,
    ROUTE_BAKAI_RATE_ALERT,
})

LEGACY_ENV_BY_ROUTE_KEY: dict[str, str] = {
    ROUTE_PLATFORM_HOURLY_REPORT: ENV_PLATFORM_HOURLY_LEGACY,
    ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT: ENV_PLATFORM_WALLET_LEGACY,
    ROUTE_CONVERSION_WALLET_EDITOR: ENV_CONVERSION_WALLET_EDITOR_LEGACY,
    ROUTE_BAKAI_RATE_CURRENT: ENV_BAKAI_RATE_CURRENT_LEGACY,
    ROUTE_BAKAI_RATE_ALERT: ENV_BAKAI_RATE_ALERT_LEGACY,
}

RouteSource = Literal[
    "legacy_env",
    "rules_v2",
    "missing_route",
    "disabled_route",
]

_SHADOW_MIN_INTERVAL_SEC = 300.0
_last_shadow_run_ts: float = 0.0
_last_shadow_summary: dict[str, Any] | None = None


def _mask_chat_id(chat_id: str) -> str:
    s = str(chat_id or "").strip()
    if len(s) <= 4:
        return "****"
    return f"{s[:2]}***{s[-2:]}"


def _normalize_chat_id_for_compare(chat_id: str) -> str:
    return str(chat_id or "").strip()


def routes_from_rules_v2_enabled() -> bool:
    return os.getenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass(frozen=True, slots=True)
class RouteResolution:
    route_key: str
    chat_id: str | None
    source: RouteSource


def _log_migrated_route_warning(route_key: str, source: RouteSource) -> None:
    if source == "missing_route":
        log.warning("%s route missing", route_key)
    elif source == "disabled_route":
        log.warning("%s route disabled", route_key)


def _log_route_resolution(resolution: RouteResolution) -> None:
    log.info(
        "[telegram_routes] route=%s source=%s",
        resolution.route_key,
        resolution.source,
    )
    if resolution.route_key in MIGRATED_RUNTIME_ROUTES:
        _log_migrated_route_warning(resolution.route_key, resolution.source)


def _resolve_from_rules_v2(route_key: str) -> RouteResolution:
    try:
        from core.rules_provider import get_snapshot_v2
        from core.rules_v2.accessors import BaseRulesAccessor
        from core.rules_v2.indexes import RulesIndexes

        snapshot = get_snapshot_v2(force_sync=False)
    except Exception:
        log.warning("%s route missing", route_key, exc_info=True)
        return RouteResolution(route_key, None, "missing_route")

    route = snapshot.telegram_routes.get(route_key)
    if route is None:
        log.warning("%s route missing", route_key)
        return RouteResolution(route_key, None, "missing_route")

    if not route.enabled:
        log.warning("%s route disabled", route_key)
        return RouteResolution(route_key, None, "disabled_route")

    indexes = RulesIndexes(telegram_routes_by_key=dict(snapshot.telegram_routes))
    accessor = BaseRulesAccessor(snapshot=snapshot, indexes=indexes)
    chat_id = accessor.get_telegram_chat_id(route_key)
    if not chat_id:
        log.warning("%s route missing", route_key)
        return RouteResolution(route_key, None, "missing_route")

    return RouteResolution(route_key, chat_id, "rules_v2")


def resolve_route_chat_id(route_key: str) -> RouteResolution:
    """Resolve delivery ``chat_id`` for ``route_key`` (no send, no emergency)."""

    key = str(route_key or "").strip()
    use_rules = routes_from_rules_v2_enabled() and key in MIGRATED_RUNTIME_ROUTES

    if use_rules:
        return _resolve_from_rules_v2(key)

    env_name = LEGACY_ENV_BY_ROUTE_KEY.get(key)
    if not env_name:
        for env, mapped_key in LEGACY_ENV_TO_TELEGRAM_ROUTE_KEYS:
            if mapped_key == key:
                env_name = env
                break
    if not env_name:
        return RouteResolution(key, None, "missing_route")

    chat_id = _read_legacy_env_chat(env_name)
    if not chat_id:
        return RouteResolution(key, None, "missing_route")

    return RouteResolution(key, chat_id, "legacy_env")


def send_message_to_route(route_key: str, text: str) -> bool:
    """Resolve route, log source, enqueue text via existing sender if resolved."""

    resolution = resolve_route_chat_id(route_key)
    _log_route_resolution(resolution)
    if not resolution.chat_id:
        if (
            not routes_from_rules_v2_enabled()
            and route_key == ROUTE_PLATFORM_HOURLY_REPORT
        ):
            raise RuntimeError(f"{ENV_PLATFORM_HOURLY_LEGACY} is not set")
        return False

    from integrations.telegram_bot import send_message_sync

    send_message_sync(text, chat_id=resolution.chat_id)
    return True


def send_file_to_route(route_key: str, path: str, caption: str | None = None) -> bool:
    """Resolve route, log source, enqueue file via existing sender if resolved."""

    resolution = resolve_route_chat_id(route_key)
    _log_route_resolution(resolution)
    if not resolution.chat_id:
        return False

    from integrations.telegram_bot import send_file_sync

    send_file_sync(path, caption, chat_id=resolution.chat_id)
    return True


def _read_legacy_env_chat(env_name: str) -> str | None:
    raw = os.getenv(env_name)
    if raw is None:
        return None
    s = str(raw).strip()
    return s if s else None


def _is_runtime_migrated_route(route_key: str) -> bool:
    """True when Rules V2 is runtime source of truth for ``route_key`` (Phase 3A)."""

    return routes_from_rules_v2_enabled() and route_key in MIGRATED_RUNTIME_ROUTES


@dataclass(frozen=True, slots=True)
class TelegramRoutesShadowResult:
    sheet_missing: bool
    matched: int
    missing_rules: int
    missing_env: int
    mismatched: int
    migrated_route_differences: int
    disabled: int
    invalid_routes: int

    def summary_line(self) -> str:
        base = (
            f"shadow_mismatches={self.mismatched} "
            f"migrated_route_differences={self.migrated_route_differences} "
            f"matched={self.matched} missing_rules={self.missing_rules} "
            f"missing_env={self.missing_env} disabled={self.disabled}"
        )
        if self.sheet_missing:
            return f"[telegram_routes][shadow] sheet_missing=1 {base}"
        return f"[telegram_routes][shadow] {base}"


def compare_telegram_routes_env_vs_rules(
    snapshot: RulesSnapshotV2,
    *,
    sheet_present: bool | None = None,
) -> TelegramRoutesShadowResult:
    """Compare legacy ENV destinations to ``telegram_routes`` (read-only)."""

    routes = snapshot.telegram_routes
    sheet_missing = not routes and (sheet_present is False or sheet_present is None)
    if sheet_present is False:
        sheet_missing = True
    elif sheet_present is True:
        sheet_missing = False
    elif routes:
        sheet_missing = False

    invalid_routes = len(validate_telegram_routes_dict(routes)) if routes else 0

    matched = 0
    missing_rules = 0
    missing_env = 0
    mismatched = 0
    migrated_route_differences = 0
    disabled = 0

    indexes = RulesIndexes(telegram_routes_by_key=dict(snapshot.telegram_routes))
    accessor = BaseRulesAccessor(snapshot=snapshot, indexes=indexes)

    for env_name, route_key in LEGACY_ENV_TO_TELEGRAM_ROUTE_KEYS:
        env_chat = _read_legacy_env_chat(env_name)
        route = routes.get(route_key)
        rules_chat = accessor.get_telegram_chat_id(route_key)

        if route is not None and not route.enabled and env_chat:
            disabled += 1
            continue

        if env_chat and rules_chat is None:
            missing_rules += 1
            continue

        if rules_chat and env_chat is None:
            missing_env += 1
            continue

        if env_chat and rules_chat:
            if _normalize_chat_id_for_compare(env_chat) != _normalize_chat_id_for_compare(rules_chat):
                if _is_runtime_migrated_route(route_key):
                    migrated_route_differences += 1
                    log.debug(
                        "[telegram_routes][shadow] migrated_route_difference "
                        "route=%s env=%s env_chat=%s rules_chat=%s",
                        route_key,
                        env_name,
                        _mask_chat_id(env_chat),
                        _mask_chat_id(rules_chat),
                    )
                else:
                    mismatched += 1
                    log.debug(
                        "[telegram_routes][shadow] shadow_mismatch route=%s env=%s "
                        "env_chat=%s rules_chat=%s",
                        route_key,
                        env_name,
                        _mask_chat_id(env_chat),
                        _mask_chat_id(rules_chat),
                    )
            else:
                matched += 1

    if sheet_missing and not routes:
        log.info(
            "[telegram_routes][shadow] telegram_routes missing (sheet %s absent); "
            "ENV delivery unchanged",
            TELEGRAM_ROUTES_SHEET,
        )

    return TelegramRoutesShadowResult(
        sheet_missing=sheet_missing and not routes,
        matched=matched,
        missing_rules=missing_rules,
        missing_env=missing_env,
        mismatched=mismatched,
        migrated_route_differences=migrated_route_differences,
        disabled=disabled,
        invalid_routes=invalid_routes,
    )


def run_telegram_routes_shadow_compare(
    logger: logging.Logger | None = None,
    *,
    snapshot: RulesSnapshotV2 | None = None,
    sheet_present: bool | None = None,
    force: bool = False,
) -> TelegramRoutesShadowResult | None:
    """Log shadow summary; throttled unless ``force=True``."""

    global _last_shadow_run_ts, _last_shadow_summary

    lg = logger or log
    now = time.time()
    if not force and (now - _last_shadow_run_ts) < _SHADOW_MIN_INTERVAL_SEC and _last_shadow_summary:
        return _last_shadow_summary

    snap = snapshot
    present = sheet_present
    if snap is None:
        try:
            from core.rules_provider import get_snapshot_v2

            snap = get_snapshot_v2(force_sync=False)
            present = bool(snap.telegram_routes)
        except Exception:
            lg.debug("[telegram_routes][shadow] snapshot unavailable", exc_info=True)
            return None

    result = compare_telegram_routes_env_vs_rules(snap, sheet_present=present)
    lg.info(result.summary_line())
    if result.invalid_routes:
        lg.info("[telegram_routes][shadow] invalid_routes=%s", result.invalid_routes)

    _last_shadow_run_ts = now
    _last_shadow_summary = {
        "sheet_missing": result.sheet_missing,
        "matched": result.matched,
        "missing_rules": result.missing_rules,
        "missing_env": result.missing_env,
        "mismatched": result.mismatched,
        "migrated_route_differences": result.migrated_route_differences,
        "disabled": result.disabled,
        "invalid_routes": result.invalid_routes,
    }
    return result


def _status_source_label(route_key: str) -> str:
    resolution = resolve_route_chat_id(route_key)
    if resolution.source == "legacy_env":
        return "legacy_env"
    if resolution.source == "rules_v2":
        return "rules_v2"
    return "missing"


def get_telegram_routes_status_dict(
    snapshot: RulesSnapshotV2 | None = None,
) -> dict[str, Any]:
    """Read-only status block for ``/status``."""

    flag_on = routes_from_rules_v2_enabled()
    mode = "rules_runtime" if flag_on else "shadow_only"

    snap = snapshot
    if snap is None:
        try:
            from core.rules_provider import get_snapshot_v2

            snap = get_snapshot_v2(force_sync=False)
        except Exception:
            return {
                "mode": mode,
                "feature_flag": "1" if flag_on else "0",
                "migrated_routes": sorted(MIGRATED_RUNTIME_ROUTES),
                "source_for_platform_hourly_report": "unknown",
                "source_for_platform_wallet_download_report": "unknown",
                "source_for_conversion_wallet_editor": "unknown",
                "source_for_bakai_rate_current": "unknown",
                "source_for_bakai_rate_alert": "unknown",
                "loaded": "unknown",
                "missing_sheet": "unknown",
                "invalid_routes": "unknown",
                "shadow_mismatches": "unknown",
                "migrated_route_differences": "unknown",
                "emergency_env": TELEGRAM_CHAT_ID_EMERGENCY_ENV,
            }

    routes = snap.telegram_routes
    enabled = sum(1 for r in routes.values() if r.enabled)
    total = len(routes)
    invalid = len(validate_telegram_routes_dict(routes)) if routes else 0
    shadow = compare_telegram_routes_env_vs_rules(snap, sheet_present=bool(routes))
    mismatches = shadow.mismatched
    migrated_diffs = shadow.migrated_route_differences

    return {
        "mode": mode,
        "feature_flag": "1" if flag_on else "0",
        "migrated_routes": sorted(MIGRATED_RUNTIME_ROUTES),
        "source_for_platform_hourly_report": _status_source_label(
            ROUTE_PLATFORM_HOURLY_REPORT
        ),
        "source_for_platform_wallet_download_report": _status_source_label(
            ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT
        ),
        "source_for_conversion_wallet_editor": _status_source_label(
            ROUTE_CONVERSION_WALLET_EDITOR
        ),
        "source_for_bakai_rate_current": _status_source_label(ROUTE_BAKAI_RATE_CURRENT),
        "source_for_bakai_rate_alert": _status_source_label(ROUTE_BAKAI_RATE_ALERT),
        "loaded": f"{enabled}/{total} enabled/total",
        "missing_sheet": "yes" if not routes else "no",
        "invalid_routes": invalid,
        "shadow_mismatches": mismatches,
        "migrated_route_differences": migrated_diffs,
        "emergency_env": TELEGRAM_CHAT_ID_EMERGENCY_ENV,
    }


def format_telegram_routes_status_lines() -> list[str]:
    """Lines for ``/status`` — does not alter telegram_sender health block."""

    try:
        status = get_telegram_routes_status_dict()
    except Exception:
        return ["telegram_routes:", "- unknown"]

    migrated = status.get("migrated_routes") or []
    migrated_s = ", ".join(migrated) if migrated else "none"

    lines = [
        "telegram_routes:",
        f"- mode={status.get('mode', 'shadow_only')}",
        f"- feature_flag={status.get('feature_flag', '0')}",
        f"- migrated_routes={migrated_s}",
        (
            "- source_for_platform_hourly_report="
            f"{status.get('source_for_platform_hourly_report', 'unknown')}"
        ),
        (
            "- source_for_platform_wallet_download_report="
            f"{status.get('source_for_platform_wallet_download_report', 'unknown')}"
        ),
        (
            "- source_for_conversion_wallet_editor="
            f"{status.get('source_for_conversion_wallet_editor', 'unknown')}"
        ),
        (
            "- source_for_bakai_rate_current="
            f"{status.get('source_for_bakai_rate_current', 'unknown')}"
        ),
        (
            "- source_for_bakai_rate_alert="
            f"{status.get('source_for_bakai_rate_alert', 'unknown')}"
        ),
        f"- loaded={status.get('loaded', 'unknown')}",
        f"- missing_sheet={status.get('missing_sheet', 'unknown')}",
        f"- invalid_routes={status.get('invalid_routes', 'unknown')}",
        f"- shadow_mismatches={status.get('shadow_mismatches', 'unknown')}",
        (
            "- migrated_route_differences="
            f"{status.get('migrated_route_differences', 'unknown')}"
        ),
    ]
    return lines


def emergency_must_not_fallback_business_route(route_key: str) -> bool:
    """Phase 2 guard: business routes must not resolve via emergency ENV."""

    _ = _read_legacy_env_chat(TELEGRAM_CHAT_ID_EMERGENCY_ENV)
    return route_key in ALLOWED_TELEGRAM_ROUTE_KEYS


__all__ = [
    "ENV_TELEGRAM_ROUTES_FROM_RULES_V2",
    "ROUTE_PLATFORM_HOURLY_REPORT",
    "ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT",
    "ROUTE_CONVERSION_WALLET_EDITOR",
    "ROUTE_BAKAI_RATE_CURRENT",
    "ROUTE_BAKAI_RATE_ALERT",
    "ENV_PLATFORM_WALLET_LEGACY",
    "ENV_CONVERSION_WALLET_EDITOR_LEGACY",
    "ENV_BAKAI_RATE_CURRENT_LEGACY",
    "ENV_BAKAI_RATE_ALERT_LEGACY",
    "MIGRATED_RUNTIME_ROUTES",
    "RouteResolution",
    "RouteSource",
    "routes_from_rules_v2_enabled",
    "resolve_route_chat_id",
    "send_message_to_route",
    "send_file_to_route",
    "TelegramRoutesShadowResult",
    "compare_telegram_routes_env_vs_rules",
    "run_telegram_routes_shadow_compare",
    "get_telegram_routes_status_dict",
    "format_telegram_routes_status_lines",
    "TELEGRAM_CHAT_ID_EMERGENCY_ENV",
]
