"""Wallet Editor Dropbox cumulative registry."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from automation.audit import Stats
from automation.runtime import WalletEditorTask
from integrations.wallet_editor_registry import (
    ALL_RESULTS_COLUMNS,
    RUNS_COLUMNS,
    SHEET_ALL_RESULTS,
    SHEET_RUNS,
    SOURCE_CONVERSION_AUTO,
    SOURCE_TELEGRAM_MANUAL,
    append_run_to_dropbox_registry,
    resolve_source,
)

MSK = ZoneInfo("Europe/Moscow")
RUN_STARTED = datetime(2026, 6, 3, 0, 40, 0, tzinfo=MSK)
RUN_FINISHED = datetime(2026, 6, 3, 0, 45, 0, tzinfo=MSK)
DROPBOX_PATH = "/Ostin/platform/Tests/wallet_editor.xlsx"


def _make_task(
    *,
    profile: str = "DENIS",
    user_id: int = 111,
    run_id: str = "run-test-001",
) -> WalletEditorTask:
    return WalletEditorTask(
        file_path="/tmp/wallet_editor/in.xlsx",
        chat_id=-1001,
        telegram_user_id=user_id,
        operator_profile=profile,
        source_file_name="batch.xlsx",
        login="login",
        password="pass",
        auth_state_path="/tmp/auth.json",
        run_id=run_id,
    )


def _write_result_xlsx(path: Path, rows: int = 2) -> None:
    data = {
        "Дата отключения": ["03.06.2026 00:40:01"] * rows,
        "card": [f"411111111111111{i}" for i in range(rows)],
        "action": ["remove_partner"] * rows,
        "value": ["Ostin"] * rows,
        "status": ["OK"] * rows,
        "comment": ["removed"] * rows,
    }
    pd.DataFrame(data).to_excel(path, index=False)


@pytest.fixture
def registry_env(monkeypatch, tmp_path):
    store: dict[str, bytes] = {}

    def fake_download(dropbox_path: str, local_path: str) -> str:
        assert dropbox_path == DROPBOX_PATH
        content = store.get(dropbox_path)
        if content is None:
            return "not_found"
        Path(local_path).write_bytes(content)
        return "ok"

    def fake_upload(local_path: str, dropbox_path: str) -> bool:
        assert dropbox_path == DROPBOX_PATH
        store[dropbox_path] = Path(local_path).read_bytes()
        return True

    monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)
    with patch(
        "integrations.wallet_editor_registry.download_file_status",
        side_effect=fake_download,
    ):
        with patch(
            "integrations.wallet_editor_registry.upload_file",
            side_effect=fake_upload,
        ):
            yield store, tmp_path


def test_registry_creates_workbook(registry_env, tmp_path):
    store, _ = registry_env
    result_path = tmp_path / "result.xlsx"
    _write_result_xlsx(result_path, rows=1)
    task = _make_task(run_id="run-create-1")
    stats = Stats(ok=1, fail=0, skip=0)

    append_run_to_dropbox_registry(
        task,
        str(result_path),
        stats,
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
    )

    assert DROPBOX_PATH in store
    all_df, runs_df = _read_registry_from_bytes(store[DROPBOX_PATH])
    assert list(all_df.columns) == ALL_RESULTS_COLUMNS
    assert list(runs_df.columns) == RUNS_COLUMNS
    assert len(all_df) == 1
    assert len(runs_df) == 1


def _read_registry_from_bytes(data: bytes) -> tuple[pd.DataFrame, pd.DataFrame]:
    import io

    with pd.ExcelFile(io.BytesIO(data), engine="openpyxl") as book:
        return pd.read_excel(book, SHEET_ALL_RESULTS), pd.read_excel(book, SHEET_RUNS)


def test_registry_appends_rows(registry_env, tmp_path):
    store, _ = registry_env
    result_path = tmp_path / "result.xlsx"
    _write_result_xlsx(result_path, rows=3)
    task = _make_task(run_id="run-rows-1")
    stats = Stats(ok=2, fail=1, skip=0)

    append_run_to_dropbox_registry(
        task,
        str(result_path),
        stats,
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
    )

    all_df, _ = _read_registry_from_bytes(store[DROPBOX_PATH])
    assert len(all_df) == 3
    assert str(all_df.iloc[0]["card"]) == "4111111111111110"
    assert all_df.iloc[0]["run_id"] == "run-rows-1"
    assert int(all_df.iloc[0]["row_index"]) == 0


def test_registry_appends_runs(registry_env, tmp_path):
    store, _ = registry_env
    result_path = tmp_path / "result.xlsx"
    _write_result_xlsx(result_path, rows=2)
    stats = Stats(ok=1, fail=0, skip=1)

    append_run_to_dropbox_registry(
        _make_task(run_id="run-a"),
        str(result_path),
        stats,
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
    )
    append_run_to_dropbox_registry(
        _make_task(run_id="run-b"),
        str(result_path),
        stats,
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
    )

    _, runs_df = _read_registry_from_bytes(store[DROPBOX_PATH])
    assert len(runs_df) == 2
    assert set(runs_df["run_id"].astype(str)) == {"run-a", "run-b"}
    assert int(runs_df.iloc[0]["success_rows"]) == 1
    assert int(runs_df.iloc[0]["skipped_rows"]) == 1


def test_registry_idempotent_same_run(registry_env, tmp_path):
    store, _ = registry_env
    result_path = tmp_path / "result.xlsx"
    _write_result_xlsx(result_path, rows=2)
    task = _make_task(run_id="run-dup")
    stats = Stats(ok=2, fail=0, skip=0)

    append_run_to_dropbox_registry(
        task, str(result_path), stats,
        run_started_at=RUN_STARTED, run_finished_at=RUN_FINISHED,
    )
    append_run_to_dropbox_registry(
        task, str(result_path), stats,
        run_started_at=RUN_STARTED, run_finished_at=RUN_FINISHED,
    )

    all_df, runs_df = _read_registry_from_bytes(store[DROPBOX_PATH])
    assert len(all_df) == 2
    assert len(runs_df) == 1


def test_registry_failure_does_not_break_worker(registry_env, tmp_path):
    import time

    import automation.worker as worker_mod

    result_path = tmp_path / "result.xlsx"
    _write_result_xlsx(result_path, rows=1)

    with worker_mod._registry_lock:
        worker_mod._profile_workers.clear()

    with patch(
        "integrations.wallet_editor_registry.download_file_status",
        return_value="error",
    ):
        with patch("automation.worker.run") as mock_run:
            mock_run.return_value = (str(result_path), Stats(ok=1, fail=0, skip=0))
            with patch("automation.worker.send_text") as send_text:
                with patch("automation.worker.send_document") as send_document:
                    worker_mod.add_task(_make_task(run_id="run-worker-fail"))
                    deadline = time.time() + 2
                    while worker_mod._profile_workers["DENIS"].queue.unfinished_tasks > 0:
                        if time.time() > deadline:
                            break
                        time.sleep(0.02)

    send_text.assert_called()
    send_document.assert_called()
    sent_text = send_text.call_args.kwargs.get("text") or send_text.call_args[0][0]
    assert "OK=" in sent_text


def test_registry_download_error_does_not_raise(registry_env, tmp_path):
    store, _ = registry_env
    result_path = tmp_path / "result.xlsx"
    _write_result_xlsx(result_path, rows=1)

    with patch(
        "integrations.wallet_editor_registry.download_file_status",
        return_value="error",
    ):
        append_run_to_dropbox_registry(
            _make_task(run_id="run-dl-err"),
            str(result_path),
            Stats(ok=1, fail=0, skip=0),
            run_started_at=RUN_STARTED,
            run_finished_at=RUN_FINISHED,
        )

    assert DROPBOX_PATH not in store


def test_registry_source_conversion_auto():
    assert resolve_source("CONVERSION_AUTO") == SOURCE_CONVERSION_AUTO
    assert resolve_source("conversion_auto") == SOURCE_CONVERSION_AUTO


def test_registry_source_manual():
    assert resolve_source("DENIS") == SOURCE_TELEGRAM_MANUAL


def test_registry_source_manual_via_append(registry_env, tmp_path):
    store, _ = registry_env
    result_path = tmp_path / "result.xlsx"
    _write_result_xlsx(result_path, rows=1)

    append_run_to_dropbox_registry(
        _make_task(profile="DENIS", run_id="run-manual"),
        str(result_path),
        Stats(ok=1, fail=0, skip=0),
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
    )
    all_df, runs_df = _read_registry_from_bytes(store[DROPBOX_PATH])
    assert all_df.iloc[0]["source"] == SOURCE_TELEGRAM_MANUAL
    assert runs_df.iloc[0]["source"] == SOURCE_TELEGRAM_MANUAL


def test_registry_source_conversion_auto_via_append(registry_env, tmp_path):
    store, _ = registry_env
    result_path = tmp_path / "result.xlsx"
    _write_result_xlsx(result_path, rows=1)

    append_run_to_dropbox_registry(
        _make_task(profile="CONVERSION_AUTO", run_id="run-conv"),
        str(result_path),
        Stats(ok=1, fail=0, skip=0),
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
    )
    all_df, runs_df = _read_registry_from_bytes(store[DROPBOX_PATH])
    assert all_df.iloc[0]["source"] == SOURCE_CONVERSION_AUTO
    assert runs_df.iloc[0]["source"] == SOURCE_CONVERSION_AUTO
