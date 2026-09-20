"""Dropbox workbook append backend for registry tests. Does not call Postgres."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd

from tests.unit.sender_test_stub import ensure_sender_stub


def dropbox_append_attempt(
    task,
    result_path: str,
    stats,
    *,
    dropbox_path: str,
    run_started_at,
    run_finished_at,
    output_file: str | None = None,
):
    from integrations.wallet_editor_registry import _AppendOutcome, _process_missing_otlezka_warnings
    from integrations.wallet_editor_registry_lifecycle import (
        apply_missing_otlezka_red_fill,
        build_runs_row,
        filter_rows_not_in_registry,
        load_processed_run_ids,
        mark_run_processed,
        missing_result_fingerprints,
        recalculate_all_results_runtime,
        rows_from_result_excel,
        run_id_already_processed,
    )
    from integrations.wallet_editor_registry_xlsx import (
        card_as_text,
        create_styled_registry_workbook,
        load_registry_frames,
        save_registry_workbook,
    )
    from integrations import wallet_editor_registry as wer

    runs_output_file = output_file or os.path.basename(result_path)
    tmpdir = tempfile.mkdtemp(prefix="we_reg_dropbox_test_")
    local = Path(tmpdir) / "registry.xlsx"
    status, rev = wer.download_file_with_rev(dropbox_path, str(local))
    if status == "error":
        return _AppendOutcome.TRANSIENT, None, None

    is_new = status == "not_found"
    if is_new:
        create_styled_registry_workbook(local)
        rev = None

    all_results_df, runs_df, hold_df, otlezka_df, hold_exists, otlezka_exists = load_registry_frames(
        local,
        status,
    )

    if run_id_already_processed(
        task.run_id,
        runs_df,
        result_path=result_path,
        all_results_df=all_results_df,
    ):
        return _AppendOutcome.DUPLICATE, rev, None

    repair_mode = task.run_id in load_processed_run_ids() and bool(
        missing_result_fingerprints(result_path, all_results_df)
    )
    result_df = pd.read_excel(
        result_path,
        engine="openpyxl",
        converters={"card": card_as_text},
    )
    input_rows = len(result_df)
    new_rows = rows_from_result_excel(result_df)
    if repair_mode:
        new_rows = filter_rows_not_in_registry(new_rows, all_results_df)
        if new_rows.empty:
            return _AppendOutcome.DUPLICATE, rev, None

    merged_all = pd.concat([all_results_df, new_rows], ignore_index=True)
    missing_details: dict[str, str] = {}
    recalculated, missing_partners = recalculate_all_results_runtime(
        merged_all,
        hold_df,
        otlezka_df,
        missing_details=missing_details,
    )
    new_run = build_runs_row(
        started_at=run_started_at,
        finished_at=run_finished_at,
        input_rows=input_rows,
        stats_ok=stats.ok,
        stats_fail=stats.fail,
        stats_skip=stats.skip,
        output_file=runs_output_file,
    )
    merged_runs = pd.concat([runs_df, new_run], ignore_index=True)

    save_registry_workbook(
        local,
        all_results=recalculated,
        runs=merged_runs,
        hold_exists=hold_exists,
        otlezka_exists=otlezka_exists,
        is_new_file=is_new,
    )
    apply_missing_otlezka_red_fill(local)

    upload = wer.upload_file_if_rev(str(local), dropbox_path, expected_rev=rev)
    if upload == "rev_conflict":
        return _AppendOutcome.TRANSIENT, None, None
    if upload != "uploaded":
        return _AppendOutcome.TRANSIENT, None, None

    mark_run_processed(task.run_id)
    _process_missing_otlezka_warnings(
        task,
        missing_partners,
        otlezka_df,
        details=missing_details,
    )
    return _AppendOutcome.SUCCESS, None, None


def _blocked_postgres(*_a, **_k):
    raise RuntimeError("postgres connect blocked in dropbox registry tests")


def _blocked_dropbox_client(*_a, **_k):
    raise RuntimeError("Dropbox client blocked in dropbox registry tests")


def install_dropbox_registry_scenario(monkeypatch) -> None:
    """Explicit dropbox workbook backend; never read working DATABASE_URL/PG."""
    ensure_sender_stub()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DROPBOX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("DROPBOX_REFRESH_TOKEN", raising=False)
    monkeypatch.setenv("WALLET_EDITOR_MANUAL_READERS_SOURCE", "dropbox")
    monkeypatch.setenv("WALLET_EDITOR_REGISTRY_SOURCE", "postgres")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(
        "integrations.wallet_editor_registry_db.config.get_database_url",
        lambda: "postgresql://registry-dropbox-test.invalid:1/unused",
    )
    monkeypatch.setattr(
        "integrations.wallet_editor_registry._append_attempt",
        dropbox_append_attempt,
    )
    monkeypatch.setattr(
        "integrations.wallet_editor_registry_db.connection.connect",
        _blocked_postgres,
    )
    monkeypatch.setattr(
        "integrations.dropbox_watcher._get_dbx",
        _blocked_dropbox_client,
    )
