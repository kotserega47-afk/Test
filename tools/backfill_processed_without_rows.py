"""CLI: backfill missing Postgres registry rows from durable processed run results."""

from __future__ import annotations

import argparse
import sys

from integrations.wallet_editor_registry_db.backfill_processed import (
    backfill_processed_runs,
    format_backfill_summary,
)
from integrations.wallet_editor_registry_db.connection import DatabaseNotConfiguredError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill missing we_registry_results rows from durable outbox result "
            "files for processed/synced run_ids (Postgres source migration repair)."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write missing rows to PostgreSQL (default: dry-run only)",
    )
    parser.add_argument(
        "--run-id",
        action="append",
        dest="run_ids",
        metavar="RUN_ID",
        help="Limit backfill to specific processed run_id (repeatable)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        summary = backfill_processed_runs(
            apply=args.apply,
            run_ids=args.run_ids,
        )
    except DatabaseNotConfiguredError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(format_backfill_summary(summary))
    return 1 if summary.error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
