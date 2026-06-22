"""Wallet Editor registry outbox durability (Phase 1)."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from automation.audit import Stats
from automation.runtime import WalletEditorAddWalletTask, WalletEditorTask
from integrations.wallet_editor_registry import (
    append_run_to_dropbox_registry,
    build_registry_health_report,
    format_registry_health_report,
    registry_stale_outbox_warning,
    replay_pending_outbox_records,
)
from integrations.wallet_editor_registry_async import (
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_PENDING,
    OUTBOX_STATUS_SYNCED,
    create_outbox_record,
    get_outbox_record,
    load_outbox_records,
    persist_durable_result_copy,
    prepare_registry_outbox_and_schedule,
)
from integrations.wallet_editor_registry_lifecycle import (
    OUTBOX_STATUS_PENDING as LIFECYCLE_PENDING,
    mark_run_processed,
    missing_result_fingerprints,
    processed_run_ids_path,
    run_id_already_processed,
    save_processed_run_ids,
)
from integrations.wallet_editor_registry_lifecycle import (
    OPERATION_DATE_COLUMN,
    DISABLE_DATE_COLUMN,
    result_row_dates,
)

MSK = ZoneInfo("Europe/Moscow")
RUN_STARTED = datetime(2026, 6, 22, 9, 0, 0, tzinfo=MSK)
RUN_FINISHED = datetime(2026, 6, 22, 9, 5, 0, tzinfo=MSK)
DROPBOX_PATH = "/Ostin/platform/Tests/wallet_editor.xlsx"


def _make_task(*, run_id: str = "outbox-run-1") -> WalletEditorTask:
    return WalletEditorTask(
        file_path="/tmp/wallet_editor/in.xlsx",
        chat_id=-1001,
        telegram_user_id=111,
        operator_profile="DENIS",
        source_file_name="batch.xlsx",
        login="login",
        password="pass",
        auth_state_path="/tmp/auth.json",
        run_id=run_id,
    )


def _write_result(path: Path, *, card: str = "4111111111111111") -> None:
    processed = datetime(2026, 6, 22, 9, 0, 0, tzinfo=MSK)
    operation_date, disable_date = result_row_dates("remove_partner", "OK", processed)
    pd.DataFrame(
        {
            OPERATION_DATE_COLUMN: [operation_date],
            DISABLE_DATE_COLUMN: [disable_date],
            "card": [card],
            "action": ["remove_partner"],
            "value": ["Ostin"],
            "status": ["OK"],
            "comment": [""],
        }
    ).to_excel(path, index=False)


@pytest.fixture
def outbox_env(monkeypatch, tmp_path):
    state_dir = tmp_path / "state"
    monkeypatch.setenv("STATE_DIR", str(state_dir))
    monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)

    store: dict[str, bytes] = {}
    revs: dict[str, str] = {"": "rev0"}

    def fake_download(path, local_path):
        if path not in store:
            return "not_found", None
        Path(local_path).write_bytes(store[path])
        return "ok", revs.get(path, "rev1")

    def fake_upload(local_path, path, expected_rev=None):
        if expected_rev is not None and revs.get(path) != expected_rev:
            return "rev_conflict"
        store[path] = Path(local_path).read_bytes()
        revs[path] = f"rev{len(revs)}"
        return "uploaded"

    monkeypatch.setattr(
        "integrations.wallet_editor_registry.download_file_with_rev",
        fake_download,
    )
    monkeypatch.setattr(
        "integrations.wallet_editor_registry.upload_file_if_rev",
        fake_upload,
    )
    monkeypatch.setattr(
        "integrations.wallet_editor_registry.load_registry_settings",
        lambda **_: __import__(
            "integrations.wallet_editor_registry_settings", fromlist=["RegistrySettings"]
        ).RegistrySettings(60, 180, 10),
    )

    return state_dir, store, revs


def test_outbox_record_created_after_durable_copy(outbox_env, tmp_path):
    state_dir, _, _ = outbox_env
    result = tmp_path / "result.xlsx"
    _write_result(result)
    task = _make_task()

    durable = persist_durable_result_copy(task.run_id, str(result))
    create_outbox_record(
        task,
        durable_path=durable,
        stats=Stats(ok=1, fail=0, skip=0),
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
        output_file="out.xlsx",
    )

    record = get_outbox_record(task.run_id)
    assert record is not None
    assert record.status == LIFECYCLE_PENDING
    assert Path(record.result_file_path).is_file()
    assert (state_dir / "wallet_editor" / "results" / f"{task.run_id}.xlsx").is_file()


def test_durable_copy_survives_tmp_cleanup(outbox_env, tmp_path):
    _, _, _ = outbox_env
    result = tmp_path / "result.xlsx"
    _write_result(result)
    task = _make_task()

    durable = persist_durable_result_copy(task.run_id, str(result))
    result.unlink()

    assert Path(durable).is_file()
    df = pd.read_excel(durable)
    assert len(df) == 1


def test_failed_upload_keeps_outbox_pending(outbox_env, tmp_path):
    _, store, revs = outbox_env
    result = tmp_path / "result.xlsx"
    _write_result(result)
    task = _make_task(run_id="fail-upload")

    durable = persist_durable_result_copy(task.run_id, str(result))
    create_outbox_record(
        task,
        durable_path=durable,
        stats=Stats(ok=1, fail=0, skip=0),
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
        output_file="out.xlsx",
    )

    revs[DROPBOX_PATH] = "stale-rev"

    def always_conflict(local_path, path, expected_rev=None):
        return "rev_conflict"

    with patch(
        "integrations.wallet_editor_registry.upload_file_if_rev",
        side_effect=always_conflict,
    ):
        with patch(
            "integrations.wallet_editor_registry._send_timeout_warning",
        ):
            with patch(
                "integrations.wallet_editor_registry._send_slow_append_warning",
            ):
                append_run_to_dropbox_registry(
                    task,
                    durable,
                    Stats(ok=1, fail=0, skip=0),
                    run_started_at=RUN_STARTED,
                    run_finished_at=RUN_FINISHED,
                    settings=__import__(
                        "integrations.wallet_editor_registry_settings",
                        fromlist=["RegistrySettings"],
                    ).RegistrySettings(1, 2, 1),
                    output_file="out.xlsx",
                )

    record = get_outbox_record(task.run_id)
    assert record is not None
    assert record.status == OUTBOX_STATUS_FAILED
    assert DROPBOX_PATH not in store or record.status != OUTBOX_STATUS_SYNCED


def test_replay_after_simulated_failure(outbox_env, tmp_path):
    _, store, _ = outbox_env
    result = tmp_path / "result.xlsx"
    _write_result(result)
    task = _make_task(run_id="replay-run")

    durable = persist_durable_result_copy(task.run_id, str(result))
    create_outbox_record(
        task,
        durable_path=durable,
        stats=Stats(ok=1, fail=0, skip=0),
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
        output_file="out.xlsx",
    )

    from integrations.wallet_editor_registry_async import update_outbox_status

    update_outbox_status(task.run_id, status=OUTBOX_STATUS_FAILED, last_error="simulated")

    result_replay = replay_pending_outbox_records()
    assert result_replay.attempted == 1
    assert result_replay.synced == 1
    assert DROPBOX_PATH in store
    record = get_outbox_record(task.run_id)
    assert record is not None
    assert record.status == OUTBOX_STATUS_SYNCED


def test_processed_run_ids_trap_allows_repair_reappend(outbox_env, tmp_path):
    _, store, _ = outbox_env
    result = tmp_path / "result.xlsx"
    _write_result(result)
    task = _make_task(run_id="trap-run")

    durable = persist_durable_result_copy(task.run_id, str(result))
    mark_run_processed(task.run_id)

    # registry empty — processed id set but no rows in workbook
    assert run_id_already_processed(
        task.run_id,
        pd.DataFrame(),
        result_path=durable,
        all_results_df=pd.DataFrame(),
    ) is False

    append_run_to_dropbox_registry(
        task,
        durable,
        Stats(ok=1, fail=0, skip=0),
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
        output_file="out.xlsx",
    )
    assert DROPBOX_PATH in store
    record = get_outbox_record(task.run_id)
    if record:
        assert record.status == OUTBOX_STATUS_SYNCED


def test_replay_no_duplicate_rows(outbox_env, tmp_path):
    _, store, _ = outbox_env
    result = tmp_path / "result.xlsx"
    _write_result(result)
    task = _make_task(run_id="nodup-run")

    durable = persist_durable_result_copy(task.run_id, str(result))
    create_outbox_record(
        task,
        durable_path=durable,
        stats=Stats(ok=1, fail=0, skip=0),
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
        output_file="out.xlsx",
    )

    replay_pending_outbox_records()
    first_size = len(store[DROPBOX_PATH])
    replay_pending_outbox_records()
    second_size = len(store[DROPBOX_PATH])
    assert first_size == second_size

    with pd.ExcelFile(__import__("io").BytesIO(store[DROPBOX_PATH])) as book:
        df = pd.read_excel(book, "all_results")
    assert len(df) == 1


def test_add_wallet_does_not_create_outbox(outbox_env, tmp_path):
    state_dir, _, _ = outbox_env
    import automation.worker as worker_mod

    with patch("automation.worker.send_text"):
        with patch("automation.worker.send_document"):
            with patch("automation.worker.delayed_cleanup"):
                with patch(
                    "automation.add_wallet_engine.run",
                    return_value=(str(tmp_path / "r.xlsx"), type("S", (), {"telegram_summary": lambda self: "ok"})()),
                ):
                    tmp_path.joinpath("r.xlsx").write_text("x")
                    task = WalletEditorAddWalletTask(
                        file_path=str(tmp_path / "in.xlsx"),
                        original_filename="in.xlsx",
                        operator_profile="DENIS",
                        chat_id=1,
                        user_id=2,
                        login="l",
                        password="p",
                        auth_state_path="/tmp/a.json",
                    )
                    tmp_path.joinpath("in.xlsx").write_text("x")
                    worker_mod._run_add_wallet_task("DENIS", task)

    assert not list((state_dir / "wallet_editor" / "outbox").glob("*")) if (state_dir / "wallet_editor" / "outbox").exists() else True
    assert load_outbox_records() == []


def test_registry_health_empty_outbox(outbox_env):
    report = build_registry_health_report()
    text = format_registry_health_report(report)
    assert "outbox pending: 0" in text
    assert report.degraded is False


def test_registry_health_pending_and_missing_durable(outbox_env, tmp_path):
    result = tmp_path / "result.xlsx"
    _write_result(result)
    task = _make_task(run_id="health-pending")
    durable = persist_durable_result_copy(task.run_id, str(result))
    create_outbox_record(
        task,
        durable_path=durable,
        stats=Stats(ok=1, fail=0, skip=0),
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
        output_file="out.xlsx",
    )
    Path(durable).unlink()

    report = build_registry_health_report()
    assert report.outbox_pending_count >= 1
    assert report.missing_durable_result_count >= 1
    assert report.degraded is True


def test_prepare_registry_outbox_and_schedule_creates_record(outbox_env, tmp_path):
    result = tmp_path / "result.xlsx"
    _write_result(result)
    task = _make_task(run_id="prep-run")

    with patch(
        "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
    ):
        durable = prepare_registry_outbox_and_schedule(
            task,
            str(result),
            Stats(ok=1, fail=0, skip=0),
            run_started_at=RUN_STARTED,
            run_finished_at=RUN_FINISHED,
            output_file="visible.xlsx",
        )
        assert Path(durable).is_file()
        record = get_outbox_record(task.run_id)
        assert record is not None
        assert record.result_file_path == durable


def test_health_detects_processed_without_rows(outbox_env, tmp_path):
    _, store, revs = outbox_env
    result = tmp_path / "result.xlsx"
    _write_result(result)
    task = _make_task(run_id="orphan-processed")

    durable = persist_durable_result_copy(task.run_id, str(result))
    create_outbox_record(
        task,
        durable_path=durable,
        stats=Stats(ok=1, fail=0, skip=0),
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
        output_file="out.xlsx",
    )
    from integrations.wallet_editor_registry_async import update_outbox_status

    update_outbox_status(task.run_id, status=OUTBOX_STATUS_SYNCED)
    mark_run_processed(task.run_id)

    # Seed empty registry workbook so health can compare rows
    from integrations.wallet_editor_registry_xlsx import create_styled_registry_workbook

    empty_wb = tmp_path / "empty.xlsx"
    create_styled_registry_workbook(empty_wb)
    store[DROPBOX_PATH] = empty_wb.read_bytes()
    revs[DROPBOX_PATH] = "rev-empty"

    report = build_registry_health_report()
    assert report.processed_without_rows_count >= 1


def test_processed_without_rows_triggers_stale_warning(outbox_env, tmp_path):
    _, store, revs = outbox_env
    result = tmp_path / "result.xlsx"
    _write_result(result)
    task = _make_task(run_id="warn-trap")

    durable = persist_durable_result_copy(task.run_id, str(result))
    create_outbox_record(
        task,
        durable_path=durable,
        stats=Stats(ok=1, fail=0, skip=0),
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
        output_file="out.xlsx",
    )
    from integrations.wallet_editor_registry_async import update_outbox_status

    update_outbox_status(task.run_id, status=OUTBOX_STATUS_SYNCED)
    mark_run_processed(task.run_id)

    from integrations.wallet_editor_registry_xlsx import create_styled_registry_workbook

    empty_wb = tmp_path / "empty.xlsx"
    create_styled_registry_workbook(empty_wb)
    store[DROPBOX_PATH] = empty_wb.read_bytes()
    revs[DROPBOX_PATH] = "rev-empty"

    warning = registry_stale_outbox_warning()
    assert warning is not None
    assert "processed_without_rows=" in warning


def test_corrupt_processed_run_ids_blocks_replay(outbox_env, tmp_path):
    state_dir, store, revs = outbox_env
    result = tmp_path / "result.xlsx"
    _write_result(result)
    task = _make_task(run_id="corrupt-replay")

    durable = persist_durable_result_copy(task.run_id, str(result))
    create_outbox_record(
        task,
        durable_path=durable,
        stats=Stats(ok=1, fail=0, skip=0),
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
        output_file="out.xlsx",
    )
    from integrations.wallet_editor_registry_async import update_outbox_status

    update_outbox_status(task.run_id, status=OUTBOX_STATUS_FAILED, last_error="simulated")

    corrupt_path = state_dir / "wallet_editor" / "registry_processed_run_ids.json"
    corrupt_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_path.write_text("{not-valid-json", encoding="utf-8")

    result_replay = replay_pending_outbox_records()
    assert result_replay.attempted == 0
    assert result_replay.synced == 0
    assert any("replay blocked" in err for err in result_replay.errors)
    assert DROPBOX_PATH not in store


def test_registry_health_reports_processed_run_ids_corruption(outbox_env, tmp_path):
    state_dir, _, _ = outbox_env
    corrupt_path = state_dir / "wallet_editor" / "registry_processed_run_ids.json"
    corrupt_path.parent.mkdir(parents=True, exist_ok=True)
    corrupt_path.write_text("[]", encoding="utf-8")

    report = build_registry_health_report()
    text = format_registry_health_report(report)

    assert report.processed_run_ids_corrupted is True
    assert report.degraded is True
    assert "processed_run_ids corrupted: True" in text
    assert "CRITICAL" in text
