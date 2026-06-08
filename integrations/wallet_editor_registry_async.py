"""Async scheduling and result-file staging for Wallet Editor registry."""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from automation.audit import Stats
from automation.runtime import WalletEditorTask
from integrations.wallet_editor_registry import append_run_to_dropbox_registry
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)

_REGISTRY_RESULT_PREFIX = "we_registry_result_"


def stage_registry_result_copy(result_path: str) -> tuple[str, bool]:
    """
    Copy per-run result xlsx for async registry append.

    Returns (path, is_staged_copy). On copy failure returns original path.
    """
    try:
        fd, staged = tempfile.mkstemp(suffix=".xlsx", prefix=_REGISTRY_RESULT_PREFIX)
        os.close(fd)
        shutil.copy2(result_path, staged)
        log.debug("[WalletEditorRegistry] staged result copy %s", staged)
        return staged, True
    except Exception:
        log.warning(
            "[WalletEditorRegistry] failed to stage result copy, using original path",
            exc_info=True,
        )
        return result_path, False


def remove_staged_result(path: str, *, is_staged_copy: bool) -> None:
    if not is_staged_copy:
        return
    try:
        os.remove(path)
        log.debug("[WalletEditorRegistry] removed staged result %s", path)
    except Exception:
        log.warning("[WalletEditorRegistry] failed to remove staged result %s", path)


def schedule_registry_append(
    task: WalletEditorTask,
    result_path: str,
    stats: Stats,
    *,
    run_started_at: datetime,
    run_finished_at: datetime,
    is_staged_copy: bool = False,
    output_file: str | None = None,
) -> None:
    """Fire-and-forget daemon thread; never blocks caller."""

    def _run() -> None:
        try:
            append_run_to_dropbox_registry(
                task,
                result_path,
                stats,
                run_started_at=run_started_at,
                run_finished_at=run_finished_at,
                output_file=output_file,
            )
        finally:
            remove_staged_result(result_path, is_staged_copy=is_staged_copy)

    thread = threading.Thread(
        target=_run,
        name=f"we-registry-{task.run_id}",
        daemon=True,
    )
    thread.start()
    log.debug(
        "[WalletEditorRegistry] scheduled async append run_id=%s path=%s",
        task.run_id,
        result_path,
    )
