"""Async scheduling, durable result copies, and outbox for Wallet Editor registry."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from automation.audit import Stats
from automation.runtime import WalletEditorTask
from core.event_log import append_event
from integrations.wallet_editor_registry_lifecycle import (
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_PENDING,
    OUTBOX_STATUS_SYNCED,
    OUTBOX_STATUS_SYNCING,
    durable_result_path,
    outbox_index_path,
    wallet_editor_outbox_dir,
    wallet_editor_results_dir,
)
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)

_REGISTRY_RESULT_PREFIX = "we_registry_result_"
_outbox_lock = threading.Lock()


@dataclass
class OutboxRecord:
    run_id: str
    operator_profile: str
    source_file_name: str
    chat_id: int
    telegram_user_id: int
    created_at: str
    result_file_path: str
    output_file: str
    status: str
    last_error: str | None
    attempts: int
    last_attempt_at: str | None
    stats_input_rows: int
    stats_success_rows: int
    stats_failed_rows: int
    stats_skipped_rows: int
    run_started_at: str
    run_finished_at: str
    registry_upload_rev: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OutboxRecord:
        return cls(
            run_id=str(data["run_id"]),
            operator_profile=str(data.get("operator_profile", "")),
            source_file_name=str(data.get("source_file_name", "")),
            chat_id=int(data.get("chat_id", 0)),
            telegram_user_id=int(data.get("telegram_user_id", 0)),
            created_at=str(data.get("created_at", "")),
            result_file_path=str(data.get("result_file_path", "")),
            output_file=str(data.get("output_file", "")),
            status=str(data.get("status", OUTBOX_STATUS_PENDING)),
            last_error=data.get("last_error"),
            attempts=int(data.get("attempts", 0)),
            last_attempt_at=data.get("last_attempt_at"),
            stats_input_rows=int(data.get("stats_input_rows", 0)),
            stats_success_rows=int(data.get("stats_success_rows", 0)),
            stats_failed_rows=int(data.get("stats_failed_rows", 0)),
            stats_skipped_rows=int(data.get("stats_skipped_rows", 0)),
            run_started_at=str(data.get("run_started_at", "")),
            run_finished_at=str(data.get("run_finished_at", "")),
            registry_upload_rev=data.get("registry_upload_rev"),
        )


def _iso_now() -> str:
    from core.datetime_utils import ensure_aware_msk, now_msk

    return ensure_aware_msk(now_msk()).isoformat()


def _load_outbox_index_unlocked() -> dict[str, dict[str, Any]]:
    path = outbox_index_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        records = data.get("records", {})
        if isinstance(records, dict):
            return records
    except Exception:
        log.exception("[WalletEditorOutbox] failed to load index")
    return {}


def _save_outbox_index_unlocked(records: dict[str, dict[str, Any]]) -> None:
    path = outbox_index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    payload = {"records": records}
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load_outbox_records() -> list[OutboxRecord]:
    with _outbox_lock:
        raw = _load_outbox_index_unlocked()
    return [OutboxRecord.from_dict(v) for v in raw.values()]


def get_outbox_record(run_id: str) -> OutboxRecord | None:
    with _outbox_lock:
        raw = _load_outbox_index_unlocked().get(run_id)
    return OutboxRecord.from_dict(raw) if raw else None


def upsert_outbox_record(record: OutboxRecord) -> None:
    with _outbox_lock:
        records = _load_outbox_index_unlocked()
        records[record.run_id] = record.to_dict()
        _save_outbox_index_unlocked(records)


def persist_durable_result_copy(run_id: str, result_path: str) -> str:
    """Copy result xlsx to STATE_DIR/wallet_editor/results/{run_id}.xlsx."""
    dest = durable_result_path(run_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(result_path, dest)
    log.info("[WalletEditorOutbox] durable result copy run_id=%s path=%s", run_id, dest)
    return str(dest)


def create_outbox_record(
    task: WalletEditorTask,
    *,
    durable_path: str,
    stats: Stats,
    run_started_at: datetime,
    run_finished_at: datetime,
    output_file: str,
) -> OutboxRecord:
    from core.datetime_utils import ensure_aware_msk

    record = OutboxRecord(
        run_id=task.run_id,
        operator_profile=task.operator_profile,
        source_file_name=task.source_file_name,
        chat_id=task.chat_id,
        telegram_user_id=task.telegram_user_id,
        created_at=_iso_now(),
        result_file_path=durable_path,
        output_file=output_file,
        status=OUTBOX_STATUS_PENDING,
        last_error=None,
        attempts=0,
        last_attempt_at=None,
        stats_input_rows=stats.ok + stats.fail + stats.skip,
        stats_success_rows=stats.ok,
        stats_failed_rows=stats.fail,
        stats_skipped_rows=stats.skip,
        run_started_at=ensure_aware_msk(run_started_at).isoformat(),
        run_finished_at=ensure_aware_msk(run_finished_at).isoformat(),
    )
    upsert_outbox_record(record)
    append_event(
        type="wallet_editor_outbox_recorded",
        job_type="wallet_editor",
        job_id=task.run_id,
        payload={
            "run_id": task.run_id,
            "operator_profile": task.operator_profile,
            "result_file_path": durable_path,
            "status": record.status,
        },
    )
    return record


def update_outbox_status(
    run_id: str,
    *,
    status: str,
    last_error: str | None = None,
    registry_upload_rev: str | None = None,
    increment_attempt: bool = False,
) -> OutboxRecord | None:
    record = get_outbox_record(run_id)
    if record is None:
        return None
    attempts = record.attempts + (1 if increment_attempt else 0)
    updated = OutboxRecord(
        **{
            **record.to_dict(),
            "status": status,
            "last_error": last_error,
            "last_attempt_at": _iso_now() if increment_attempt or status != record.status else record.last_attempt_at,
            "attempts": attempts,
            "registry_upload_rev": registry_upload_rev or record.registry_upload_rev,
        }
    )
    upsert_outbox_record(updated)
    return updated


def stage_registry_result_copy(result_path: str) -> tuple[str, bool]:
    """
    Copy per-run result xlsx for async registry append (legacy /tmp staging).

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


def prepare_registry_outbox_and_schedule(
    task: WalletEditorTask,
    result_path: str,
    stats: Stats,
    *,
    run_started_at: datetime,
    run_finished_at: datetime,
    output_file: str,
) -> str:
    """
    Persist durable result copy, create outbox record, schedule Dropbox sync.

    Returns durable result path on STATE_DIR.
    """
    durable_path = persist_durable_result_copy(task.run_id, result_path)
    create_outbox_record(
        task,
        durable_path=durable_path,
        stats=stats,
        run_started_at=run_started_at,
        run_finished_at=run_finished_at,
        output_file=output_file,
    )
    schedule_registry_append(
        task,
        durable_path,
        stats,
        run_started_at=run_started_at,
        run_finished_at=run_finished_at,
        output_file=output_file,
        from_durable_copy=True,
    )
    return durable_path


def schedule_registry_append(
    task: WalletEditorTask,
    result_path: str,
    stats: Stats,
    *,
    run_started_at: datetime,
    run_finished_at: datetime,
    is_staged_copy: bool = False,
    output_file: str | None = None,
    from_durable_copy: bool = False,
) -> None:
    """Fire-and-forget daemon thread; never blocks caller."""
    from integrations.wallet_editor_registry import append_run_to_dropbox_registry

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
            if is_staged_copy and not from_durable_copy:
                remove_staged_result(result_path, is_staged_copy=True)

    thread = threading.Thread(
        target=_run,
        name=f"we-registry-{task.run_id}",
        daemon=True,
    )
    thread.start()
    log.debug(
        "[WalletEditorRegistry] scheduled async append run_id=%s path=%s durable=%s",
        task.run_id,
        result_path,
        from_durable_copy,
    )
