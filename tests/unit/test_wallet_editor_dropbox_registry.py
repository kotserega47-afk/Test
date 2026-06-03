"""Wallet Editor Dropbox registry — lifecycle sheets and rules."""
from __future__ import annotations

import io
import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from automation.audit import Stats
from automation.runtime import WalletEditorTask
from integrations.wallet_editor_registry import (
    SHEET_ALL_RESULTS,
    SHEET_RUNS,
    append_run_to_dropbox_registry,
    resolve_source,
)
from integrations.wallet_editor_registry_lifecycle import (
    ALL_RESULTS_COLUMNS,
    HOLD_COLUMNS,
    HOLD_MARK,
    MISSING_OTLEZKA_DATE_TEXT,
    MISSING_OTLEZKA_STATUS,
    OTLEZKA_COLUMNS,
    RUNS_COLUMNS,
    SHEET_HOLD,
    SHEET_OTLEZKA,
    STATUS_K_VKLUCHENIYU,
    STATUS_OZHIDAET,
    STATUS_PROSROCHENO,
    STATUS_VKLUCHENO,
    WARN_MESSAGE_TEMPLATE,
    load_warned_partners,
    partner_from_row,
    recalculate_all_results,
    warned_partners_path,
)

MSK = ZoneInfo("Europe/Moscow")
TODAY = date(2026, 6, 3)
RUN_STARTED = datetime(2026, 6, 3, 9, 0, 0, tzinfo=MSK)
RUN_FINISHED = datetime(2026, 6, 3, 9, 5, 0, tzinfo=MSK)
DROPBOX_PATH = "/Ostin/platform/Tests/wallet_editor.xlsx"


def _make_task(
    *,
    profile: str = "DENIS",
    chat_id: int = -1001,
    run_id: str = "run-test-001",
) -> WalletEditorTask:
    return WalletEditorTask(
        file_path="/tmp/wallet_editor/in.xlsx",
        chat_id=chat_id,
        telegram_user_id=111,
        operator_profile=profile,
        source_file_name="batch.xlsx",
        login="login",
        password="pass",
        auth_state_path="/tmp/auth.json",
        run_id=run_id,
    )


def _write_result_xlsx(
    path: Path,
    *,
    rows: int = 1,
    partner: str = "Ostin",
    disable_at: str = "03.06.2026 09:00:00",
    status: str = "OK",
    action: str = "remove_partner",
) -> None:
    pd.DataFrame(
        {
            "Дата отключения": [disable_at] * rows,
            "card": [f"411111111111111{i}" for i in range(rows)],
            "action": [action] * rows,
            "value": [partner] * rows,
            "status": [status] * rows,
            "comment": ["removed"] * rows,
        }
    ).to_excel(path, index=False)


def _read_registry(data: bytes) -> dict[str, pd.DataFrame]:
    with pd.ExcelFile(io.BytesIO(data), engine="openpyxl") as book:
        out = {
            "all_results": pd.read_excel(book, SHEET_ALL_RESULTS),
            "runs": pd.read_excel(book, SHEET_RUNS),
        }
        if SHEET_HOLD in book.sheet_names:
            out["hold"] = pd.read_excel(book, SHEET_HOLD)
        else:
            out["hold"] = pd.DataFrame(columns=HOLD_COLUMNS)
        if SHEET_OTLEZKA in book.sheet_names:
            out[SHEET_OTLEZKA] = pd.read_excel(book, SHEET_OTLEZKA)
        else:
            out[SHEET_OTLEZKA] = pd.DataFrame(columns=OTLEZKA_COLUMNS)
        return out


@pytest.fixture
def registry_env(monkeypatch, tmp_path):
    store: dict[str, bytes] = {}

    def fake_download(dropbox_path: str, local_path: str) -> str:
        content = store.get(dropbox_path)
        if content is None:
            return "not_found"
        Path(local_path).write_bytes(content)
        return "ok"

    def fake_upload(local_path: str, dropbox_path: str) -> bool:
        store[dropbox_path] = Path(local_path).read_bytes()
        return True

    state_root = tmp_path / "state"
    monkeypatch.setenv("STATE_DIR", str(state_root))
    monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)
    with patch(
        "integrations.wallet_editor_registry.download_file_status",
        side_effect=fake_download,
    ):
        with patch(
            "integrations.wallet_editor_registry.upload_file",
            side_effect=fake_upload,
        ):
            with patch(
                "integrations.wallet_editor_registry_lifecycle.now_msk",
                return_value=datetime(2026, 6, 3, 12, 0, 0, tzinfo=MSK),
            ):
                yield store, tmp_path


def test_registry_all_results_columns_order(registry_env, tmp_path):
    store, _ = registry_env
    result_path = tmp_path / "result.xlsx"
    _write_result_xlsx(result_path)
    append_run_to_dropbox_registry(
        _make_task(run_id="run-cols"),
        str(result_path),
        Stats(ok=1, fail=0, skip=0),
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
    )
    sheets = _read_registry(store[DROPBOX_PATH])
    assert list(sheets["all_results"].columns) == ALL_RESULTS_COLUMNS


def test_registry_runs_columns_order(registry_env, tmp_path):
    store, _ = registry_env
    result_path = tmp_path / "result.xlsx"
    _write_result_xlsx(result_path)
    append_run_to_dropbox_registry(
        _make_task(run_id="run-runs-cols"),
        str(result_path),
        Stats(ok=1, fail=0, skip=0),
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
    )
    sheets = _read_registry(store[DROPBOX_PATH])
    assert list(sheets["runs"].columns) == RUNS_COLUMNS


def test_registry_creates_hold_sheet(registry_env, tmp_path):
    store, _ = registry_env
    result_path = tmp_path / "result.xlsx"
    _write_result_xlsx(result_path)
    append_run_to_dropbox_registry(
        _make_task(run_id="run-hold-sheet"),
        str(result_path),
        Stats(ok=1, fail=0, skip=0),
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
    )
    sheets = _read_registry(store[DROPBOX_PATH])
    assert list(sheets["hold"].columns) == HOLD_COLUMNS


def test_registry_creates_otlezka_sheet(registry_env, tmp_path):
    store, _ = registry_env
    result_path = tmp_path / "result.xlsx"
    _write_result_xlsx(result_path)
    append_run_to_dropbox_registry(
        _make_task(run_id="run-otlezka-sheet"),
        str(result_path),
        Stats(ok=1, fail=0, skip=0),
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
    )
    sheets = _read_registry(store[DROPBOX_PATH])
    assert list(sheets[SHEET_OTLEZKA].columns) == OTLEZKA_COLUMNS


def test_registry_partner_from_remove_partner_value():
    assert partner_from_row("remove_partner", "Ostin") == "Ostin"
    assert partner_from_row("set_status", "Ostin") == ""


def test_registry_reenable_date_uses_partner_days():
    all_df = pd.DataFrame(
        [
            {
                "Дата отключения": "01.06.2026 10:00:00",
                "Дата включения": "",
                "Статус включения": "",
                "Включено": "",
                "Комментарий включения": "",
                "card": "4111",
                "partner": "",
                "action": "remove_partner",
                "value": "Ostin",
                "status": "OK",
                "comment": "",
                "hold": "",
            }
        ]
    )
    otlezka = pd.DataFrame([{"partner": "Ostin", "Полные дни": 3, "comment": ""}])
    out, missing = recalculate_all_results(all_df, pd.DataFrame(), otlezka, today=TODAY)
    assert out.iloc[0]["Дата включения"] == "04.06.2026"
    assert missing == set()


def test_registry_missing_otlezka_sets_text_and_status():
    all_df = pd.DataFrame(
        [
            {
                "Дата отключения": "03.06.2026 09:00:00",
                "Дата включения": "",
                "Статус включения": "",
                "Включено": "",
                "Комментарий включения": "",
                "card": "4111",
                "partner": "",
                "action": "remove_partner",
                "value": "UnknownPartner",
                "status": "OK",
                "comment": "",
                "hold": "",
            }
        ]
    )
    out, missing = recalculate_all_results(all_df, pd.DataFrame(), pd.DataFrame(), today=TODAY)
    assert out.iloc[0]["Дата включения"] == MISSING_OTLEZKA_DATE_TEXT
    assert out.iloc[0]["Статус включения"] == MISSING_OTLEZKA_STATUS
    assert "UnknownPartner" in missing


def test_registry_hold_blocks_reenable():
    all_df = pd.DataFrame(
        [
            {
                "Дата отключения": "01.06.2026 10:00:00",
                "Дата включения": "",
                "Статус включения": "",
                "Включено": "",
                "Комментарий включения": "",
                "card": "4111",
                "partner": "",
                "action": "remove_partner",
                "value": "Ostin",
                "status": "OK",
                "comment": "",
                "hold": "",
            }
        ]
    )
    hold = pd.DataFrame(
        [{"Дата добавления": "01.06.2026", "card": "4111", "partner": "Ostin", "comment": ""}]
    )
    otlezka = pd.DataFrame([{"partner": "Ostin", "Полные дни": 3, "comment": ""}])
    out, _ = recalculate_all_results(all_df, hold, otlezka, today=TODAY)
    assert out.iloc[0]["hold"] == HOLD_MARK
    assert out.iloc[0]["Дата включения"] == ""
    assert out.iloc[0]["Статус включения"] == HOLD_MARK


def test_registry_recalculates_existing_rows_after_otlezka_added(registry_env, tmp_path):
    store, _ = registry_env
    result_path = tmp_path / "result.xlsx"
    _write_result_xlsx(result_path, partner="Ostin", disable_at="01.06.2026 10:00:00")

    append_run_to_dropbox_registry(
        _make_task(run_id="run-before-otlezka"),
        str(result_path),
        Stats(ok=1, fail=0, skip=0),
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
    )
    sheets = _read_registry(store[DROPBOX_PATH])
    assert sheets["all_results"].iloc[0]["Дата включения"] == MISSING_OTLEZKA_DATE_TEXT

    local = tmp_path / "with_otlezka.xlsx"
    with pd.ExcelWriter(local, engine="openpyxl") as writer:
        sheets["all_results"].to_excel(writer, sheet_name=SHEET_ALL_RESULTS, index=False)
        sheets["runs"].to_excel(writer, sheet_name=SHEET_RUNS, index=False)
        sheets["hold"].to_excel(writer, sheet_name=SHEET_HOLD, index=False)
        pd.DataFrame([{"partner": "Ostin", "Полные дни": 3, "comment": ""}]).to_excel(
            writer, sheet_name=SHEET_OTLEZKA, index=False
        )
    store[DROPBOX_PATH] = local.read_bytes()

    append_run_to_dropbox_registry(
        _make_task(run_id="run-after-otlezka"),
        str(result_path),
        Stats(ok=1, fail=0, skip=0),
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
    )
    sheets2 = _read_registry(store[DROPBOX_PATH])
    assert sheets2["all_results"].iloc[0]["Дата включения"] == "04.06.2026"
    assert len(sheets2["all_results"]) == 2


def test_registry_lifecycle_status_waiting_today_overdue():
    base = {
        "Дата отключения": "01.06.2026 10:00:00",
        "Дата включения": "",
        "Статус включения": "",
        "Включено": "",
        "Комментарий включения": "",
        "card": "1",
        "partner": "",
        "action": "remove_partner",
        "value": "Ostin",
        "status": "OK",
        "comment": "",
        "hold": "",
    }
    otlezka = pd.DataFrame([{"partner": "Ostin", "Полные дни": 5, "comment": ""}])

    waiting, _ = recalculate_all_results(
        pd.DataFrame([{**base}]), pd.DataFrame(), otlezka, today=date(2026, 6, 2)
    )
    assert waiting.iloc[0]["Статус включения"] == STATUS_OZHIDAET

    today_row, _ = recalculate_all_results(
        pd.DataFrame([{**base}]), pd.DataFrame(), otlezka, today=date(2026, 6, 6)
    )
    assert today_row.iloc[0]["Статус включения"] == STATUS_K_VKLUCHENIYU

    overdue, _ = recalculate_all_results(
        pd.DataFrame([{**base}]), pd.DataFrame(), otlezka, today=date(2026, 6, 10)
    )
    assert overdue.iloc[0]["Статус включения"] == STATUS_PROSROCHENO


def test_registry_included_status_overrides_lifecycle():
    all_df = pd.DataFrame(
        [
            {
                "Дата отключения": "01.06.2026 10:00:00",
                "Дата включения": "10.06.2026",
                "Статус включения": "",
                "Включено": "OK",
                "Комментарий включения": "done",
                "card": "1",
                "partner": "",
                "action": "remove_partner",
                "value": "Ostin",
                "status": "OK",
                "comment": "",
                "hold": "",
            }
        ]
    )
    otlezka = pd.DataFrame([{"partner": "Ostin", "Полные дни": 3, "comment": ""}])
    out, _ = recalculate_all_results(all_df, pd.DataFrame(), otlezka, today=date(2026, 6, 2))
    assert out.iloc[0]["Статус включения"] == STATUS_VKLUCHENO


def test_missing_otlezka_warning_sent_once_per_partner(registry_env, tmp_path):
    store, _ = registry_env
    result_path = tmp_path / "result.xlsx"
    _write_result_xlsx(result_path, partner="NoOtlezka")
    messages: list[str] = []

    with patch(
        "integrations.wallet_editor_registry.send_message_sync",
        side_effect=lambda text, chat_id=None, **_kw: messages.append(text),
    ):
        append_run_to_dropbox_registry(
            _make_task(run_id="warn-1", chat_id=-555),
            str(result_path),
            Stats(ok=1, fail=0, skip=0),
            run_started_at=RUN_STARTED,
            run_finished_at=RUN_FINISHED,
        )
        append_run_to_dropbox_registry(
            _make_task(run_id="warn-2", chat_id=-555),
            str(result_path),
            Stats(ok=1, fail=0, skip=0),
            run_started_at=RUN_STARTED,
            run_finished_at=RUN_FINISHED,
        )

    warn_msgs = [m for m in messages if "Не настроена отлёжка" in m]
    assert len(warn_msgs) == 1
    assert "NoOtlezka" in warn_msgs[0]


def test_missing_otlezka_warning_state_cleared_after_fix(registry_env, tmp_path):
    store, _ = registry_env
    result_path = tmp_path / "result.xlsx"
    _write_result_xlsx(result_path, partner="Teon")

    with patch("integrations.wallet_editor_registry.send_message_sync"):
        append_run_to_dropbox_registry(
            _make_task(run_id="teon-1"),
            str(result_path),
            Stats(ok=1, fail=0, skip=0),
            run_started_at=RUN_STARTED,
            run_finished_at=RUN_FINISHED,
        )
    assert "teon" in load_warned_partners()

    local = tmp_path / "add_teon.xlsx"
    sheets = _read_registry(store[DROPBOX_PATH])
    with pd.ExcelWriter(local, engine="openpyxl") as writer:
        sheets["all_results"].to_excel(writer, sheet_name=SHEET_ALL_RESULTS, index=False)
        sheets["runs"].to_excel(writer, sheet_name=SHEET_RUNS, index=False)
        sheets["hold"].to_excel(writer, sheet_name=SHEET_HOLD, index=False)
        pd.DataFrame([{"partner": "Teon", "Полные дни": 2, "comment": ""}]).to_excel(
            writer, sheet_name=SHEET_OTLEZKA, index=False
        )
    store[DROPBOX_PATH] = local.read_bytes()

    with patch("integrations.wallet_editor_registry.send_message_sync"):
        append_run_to_dropbox_registry(
            _make_task(run_id="teon-2"),
            str(result_path),
            Stats(ok=1, fail=0, skip=0),
            run_started_at=RUN_STARTED,
            run_finished_at=RUN_FINISHED,
        )

    warned = load_warned_partners()
    assert "teon" not in warned
    sheets2 = _read_registry(store[DROPBOX_PATH])
    assert sheets2["all_results"].iloc[0]["Дата включения"] == "05.06.2026"


def test_registry_best_effort_failure_still_does_not_break_worker(registry_env, tmp_path):
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


def test_registry_idempotent_same_run(registry_env, tmp_path):
    store, _ = registry_env
    result_path = tmp_path / "result.xlsx"
    _write_result_xlsx(result_path, rows=2)
    task = _make_task(run_id="run-dup")

    append_run_to_dropbox_registry(
        task, str(result_path), Stats(ok=2, fail=0, skip=0),
        run_started_at=RUN_STARTED, run_finished_at=RUN_FINISHED,
    )
    append_run_to_dropbox_registry(
        task, str(result_path), Stats(ok=2, fail=0, skip=0),
        run_started_at=RUN_STARTED, run_finished_at=RUN_FINISHED,
    )

    sheets = _read_registry(store[DROPBOX_PATH])
    assert len(sheets["all_results"]) == 2
    assert len(sheets["runs"]) == 1


def test_registry_source_conversion_auto():
    assert resolve_source("CONVERSION_AUTO") == "conversion_auto"
