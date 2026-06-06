from __future__ import annotations

from unittest.mock import patch

import pytest

from integrations.wallet_editor_auto_enable_settings import (
    DEFAULT_ALLOWED_STATUSES,
    DEFAULT_BATCH_TIMEOUT_BUFFER_SECONDS,
    DEFAULT_DRY_RUN,
    DEFAULT_ENABLED,
    DEFAULT_MAX_ROWS_PER_BATCH,
    DEFAULT_MAX_ROWS_PER_RUN,
    DEFAULT_SECONDS_PER_CARD_TIMEOUT,
    JOB_KEY,
    load_auto_enable_settings,
)


def _mock_accessor(params: dict[str, object | None]):
    class _Accessor:
        def get_job_param(self, job_key, param_key, default=None):
            assert job_key == JOB_KEY
            return params.get(param_key, default)

    return _Accessor()


def test_load_auto_enable_settings_defaults():
    with patch("integrations.wallet_editor_auto_enable_settings.get_snapshot_v2"):
        with patch("integrations.wallet_editor_auto_enable_settings.get_indexes_v2"):
            with patch(
                "integrations.wallet_editor_auto_enable_settings.BaseRulesAccessor",
                return_value=_mock_accessor({}),
            ):
                settings = load_auto_enable_settings()

    assert settings.enabled is DEFAULT_ENABLED
    assert settings.dry_run is DEFAULT_DRY_RUN
    assert settings.approval_required is True
    assert settings.max_rows_per_batch == DEFAULT_MAX_ROWS_PER_BATCH
    assert settings.max_rows_per_run == DEFAULT_MAX_ROWS_PER_RUN
    assert settings.seconds_per_card_timeout == DEFAULT_SECONDS_PER_CARD_TIMEOUT
    assert settings.batch_timeout_buffer_seconds == DEFAULT_BATCH_TIMEOUT_BUFFER_SECONDS
    assert settings.allowed_statuses_for_enable == DEFAULT_ALLOWED_STATUSES
    assert settings.include_overdue is True
    assert settings.telegram_route_report == "wallet_editor_auto_enable"
    assert settings.telegram_route_alert == "wallet_editor_auto_enable_alert"


def test_load_auto_enable_settings_bool_and_int_parsing():
    params = {
        "enabled": "1",
        "dry_run": "false",
        "approval_required": "yes",
        "max_rows_per_batch": "150",
        "max_rows_per_run": "25",
        "seconds_per_card_timeout": "12",
        "batch_timeout_buffer_seconds": "60",
        "include_overdue": "0",
    }
    with patch("integrations.wallet_editor_auto_enable_settings.get_snapshot_v2"):
        with patch("integrations.wallet_editor_auto_enable_settings.get_indexes_v2"):
            with patch(
                "integrations.wallet_editor_auto_enable_settings.BaseRulesAccessor",
                return_value=_mock_accessor(params),
            ):
                settings = load_auto_enable_settings()

    assert settings.enabled is True
    assert settings.dry_run is False
    assert settings.approval_required is True
    assert settings.max_rows_per_batch == 150
    assert settings.max_rows_per_run == 25
    assert settings.seconds_per_card_timeout == 12
    assert settings.batch_timeout_buffer_seconds == 60
    assert settings.include_overdue is False


def test_load_auto_enable_settings_csv_statuses():
    params = {
        "allowed_statuses_for_enable": "Готов к работе, Активный выход",
    }
    with patch("integrations.wallet_editor_auto_enable_settings.get_snapshot_v2"):
        with patch("integrations.wallet_editor_auto_enable_settings.get_indexes_v2"):
            with patch(
                "integrations.wallet_editor_auto_enable_settings.BaseRulesAccessor",
                return_value=_mock_accessor(params),
            ):
                settings = load_auto_enable_settings()

    assert settings.allowed_statuses_for_enable == ("Готов к работе", "Активный выход")


@pytest.mark.parametrize(
    "bad_value,field,expected",
    [
        ("abc", "max_rows_per_batch", DEFAULT_MAX_ROWS_PER_BATCH),
        ("abc", "max_rows_per_run", DEFAULT_MAX_ROWS_PER_RUN),
        ("-5", "seconds_per_card_timeout", DEFAULT_SECONDS_PER_CARD_TIMEOUT),
        ("-3", "max_rows_per_run", DEFAULT_MAX_ROWS_PER_RUN),
        ("maybe", "enabled", DEFAULT_ENABLED),
    ],
)
def test_load_auto_enable_settings_invalid_values_fallback(bad_value, field, expected):
    with patch("integrations.wallet_editor_auto_enable_settings.get_snapshot_v2"):
        with patch("integrations.wallet_editor_auto_enable_settings.get_indexes_v2"):
            with patch(
                "integrations.wallet_editor_auto_enable_settings.BaseRulesAccessor",
                return_value=_mock_accessor({field: bad_value}),
            ):
                settings = load_auto_enable_settings()

    assert getattr(settings, field) == expected
