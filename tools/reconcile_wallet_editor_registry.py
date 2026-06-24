"""CLI: reconcile wallet_editor.xlsx all_results against PostgreSQL mirror."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from integrations.wallet_editor_registry import wallet_editor_dropbox_path
from integrations.wallet_editor_registry_db.config import ENV_DATABASE_URL
from integrations.wallet_editor_registry_db.connection import DatabaseNotConfiguredError, connect
from integrations.wallet_editor_registry_db.import_workbook import ImportWorkbookError
from integrations.wallet_editor_registry_db.reconcile import (
    format_reconcile_report,
    persist_reconcile_report,
    reconcile_exit_code,
    reconcile_workbook_against_db,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Reconcile Wallet Editor registry Excel all_results against "
            "PostgreSQL we_registry_results (observation / drift detection)."
        ),
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "workbook",
        nargs="?",
        type=Path,
        help="Local path to wallet_editor.xlsx",
    )
    source.add_argument(
        "--dropbox",
        action="store_true",
        help="Download workbook from DROPBOX_WALLET_EDITOR_PATH then reconcile",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="Persist drift findings to we_registry_reconcile and update meta",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable summary JSON on stdout after report",
    )
    return parser


def _download_workbook(local_path: Path) -> None:
    from integrations.dropbox_watcher import download_file_with_rev

    dropbox_path = wallet_editor_dropbox_path()
    if not dropbox_path:
        raise ImportWorkbookError("DROPBOX_WALLET_EDITOR_PATH is not set")
    status, _rev = download_file_with_rev(dropbox_path, str(local_path))
    if status == "not_found":
        raise ImportWorkbookError(f"registry workbook not found in Dropbox: {dropbox_path}")
    if status != "ok":
        raise ImportWorkbookError(
            f"registry workbook download failed: {dropbox_path} status={status}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.dropbox:
            with tempfile.TemporaryDirectory(prefix="we_reconcile_") as tmp:
                local_path = Path(tmp) / "wallet_editor.xlsx"
                _download_workbook(local_path)
                report = reconcile_workbook_against_db(local_path)
        else:
            path = args.workbook
            if path is None:
                parser.error("workbook path is required unless --dropbox is used")
            report = reconcile_workbook_against_db(path)

        if args.record:
            with connect(for_mirror=False) as conn:
                with conn.cursor() as cur:
                    inserted = persist_reconcile_report(cur, report)
            print(f"Recorded {inserted} reconcile finding(s) in we_registry_reconcile")

        print(format_reconcile_report(report))

        if args.json:
            import json

            payload = {
                "status": report.status.value,
                "excel_row_count": report.excel_row_count,
                "db_row_count": report.db_row_count,
                "total_issues": report.total_issue_count,
                "missing_in_db": list(report.missing_in_db),
                "missing_in_excel": list(report.missing_in_excel),
                "content_mismatch_count": len(report.content_mismatches),
                "checked_at": report.checked_at,
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        return reconcile_exit_code(report)
    except DatabaseNotConfiguredError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Set {ENV_DATABASE_URL} and apply schema.sql first.", file=sys.stderr)
        return 2
    except ImportWorkbookError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: reconcile failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
