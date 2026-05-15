# core/rules_writer.py

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List

import pandas as pd

from core.config_manager import clear_rules_caches
from core.event_log import append_event
from core.rules_provider import _rules_dropbox_path
from core.rules_v2.contract_publish import (
    ContractValidationMode,
    evaluate_snapshot_publish,
)
from core.rules_v2.validation_issues import ValidationIssue, ValidationSeverity, is_blocking
from core.state_store import state_update_meta
from integrations.dropbox_watcher import download_file, upload_file


_TMP_DIR = Path("/tmp/rules_writer")
_TMP_DIR.mkdir(parents=True, exist_ok=True)


def _format_contract_issue_line(issue: ValidationIssue) -> str:
    where = issue.sheet or "workbook"
    parts = [issue.code, issue.message]
    if issue.rule_id:
        parts.append(f"id={issue.rule_id}")
    if issue.field:
        parts.append(f"field={issue.field}")
    if issue.row_index is not None:
        parts.append(f"row={issue.row_index}")
    return f"{where}: {' | '.join(parts)}"


def _validate_rules_file(local_path: Path) -> None:
    """
    Pre-upload validation via the contract pipeline (C2 + C2.5 + C3) in STRICT mode.

    Upload is blocked when ``publish_allowed`` is false or any infra failure
    (load / build / validation crash) is present. WARN-level contract findings
    do not block upload.
    """
    decision = evaluate_snapshot_publish(
        local_path,
        policy_mode=ContractValidationMode.STRICT,
    )

    errors: List[str] = []
    warnings: List[str] = []

    if decision.load_error:
        errors.append(f"workbook: load: {decision.load_error}")
    if decision.build_error:
        errors.append(f"workbook: build: {decision.build_error}")
    if decision.validation_crash:
        errors.append(f"workbook: validation: {decision.validation_crash}")

    for issue in decision.contract_issues:
        line = _format_contract_issue_line(issue)
        if is_blocking(issue):
            errors.append(line)
        elif issue.severity == ValidationSeverity.WARN:
            warnings.append(line)

    blocked = (
        decision.load_error is not None
        or decision.build_error is not None
        or decision.validation_crash is not None
        or not decision.publish_allowed
    )

    if not blocked:
        return

    details_lines = [f"- {x}" for x in errors[:30]]
    if len(errors) > 30:
        details_lines.append(f"- ... and {len(errors) - 30} more")
    if warnings:
        details_lines.append("")
        details_lines.append("Warnings (context, non-blocking):")
        details_lines.extend(f"- {x}" for x in warnings[:10])
        if len(warnings) > 10:
            details_lines.append(f"- ... and {len(warnings) - 10} more warnings")

    details = "\n".join(details_lines)
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
