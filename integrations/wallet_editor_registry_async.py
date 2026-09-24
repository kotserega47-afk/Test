"""Async scheduling, durable result copies, and outbox for Wallet Editor registry."""

from __future__ import annotations

import asyncio
import enum
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

_daemon_ops_lock = threading.Lock()
_daemon_ops_frozen = False
_daemon_ops_wait_done = False
_daemon_ops_joined: tuple[str, ...] = ()
_daemon_ops: dict[str, "_RegistryDaemonOp"] = {}


class RegistryDaemonLifecycle(enum.Enum):
    REGISTERED = "registered"
    STARTED = "started"
    TERMINAL = "terminal"


@dataclass
class _RegistryDaemonOp:
    run_id: str
    thread: threading.Thread
    state: RegistryDaemonLifecycle
    started: bool = False


@dataclass(frozen=True)
class RegistryDaemonRemainderEntry:
    run_id: str
    lifecycle: str
    thread_name: str
    started: bool
    alive: bool
    outbox_status: str | None


@dataclass(frozen=True)
class RegistryDaemonRemainder:
    frozen: bool
    entries: tuple[RegistryDaemonRemainderEntry, ...]
    reason: str


class IsolatedRegistryDaemonStopError(RuntimeError):
    """Isolated registry daemon wait refused or failed. Not a retry signal."""

    def __init__(self, remainder: RegistryDaemonRemainder) -> None:
        super().__init__(remainder.reason)
        self.remainder = remainder


class IsolatedRegistryDaemonCreateRejected(RuntimeError):
    """Daemon registry frozen: no new we-registry-* after the stop freeze."""


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


def _outbox_status_for(run_id: str) -> str | None:
    record = get_outbox_record(run_id)
    return record.status if record is not None else None


def _mark_daemon_terminal(run_id: str) -> None:
    with _daemon_ops_lock:
        op = _daemon_ops.get(run_id)
        if op is None:
            return
        op.state = RegistryDaemonLifecycle.TERMINAL
        if _daemon_ops_wait_done:
            _daemon_ops.pop(run_id, None)


def _continuation_states(admission) -> tuple[str, ...]:
    with admission._lock:
        return tuple(record.state for record in admission._ae_continuations.values())


def _we_profiles_unfinished() -> list[tuple[str, int]]:
    from automation import worker as worker_mod

    with worker_mod._registry_lock:
        items = list(worker_mod._profile_workers.items())
    return [
        (key, worker.queue.unfinished_tasks)
        for key, worker in items
        if worker.queue.unfinished_tasks
    ]


def snapshot_isolated_registry_daemon_ops() -> dict[str, _RegistryDaemonOp]:
    """Live daemon-ops copy. Observation during drain; not a stop."""

    with _daemon_ops_lock:
        return dict(_daemon_ops)


def collect_registry_daemon_remainder(*, reason: str) -> RegistryDaemonRemainder:
    """Build failure remainder: unfinished or abnormal ops only.

    Normal TERMINAL resource-terminal ops stay in accounting until reap but are
    not listed as failure remainder entries.

    Included:
    - REGISTERED (inconsistent during drain);
    - STARTED alive (in-flight);
    - STARTED dead without TERMINAL accounting (inconsistent).

    Excluded:
    - TERMINAL (normal resource-terminal).
    """

    with _daemon_ops_lock:
        frozen = _daemon_ops_frozen
        items = list(_daemon_ops.values())
    entries: list[RegistryDaemonRemainderEntry] = []
    for op in items:
        if op.state is RegistryDaemonLifecycle.TERMINAL:
            continue
        thread = op.thread
        entries.append(
            RegistryDaemonRemainderEntry(
                run_id=op.run_id,
                lifecycle=op.state.value,
                thread_name=thread.name if thread is not None else "",
                started=op.started,
                alive=bool(thread is not None and thread.is_alive()),
                outbox_status=_outbox_status_for(op.run_id),
            )
        )
    return RegistryDaemonRemainder(
        frozen=frozen,
        entries=tuple(entries),
        reason=reason,
    )


def _raise_daemon_stop(reason: str) -> None:
    raise IsolatedRegistryDaemonStopError(
        collect_registry_daemon_remainder(reason=reason)
    )


def _reset_registry_daemon_ops_for_tests() -> None:
    """Test-only: clear process-local daemon accounting. Not a production API."""

    global _daemon_ops_frozen, _daemon_ops_wait_done, _daemon_ops_joined
    with _daemon_ops_lock:
        _daemon_ops.clear()
        _daemon_ops_frozen = False
        _daemon_ops_wait_done = False
        _daemon_ops_joined = ()


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
    """Fire-and-forget daemon thread with process-local accounting (TASK-43)."""

    from integrations.wallet_editor_registry import append_run_to_dropbox_registry

    run_id = task.run_id

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
            try:
                if is_staged_copy and not from_durable_copy:
                    remove_staged_result(result_path, is_staged_copy=True)
            finally:
                _mark_daemon_terminal(run_id)

    thread = threading.Thread(
        target=_run,
        name=f"we-registry-{run_id}",
        daemon=True,
    )

    with _daemon_ops_lock:
        if _daemon_ops_frozen:
            raise IsolatedRegistryDaemonCreateRejected(
                f"isolated registry daemon create rejected after freeze: {run_id}"
            )
        existing = _daemon_ops.get(run_id)
        if existing is not None and existing.state is not RegistryDaemonLifecycle.TERMINAL:
            raise IsolatedRegistryDaemonCreateRejected(
                f"registry daemon operation already live: {run_id}"
            )
        if existing is not None:
            _daemon_ops.pop(run_id, None)

        op = _RegistryDaemonOp(
            run_id=run_id,
            thread=thread,
            state=RegistryDaemonLifecycle.REGISTERED,
            started=False,
        )
        _daemon_ops[run_id] = op
        try:
            thread.start()
        except BaseException:
            op.state = RegistryDaemonLifecycle.TERMINAL
            _daemon_ops.pop(run_id, None)
            raise
        # Child may have finished and set TERMINAL before we mark STARTED.
        if op.state is not RegistryDaemonLifecycle.TERMINAL:
            op.state = RegistryDaemonLifecycle.STARTED
            op.started = True

    log.debug(
        "[WalletEditorRegistry] scheduled async append run_id=%s path=%s durable=%s",
        run_id,
        result_path,
        from_durable_copy,
    )


async def wait_isolated_registry_daemon_ops(
    admission,
    *,
    producers_complete: bool,
    timeout: float | None = None,
) -> tuple[str, ...]:
    """Freeze daemon creation and join successfully started we-registry-* threads.

    Not a full graceful shutdown. Join is not business success. Durable outbox
    statuses are not rewritten. Does not stop mixed/unbound runtimes.
    """

    global _daemon_ops_frozen, _daemon_ops_wait_done, _daemon_ops_joined
    from modules.antares.work_admission import AdmissionState, bound_admission

    if bound_admission() is not admission:
        _raise_daemon_stop("mixed/unbound registry daemons are not stopped")
    if not producers_complete:
        _raise_daemon_stop("PTB producers are not complete")
    if admission.state is not AdmissionState.SEALED:
        _raise_daemon_stop("admission is not sealed")
    if admission.accepted_executor_futures():
        _raise_daemon_stop("accepted executor work remains")
    if _continuation_states(admission):
        _raise_daemon_stop("auto-enable continuation remains")

    unfinished = _we_profiles_unfinished()
    if unfinished:
        detail = ", ".join(f"{k} unfinished={n}" for k, n in unfinished)
        _raise_daemon_stop(f"WE profile work is not drained: {detail}")

    with _daemon_ops_lock:
        if _daemon_ops_wait_done:
            return _daemon_ops_joined
        _daemon_ops_frozen = True
        snapshot = list(_daemon_ops.values())

    for op in snapshot:
        if op.state is RegistryDaemonLifecycle.REGISTERED:
            _raise_daemon_stop(
                f"registry daemon accounting inconsistent registered={op.run_id}"
            )
        if op.started and op.thread is None:
            _raise_daemon_stop(
                f"registry daemon accounting inconsistent started_without_thread={op.run_id}"
            )

    loop = asyncio.get_running_loop()
    deadline = None if timeout is None else (loop.time() + timeout)
    joined: list[str] = []

    for op in snapshot:
        if not op.started:
            # TERMINAL without successful start should already be reaped (R15).
            # If still present, treat as inconsistent.
            if op.state is RegistryDaemonLifecycle.TERMINAL:
                continue
            _raise_daemon_stop(
                f"registry daemon accounting inconsistent not_started={op.run_id}"
            )
        thread = op.thread
        while thread.is_alive():
            if deadline is not None and loop.time() >= deadline:
                _raise_daemon_stop(
                    f"registry daemon {op.run_id} join deadline exceeded"
                )
            slice_timeout = 0.05
            if deadline is not None:
                slice_timeout = min(slice_timeout, max(0.0, deadline - loop.time()))
            await asyncio.to_thread(thread.join, slice_timeout)
        joined.append(op.run_id)

    with _daemon_ops_lock:
        for op in snapshot:
            if op.started:
                op.state = RegistryDaemonLifecycle.TERMINAL
        for run_id, op in list(_daemon_ops.items()):
            if op.state is RegistryDaemonLifecycle.TERMINAL:
                _daemon_ops.pop(run_id, None)
        _daemon_ops_wait_done = True
        _daemon_ops_joined = tuple(sorted(joined))
        return _daemon_ops_joined
