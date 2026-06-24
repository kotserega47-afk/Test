"""CLI: one-shot import wallet_editor.xlsx into PostgreSQL mirror (Stage B)."""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from integrations.wallet_editor_registry import wallet_editor_dropbox_path
from integrations.wallet_editor_registry_db.config import ENV_DATABASE_URL, ENV_MIRROR_ENABLED
from integrations.wallet_editor_registry_db.connection import DatabaseNotConfiguredError
from integrations.wallet_editor_registry_db.import_workbook import (
    ImportWorkbookError,
    import_registry_from_dropbox,
    import_registry_workbook,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Import Wallet Editor registry workbook (all_results + runs) into PostgreSQL. "
            "Mirror flag may remain off; requires DATABASE_URL."
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
        help="Download workbook from DROPBOX_WALLET_EDITOR_PATH then import",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse workbook and print row counts without writing to PostgreSQL",
    )
    return parser


def _print_stats(stats) -> None:
    print(
        "Import complete:",
        f"runs inserted={stats.runs_inserted}",
        f"skipped={stats.runs_skipped}",
        f"results inserted={stats.results_inserted}",
        f"updated={stats.results_updated}",
        f"skipped={stats.results_skipped}",
        f"errors={stats.results_errors}",
    )
    if stats.error_messages:
        print("Row errors:", file=sys.stderr)
        for message in stats.error_messages:
            print(f"  - {message}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.dry_run:
        from integrations.wallet_editor_registry_db.import_workbook import (
            import_registry_frames,
            load_registry_dataframes,
        )
        from integrations.wallet_editor_registry_db.store import InMemoryRegistryStore

        path = args.workbook
        if args.dropbox:
            dropbox_path = wallet_editor_dropbox_path()
            if not dropbox_path:
                print(
                    "ERROR: DROPBOX_WALLET_EDITOR_PATH is not set",
                    file=sys.stderr,
                )
                return 2
            with tempfile.TemporaryDirectory(prefix="we_import_") as tmp:
                local_path = Path(tmp) / "wallet_editor.xlsx"
                stats = import_registry_from_dropbox(dropbox_path, local_path=local_path)
        else:
            if path is None:
                parser.error("workbook path is required unless --dropbox is used")
            all_df, runs_df = load_registry_dataframes(path)
            stats = import_registry_frames(all_df, runs_df, InMemoryRegistryStore())
        _print_stats(stats)
        return 0

    try:
        if args.dropbox:
            dropbox_path = wallet_editor_dropbox_path()
            if not dropbox_path:
                print(
                    "ERROR: DROPBOX_WALLET_EDITOR_PATH is not set",
                    file=sys.stderr,
                )
                return 2
            with tempfile.TemporaryDirectory(prefix="we_import_") as tmp:
                local_path = Path(tmp) / "wallet_editor.xlsx"
                stats = import_registry_from_dropbox(dropbox_path, local_path=local_path)
        else:
            path = args.workbook
            if path is None:
                parser.error("workbook path is required unless --dropbox is used")
            stats = import_registry_workbook(path)
    except DatabaseNotConfiguredError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(f"Set {ENV_DATABASE_URL} and apply schema.sql first.", file=sys.stderr)
        return 2
    except ImportWorkbookError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: import failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    _print_stats(stats)
    print(
        f"Note: {ENV_MIRROR_ENABLED} is not required for this manual import; "
        "runtime mirror remains off unless explicitly enabled."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
