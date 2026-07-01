"""CLI: refresh Postgres lifecycle fields after Отлёжка sheet repair."""

from __future__ import annotations

import argparse
import sys

from integrations.wallet_editor_registry_db.connection import DatabaseNotConfiguredError
from integrations.wallet_editor_registry_db.refresh_lifecycle_fields import (
    format_refresh_lifecycle_summary,
    refresh_lifecycle_fields_from_postgres,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Recalculate lifecycle fields (Дата включения / Статус включения / hold) "
            "for Postgres registry rows using current hold/Отлёжка config."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Update PostgreSQL (default: dry-run only)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        summary = refresh_lifecycle_fields_from_postgres(apply=args.apply)
    except DatabaseNotConfiguredError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(format_refresh_lifecycle_summary(summary))
    return 1 if summary.error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
