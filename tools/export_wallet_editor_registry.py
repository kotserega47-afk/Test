"""CLI: export Wallet Editor registry from PostgreSQL to a local Excel file."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from integrations.wallet_editor_registry_db.connection import DatabaseNotConfiguredError
from integrations.wallet_editor_registry_db.registry_export_builder import (
    RegistryExportBuilder,
    format_registry_export_summary,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a registry export workbook from PostgreSQL (read-only).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output .xlsx path or directory (default: temp file path in summary)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        artifact = RegistryExportBuilder().build(output_path=args.output)
    except DatabaseNotConfiguredError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: export failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(format_registry_export_summary(artifact.summary))
    print(f"path: {artifact.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
