"""Antares JOB_REGISTRY bindings. Import does not register jobs."""

from __future__ import annotations

import time
from typing import Callable, MutableMapping

ANTARES_JOB_KEYS = frozenset(
    {
        "wallet",
        "hourly",
        "rate",
        "download",
        "wallet_editor_registry_refresh",
        "wallet_editor_registry_replay",
    }
)


def run_hourly_job() -> None:
    """
    Contract:
      - run_hourly_report() does: download + fp compare + DTO + render (NO TG, NO fp commit)
      - wrapper does: send + fp commit ONLY after successful send
      - skip/no-changes -> only event_log (handled inside hourly_report)
    """
    from analyzers.hourly_report import run_hourly_report
    from core.state_store import state_update
    from integrations.telegram_routes import (
        ROUTE_PLATFORM_HOURLY_REPORT,
        send_message_to_route,
    )

    res = run_hourly_report(job="hourly")
    if res.skipped_no_changes or not res.text:
        return

    if not send_message_to_route(ROUTE_PLATFORM_HOURLY_REPORT, res.text):
        return

    if res.fingerprint:
        state_update(
            "hourly",
            {
                "last_fingerprint": res.fingerprint,
                "last_sent_ts": int(time.time()),
            },
        )


def run_download_job() -> None:
    from integrations.downloader import run_download

    run_download()


def antares_job_executors() -> dict[str, Callable[..., object]]:
    """Real Antares job callables. Importing this does not run jobs."""
    from integrations.bakai_monitor_playwright import run_rate_monitor_safe
    from integrations.downloader_wallets import run_wallet_cycle
    from integrations.wallet_editor_registry import run_registry_outbox_replay_job
    from integrations.wallet_editor_registry_refresh import (
        run_wallet_editor_registry_refresh_job,
    )

    return {
        "wallet": run_wallet_cycle,
        "hourly": run_hourly_job,
        "rate": run_rate_monitor_safe,
        "download": run_download_job,
        "wallet_editor_registry_refresh": run_wallet_editor_registry_refresh_job,
        "wallet_editor_registry_replay": run_registry_outbox_replay_job,
    }


def register_jobs(registry: MutableMapping[str, Callable[..., object]]) -> None:
    """Bind Antares job_types onto an existing registry. Does not run jobs."""
    registry.update(antares_job_executors())
