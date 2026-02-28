# core/rules_writer.py

import shutil
from pathlib import Path
from typing import Any, Callable, Dict

import pandas as pd

from core.event_log import append_event
from core.state_store import state_update_meta
from core.rules_provider import _dropbox_rules_file_path
from integrations.dropbox_watcher import download_file, upload_file
from core.config_manager import clear_rules_caches


_TMP_DIR = Path("/tmp/rules_writer")
_TMP_DIR.mkdir(parents=True, exist_ok=True)


def update_sheet(
    sheet_name: str,
    actor: Dict[str, Any],
    mutate_fn: Callable[[pd.DataFrame], pd.DataFrame],
) -> None:

    db_path = _dropbox_rules_file_path()
    local_path = _TMP_DIR / "rules.xlsx"

    # 1️⃣ download fresh
    status = download_file(db_path, str(local_path))
    if status != "ok":
        raise RuntimeError("Failed to download rules.xlsx")

    # 2️⃣ read sheet
    try:
        df = pd.read_excel(local_path, sheet_name=sheet_name, engine="openpyxl")
    except ValueError:
        df = pd.DataFrame()

    # 3️⃣ mutate
    df2 = mutate_fn(df.copy())

    # 4️⃣ write back sheet (preserving others)
    with pd.ExcelWriter(local_path, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
        df2.to_excel(writer, sheet_name=sheet_name, index=False)

    # 5️⃣ validate ALL rules
    _validate_all(local_path)

    # 6️⃣ upload overwrite
    upload_file(str(local_path), db_path)

    # 7️⃣ clear local caches
    clear_rules_caches()

    # 8️⃣ update state.meta
    state_update_meta(actor)

    # 9️⃣ event
    append_event(
        type="rules_updated",
        payload={"sheet": sheet_name},
        actor=actor,
    )