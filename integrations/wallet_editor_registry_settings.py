"""Wallet Editor Dropbox registry timeout settings from Rules V2 job_params."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from core.rules_provider import get_indexes_v2, get_snapshot_v2
from core.rules_v2.accessors import BaseRulesAccessor

log = logging.getLogger(__name__)

JOB_KEY = "wallet_editor"

DEFAULT_REGISTRY_WARNING_SECONDS = 60
DEFAULT_REGISTRY_TIMEOUT_SECONDS = 180
DEFAULT_REGISTRY_RETRY_INTERVAL_SECONDS = 10

PARAM_WARNING = "registry_warning_seconds"
PARAM_TIMEOUT = "registry_timeout_seconds"
PARAM_RETRY = "registry_retry_interval_seconds"


@dataclass(frozen=True, slots=True)
class RegistrySettings:
    registry_warning_seconds: int
    registry_timeout_seconds: int
    registry_retry_interval_seconds: int


def _parse_positive_int(value: object, *, default: int, param_name: str) -> int:
    if value is None or value == "":
        return default
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        log.warning(
            "[WalletEditorRegistry] invalid job_param %s=%r, using default=%s",
            param_name,
            value,
            default,
        )
        return default
    if parsed <= 0:
        log.warning(
            "[WalletEditorRegistry] invalid job_param %s=%s, using default=%s",
            param_name,
            parsed,
            default,
        )
        return default
    return parsed


def _normalize_settings(
    warning_seconds: int,
    timeout_seconds: int,
    retry_interval_seconds: int,
) -> RegistrySettings:
    if timeout_seconds < 10:
        log.warning(
            "[WalletEditorRegistry] registry_timeout_seconds too small (%s), using %s",
            timeout_seconds,
            DEFAULT_REGISTRY_TIMEOUT_SECONDS,
        )
        timeout_seconds = DEFAULT_REGISTRY_TIMEOUT_SECONDS

    if warning_seconds >= timeout_seconds:
        adjusted = min(DEFAULT_REGISTRY_WARNING_SECONDS, timeout_seconds - 1)
        if adjusted < 1:
            adjusted = max(1, timeout_seconds // 2)
        log.warning(
            "[WalletEditorRegistry] registry_warning_seconds (%s) >= timeout (%s), "
            "adjusted warning to %s",
            warning_seconds,
            timeout_seconds,
            adjusted,
        )
        warning_seconds = adjusted

    return RegistrySettings(
        registry_warning_seconds=warning_seconds,
        registry_timeout_seconds=timeout_seconds,
        registry_retry_interval_seconds=retry_interval_seconds,
    )


def load_registry_settings(*, force_sync: bool = False) -> RegistrySettings:
    """
    Read wallet_editor job_params via Rules V2 snapshot accessor.
    Falls back to defaults on missing/invalid values.
    """
    warning_raw = None
    timeout_raw = None
    retry_raw = None
    used_defaults = False

    try:
        snapshot = get_snapshot_v2(force_sync=force_sync)
        indexes = get_indexes_v2(force_sync=force_sync)
        accessor = BaseRulesAccessor(snapshot=snapshot, indexes=indexes)
        warning_raw = accessor.get_job_param(JOB_KEY, PARAM_WARNING, default=None)
        timeout_raw = accessor.get_job_param(JOB_KEY, PARAM_TIMEOUT, default=None)
        retry_raw = accessor.get_job_param(JOB_KEY, PARAM_RETRY, default=None)
    except Exception:
        log.warning(
            "[WalletEditorRegistry] failed to load job_params for %s, using defaults",
            JOB_KEY,
            exc_info=True,
        )
        used_defaults = True

    if warning_raw is None:
        used_defaults = True
    if timeout_raw is None:
        used_defaults = True
    if retry_raw is None:
        used_defaults = True

    warning_seconds = _parse_positive_int(
        warning_raw,
        default=DEFAULT_REGISTRY_WARNING_SECONDS,
        param_name=PARAM_WARNING,
    )
    timeout_seconds = _parse_positive_int(
        timeout_raw,
        default=DEFAULT_REGISTRY_TIMEOUT_SECONDS,
        param_name=PARAM_TIMEOUT,
    )
    retry_interval_seconds = _parse_positive_int(
        retry_raw,
        default=DEFAULT_REGISTRY_RETRY_INTERVAL_SECONDS,
        param_name=PARAM_RETRY,
    )

    if used_defaults:
        log.info(
            "[WalletEditorRegistry] using registry settings (defaults applied where missing): "
            "warning=%ss timeout=%ss retry=%ss",
            warning_seconds,
            timeout_seconds,
            retry_interval_seconds,
        )

    return _normalize_settings(warning_seconds, timeout_seconds, retry_interval_seconds)
