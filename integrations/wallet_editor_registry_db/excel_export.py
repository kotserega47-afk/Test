"""Best-effort Excel export to Dropbox after Postgres commits."""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path

from core.event_log import append_event
from integrations.dropbox_watcher import download_file_with_rev, upload_file_if_rev
from integrations.wallet_editor_registry_db.config import registry_source_is_postgres
from integrations.wallet_editor_registry_db.excel_export_state import (
    record_excel_export_failure,
    record_excel_export_success,
)
from integrations.wallet_editor_registry_db.frames import load_registry_frames_from_postgres
from integrations.wallet_editor_registry_lifecycle import normalize_all_results
from integrations.wallet_editor_registry_xlsx import load_registry_frames, save_registry_workbook
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)


def export_registry_workbook_to_dropbox(dropbox_path: str) -> None:
    """Build xlsx from Postgres history + preserved hold/otlezka and upload."""
    all_results, runs = load_registry_frames_from_postgres()
    all_results = normalize_all_results(all_results)

    with tempfile.TemporaryDirectory(prefix="we_export_") as tmp:
        local_path = Path(tmp) / "wallet_editor.xlsx"
        status, download_rev = download_file_with_rev(dropbox_path, str(local_path))
        is_new_file = status == "not_found"
        if status == "error":
            raise RuntimeError(f"registry export download failed path={dropbox_path}")

        hold_exists = otlezka_exists = False
        if not is_new_file:
            _a, _r, _hold, _otlezka, hold_exists, otlezka_exists = load_registry_frames(
                local_path,
                status,
            )

        save_registry_workbook(
            local_path,
            all_results=all_results,
            runs=runs,
            hold_exists=hold_exists,
            otlezka_exists=otlezka_exists,
            is_new_file=is_new_file,
        )

        expected_rev = None if is_new_file else download_rev
        upload_status = upload_file_if_rev(str(local_path), dropbox_path, expected_rev)
        if upload_status != "uploaded":
            raise RuntimeError(f"registry export upload failed status={upload_status}")


def schedule_excel_export(*, operation: str, dropbox_path: str) -> None:
    """Fire-and-forget Excel export; failures do not affect Postgres commits."""
    if not registry_source_is_postgres():
        return
    if not dropbox_path:
        return

    def _run() -> None:
        try:
            export_registry_workbook_to_dropbox(dropbox_path)
            record_excel_export_success(operation=operation)
            log.info("[WalletEditorRegistryExport] success operation=%s path=%s", operation, dropbox_path)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            record_excel_export_failure(operation=operation, error=error)
            append_event(
                type="wallet_editor_registry_excel_export_failed",
                job_type="wallet_editor",
                payload={"operation": operation, "error": error, "path": dropbox_path},
            )
            log.exception(
                "[WalletEditorRegistryExport] failed operation=%s path=%s",
                operation,
                dropbox_path,
            )

    thread = threading.Thread(
        target=_run,
        name=f"we-registry-export-{operation}",
        daemon=True,
    )
    thread.start()
    log.debug(
        "[WalletEditorRegistryExport] scheduled operation=%s path=%s",
        operation,
        dropbox_path,
    )
