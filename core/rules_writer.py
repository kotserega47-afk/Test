# core/rules_writer.py

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List

import pandas as pd

from core.config_manager import clear_rules_caches
from core.event_log import append_event
from core.rules_provider import _rules_dropbox_path
from core.rules_v2.bridge_legacy import build_snapshot_v2_from_legacy
from core.rules_v2.validators import validate_snapshot
from core.state_store import state_update_meta
from integrations.dropbox_watcher import download_file, upload_file
from tools.validate_rules_xlsx import check_rules_xlsx


_TMP_DIR = Path("/tmp/rules_writer")
_TMP_DIR.mkdir(parents=True, exist_ok=True)


def _validate_rules_file(local_path: Path) -> None:
    """
    Валидирует именно тот workbook, который только что изменили.

    1) legacy/workbook-level checks
    2) snapshot-v2 checks

    Если есть хотя бы одна ошибка — бросает RuntimeError.
    Warnings не блокируют upload.
    """
    errors: List[str] = []
    warnings: List[str] = []

    # 1. workbook / sheet-level validation
    workbook_msgs = check_rules_xlsx(local_path)
    for msg in workbook_msgs:
        line = f"{msg.where}: {msg.message}"
        if msg.level == "ERROR":
            errors.append(line)
        elif msg.level == "WARN":
            warnings.append(line)

    # 2. snapshot-v2 validation
    try:
        snapshot = build_snapshot_v2_from_legacy(local_path)
        result = validate_snapshot(snapshot)

        for issue in result.errors:
            errors.append(issue.message)

        for issue in result.warnings:
            warnings.append(issue.message)

    except Exception as e:
        errors.append(f"snapshot build/validate failed: {e}")

    if errors:
        details = "\n".join(f"- {x}" for x in errors[:30])
        if len(errors) > 30:
            details += f"\n- ... and {len(errors) - 30} more"
        raise RuntimeError(f"rules validation failed ({len(errors)} errors)\n{details}")


def update_sheet(
    sheet_name: str,
    actor: Dict[str, Any],
    mutate_fn: Callable[[pd.DataFrame], pd.DataFrame],
) -> None:
    db_path = _rules_dropbox_path()
    local_path = _TMP_DIR / "rules.xlsx"

    # 1. download fresh copy
    status = download_file(db_path, str(local_path))
    if status != "ok":
        raise RuntimeError(
            f"Failed to download rules.xlsx: status={status}, path={db_path}"
        )

    # 2. read target sheet
    try:
        df = pd.read_excel(local_path, sheet_name=sheet_name, engine="openpyxl")
    except ValueError:
        # sheet not found -> treat as empty sheet
        df = pd.DataFrame()

    # 3. apply mutation
    df2 = mutate_fn(df.copy())
    if not isinstance(df2, pd.DataFrame):
        raise TypeError(
            f"mutate_fn must return pandas.DataFrame, got {type(df2).__name__}"
        )

    # 4. write sheet back, preserving the rest of workbook
    with pd.ExcelWriter(
        local_path,
        engine="openpyxl",
        mode="a",
        if_sheet_exists="replace",
    ) as writer:
        df2.to_excel(writer, sheet_name=sheet_name, index=False)

    # 5. validate changed workbook
    _validate_rules_file(local_path)

    # 6. upload overwrite
    up_ok = upload_file(str(local_path), db_path)
    if not up_ok:
        raise RuntimeError(f"Failed to upload rules.xlsx to Dropbox: {db_path}")

    # 7. clear local caches
    clear_rules_caches()

    # 8. update state.meta
    state_update_meta(actor)

    # 9. event log
    append_event(
        type="rules_updated",
        payload={"sheet": sheet_name},
        actor=actor,
    )