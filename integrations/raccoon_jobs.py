# integrations/raccoon_jobs.py
"""Raccoon job wrappers and JOB_REGISTRY bindings (Phase R2)."""
from __future__ import annotations

from analyzers.raccoon_daily_conversion import run_daily_conversion_report
from analyzers.raccoon_hourly_report import (
    run_conversion_monitor_from_payin,
    run_hourly_report as run_raccoon_hourly_report_fn,
)
from core.job_runner import JOB_REGISTRY
from integrations.raccoon_hourly_downloader import run_hourly_raccoon_cycle
from integrations.raccoon_wallet_downloader import run_raccoon_wallet_cycle

RACCOON_HOURLY_PAYIN_PATH = "/tmp/hourly_raccoon/payin.xlsx"


def run_raccoon_wallet_job() -> None:
    run_raccoon_wallet_cycle()


def run_raccoon_hourly_job() -> None:
    run_hourly_raccoon_cycle()
    run_conversion_monitor_from_payin()
    run_raccoon_hourly_report_fn()


def run_raccoon_daily_conversion_job() -> None:
    run_daily_conversion_report(RACCOON_HOURLY_PAYIN_PATH)


JOB_REGISTRY.update(
    {
        "raccoon_wallet": run_raccoon_wallet_job,
        "raccoon_hourly": run_raccoon_hourly_job,
        "raccoon_daily_conversion": run_raccoon_daily_conversion_job,
    }
)
