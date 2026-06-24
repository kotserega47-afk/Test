"""Load hold / Отлёжка config sheets from Excel (not migrated to Postgres)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from integrations.dropbox_watcher import download_file_with_rev
from integrations.wallet_editor_registry_xlsx import load_registry_frames
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["DROPBOX"]
log = get_logger(name, icon)


def load_hold_otlezka_from_dropbox(
    dropbox_path: str,
) -> tuple[pd.DataFrame, pd.DataFrame, bool, bool]:
    """
    Download workbook and return hold + otlezka frames only.

  Best-effort: on download failure returns empty frames.
    """
    with tempfile.TemporaryDirectory(prefix="we_hold_") as tmp:
        local_path = Path(tmp) / "wallet_editor.xlsx"
        status, _rev = download_file_with_rev(dropbox_path, str(local_path))
        if status == "error":
            log.warning(
                "[WalletEditorRegistry] hold/otlezka download failed path=%s",
                dropbox_path,
            )
            return (
                pd.DataFrame(columns=["Дата добавления", "card", "partner", "comment"]),
                pd.DataFrame(columns=["partner", "Полные дни", "comment"]),
                False,
                False,
            )
        _all_df, _runs_df, hold_df, otlezka_df, hold_exists, otlezka_exists = (
            load_registry_frames(local_path, status)
        )
        return hold_df, otlezka_df, hold_exists, otlezka_exists
