"""script_job:hello_world job_params must not break legacy hourly get_job_params (S4)."""
from __future__ import annotations

from unittest.mock import patch

from core.config_manager import ALLOWED_JOB_PARAMS, get_job_params, validate_job_params
import pandas as pd


def _row(row_id: str, job: str, key: str, value_type: str, value) -> dict:
    return {
        "id": row_id,
        "enabled": 1,
        "job": job,
        "scope": "job",
        "scope_value": job,
        "key": key,
        "value_type": value_type,
        "value": value,
    }


def test_script_job_hello_world_in_legacy_whitelist():
    assert "script_job:hello_world" in ALLOWED_JOB_PARAMS
    assert "enabled" in ALLOWED_JOB_PARAMS["script_job:hello_world"]
    assert "telegram_route_report" in ALLOWED_JOB_PARAMS["script_job:hello_world"]


def test_hourly_params_still_resolve_when_script_job_rows_present():
    df = pd.DataFrame(
        [
            _row("JP-H1", "hourly", "intraday_interval_minutes", "int", 5),
            _row("JP-H2", "hourly", "final_daily_time", "str", "02:00"),
            _row("JP-S1", "script_job:hello_world", "enabled", "bool", True),
            _row("JP-S2", "script_job:hello_world", "telegram_route_report", "str", "platform_hourly_report"),
        ]
    )
    result = validate_job_params(df)
    assert result.ok, result.errors

    overrides = {
        "hourly": {"global": {"__global__": {}}, "job": {"hourly": {"intraday_interval_minutes": 5}}},
        "script_job:hello_world": {
            "global": {"__global__": {}},
            "job": {"script_job:hello_world": {"enabled": True}},
        },
    }
    with patch("core.config_manager.get_job_params_overrides", return_value=overrides):
        hourly = get_job_params(job="hourly")
    assert hourly.get("intraday_interval_minutes") == 5
