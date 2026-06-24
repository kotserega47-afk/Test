"""CLI: diagnose processed_run_ids without matching PostgreSQL registry rows."""

from __future__ import annotations

import argparse
import sys

from integrations.wallet_editor_registry_db.connection import DatabaseNotConfiguredError
from integrations.wallet_editor_registry_db.diagnose_processed import (
    diagnose_processed_runs,
    diagnose_processed_report_json,
    format_diagnose_processed_report,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only diagnostic: compare STATE_DIR processed_run_ids + durable "
            "result files against PostgreSQL we_registry_results."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum processed run_ids to inspect (default: 20)",
    )
    parser.add_argument(
        "--only-health-failures",
        action="store_true",
        help=(
            "Scan all processed run_ids and show only entries where "
            "health_would_count is true (matches /registry_health counter)"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON report",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.limit < 0:
        print("ERROR: --limit must be >= 0", file=sys.stderr)
        return 2

    try:
        report = diagnose_processed_runs(
            limit=args.limit,
            only_health_failures=args.only_health_failures,
        )
    except DatabaseNotConfiguredError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(diagnose_processed_report_json(report))
    else:
        print(format_diagnose_processed_report(report))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
