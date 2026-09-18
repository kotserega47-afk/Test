# integrations/wallet_editor_tg.py
"""Compatible re-export of Antares Wallet Editor document ingest."""

from __future__ import annotations

from modules.antares.document_ingest import (
    handle_wallet_editor_document,
    is_wallet_editor_chat_allowed,
    is_xlsx_file_name,
    log_wallet_editor_allowlist_startup_warning,
    parse_allowed_chat_ids,
)

__all__ = (
    "handle_wallet_editor_document",
    "is_wallet_editor_chat_allowed",
    "is_xlsx_file_name",
    "log_wallet_editor_allowlist_startup_warning",
    "parse_allowed_chat_ids",
)
