# core/job_dispatch.py
"""Shared job worker pool for Playwright jobs (Phase 3a / Variant B)."""
from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from core.job_runner import Actor, request_job

_DEFAULT_MAX_WORKERS = 2
_JOB_EXECUTOR: Optional[ThreadPoolExecutor] = None


def _dispatch_enabled() -> bool:
    return os.getenv("JOB_DISPATCH_VIA_EXECUTOR", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "n",
    }


def _max_workers() -> int:
    raw = os.getenv("JOB_EXECUTOR_MAX_WORKERS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return _DEFAULT_MAX_WORKERS


def get_job_executor() -> ThreadPoolExecutor:
    global _JOB_EXECUTOR
    if _JOB_EXECUTOR is None:
        _JOB_EXECUTOR = ThreadPoolExecutor(
            max_workers=_max_workers(),
            thread_name_prefix="job-worker",
        )
    return _JOB_EXECUTOR


def _reset_job_executor_for_tests() -> None:
    global _JOB_EXECUTOR
    if _JOB_EXECUTOR is not None:
        _JOB_EXECUTOR.shutdown(wait=False, cancel_futures=True)
    _JOB_EXECUTOR = None


def dispatch_job_sync(
    job_type: str,
    actor: Actor,
    *,
    force_rules_sync: bool = False,
) -> str:
    if not _dispatch_enabled():
        return request_job(job_type, actor, force_rules_sync=force_rules_sync)

    future = get_job_executor().submit(
        request_job,
        job_type,
        actor,
        force_rules_sync=force_rules_sync,
    )
    return future.result()


async def dispatch_job_async(
    job_type: str,
    actor: Actor,
    *,
    force_rules_sync: bool = False,
) -> str:
    if not _dispatch_enabled():
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: request_job(job_type, actor, force_rules_sync=force_rules_sync),
        )

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        get_job_executor(),
        lambda: request_job(job_type, actor, force_rules_sync=force_rules_sync),
    )
