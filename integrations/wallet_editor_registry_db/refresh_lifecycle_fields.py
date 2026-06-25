"""One-shot lifecycle field refresh for Postgres registry source (Отлёжка repair)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Sequence

import pandas as pd

from core.datetime_utils import now_msk
from integrations.wallet_editor_registry import wallet_editor_dropbox_path
from integrations.wallet_editor_registry_db.connection import connect
from integrations.wallet_editor_registry_db.frames import load_registry_frames_from_postgres
from integrations.wallet_editor_registry_db.hold_loader import load_hold_otlezka_from_dropbox
from integrations.wallet_editor_registry_db.mapping import _optional_str
from integrations.wallet_editor_registry_db.store import PostgresRegistryStore, RegistryStore
from integrations.wallet_editor_registry_lifecycle import (
    MISSING_OTLEZKA_DATE_TEXT,
    MISSING_OTLEZKA_STATUS,
    _cell_str,
    normalize_all_results,
    recalculate_all_results,
    result_row_fingerprint,
)
from integrations.wallet_editor_registry_refresh import _lifecycle_row_changed
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)

SAMPLE_CHANGED_LIMIT = 5


@dataclass(frozen=True, slots=True)
class LifecycleFieldChange:
    row_fingerprint: str
    card: str
    partner: str
    before_reenable: str
    after_reenable: str
    before_status: str
    after_status: str
    before_hold: str
    after_hold: str


@dataclass(frozen=True, slots=True)
class LifecyclePatch:
    row_fingerprint: str
    reenable_date: str | None
    enable_status: str | None
    hold_mark: str | None
    sample: LifecycleFieldChange


@dataclass
class RefreshLifecycleSummary:
    total_rows_scanned: int = 0
    stale_missing_otlezka_rows: int = 0
    rows_changed: int = 0
    remaining_missing_partners: tuple[str, ...] = ()
    sample_changes: tuple[LifecycleFieldChange, ...] = ()
    dry_run: bool = True
    excel_exported: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return len(self.errors)


def _lifecycle_optional(row: pd.Series, column: str) -> str | None:
    return _optional_str(_cell_str(row.get(column, "")))


def _is_stale_missing_otlezka_row(row: pd.Series) -> bool:
    return (
        _cell_str(row.get("Дата включения", "")) == MISSING_OTLEZKA_DATE_TEXT
        or _cell_str(row.get("Статус включения", "")) == MISSING_OTLEZKA_STATUS
    )


def plan_lifecycle_patches(
    all_results: pd.DataFrame,
    hold_df: pd.DataFrame,
    otlezka_df: pd.DataFrame,
    *,
    today: date | None = None,
) -> tuple[list[LifecyclePatch], int, set[str]]:
    """
    Recalculate lifecycle fields and return patches keyed by existing fingerprints.

    Operation columns and fingerprints are taken from the *before* frame so history
  is never rewritten.
    """
    before_df = normalize_all_results(all_results)
    stale_rows = sum(1 for idx in before_df.index if _is_stale_missing_otlezka_row(before_df.loc[idx]))
    recalculated, missing_partners = recalculate_all_results(
        before_df,
        hold_df,
        otlezka_df,
        today=today,
    )
    after_df = normalize_all_results(recalculated)

    patches: list[LifecyclePatch] = []
    for idx in before_df.index:
        if not _lifecycle_row_changed(before_df, after_df, idx):
            continue
        before_row = before_df.loc[idx]
        after_row = after_df.loc[idx]
        fingerprint = result_row_fingerprint(before_row)
        sample = LifecycleFieldChange(
            row_fingerprint=fingerprint,
            card=_cell_str(before_row.get("card", "")),
            partner=_cell_str(before_row.get("partner", "")),
            before_reenable=_cell_str(before_row.get("Дата включения", "")),
            after_reenable=_cell_str(after_row.get("Дата включения", "")),
            before_status=_cell_str(before_row.get("Статус включения", "")),
            after_status=_cell_str(after_row.get("Статус включения", "")),
            before_hold=_cell_str(before_row.get("hold", "")),
            after_hold=_cell_str(after_row.get("hold", "")),
        )
        patches.append(
            LifecyclePatch(
                row_fingerprint=fingerprint,
                reenable_date=_lifecycle_optional(after_row, "Дата включения"),
                enable_status=_lifecycle_optional(after_row, "Статус включения"),
                hold_mark=_lifecycle_optional(after_row, "hold"),
                sample=sample,
            )
        )
    return patches, stale_rows, missing_partners


def _apply_patches(patches: Sequence[LifecyclePatch], store: RegistryStore) -> int:
    updated = 0
    for patch in patches:
        if store.patch_lifecycle_fields(
            row_fingerprint=patch.row_fingerprint,
            reenable_date=patch.reenable_date,
            enable_status=patch.enable_status,
            hold_mark=patch.hold_mark,
        ):
            updated += 1
    return updated


def refresh_lifecycle_fields_from_postgres(
    *,
    apply: bool = False,
    today: date | None = None,
    dropbox_path: str | None = None,
    export_excel: bool = True,
    store: RegistryStore | None = None,
) -> RefreshLifecycleSummary:
    """Recalculate lifecycle fields from Postgres history + Dropbox Отлёжка."""
    summary = RefreshLifecycleSummary(dry_run=not apply)
    today = today or now_msk().date()
    dropbox_path = dropbox_path or wallet_editor_dropbox_path()
    if not dropbox_path:
        summary.errors.append("DROPBOX_WALLET_EDITOR_PATH is not set")
        return summary

    try:
        all_results_df, _runs_df = load_registry_frames_from_postgres()
        hold_df, otlezka_df, _hold_exists, _otlezka_exists = load_hold_otlezka_from_dropbox(
            dropbox_path,
        )
    except Exception as exc:
        summary.errors.append(f"load failed: {exc}")
        return summary

    summary.total_rows_scanned = len(all_results_df)
    patches, stale_rows, missing_partners = plan_lifecycle_patches(
        all_results_df,
        hold_df,
        otlezka_df,
        today=today,
    )
    summary.stale_missing_otlezka_rows = stale_rows
    summary.rows_changed = len(patches)
    summary.remaining_missing_partners = tuple(sorted(missing_partners))
    summary.sample_changes = tuple(
        patch.sample for patch in patches[:SAMPLE_CHANGED_LIMIT]
    )

    if not apply:
        return summary

    try:
        if store is not None:
            summary.rows_changed = _apply_patches(patches, store)
        else:
            with connect(for_mirror=False) as conn:
                with conn.cursor() as cur:
                    pg_store = PostgresRegistryStore(cur)
                    summary.rows_changed = _apply_patches(patches, pg_store)
                conn.commit()
    except Exception as exc:
        summary.errors.append(f"postgres update failed: {exc}")
        return summary

    if export_excel and summary.rows_changed > 0:
        try:
            from integrations.wallet_editor_registry_db.excel_export import (
                export_registry_workbook_to_dropbox,
            )

            export_registry_workbook_to_dropbox(dropbox_path)
            summary.excel_exported = True
        except Exception as exc:
            summary.errors.append(f"excel export failed: {exc}")

    log.info(
        "[WalletEditorRegistry] lifecycle refresh apply rows_changed=%s stale=%s missing_partners=%s",
        summary.rows_changed,
        summary.stale_missing_otlezka_rows,
        len(summary.remaining_missing_partners),
    )
    return summary


def format_refresh_lifecycle_summary(summary: RefreshLifecycleSummary) -> str:
    mode = "apply" if not summary.dry_run else "dry-run"
    lines = [
        "WalletEditor registry lifecycle refresh",
        "",
        f"mode: {mode}",
        f"total rows scanned: {summary.total_rows_scanned}",
        f"rows with old Нет даты отлёжки: {summary.stale_missing_otlezka_rows}",
        f"rows changed: {summary.rows_changed}",
        f"remaining missing partners: {len(summary.remaining_missing_partners)}",
        f"errors: {summary.error_count}",
    ]
    if summary.remaining_missing_partners:
        lines.append("")
        lines.append("missing partners:")
        for partner in summary.remaining_missing_partners[:10]:
            lines.append(f"  - {partner}")
        if len(summary.remaining_missing_partners) > 10:
            lines.append(
                f"  ... and {len(summary.remaining_missing_partners) - 10} more"
            )
    if summary.sample_changes:
        lines.append("")
        lines.append("sample changed rows:")
        for change in summary.sample_changes:
            lines.append(
                f"  - {change.card} / {change.partner} ({change.row_fingerprint})"
            )
            lines.append(
                f"      Дата включения: {change.before_reenable!r} -> {change.after_reenable!r}"
            )
            lines.append(
                f"      Статус включения: {change.before_status!r} -> {change.after_status!r}"
            )
            if change.before_hold != change.after_hold:
                lines.append(f"      hold: {change.before_hold!r} -> {change.after_hold!r}")
    if not summary.dry_run:
        lines.append("")
        lines.append(f"excel exported: {'yes' if summary.excel_exported else 'no'}")
    if summary.errors:
        lines.append("")
        lines.append("error details:")
        for message in summary.errors:
            lines.append(f"  - {message}")
    return "\n".join(lines)
