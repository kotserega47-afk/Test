"""PG-backed registry export workbook builder (delivery-agnostic)."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from integrations.wallet_editor_registry_db.config import registry_source_is_postgres
from integrations.wallet_editor_registry_db.connection import DatabaseNotConfiguredError, connect
from integrations.wallet_editor_registry_db.frames import load_registry_frames_from_postgres
from integrations.wallet_editor_registry_db.manual_readers import load_hold_otlezka_frames_for_export
from integrations.wallet_editor_registry_db.manual_store import (
    ManualSyncStore,
    PostgresManualSyncStore,
)
from integrations.wallet_editor_registry_db.manual_sync_state import load_manual_sync_meta
from integrations.wallet_editor_registry_lifecycle import normalize_all_results
from integrations.wallet_editor_registry_xlsx import save_registry_export_workbook
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)

MSK = ZoneInfo("Europe/Moscow")
EXPORT_FILENAME_PREFIX = "wallet_editor_export_"


@dataclass(frozen=True, slots=True)
class RegistryExportSummary:
    all_results_rows: int
    runs_rows: int
    hold_rows: int
    otlezka_rows: int
    last_manual_sync_at: str | None
    snapshot_hash_short: str | None
    manual_sync_degraded: bool
    generated_at: str
    filename: str


@dataclass(frozen=True, slots=True)
class RegistryExportArtifact:
    path: Path
    filename: str
    summary: RegistryExportSummary


def format_registry_export_summary(summary: RegistryExportSummary) -> str:
    lines = [
        "WalletEditor registry export",
        "",
        f"filename: {summary.filename}",
        f"generated at: {summary.generated_at}",
        f"all_results rows: {summary.all_results_rows}",
        f"runs rows: {summary.runs_rows}",
        f"active hold rows: {summary.hold_rows}",
        f"active Отлёжка rows: {summary.otlezka_rows}",
        f"last manual sync: {summary.last_manual_sync_at or 'none'}",
        f"snapshot hash: {summary.snapshot_hash_short or 'n/a'}",
    ]
    if summary.manual_sync_degraded:
        lines.append("")
        lines.append("⚠️ DEGRADED: no successful manual sync recorded")
    lines.append("")
    lines.append("Read-only export. Do not edit this workbook.")
    lines.append("Use /registry_export in Telegram to obtain the registry.")
    return "\n".join(lines)


def _export_filename_msk(now: datetime | None = None) -> str:
    ts = now or datetime.now(MSK)
    return f"{EXPORT_FILENAME_PREFIX}{ts.strftime('%Y%m%d_%H%M%S')}_MSK.xlsx"


def _hash_short(snapshot_hash: str | None) -> str | None:
    if not snapshot_hash:
        return None
    trimmed = snapshot_hash.strip()
    return trimmed[:12] if trimmed else None


def _readme_lines(
    *,
    generated_at: str,
    snapshot_hash_short: str | None,
    last_manual_sync_at: str | None,
) -> list[str]:
    return [
        "WalletEditor Registry Export",
        "",
        "This workbook is a generated, read-only report.",
        "Do not edit this workbook.",
        "",
        "Source of truth:",
        "- Registry history (all_results, runs): PostgreSQL",
        "- Hold and Отлёжка runtime: PostgreSQL (after manual sync)",
        "",
        "The Dropbox operator workbook is used only for manual input",
        "(hold and Отлёжка sheets). Changes there require /manual_sync.",
        "",
        "To obtain the registry, use /registry_export in Telegram.",
        "",
        f"Generated at: {generated_at}",
        f"Manual snapshot hash: {snapshot_hash_short or 'n/a'}",
        f"Last manual sync: {last_manual_sync_at or 'none'}",
    ]


def _sync_status_rows(meta) -> list[tuple[str, str]]:
    return [
        ("last_manual_sync_at", meta.last_sync_at or ""),
        ("last_manual_snapshot_hash", meta.last_snapshot_hash or ""),
        ("last_manual_sync_status", meta.last_sync_status or ""),
        ("last_manual_source_rev", meta.last_source_rev or ""),
        ("active_hold_rows", str(meta.active_hold_rows)),
        ("active_otlezka_rows", str(meta.active_otlezka_rows)),
        ("last_manual_sync_run_id", meta.last_sync_run_id or ""),
    ]


class RegistryExportBuilder:
    """Build a registry export workbook from PostgreSQL (no delivery side effects)."""

    def __init__(self, *, store: ManualSyncStore | None = None) -> None:
        self._store = store

    def load(
        self,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, object, bool]:
        if not registry_source_is_postgres():
            raise RuntimeError("registry export requires WALLET_EDITOR_REGISTRY_SOURCE=postgres")

        all_results, runs = load_registry_frames_from_postgres()
        all_results = normalize_all_results(all_results)

        if self._store is not None:
            hold_df, otlezka_df, degraded = load_hold_otlezka_frames_for_export(
                store=self._store,
            )
            meta = load_manual_sync_meta(self._store)
            return all_results, runs, hold_df, otlezka_df, meta, degraded

        with connect(for_mirror=False) as conn:
            store = PostgresManualSyncStore(conn)
            hold_df, otlezka_df, degraded = load_hold_otlezka_frames_for_export(store=store)
            meta = load_manual_sync_meta(store)

        return all_results, runs, hold_df, otlezka_df, meta, degraded

    def build(
        self,
        *,
        output_path: Path | None = None,
        generated_at: datetime | None = None,
    ) -> RegistryExportArtifact:
        generated = generated_at or datetime.now(MSK)
        generated_at_text = generated.isoformat()

        all_results, runs, hold_df, otlezka_df, meta, degraded = self.load()

        filename = _export_filename_msk(generated)
        hash_short = _hash_short(meta.last_snapshot_hash)

        if output_path is None:
            tmp_dir = Path(tempfile.mkdtemp(prefix="we_registry_export_"))
            output_path = tmp_dir / filename
        else:
            output_path = Path(output_path)
            if output_path.is_dir():
                output_path = output_path / filename

        readme_lines = _readme_lines(
            generated_at=generated_at_text,
            snapshot_hash_short=hash_short,
            last_manual_sync_at=meta.last_sync_at,
        )

        save_registry_export_workbook(
            output_path,
            all_results=all_results,
            runs=runs,
            hold=hold_df,
            otlezka=otlezka_df,
            readme_lines=readme_lines,
            sync_status_rows=_sync_status_rows(meta),
        )

        summary = RegistryExportSummary(
            all_results_rows=len(all_results),
            runs_rows=len(runs),
            hold_rows=len(hold_df),
            otlezka_rows=len(otlezka_df),
            last_manual_sync_at=meta.last_sync_at,
            snapshot_hash_short=hash_short,
            manual_sync_degraded=degraded,
            generated_at=generated_at_text,
            filename=filename,
        )

        log.info(
            "[RegistryExportBuilder] built path=%s all_results=%s runs=%s hold=%s otlezka=%s degraded=%s",
            output_path,
            summary.all_results_rows,
            summary.runs_rows,
            summary.hold_rows,
            summary.otlezka_rows,
            summary.manual_sync_degraded,
        )

        return RegistryExportArtifact(
            path=output_path,
            filename=filename,
            summary=summary,
        )


def build_registry_export_from_postgres(
    *,
    store: ManualSyncStore | None = None,
    output_path: Path | None = None,
) -> RegistryExportArtifact:
    """Convenience wrapper for callers (TG, future CLI)."""
    try:
        return RegistryExportBuilder(store=store).build(output_path=output_path)
    except DatabaseNotConfiguredError as exc:
        raise RuntimeError("DATABASE_URL is not set") from exc
