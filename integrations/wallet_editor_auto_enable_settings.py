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
DEFAULT_SECONDS_PER_CARD_TIMEOUT = 10
DEFAULT_BATCH_TIMEOUT_BUFFER_SECONDS = 300
DEFAULT_INCLUDE_OVERDUE = True
DEFAULT_TELEGRAM_ROUTE_REPORT = "wallet_editor_auto_enable"
DEFAULT_TELEGRAM_ROUTE_ALERT = "wallet_editor_auto_enable_alert"
DEFAULT_ALLOWED_STATUSES = (
    "готов к работе",
    "активный вход",
    "активный выход",
)

PARAM_ENABLED = "enabled"
PARAM_DRY_RUN = "dry_run"
PARAM_APPROVAL_REQUIRED = "approval_required"
PARAM_MAX_ROWS_PER_BATCH = "max_rows_per_batch"
PARAM_SECONDS_PER_CARD_TIMEOUT = "seconds_per_card_timeout"
PARAM_BATCH_TIMEOUT_BUFFER_SECONDS = "batch_timeout_buffer_seconds"
PARAM_ALLOWED_STATUSES = "allowed_statuses_for_enable"
PARAM_INCLUDE_OVERDUE = "include_overdue"
PARAM_TELEGRAM_ROUTE_REPORT = "telegram_route_report"
PARAM_TELEGRAM_ROUTE_ALERT = "telegram_route_alert"


@dataclass(frozen=True, slots=True)
class AutoEnableSettings:
    enabled: bool
    dry_run: bool
    approval_required: bool
    max_rows_per_batch: int
    seconds_per_card_timeout: int
    batch_timeout_buffer_seconds: int
    allowed_statuses_for_enable: tuple[str, ...]
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


def _parse_csv_statuses(value: object) -> tuple[str, ...]:
    if value is None or value == "":
        return DEFAULT_ALLOWED_STATUSES
    if isinstance(value, (list, tuple)):
        items = [str(x).strip() for x in value if str(x).strip()]
        return tuple(items) if items else DEFAULT_ALLOWED_STATUSES
    text = str(value).strip()
    if not text:
        return DEFAULT_ALLOWED_STATUSES
    items = [part.strip() for part in text.split(",") if part.strip()]
    return tuple(items) if items else DEFAULT_ALLOWED_STATUSES


def _parse_route(value: object, *, default: str, param_name: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    return text


def load_auto_enable_settings(*, force_sync: bool = False) -> AutoEnableSettings:
    """Read wallet_editor_auto_enable job_params via Rules V2 snapshot accessor."""
    raw: dict[str, object | None] = {key: None for key in (
        PARAM_ENABLED,
        PARAM_DRY_RUN,
        PARAM_APPROVAL_REQUIRED,
        PARAM_MAX_ROWS_PER_BATCH,
        PARAM_SECONDS_PER_CARD_TIMEOUT,
        PARAM_BATCH_TIMEOUT_BUFFER_SECONDS,
        PARAM_ALLOWED_STATUSES,
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
        allowed_statuses_for_enable=_parse_csv_statuses(raw[PARAM_ALLOWED_STATUSES]),
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
