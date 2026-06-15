"""Legacy job_params whitelist — WalletEditor rows must not break hourly gate."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from core.config_manager import (
    build_job_params_overrides,
    get_job_params,
    validate_job_params,
)
from scheduler import HourlyGate, evaluate_hourly_gate

MSK = ZoneInfo("Europe/Moscow")


def _job_param_row(
    row_id: str,
    *,
    job: str,
    key: str,
    value_type: str,
    value,
    scope: str = "job",
    scope_value: str | None = None,
    enabled: int = 1,
) -> dict:
    return {
        "id": row_id,
        "enabled": enabled,
        "job": job,
        "scope": scope,
        "scope_value": scope_value if scope_value is not None else job,
        "key": key,
        "value_type": value_type,
        "value": value,
    }


def _prod_like_job_params_df() -> pd.DataFrame:
    """Hourly + wallet jobs plus WalletEditor rows that previously broke the whole sheet."""
    rows = [
        _job_param_row("JP-00001", job="hourly", key="intraday_interval_minutes", value_type="int", value=5),
        _job_param_row("JP-00002", job="hourly", key="final_daily_time", value_type="str", value="02:00"),
        _job_param_row("JP-00003", job="wallet", key="window_minutes", value_type="int", value=30),
        _job_param_row("JP-00004", job="raccoon_wallet", key="window_minutes", value_type="int", value=10),
        _job_param_row(
            "JP-00005",
            job="wallet_editor",
            key="registry_warning_seconds",
            value_type="int",
            value=60,
        ),
        _job_param_row(
            "JP-00006",
            job="wallet_editor",
            key="registry_timeout_seconds",
            value_type="int",
            value=180,
        ),
        _job_param_row(
            "JP-00007",
            job="wallet_editor",
            key="registry_retry_interval_seconds",
            value_type="int",
            value=30,
        ),
        _job_param_row("JP-00008", job="wallet_editor_auto_enable", key="enabled", value_type="bool", value=1),
        _job_param_row("JP-00009", job="wallet_editor_auto_enable", key="dry_run", value_type="bool", value=0),
        _job_param_row(
            "JP-00010",
            job="wallet_editor_auto_enable",
            key="max_rows_per_run",
            value_type="int",
            value=50,
        ),
    ]
    return pd.DataFrame(rows)


def _resolved_job_params(overrides: dict, job: str) -> dict:
    job_l = job.strip().lower()
    result: dict = {}
    result.update(overrides.get(job_l, {}).get("global", {}).get("__global__", {}))
    result.update(overrides.get(job_l, {}).get("job", {}).get(job_l, {}))
    return result


def test_mixed_job_params_sheet_passes_validation_with_wallet_editor_rows() -> None:
    res = validate_job_params(_prod_like_job_params_df())
    assert res.ok is True
    assert res.errors == []

    overrides, errs = build_job_params_overrides(_prod_like_job_params_df())
    assert errs == []
    assert "hourly" in overrides
    assert "wallet_editor" in overrides
    assert "wallet_editor_auto_enable" in overrides


def test_get_job_params_hourly_returns_gate_config_when_wallet_editor_rows_present() -> None:
    overrides, _ = build_job_params_overrides(_prod_like_job_params_df())
    hourly = _resolved_job_params(overrides, "hourly")

    with patch("core.config_manager.get_job_params_overrides", return_value=overrides):
        assert get_job_params(job="hourly") == hourly

    assert hourly["intraday_interval_minutes"] == 5
    assert hourly["final_daily_time"] == "02:00"


def test_truly_unknown_job_still_fails_validation() -> None:
    df = _prod_like_job_params_df()
    df = pd.concat(
        [
            df,
            pd.DataFrame(
                [
                    _job_param_row(
                        "JP-99999",
                        job="truly_unknown_job",
                        key="foo",
                        value_type="str",
                        value="bar",
                    )
                ]
            ),
        ],
        ignore_index=True,
    )

    res = validate_job_params(df)
    assert res.ok is False
    assert any("unknown job 'truly_unknown_job'" in e for e in res.errors)

    overrides, errs = build_job_params_overrides(df)
    assert overrides == {}
    assert errs


def test_hourly_gate_no_config_skip_on_valid_sheet_with_wallet_editor_rows() -> None:
    overrides, _ = build_job_params_overrides(_prod_like_job_params_df())
    hourly_params = _resolved_job_params(overrides, "hourly")
    gate = HourlyGate()
    now = datetime(2026, 6, 11, 10, 5, tzinfo=MSK)

    with patch("scheduler.get_job_params", return_value=hourly_params):
        fired, reason = evaluate_hourly_gate(now, gate)

    assert fired is True
    assert "no_gate_config" not in reason
    assert "intraday_interval_minutes=5" in reason
