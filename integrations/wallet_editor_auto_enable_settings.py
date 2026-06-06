"""Wallet Editor auto-enable job_params (Rules V2)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from core.rules_provider import get_indexes_v2, get_snapshot_v2
from core.rules_v2.accessors import BaseRulesAccessor

log = logging.getLogger(__name__)

JOB_KEY = "wallet_editor_auto_enable"

DEFAULT_ENABLED = False
DEFAULT_DRY_RUN = True
DEFAULT_APPROVAL_REQUIRED = True
DEFAULT_MAX_ROWS_PER_BATCH = 200
DEFAULT_MAX_ROWS_PER_RUN = 0
DEFAULT_SECONDS_PER_CARD_TIMEOUT = 10
DEFAULT_BATCH_TIMEOUT_BUFFER_SECONDS = 300
DEFAULT_INCLUDE_OVERDUE = True
DEFAULT_TELEGRAM_ROUTE_REPORT = "wallet_editor_auto_enable"
DEFAULT_TELEGRAM_ROUTE_ALERT = "wallet_editor_auto_enable_alert"
DEFAULT_WORKING_STATUSES = (
    "готов к работе",
    "активный вход",
    "активный выход",
)
DEFAULT_AUTO_RETURN_STATUSES: tuple[str, ...] = ()
DEFAULT_AUTO_RETURN_TARGET_STATUS = "Готов к работе"
DEFAULT_ALLOWED_STATUSES = DEFAULT_WORKING_STATUSES

PARAM_ENABLED = "enabled"
PARAM_DRY_RUN = "dry_run"
PARAM_APPROVAL_REQUIRED = "approval_required"
PARAM_MAX_ROWS_PER_BATCH = "max_rows_per_batch"
PARAM_MAX_ROWS_PER_RUN = "max_rows_per_run"
PARAM_SECONDS_PER_CARD_TIMEOUT = "seconds_per_card_timeout"
PARAM_BATCH_TIMEOUT_BUFFER_SECONDS = "batch_timeout_buffer_seconds"
PARAM_ALLOWED_STATUSES = "allowed_statuses_for_enable"
PARAM_WORKING_STATUSES = "working_statuses"
PARAM_AUTO_RETURN_STATUSES = "auto_return_statuses"
PARAM_AUTO_RETURN_TARGET_STATUS = "auto_return_target_status"
PARAM_INCLUDE_OVERDUE = "include_overdue"
PARAM_TELEGRAM_ROUTE_REPORT = "telegram_route_report"
PARAM_TELEGRAM_ROUTE_ALERT = "telegram_route_alert"


@dataclass(frozen=True, slots=True)
class AutoEnableSettings:
    enabled: bool
    dry_run: bool
    approval_required: bool
    max_rows_per_batch: int
    max_rows_per_run: int
    seconds_per_card_timeout: int
    batch_timeout_buffer_seconds: int
    working_statuses: tuple[str, ...]
    auto_return_statuses: tuple[str, ...]
    auto_return_target_status: str
    allowed_statuses_for_enable: tuple[str, ...]
    deprecated_working_statuses_fallback: bool
    include_overdue: bool
    telegram_route_report: str
    telegram_route_alert: str


def _parse_bool(value: object, *, default: bool, param_name: str) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(int(value))
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    log.warning(
        "[AutoEnable] invalid job_param %s=%r, using default=%s",
        param_name,
        value,
        default,
    )
    return default


def _parse_positive_int(value: object, *, default: int, param_name: str) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        log.warning(
            "[AutoEnable] invalid job_param %s=%r, using default=%s",
            param_name,
            value,
            default,
        )
        return default
    if parsed <= 0:
        log.warning(
            "[AutoEnable] invalid job_param %s=%s, using default=%s",
            param_name,
            parsed,
            default,
        )
        return default
    return parsed


def _parse_non_negative_int(value: object, *, default: int, param_name: str) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        log.warning(
            "[AutoEnable] invalid job_param %s=%r, using default=%s",
            param_name,
            value,
            default,
        )
        return default
    if parsed < 0:
        log.warning(
            "[AutoEnable] invalid job_param %s=%s, using default=%s",
            param_name,
            parsed,
            default,
        )
        return default
    return parsed


def _parse_csv_list(
    value: object,
    *,
    default: tuple[str, ...],
    param_name: str,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        items = [str(x).strip() for x in value if str(x).strip()]
        if not items:
            return () if allow_empty else default
        return tuple(items)
    text = str(value).strip()
    if not text:
        return () if allow_empty else default
    items = [part.strip() for part in text.split(",") if part.strip()]
    if not items:
        return () if allow_empty else default
    return tuple(items)


def _parse_status_text(value: object, *, default: str, param_name: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    return text


def _parse_route(value: object, *, default: str, param_name: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    return text


def _resolve_working_statuses(
    raw_working: object | None,
    raw_allowed: object | None,
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """
    Return (working_statuses, deprecated_allowed_raw, fallback_used).

    ``working_statuses`` uses ``working_statuses`` param when explicitly set;
    otherwise falls back to deprecated ``allowed_statuses_for_enable``.
    """
    deprecated_raw = _parse_csv_list(
        raw_allowed,
        default=DEFAULT_ALLOWED_STATUSES,
        param_name=PARAM_ALLOWED_STATUSES,
        allow_empty=True,
    )
    if raw_working is not None and str(raw_working).strip() != "":
        working = _parse_csv_list(
            raw_working,
            default=DEFAULT_WORKING_STATUSES,
            param_name=PARAM_WORKING_STATUSES,
            allow_empty=False,
        )
        return working, deprecated_raw, False

    if raw_allowed is not None and str(raw_allowed).strip() != "":
        return deprecated_raw, deprecated_raw, True

    return DEFAULT_WORKING_STATUSES, deprecated_raw, False


def load_auto_enable_settings(*, force_sync: bool = False) -> AutoEnableSettings:
    """Read wallet_editor_auto_enable job_params via Rules V2 snapshot accessor."""
    raw: dict[str, object | None] = {key: None for key in (
        PARAM_ENABLED,
        PARAM_DRY_RUN,
        PARAM_APPROVAL_REQUIRED,
        PARAM_MAX_ROWS_PER_BATCH,
        PARAM_MAX_ROWS_PER_RUN,
        PARAM_SECONDS_PER_CARD_TIMEOUT,
        PARAM_BATCH_TIMEOUT_BUFFER_SECONDS,
        PARAM_ALLOWED_STATUSES,
        PARAM_WORKING_STATUSES,
        PARAM_AUTO_RETURN_STATUSES,
        PARAM_AUTO_RETURN_TARGET_STATUS,
        PARAM_INCLUDE_OVERDUE,
        PARAM_TELEGRAM_ROUTE_REPORT,
        PARAM_TELEGRAM_ROUTE_ALERT,
    )}

    try:
        snapshot = get_snapshot_v2(force_sync=force_sync)
        indexes = get_indexes_v2(force_sync=force_sync)
        accessor = BaseRulesAccessor(snapshot=snapshot, indexes=indexes)
        for key in raw:
            raw[key] = accessor.get_job_param(JOB_KEY, key, default=None)
    except Exception:
        log.warning(
            "[AutoEnable] failed to load job_params for %s, using defaults",
            JOB_KEY,
            exc_info=True,
        )

    working_statuses, deprecated_raw, fallback_used = _resolve_working_statuses(
        raw[PARAM_WORKING_STATUSES],
        raw[PARAM_ALLOWED_STATUSES],
    )
    auto_return_statuses = _parse_csv_list(
        raw[PARAM_AUTO_RETURN_STATUSES],
        default=DEFAULT_AUTO_RETURN_STATUSES,
        param_name=PARAM_AUTO_RETURN_STATUSES,
        allow_empty=True,
    )

    return AutoEnableSettings(
        enabled=_parse_bool(raw[PARAM_ENABLED], default=DEFAULT_ENABLED, param_name=PARAM_ENABLED),
        dry_run=_parse_bool(raw[PARAM_DRY_RUN], default=DEFAULT_DRY_RUN, param_name=PARAM_DRY_RUN),
        approval_required=_parse_bool(
            raw[PARAM_APPROVAL_REQUIRED],
            default=DEFAULT_APPROVAL_REQUIRED,
            param_name=PARAM_APPROVAL_REQUIRED,
        ),
        max_rows_per_batch=_parse_positive_int(
            raw[PARAM_MAX_ROWS_PER_BATCH],
            default=DEFAULT_MAX_ROWS_PER_BATCH,
            param_name=PARAM_MAX_ROWS_PER_BATCH,
        ),
        max_rows_per_run=_parse_non_negative_int(
            raw[PARAM_MAX_ROWS_PER_RUN],
            default=DEFAULT_MAX_ROWS_PER_RUN,
            param_name=PARAM_MAX_ROWS_PER_RUN,
        ),
        seconds_per_card_timeout=_parse_non_negative_int(
            raw[PARAM_SECONDS_PER_CARD_TIMEOUT],
            default=DEFAULT_SECONDS_PER_CARD_TIMEOUT,
            param_name=PARAM_SECONDS_PER_CARD_TIMEOUT,
        ),
        batch_timeout_buffer_seconds=_parse_non_negative_int(
            raw[PARAM_BATCH_TIMEOUT_BUFFER_SECONDS],
            default=DEFAULT_BATCH_TIMEOUT_BUFFER_SECONDS,
            param_name=PARAM_BATCH_TIMEOUT_BUFFER_SECONDS,
        ),
        working_statuses=working_statuses,
        auto_return_statuses=auto_return_statuses,
        auto_return_target_status=_parse_status_text(
            raw[PARAM_AUTO_RETURN_TARGET_STATUS],
            default=DEFAULT_AUTO_RETURN_TARGET_STATUS,
            param_name=PARAM_AUTO_RETURN_TARGET_STATUS,
        ),
        allowed_statuses_for_enable=deprecated_raw,
        deprecated_working_statuses_fallback=fallback_used,
        include_overdue=_parse_bool(
            raw[PARAM_INCLUDE_OVERDUE],
            default=DEFAULT_INCLUDE_OVERDUE,
            param_name=PARAM_INCLUDE_OVERDUE,
        ),
        telegram_route_report=_parse_route(
            raw[PARAM_TELEGRAM_ROUTE_REPORT],
            default=DEFAULT_TELEGRAM_ROUTE_REPORT,
            param_name=PARAM_TELEGRAM_ROUTE_REPORT,
        ),
        telegram_route_alert=_parse_route(
            raw[PARAM_TELEGRAM_ROUTE_ALERT],
            default=DEFAULT_TELEGRAM_ROUTE_ALERT,
            param_name=PARAM_TELEGRAM_ROUTE_ALERT,
        ),
    )
