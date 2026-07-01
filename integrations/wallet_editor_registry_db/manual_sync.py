"""Manual workbook snapshot sync orchestration (rev → hash → PostgreSQL)."""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from integrations.dropbox_watcher import download_file_with_rev, get_dropbox_file_rev
from integrations.wallet_editor_registry import wallet_editor_dropbox_path
from integrations.wallet_editor_registry_db.config import manual_sync_enabled
from integrations.wallet_editor_registry_db.connection import connect
from integrations.wallet_editor_registry_db.manual_snapshot import (
    ManualSyncValidationError,
    compute_snapshot_hash,
    parse_manual_workbook,
)
from integrations.wallet_editor_registry_db.manual_store import (
    InMemoryManualSyncStore,
    ManualSyncStore,
    PostgresManualSyncStore,
    new_sync_id,
    utc_now_iso,
)
from integrations.wallet_editor_registry_db.manual_sync_state import (
    record_manual_sync_failure,
    save_manual_sync_meta,
)
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)


class ManualSyncDecision(str, Enum):
    SKIPPED_REV = "skipped_rev"
    SKIPPED_HASH = "skipped_hash"
    SKIPPED_DISABLED = "skipped_disabled"
    SYNCED = "synced"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RunSnapshotBinding:
    """I-MAN-10: snapshot fixed for a single WalletEditor run (wire in Phase 2)."""

    manual_sync_run_id: int
    manual_snapshot_hash: str
    manual_snapshot_synced_at: str


@dataclass(frozen=True, slots=True)
class ManualSyncResult:
    decision: ManualSyncDecision
    blocked: bool
    snapshot_hash: str | None
    source_rev: str | None
    sync_run_id: int | None
    synced_at: str | None
    warnings: tuple[str, ...] = ()
    error: str | None = None
    run_binding: RunSnapshotBinding | None = None


def _finish_failed_run(
    store: ManualSyncStore,
    run_id: int | None,
    *,
    source_path: str,
    source_rev: str | None,
    triggered_by: str,
    actor: str | None,
    error_code: str,
    error_message: str,
    started_at: str,
    content_hash: str | None = None,
) -> int:
    finished_at = utc_now_iso()
    if run_id is not None:
        store.finish_sync_run(
            run_id,
            status="failed",
            finished_at=finished_at,
            hold_rows_activated=0,
            hold_rows_deactivated=0,
            otlezka_rows_activated=0,
            otlezka_rows_deactivated=0,
            error_code=error_code,
            error_message=error_message,
        )
        return run_id
    return store.persist_sync_run(
        sync_id=new_sync_id(),
        status="failed",
        triggered_by=triggered_by,
        triggered_by_actor=actor,
        source_path=source_path,
        source_rev=source_rev,
        content_hash=content_hash,
        hold_rows_seen=0,
        hold_rows_activated=0,
        hold_rows_deactivated=0,
        otlezka_rows_seen=0,
        otlezka_rows_activated=0,
        otlezka_rows_deactivated=0,
        validation_warnings=json.dumps([]),
        error_code=error_code,
        error_message=error_message,
        started_at=started_at,
        finished_at=finished_at,
    )


def ensure_manual_snapshot_current(
    store: ManualSyncStore,
    *,
    dropbox_path: str | None = None,
    triggered_by: str = "manual",
    actor: str | None = None,
    download_fn: Callable[[str, str], tuple[str, str | None]] | None = None,
    rev_fn: Callable[[str], str | None] | None = None,
) -> ManualSyncResult:
    """
    Rev check → optional download → validate → hash → sync decision.

    Does not block callers when disabled; returns SKIPPED_DISABLED.
    """
    if not manual_sync_enabled():
        log.info("[ManualSync] skipped: WALLET_EDITOR_MANUAL_SYNC_ENABLED is off")
        return ManualSyncResult(
            decision=ManualSyncDecision.SKIPPED_DISABLED,
            blocked=False,
            snapshot_hash=None,
            source_rev=None,
            sync_run_id=None,
            synced_at=None,
        )

    path = dropbox_path or wallet_editor_dropbox_path()
    if not path:
        return ManualSyncResult(
            decision=ManualSyncDecision.FAILED,
            blocked=True,
            snapshot_hash=None,
            source_rev=None,
            sync_run_id=None,
            synced_at=None,
            error="DROPBOX_WALLET_EDITOR_PATH is not set",
        )

    _download = download_fn or download_file_with_rev
    _rev = rev_fn or get_dropbox_file_rev
    started_at = utc_now_iso()

    current_rev = _rev(path)
    if current_rev is None:
        run_id = _finish_failed_run(
            store,
            None,
            source_path=path,
            source_rev=None,
            triggered_by=triggered_by,
            actor=actor,
            error_code="rev_unavailable",
            error_message="cannot read Dropbox rev",
            started_at=started_at,
        )
        record_manual_sync_failure(store, sync_run_id=run_id)
        return ManualSyncResult(
            decision=ManualSyncDecision.FAILED,
            blocked=True,
            snapshot_hash=None,
            source_rev=None,
            sync_run_id=None,
            synced_at=None,
            error="cannot read Dropbox rev",
        )

    last_rev = store.get_meta("last_manual_source_rev")
    last_hash = store.get_meta("last_manual_snapshot_hash")
    last_status = store.get_meta("last_manual_sync_status")

    if (
        current_rev == last_rev
        and last_hash
        and last_status in {"success", "skipped_hash", "skipped_rev"}
    ):
        log.info("[ManualSync] skipped_rev path=%s rev=%s", path, current_rev)
        synced_at = store.get_meta("last_manual_sync_at")
        run_id_raw = store.get_meta("last_manual_sync_run_id")
        run_id = int(run_id_raw) if run_id_raw and run_id_raw.isdigit() else None
        binding = None
        if run_id and last_hash and synced_at:
            binding = RunSnapshotBinding(
                manual_sync_run_id=run_id,
                manual_snapshot_hash=last_hash,
                manual_snapshot_synced_at=synced_at,
            )
        return ManualSyncResult(
            decision=ManualSyncDecision.SKIPPED_REV,
            blocked=False,
            snapshot_hash=last_hash,
            source_rev=current_rev,
            sync_run_id=run_id,
            synced_at=synced_at,
            run_binding=binding,
        )

    try:
        with tempfile.TemporaryDirectory(prefix="we_manual_sync_") as tmp:
            local_path = str(Path(tmp) / "wallet_editor.xlsx")
            status, download_rev = _download(path, local_path)
            snapshot = parse_manual_workbook(Path(local_path), status)
            snapshot_hash = compute_snapshot_hash(snapshot)
    except ManualSyncValidationError as exc:
        run_id = _finish_failed_run(
            store,
            None,
            source_path=path,
            source_rev=current_rev,
            triggered_by=triggered_by,
            actor=actor,
            error_code="validation",
            error_message=str(exc),
            started_at=started_at,
        )
        record_manual_sync_failure(store, sync_run_id=run_id)
        return ManualSyncResult(
            decision=ManualSyncDecision.FAILED,
            blocked=True,
            snapshot_hash=None,
            source_rev=current_rev,
            sync_run_id=None,
            synced_at=None,
            error=str(exc),
        )
    except Exception as exc:
        log.exception("[ManualSync] parse failed path=%s", path)
        run_id = _finish_failed_run(
            store,
            None,
            source_path=path,
            source_rev=current_rev,
            triggered_by=triggered_by,
            actor=actor,
            error_code="parse_error",
            error_message=f"{type(exc).__name__}: {exc}",
            started_at=started_at,
        )
        record_manual_sync_failure(store, sync_run_id=run_id)
        return ManualSyncResult(
            decision=ManualSyncDecision.FAILED,
            blocked=True,
            snapshot_hash=None,
            source_rev=current_rev,
            sync_run_id=None,
            synced_at=None,
            error=str(exc),
        )

    effective_rev = download_rev or current_rev

    if last_hash and snapshot_hash == last_hash:
        sync_run_id = store.persist_sync_run(
            sync_id=new_sync_id(),
            status="skipped_hash",
            triggered_by=triggered_by,
            triggered_by_actor=actor,
            source_path=path,
            source_rev=effective_rev,
            content_hash=snapshot_hash,
            hold_rows_seen=len(snapshot.hold_rows),
            hold_rows_activated=0,
            hold_rows_deactivated=0,
            otlezka_rows_seen=len(snapshot.otlezka_rows),
            otlezka_rows_activated=0,
            otlezka_rows_deactivated=0,
            validation_warnings=json.dumps(list(snapshot.warnings), ensure_ascii=False),
            error_code=None,
            error_message=None,
            started_at=started_at,
            finished_at=utc_now_iso(),
        )
        save_manual_sync_meta(
            store,
            source_rev=effective_rev,
            snapshot_hash=snapshot_hash,
            sync_status="skipped_hash",
            sync_run_id=sync_run_id,
            active_hold=store.count_active_hold(),
            active_otlezka=store.count_active_otlezka(),
        )
        synced_at = datetime.now(timezone.utc).isoformat()
        binding = RunSnapshotBinding(
            manual_sync_run_id=sync_run_id,
            manual_snapshot_hash=snapshot_hash,
            manual_snapshot_synced_at=synced_at,
        )
        return ManualSyncResult(
            decision=ManualSyncDecision.SKIPPED_HASH,
            blocked=False,
            snapshot_hash=snapshot_hash,
            source_rev=effective_rev,
            sync_run_id=sync_run_id,
            synced_at=synced_at,
            warnings=snapshot.warnings,
            run_binding=binding,
        )

    sync_run_id: int | None = None
    try:
        sync_run_id = store.persist_sync_run(
            sync_id=new_sync_id(),
            status="in_progress",
            triggered_by=triggered_by,
            triggered_by_actor=actor,
            source_path=path,
            source_rev=effective_rev,
            content_hash=snapshot_hash,
            hold_rows_seen=len(snapshot.hold_rows),
            hold_rows_activated=0,
            hold_rows_deactivated=0,
            otlezka_rows_seen=len(snapshot.otlezka_rows),
            otlezka_rows_activated=0,
            otlezka_rows_deactivated=0,
            validation_warnings=json.dumps(list(snapshot.warnings), ensure_ascii=False),
            error_code=None,
            error_message=None,
            started_at=started_at,
            finished_at=None,
        )
        persist = store.apply_snapshot(snapshot, sync_run_id=sync_run_id)
        finished_at = utc_now_iso()
        store.finish_sync_run(
            sync_run_id,
            status="success",
            finished_at=finished_at,
            hold_rows_activated=persist.hold_activated,
            hold_rows_deactivated=persist.hold_deactivated,
            otlezka_rows_activated=persist.otlezka_activated,
            otlezka_rows_deactivated=persist.otlezka_deactivated,
            error_code=None,
            error_message=None,
        )
        save_manual_sync_meta(
            store,
            source_rev=effective_rev,
            snapshot_hash=snapshot_hash,
            sync_status="success",
            sync_run_id=sync_run_id,
            active_hold=store.count_active_hold(),
            active_otlezka=store.count_active_otlezka(),
        )
    except Exception as exc:
        log.exception("[ManualSync] PG sync failed path=%s", path)
        run_id = _finish_failed_run(
            store,
            sync_run_id,
            source_path=path,
            source_rev=effective_rev,
            triggered_by=triggered_by,
            actor=actor,
            error_code="db_error",
            error_message=f"{type(exc).__name__}: {exc}",
            started_at=started_at,
            content_hash=snapshot_hash,
        )
        record_manual_sync_failure(store, sync_run_id=run_id)
        return ManualSyncResult(
            decision=ManualSyncDecision.FAILED,
            blocked=True,
            snapshot_hash=snapshot_hash,
            source_rev=effective_rev,
            sync_run_id=None,
            synced_at=None,
            error=str(exc),
        )

    synced_at = datetime.now(timezone.utc).isoformat()
    binding = RunSnapshotBinding(
        manual_sync_run_id=sync_run_id,
        manual_snapshot_hash=snapshot_hash,
        manual_snapshot_synced_at=synced_at,
    )
    return ManualSyncResult(
        decision=ManualSyncDecision.SYNCED,
        blocked=False,
        snapshot_hash=snapshot_hash,
        source_rev=effective_rev,
        sync_run_id=sync_run_id,
        synced_at=synced_at,
        warnings=snapshot.warnings,
        run_binding=binding,
    )


def postgres_manual_sync_store(conn: Any) -> PostgresManualSyncStore:
    return PostgresManualSyncStore(conn)


def in_memory_manual_sync_store() -> InMemoryManualSyncStore:
    return InMemoryManualSyncStore()


def sync_manual_to_postgres(
    store: ManualSyncStore | None = None,
    *,
    conn: Any | None = None,
    dropbox_path: str | None = None,
    triggered_by: str = "manual",
    actor: str | None = None,
    download_fn: Callable[[str, str], tuple[str, str | None]] | None = None,
    rev_fn: Callable[[str], str | None] | None = None,
) -> ManualSyncResult:
    """Entry point for manual sync using an existing store or PostgreSQL connection."""
    if store is not None:
        return ensure_manual_snapshot_current(
            store,
            dropbox_path=dropbox_path,
            triggered_by=triggered_by,
            actor=actor,
            download_fn=download_fn,
            rev_fn=rev_fn,
        )
    if conn is not None:
        pg_store = PostgresManualSyncStore(conn)
        return ensure_manual_snapshot_current(
            pg_store,
            dropbox_path=dropbox_path,
            triggered_by=triggered_by,
            actor=actor,
            download_fn=download_fn,
            rev_fn=rev_fn,
        )
    with connect() as pg_conn:
        pg_store = PostgresManualSyncStore(pg_conn)
        return ensure_manual_snapshot_current(
            pg_store,
            dropbox_path=dropbox_path,
            triggered_by=triggered_by,
            actor=actor,
            download_fn=download_fn,
            rev_fn=rev_fn,
        )


GATE_FAILURE_MESSAGE_TEMPLATE = (
    "WalletEditor не запущен: не удалось синхронизировать hold/Отлёжка.\n\n"
    "Причина: {reason}\n\n"
    "Проверьте Dropbox-книгу и повторите запуск."
)


@dataclass(frozen=True, slots=True)
class ManualSyncPrerunResult:
    ok: bool
    binding: RunSnapshotBinding | None
    operator_message: str | None
    sync_result: ManualSyncResult | None = None
    bypassed: bool = False


def format_manual_sync_gate_failure_message(error: str | None) -> str:
    reason = (error or "неизвестная ошибка").strip()
    return GATE_FAILURE_MESSAGE_TEMPLATE.format(reason=reason)


def run_manual_sync_prerun_gate(
    *,
    triggered_by: str,
    actor: str | None = None,
    store: ManualSyncStore | None = None,
    download_fn: Callable[[str, str], tuple[str, str | None]] | None = None,
    rev_fn: Callable[[str], str | None] | None = None,
) -> ManualSyncPrerunResult:
    """
    Authoritative pre-run gate (I-MAN-10).

    Call at execution start (worker/job), not at Telegram enqueue.
    """
    if not manual_sync_enabled():
        log.info("[ManualSync] prerun gate bypassed: WALLET_EDITOR_MANUAL_SYNC_ENABLED is off")
        return ManualSyncPrerunResult(
            ok=True,
            binding=None,
            operator_message=None,
            bypassed=True,
        )

    sync_result = sync_manual_to_postgres(
        store=store,
        triggered_by=triggered_by,
        actor=actor,
        download_fn=download_fn,
        rev_fn=rev_fn,
    )
    if sync_result.blocked or sync_result.decision == ManualSyncDecision.FAILED:
        message = format_manual_sync_gate_failure_message(sync_result.error)
        log.error(
            "[ManualSync] prerun gate blocked triggered_by=%s error=%s",
            triggered_by,
            sync_result.error,
        )
        return ManualSyncPrerunResult(
            ok=False,
            binding=None,
            operator_message=message,
            sync_result=sync_result,
        )

    if sync_result.run_binding is None:
        message = format_manual_sync_gate_failure_message(
            "manual snapshot binding unavailable after sync"
        )
        log.error(
            "[ManualSync] prerun gate blocked triggered_by=%s reason=missing_binding",
            triggered_by,
        )
        return ManualSyncPrerunResult(
            ok=False,
            binding=None,
            operator_message=message,
            sync_result=sync_result,
        )

    log.info(
        "[ManualSync] prerun gate ok triggered_by=%s sync_run_id=%s hash=%s",
        triggered_by,
        sync_result.run_binding.manual_sync_run_id,
        sync_result.run_binding.manual_snapshot_hash[:12],
    )
    return ManualSyncPrerunResult(
        ok=True,
        binding=sync_result.run_binding,
        operator_message=None,
        sync_result=sync_result,
    )


def log_run_snapshot_binding(binding: RunSnapshotBinding | None, *, context: str) -> None:
    if binding is None:
        return
    log.info(
        "[ManualSync] binding context=%s sync_run_id=%s hash=%s synced_at=%s",
        context,
        binding.manual_sync_run_id,
        binding.manual_snapshot_hash[:12],
        binding.manual_snapshot_synced_at,
    )


def build_manual_snapshot_health_block() -> str:
    """Build manual snapshot section for /registry_health."""
    from integrations.dropbox_watcher import get_dropbox_file_rev
    from integrations.wallet_editor_registry import wallet_editor_dropbox_path
    from integrations.wallet_editor_registry_db.connection import DatabaseNotConfiguredError, connect
    from integrations.wallet_editor_registry_db.manual_sync_state import (
        build_manual_snapshot_health_report,
        format_manual_snapshot_health_report,
    )

    dropbox_path = wallet_editor_dropbox_path()
    current_rev = get_dropbox_file_rev(dropbox_path) if dropbox_path else None
    try:
        with connect() as conn:
            store = PostgresManualSyncStore(conn)
            report = build_manual_snapshot_health_report(
                store,
                current_dropbox_rev=current_rev,
            )
            return format_manual_snapshot_health_report(report)
    except DatabaseNotConfiguredError:
        return format_manual_snapshot_health_report(
            build_manual_snapshot_health_report(
                InMemoryManualSyncStore(),
                current_dropbox_rev=current_rev,
            ),
        )
