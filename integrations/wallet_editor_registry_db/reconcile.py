"""Excel ↔ PostgreSQL registry mirror reconcile (observation / Stage D)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import pandas as pd

from integrations.wallet_editor_registry_db.connection import connect
from integrations.wallet_editor_registry_db.import_workbook import load_registry_dataframes
from integrations.wallet_editor_registry_db.mapping import map_all_results_row
from integrations.wallet_editor_registry_db.models import RegistryResultRow
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)

# Drift thresholds (I-OBS: Excel is source of truth; missing_in_db = mirror gap)
WARNING_MAX_ISOLATED_ISSUES = 3
MASS_MISMATCH_RATIO = 0.05
MASS_MISMATCH_MIN_COUNT = 10

_COMPARABLE_FIELDS = (
    "operation_date",
    "disable_at",
    "reenable_date",
    "enable_status",
    "vklyucheno",
    "enable_comment",
    "card",
    "partner",
    "action",
    "status",
    "comment",
    "hold_mark",
)

FETCH_RESULTS_SQL = """
SELECT
    row_fingerprint,
    run_id,
    operation_date,
    disable_at,
    reenable_date,
    enable_status,
    vklyucheno,
    enable_comment,
    card,
    partner,
    action,
    status,
    comment,
    hold_mark,
    source_row_index
FROM we_registry_results
ORDER BY row_fingerprint
"""

INSERT_RECONCILE_SQL = """
INSERT INTO we_registry_reconcile (
    entity_type,
    entity_key,
    excel_hash,
    db_hash,
    status,
    details
) VALUES (
    %(entity_type)s,
    %(entity_key)s,
    %(excel_hash)s,
    %(db_hash)s,
    'open',
    %(details)s
)
"""

UPSERT_META_SQL = """
INSERT INTO we_registry_meta (key, value, updated_at)
VALUES (%(key)s, %(value)s, now())
ON CONFLICT (key) DO UPDATE
SET value = EXCLUDED.value,
    updated_at = EXCLUDED.updated_at
"""


class ReconcileStatus(str, Enum):
    OK = "OK"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class FieldMismatch:
    field: str
    excel_value: str | None
    db_value: str | None


@dataclass(frozen=True, slots=True)
class ContentMismatch:
    fingerprint: str
    fields: tuple[FieldMismatch, ...]


@dataclass(frozen=True, slots=True)
class ReconcileReport:
    status: ReconcileStatus
    excel_row_count: int
    db_row_count: int
    missing_in_db: tuple[str, ...]
    missing_in_excel: tuple[str, ...]
    content_mismatches: tuple[ContentMismatch, ...]
    duplicate_excel_fingerprints: tuple[str, ...]
    duplicate_db_fingerprints: tuple[str, ...]
    checked_at: str

    @property
    def total_issue_count(self) -> int:
        return (
            len(self.missing_in_db)
            + len(self.missing_in_excel)
            + len(self.content_mismatches)
            + len(self.duplicate_excel_fingerprints)
            + len(self.duplicate_db_fingerprints)
        )


def _row_field_value(row: RegistryResultRow, field: str) -> str | None:
    value = getattr(row, field)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _compare_result_rows(
    excel_row: RegistryResultRow,
    db_row: RegistryResultRow,
) -> tuple[FieldMismatch, ...]:
    mismatches: list[FieldMismatch] = []
    for field in _COMPARABLE_FIELDS:
        excel_value = _row_field_value(excel_row, field)
        db_value = _row_field_value(db_row, field)
        if excel_value != db_value:
            mismatches.append(
                FieldMismatch(field=field, excel_value=excel_value, db_value=db_value)
            )
    return tuple(mismatches)


def _index_excel_results(all_results: pd.DataFrame) -> tuple[dict[str, RegistryResultRow], list[str]]:
    by_fingerprint: dict[str, RegistryResultRow] = {}
    duplicates: list[str] = []
    for index in all_results.index:
        row = all_results.loc[index]
        mapped = map_all_results_row(row, source_row_index=int(index))
        fingerprint = mapped.row_fingerprint
        if fingerprint in by_fingerprint:
            duplicates.append(fingerprint)
        by_fingerprint[fingerprint] = mapped
    return by_fingerprint, duplicates


def _row_from_db_record(record: tuple[Any, ...]) -> RegistryResultRow:
    (
        row_fingerprint,
        run_id,
        operation_date,
        disable_at,
        reenable_date,
        enable_status,
        vklyucheno,
        enable_comment,
        card,
        partner,
        action,
        status,
        comment,
        hold_mark,
        source_row_index,
    ) = record
    return RegistryResultRow(
        row_fingerprint=row_fingerprint,
        run_id=run_id,
        operation_date=operation_date,
        disable_at=disable_at,
        reenable_date=reenable_date,
        enable_status=enable_status,
        vklyucheno=vklyucheno,
        enable_comment=enable_comment,
        card=card,
        partner=partner or "",
        action=action,
        status=status or "",
        comment=comment,
        hold_mark=hold_mark,
        source_row_index=source_row_index,
    )


def fetch_db_results(cursor: Any) -> tuple[dict[str, RegistryResultRow], list[str]]:
    """Load all mirrored result rows keyed by fingerprint."""
    cursor.execute(FETCH_RESULTS_SQL)
    rows = cursor.fetchall()
    by_fingerprint: dict[str, RegistryResultRow] = {}
    duplicates: list[str] = []
    for record in rows:
        mapped = _row_from_db_record(record)
        fingerprint = mapped.row_fingerprint
        if fingerprint in by_fingerprint:
            duplicates.append(fingerprint)
        by_fingerprint[fingerprint] = mapped
    if duplicates:
        log.warning(
            "[WalletEditorRegistryReconcile] duplicate DB fingerprints detected count=%s",
            len(duplicates),
        )
    return by_fingerprint, duplicates


def reconcile_result_maps(
    excel_by_fp: dict[str, RegistryResultRow],
    db_by_fp: dict[str, RegistryResultRow],
    *,
    duplicate_excel: list[str] | None = None,
    duplicate_db: list[str] | None = None,
    checked_at: str | None = None,
) -> ReconcileReport:
    """Compare Excel and DB fingerprint indexes and classify drift."""
    excel_fps = set(excel_by_fp)
    db_fps = set(db_by_fp)
    missing_in_db = tuple(sorted(excel_fps - db_fps))
    missing_in_excel = tuple(sorted(db_fps - excel_fps))

    content_mismatches: list[ContentMismatch] = []
    for fingerprint in sorted(excel_fps & db_fps):
        field_diffs = _compare_result_rows(excel_by_fp[fingerprint], db_by_fp[fingerprint])
        if field_diffs:
            content_mismatches.append(
                ContentMismatch(fingerprint=fingerprint, fields=field_diffs)
            )

    report = ReconcileReport(
        status=ReconcileStatus.OK,
        excel_row_count=len(excel_by_fp),
        db_row_count=len(db_by_fp),
        missing_in_db=missing_in_db,
        missing_in_excel=missing_in_excel,
        content_mismatches=tuple(content_mismatches),
        duplicate_excel_fingerprints=tuple(sorted(set(duplicate_excel or []))),
        duplicate_db_fingerprints=tuple(sorted(set(duplicate_db or []))),
        checked_at=checked_at or datetime.now(timezone.utc).isoformat(),
    )
    status = classify_drift(report)
    return ReconcileReport(
        status=status,
        excel_row_count=report.excel_row_count,
        db_row_count=report.db_row_count,
        missing_in_db=report.missing_in_db,
        missing_in_excel=report.missing_in_excel,
        content_mismatches=report.content_mismatches,
        duplicate_excel_fingerprints=report.duplicate_excel_fingerprints,
        duplicate_db_fingerprints=report.duplicate_db_fingerprints,
        checked_at=report.checked_at,
    )


def classify_drift(report: ReconcileReport) -> ReconcileStatus:
    """
    Classify reconcile outcome.

    WARNING — isolated discrepancies (≤ WARNING_MAX_ISOLATED_ISSUES).
    ERROR — data loss (Excel rows missing in DB) beyond threshold, or mass mismatch.
    """
    if report.total_issue_count == 0:
        return ReconcileStatus.OK

    issue_count = report.total_issue_count
    excel_rows = max(report.excel_row_count, 1)
    missing_in_db_count = len(report.missing_in_db)
    mismatch_count = len(report.content_mismatches)

    mass_missing = missing_in_db_count >= max(
        MASS_MISMATCH_MIN_COUNT,
        int(excel_rows * MASS_MISMATCH_RATIO),
    )
    mass_content = mismatch_count >= max(
        MASS_MISMATCH_MIN_COUNT,
        int(excel_rows * MASS_MISMATCH_RATIO),
    )

    if mass_missing or mass_content or missing_in_db_count > WARNING_MAX_ISOLATED_ISSUES:
        return ReconcileStatus.ERROR

    if issue_count <= WARNING_MAX_ISOLATED_ISSUES:
        return ReconcileStatus.WARNING

    return ReconcileStatus.ERROR


def reconcile_workbook_against_db(
    workbook_path: Path,
    *,
    connection: Any | None = None,
) -> ReconcileReport:
    """Load Excel all_results and compare to PostgreSQL we_registry_results."""
    all_results, _runs = load_registry_dataframes(workbook_path)
    excel_by_fp, duplicate_excel = _index_excel_results(all_results)

    if connection is not None:
        with connection.cursor() as cur:
            db_by_fp, duplicate_db = fetch_db_results(cur)
    else:
        with connect(for_mirror=False) as conn:
            with conn.cursor() as cur:
                db_by_fp, duplicate_db = fetch_db_results(cur)

    return reconcile_result_maps(
        excel_by_fp,
        db_by_fp,
        duplicate_excel=duplicate_excel,
        duplicate_db=duplicate_db,
    )


def reconcile_frames_against_store(
    all_results: pd.DataFrame,
    db_by_fp: dict[str, RegistryResultRow],
) -> ReconcileReport:
    """Compare in-memory Excel frame to a DB result map (tests / dry-run)."""
    excel_by_fp, duplicate_excel = _index_excel_results(all_results)
    return reconcile_result_maps(excel_by_fp, db_by_fp, duplicate_excel=duplicate_excel)


def persist_reconcile_report(cursor: Any, report: ReconcileReport) -> int:
    """Persist open drift rows into we_registry_reconcile. Returns rows inserted."""
    inserted = 0
    for fingerprint in report.missing_in_db:
        cursor.execute(
            INSERT_RECONCILE_SQL,
            {
                "entity_type": "results",
                "entity_key": fingerprint,
                "excel_hash": fingerprint,
                "db_hash": None,
                "details": "missing_in_db",
            },
        )
        inserted += 1

    for fingerprint in report.missing_in_excel:
        cursor.execute(
            INSERT_RECONCILE_SQL,
            {
                "entity_type": "results",
                "entity_key": fingerprint,
                "excel_hash": None,
                "db_hash": fingerprint,
                "details": "missing_in_excel",
            },
        )
        inserted += 1

    for mismatch in report.content_mismatches:
        cursor.execute(
            INSERT_RECONCILE_SQL,
            {
                "entity_type": "results",
                "entity_key": mismatch.fingerprint,
                "excel_hash": mismatch.fingerprint,
                "db_hash": mismatch.fingerprint,
                "details": json.dumps(
                    [
                        {
                            "field": field.field,
                            "excel": field.excel_value,
                            "db": field.db_value,
                        }
                        for field in mismatch.fields
                    ],
                    ensure_ascii=False,
                ),
            },
        )
        inserted += 1

    summary = {
        "status": report.status.value,
        "excel_row_count": report.excel_row_count,
        "db_row_count": report.db_row_count,
        "total_issues": report.total_issue_count,
        "checked_at": report.checked_at,
    }
    cursor.execute(
        UPSERT_META_SQL,
        {"key": "last_reconcile_at", "value": report.checked_at},
    )
    cursor.execute(
        UPSERT_META_SQL,
        {"key": "last_reconcile_status", "value": report.status.value},
    )
    cursor.execute(
        UPSERT_META_SQL,
        {"key": "last_reconcile_summary", "value": json.dumps(summary, ensure_ascii=False)},
    )
    return inserted


def format_reconcile_report(report: ReconcileReport) -> str:
    """Human-readable reconcile summary for CLI / ops."""
    lines = [
        "WalletEditor registry reconcile",
        "",
        f"status: {report.status.value}",
        f"checked at: {report.checked_at}",
        f"excel rows: {report.excel_row_count}",
        f"postgres rows: {report.db_row_count}",
        f"total issues: {report.total_issue_count}",
        "",
        f"missing in DB: {len(report.missing_in_db)}",
        f"missing in Excel: {len(report.missing_in_excel)}",
        f"content mismatches: {len(report.content_mismatches)}",
        f"duplicate Excel fingerprints: {len(report.duplicate_excel_fingerprints)}",
        f"duplicate DB fingerprints: {len(report.duplicate_db_fingerprints)}",
    ]

    if report.missing_in_db:
        lines.append("")
        lines.append("missing in DB (sample):")
        for fingerprint in report.missing_in_db[:10]:
            lines.append(f"  - {fingerprint}")
        if len(report.missing_in_db) > 10:
            lines.append(f"  ... and {len(report.missing_in_db) - 10} more")

    if report.missing_in_excel:
        lines.append("")
        lines.append("missing in Excel (sample):")
        for fingerprint in report.missing_in_excel[:10]:
            lines.append(f"  - {fingerprint}")
        if len(report.missing_in_excel) > 10:
            lines.append(f"  ... and {len(report.missing_in_excel) - 10} more")

    if report.content_mismatches:
        lines.append("")
        lines.append("content mismatches (sample):")
        for mismatch in report.content_mismatches[:5]:
            lines.append(f"  - {mismatch.fingerprint}")
            for field in mismatch.fields[:5]:
                lines.append(
                    f"      {field.field}: excel={field.excel_value!r} db={field.db_value!r}"
                )

    return "\n".join(lines)


def reconcile_exit_code(report: ReconcileReport) -> int:
    """CLI exit code: 0 OK, 1 WARNING, 2 ERROR."""
    if report.status == ReconcileStatus.OK:
        return 0
    if report.status == ReconcileStatus.WARNING:
        return 1
    return 2
