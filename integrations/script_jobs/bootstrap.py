"""Explicit mixed registration of both script jobs. Import does not register."""

from __future__ import annotations

from integrations.script_jobs.bind import register_script_job

MIXED_SCRIPT_KEYS: tuple[str, ...] = (
    "hello_world",
    "operator_wallets_ready",
)


def register_all_script_jobs() -> None:
    """Register both mixed script jobs onto ``core.job_runner.JOB_REGISTRY``."""
    for script_key in MIXED_SCRIPT_KEYS:
        register_script_job(script_key)
