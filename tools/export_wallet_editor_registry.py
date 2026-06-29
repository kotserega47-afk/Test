"""CLI: export Wallet Editor registry from Postgres to Dropbox Excel."""

from __future__ import annotations

import sys

from integrations.wallet_editor_registry import wallet_editor_dropbox_path
from integrations.wallet_editor_registry_db.connection import DatabaseNotConfiguredError
from integrations.wallet_editor_registry_db.excel_export import (
    export_registry_workbook_to_dropbox,
    format_export_registry_summary,
)


def main(argv: list[str] | None = None) -> int:
    dropbox_path = wallet_editor_dropbox_path()
    if not dropbox_path:
        print("ERROR: DROPBOX_WALLET_EDITOR_PATH is not set", file=sys.stderr)
        return 2

    try:
        summary = export_registry_workbook_to_dropbox(dropbox_path)
    except DatabaseNotConfiguredError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: export failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(format_export_registry_summary(summary))
    return 0 if summary.upload_status == "uploaded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
