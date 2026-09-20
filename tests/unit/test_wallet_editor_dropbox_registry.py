"""Wallet Editor Dropbox registry — lifecycle sheets and rules."""
from __future__ import annotations

import io
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from automation.audit import Stats
from automation.runtime import WalletEditorTask
from integrations.wallet_editor_registry import resolve_source
from integrations.wallet_editor_registry_xlsx import (
    card_as_text,
    create_styled_registry_workbook,
    load_registry_frames,
    save_registry_workbook,
)
from integrations.wallet_editor_auto_enable_eligibility import select_auto_enable_candidates
from integrations.wallet_editor_registry_lifecycle import (
    ALL_RESULTS_COLUMNS,
    DISABLE_DATE_COLUMN,
    HOLD_COLUMNS,
    HOLD_MARK,
    MISSING_OTLEZKA_DATE_TEXT,
    MISSING_OTLEZKA_STATUS,
    OPERATION_DATE_COLUMN,
    OPERATION_DATE_NUMBER_FORMAT,
    OTLEZKA_COLUMNS,
    RUNS_COLUMNS,
    SHEET_ALL_RESULTS,
    SHEET_HOLD,
    SHEET_OTLEZKA,
    SHEET_RUNS,
    STATUS_K_VKLUCHENIYU,
    STATUS_OZHIDAET,
    STATUS_PROSROCHENO,
    STATUS_VKLUCHENO,
    apply_missing_otlezka_red_fill,
    build_runs_row,
    mark_run_processed,
    partner_from_row,
    recalculate_all_results,
    result_row_dates,
    normalize_all_results,
    rows_from_result_excel,
    run_id_already_processed,
)

MSK = ZoneInfo("Europe/Moscow")
TODAY = date(2026, 6, 3)
RUN_STARTED = datetime(2026, 6, 3, 9, 0, 0, tzinfo=MSK)
RUN_FINISHED = datetime(2026, 6, 3, 9, 5, 0, tzinfo=MSK)


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
    processed = datetime.strptime(disable_at, "%d.%m.%Y %H:%M:%S").replace(tzinfo=MSK)
    operation_date, disable_date = result_row_dates(action, status, processed)
    pd.DataFrame(
        {
            OPERATION_DATE_COLUMN: [operation_date] * rows,
            DISABLE_DATE_COLUMN: [disable_date] * rows,
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


def _compose_registry_workbook(
    dest: Path,
    result_path: Path,
    *,
    existing: bytes | None = None,
    output_file: str | None = None,
    stats: Stats | None = None,
    extra_otlezka: pd.DataFrame | None = None,
) -> bytes:
    """Call production lifecycle/xlsx helpers. Not a stand-in for _append_attempt."""
    stats = stats or Stats(ok=1, fail=0, skip=0)
    local = dest
    is_new = existing is None
    if existing is None:
        create_styled_registry_workbook(local)
        status = "not_found"
    else:
        local.write_bytes(existing)
        status = "ok"
    all_df, runs_df, hold_df, otlezka_df, hold_exists, otlezka_exists = load_registry_frames(
        local,
        status,
    )
    if extra_otlezka is not None:
        otlezka_df = extra_otlezka
        otlezka_exists = True
    result_df = pd.read_excel(
        result_path,
        engine="openpyxl",
        converters={"card": card_as_text},
    )
    new_rows = rows_from_result_excel(result_df)
    merged = pd.concat([all_df, new_rows], ignore_index=True)
    recalc, _missing = recalculate_all_results(
        merged,
        hold_df,
        otlezka_df,
        today=TODAY,
    )
    new_run = build_runs_row(
        started_at=RUN_STARTED,
        finished_at=RUN_FINISHED,
        input_rows=len(result_df),
        stats_ok=stats.ok,
        stats_fail=stats.fail,
        stats_skip=stats.skip,
        output_file=output_file or result_path.name,
    )
    merged_runs = pd.concat([runs_df, new_run], ignore_index=True)
    save_registry_workbook(
        local,
        all_results=recalc,
        runs=merged_runs,
        hold_exists=hold_exists,
        otlezka_exists=otlezka_exists,
        is_new_file=is_new,
    )
    apply_missing_otlezka_red_fill(local)
    return local.read_bytes()


@pytest.fixture
def registry_fs(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DROPBOX_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    with patch(
        "integrations.wallet_editor_registry_lifecycle.now_msk",
        return_value=datetime(2026, 6, 3, 12, 0, 0, tzinfo=MSK),
    ):
        yield tmp_path


def test_registry_all_results_columns_order(registry_fs):
    result_path = registry_fs / "result.xlsx"
    dest = registry_fs / "registry.xlsx"
    _write_result_xlsx(result_path)
    sheets = _read_registry(_compose_registry_workbook(dest, result_path))
    assert list(sheets["all_results"].columns) == ALL_RESULTS_COLUMNS


def test_registry_runs_columns_order(registry_fs):
    result_path = registry_fs / "result.xlsx"
    dest = registry_fs / "registry.xlsx"
    _write_result_xlsx(result_path)
    sheets = _read_registry(_compose_registry_workbook(dest, result_path))
    assert list(sheets["runs"].columns) == RUNS_COLUMNS


def test_registry_output_file_uses_user_visible_name(registry_fs):
    staged_path = registry_fs / "we_registry_result_staged.xlsx"
    dest = registry_fs / "registry.xlsx"
    user_visible = "wallet_editor_result_batch_DENIS.xlsx"
    _write_result_xlsx(staged_path)
    sheets = _read_registry(
        _compose_registry_workbook(dest, staged_path, output_file=user_visible)
    )
    assert sheets["runs"].iloc[-1]["output_file"] == user_visible
    assert sheets["runs"].iloc[-1]["output_file"] != staged_path.name


def test_registry_output_file_fallback_to_result_basename(registry_fs):
    result_path = registry_fs / "result.xlsx"
    dest = registry_fs / "registry.xlsx"
    _write_result_xlsx(result_path)
    sheets = _read_registry(_compose_registry_workbook(dest, result_path))
    assert sheets["runs"].iloc[-1]["output_file"] == "result.xlsx"


def test_registry_creates_hold_sheet(registry_fs):
    result_path = registry_fs / "result.xlsx"
    dest = registry_fs / "registry.xlsx"
    _write_result_xlsx(result_path)
    sheets = _read_registry(_compose_registry_workbook(dest, result_path))
    assert list(sheets["hold"].columns) == HOLD_COLUMNS


def test_registry_creates_otlezka_sheet(registry_fs):
    result_path = registry_fs / "result.xlsx"
    dest = registry_fs / "registry.xlsx"
    _write_result_xlsx(result_path)
    sheets = _read_registry(_compose_registry_workbook(dest, result_path))
    assert list(sheets[SHEET_OTLEZKA].columns) == OTLEZKA_COLUMNS


def test_registry_partner_from_remove_partner_value():
    assert partner_from_row("remove_partner", "Ostin") == "Ostin"
    assert partner_from_row("add_partner", "Partner A") == "Partner A"
    assert partner_from_row("add_partner", "") == ""
    assert partner_from_row("set_status", "Ostin") == ""


def test_rows_from_result_excel_add_partner_populates_partner():
    result_df = pd.DataFrame(
        [
            {
                "Дата отключения": "01.06.2026 10:00:00",
                "card": "4111",
                "action": "add_partner",
                "value": "Partner A",
                "status": "OK",
                "comment": "added Partner A",
            }
        ]
    )
    rows = rows_from_result_excel(result_df)
    assert rows.iloc[0]["partner"] == "Partner A"
    assert rows.iloc[0]["action"] == "add_partner"
    assert rows.iloc[0]["status"] == "OK"
    assert rows.iloc[0]["comment"] == "added Partner A"


def test_recalculate_add_partner_does_not_set_lifecycle_dates():
    all_df = pd.DataFrame(
        [
            {
                "Дата отключения": "01.06.2026 10:00:00",
                "Дата включения": "",
                "Статус включения": "",
                "Включено": "",
                "Комментарий включения": "",
                "card": "4111",
                "partner": "Partner A",
                "action": "add_partner",
                "status": "OK",
                "comment": "added Partner A",
                "hold": "",
            }
        ]
    )
    otlezka = pd.DataFrame([{"partner": "Partner A", "Полные дни": 3, "comment": ""}])
    out, missing = recalculate_all_results(all_df, pd.DataFrame(), otlezka, today=TODAY)
    row = out.iloc[0]
    assert row["partner"] == "Partner A"
    assert row["Дата включения"] == ""
    assert row["Статус включения"] == ""
    assert row["hold"] == ""
    assert missing == set()

    eligible = select_auto_enable_candidates(out, include_overdue=True)
    assert eligible.selected == ()


def test_recalculate_add_partner_via_rows_from_result_preserves_partner():
    result_df = pd.DataFrame(
        [
            {
                "Дата отключения": "01.06.2026 10:00:00",
                "card": "4111",
                "action": "add_partner",
                "value": "Partner A",
                "status": "OK",
                "comment": "added Partner A",
            }
        ]
    )
    rows = rows_from_result_excel(result_df)
    otlezka = pd.DataFrame([{"partner": "Partner A", "Полные дни": 3, "comment": ""}])
    out, _ = recalculate_all_results(rows, pd.DataFrame(), otlezka, today=TODAY)
    assert out.iloc[0]["partner"] == "Partner A"


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


def test_registry_recalculates_existing_rows_after_otlezka_added(registry_fs):
    result_path = registry_fs / "result.xlsx"
    dest = registry_fs / "registry.xlsx"
    _write_result_xlsx(result_path, partner="Ostin", disable_at="01.06.2026 10:00:00")
    first = _compose_registry_workbook(dest, result_path)
    sheets = _read_registry(first)
    assert sheets["all_results"].iloc[0]["Дата включения"] == MISSING_OTLEZKA_DATE_TEXT

    otlezka = pd.DataFrame([{"partner": "Ostin", "Полные дни": 3, "comment": ""}])
    second = _compose_registry_workbook(
        dest,
        result_path,
        existing=first,
        extra_otlezka=otlezka,
    )
    sheets2 = _read_registry(second)
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


def test_registry_best_effort_failure_still_does_not_break_worker(tmp_path):
    import time

    import automation.worker as worker_mod

    result_path = tmp_path / "result.xlsx"
    _write_result_xlsx(result_path, rows=1)

    with worker_mod._registry_lock:
        worker_mod._profile_workers.clear()

    with patch(
        "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
        side_effect=lambda *a, **k: None,
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


def test_registry_idempotent_same_run(registry_fs):
    result_path = registry_fs / "result.xlsx"
    _write_result_xlsx(result_path, rows=2)
    new_rows = rows_from_result_excel(pd.read_excel(result_path, engine="openpyxl"))
    empty_runs = pd.DataFrame(columns=RUNS_COLUMNS)
    empty_all = pd.DataFrame(columns=ALL_RESULTS_COLUMNS)
    assert not run_id_already_processed(
        "run-dup",
        empty_runs,
        result_path=str(result_path),
        all_results_df=empty_all,
    )
    mark_run_processed("run-dup")
    merged = pd.concat([empty_all, new_rows], ignore_index=True)
    assert run_id_already_processed(
        "run-dup",
        empty_runs,
        result_path=str(result_path),
        all_results_df=merged,
    )
    assert len(merged) == 2


def test_registry_source_conversion_auto():
    assert resolve_source("CONVERSION_AUTO") == "conversion_auto"


def _compose_once(tmp_path: Path, *, existing: bytes | None = None, result_path: Path | None = None) -> bytes:
    dest = tmp_path / "registry.xlsx"
    if result_path is None:
        result_path = tmp_path / "result.xlsx"
        _write_result_xlsx(result_path)
    return _compose_registry_workbook(dest, result_path, existing=existing)


def test_registry_preserves_column_widths(registry_fs):
    styled = registry_fs / "styled.xlsx"
    create_styled_registry_workbook(styled)
    data = _compose_once(registry_fs, existing=styled.read_bytes())
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data))
    ws = wb[SHEET_ALL_RESULTS]
    assert ws.column_dimensions["A"].width == 22.5
    assert ws.column_dimensions["G"].width == 18.0
    wb.close()


def test_registry_preserves_freeze_panes(registry_fs):
    styled = registry_fs / "styled.xlsx"
    create_styled_registry_workbook(styled)
    data = _compose_once(registry_fs, existing=styled.read_bytes())
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data))
    assert wb[SHEET_ALL_RESULTS].freeze_panes == "A2"
    wb.close()


def test_registry_preserves_header_fill(registry_fs):
    styled = registry_fs / "styled.xlsx"
    create_styled_registry_workbook(styled)
    data = _compose_once(registry_fs, existing=styled.read_bytes())
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data))
    cell = wb[SHEET_ALL_RESULTS].cell(row=1, column=1)
    assert cell.fill.start_color.rgb in ("004472C4", "4472C4")
    wb.close()


def test_registry_card_written_as_text(registry_fs):
    styled = registry_fs / "styled.xlsx"
    create_styled_registry_workbook(styled)
    result_path = registry_fs / "result.xlsx"
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    headers = ["Дата отключения", "card", "action", "value", "status", "comment"]
    for col_idx, name in enumerate(headers, 1):
        ws.cell(row=1, column=col_idx, value=name)
    ws.cell(row=2, column=1, value="03.06.2026 09:00:00")
    card_cell = ws.cell(row=2, column=2, value="0041111111111111")
    card_cell.number_format = "@"
    ws.cell(row=2, column=3, value="remove_partner")
    ws.cell(row=2, column=4, value="Ostin")
    ws.cell(row=2, column=5, value="OK")
    ws.cell(row=2, column=6, value="removed")
    wb.save(result_path)
    wb.close()

    dest = registry_fs / "registry.xlsx"
    data = _compose_registry_workbook(dest, result_path, existing=styled.read_bytes())
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data))
    ws = wb[SHEET_ALL_RESULTS]
    headers = [c.value for c in ws[1]]
    card_col = headers.index("card") + 1
    cell = ws.cell(row=2, column=card_col)
    assert cell.number_format == "@"
    assert str(cell.value) == "0041111111111111"
    wb.close()


def test_registry_does_not_write_value_column_to_all_results(registry_fs):
    sheets = _read_registry(_compose_once(registry_fs))
    assert "value" not in sheets["all_results"].columns


def test_registry_migrates_partner_from_value(registry_fs):
    legacy_cols = list(ALL_RESULTS_COLUMNS)
    legacy_cols.insert(legacy_cols.index("partner") + 1, "value")
    row = {col: "" for col in legacy_cols}
    row.update(
        {
            "Дата отключения": "01.06.2026 10:00:00",
            "card": "4111",
            "action": "remove_partner",
            "value": "LegacyPartner",
            "status": "OK",
        }
    )
    out = normalize_all_results(pd.DataFrame([row], columns=legacy_cols))
    assert "value" not in out.columns
    assert out.iloc[0]["partner"] == "LegacyPartner"


def test_legacy_workbook_inserts_operation_date_column(registry_fs):
    legacy_path = registry_fs / "legacy_no_op_date.xlsx"
    legacy_cols = [col for col in ALL_RESULTS_COLUMNS if col != OPERATION_DATE_COLUMN]
    row = {col: "" for col in legacy_cols}
    row.update(
        {
            DISABLE_DATE_COLUMN: "01.06.2026 10:00:00",
            "card": "4111",
            "action": "remove_partner",
            "partner": "Ostin",
            "status": "OK",
        }
    )
    with pd.ExcelWriter(legacy_path, engine="openpyxl") as writer:
        pd.DataFrame([row], columns=legacy_cols).to_excel(
            writer, sheet_name=SHEET_ALL_RESULTS, index=False
        )
        pd.DataFrame(columns=RUNS_COLUMNS).to_excel(writer, sheet_name=SHEET_RUNS, index=False)
    dest = registry_fs / "registry.xlsx"
    result_path = registry_fs / "result.xlsx"
    _write_result_xlsx(result_path)
    data = _compose_registry_workbook(dest, result_path, existing=legacy_path.read_bytes())
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data))
    ws = wb[SHEET_ALL_RESULTS]
    headers = [ws.cell(row=1, column=col_idx).value for col_idx in range(1, len(ALL_RESULTS_COLUMNS) + 1)]
    assert headers[0] == OPERATION_DATE_COLUMN
    assert headers[1] == DISABLE_DATE_COLUMN
    sheets = _read_registry(data)
    assert list(sheets["all_results"].columns) == ALL_RESULTS_COLUMNS
    wb.close()


def test_registry_does_not_overwrite_hold_sheet(registry_fs):
    wb_path = registry_fs / "with_hold.xlsx"
    with pd.ExcelWriter(wb_path, engine="openpyxl") as writer:
        pd.DataFrame(columns=ALL_RESULTS_COLUMNS).to_excel(
            writer, sheet_name=SHEET_ALL_RESULTS, index=False
        )
        pd.DataFrame(columns=RUNS_COLUMNS).to_excel(writer, sheet_name=SHEET_RUNS, index=False)
        pd.DataFrame(
            [{"Дата добавления": "01.01.2020", "card": "USERCARD", "partner": "UserP", "comment": "keep"}]
        ).to_excel(writer, sheet_name=SHEET_HOLD, index=False)
    data = _compose_once(registry_fs, existing=wb_path.read_bytes())
    sheets = _read_registry(data)
    assert sheets["hold"].iloc[0]["card"] == "USERCARD"
    assert sheets["hold"].iloc[0]["comment"] == "keep"


def test_registry_does_not_overwrite_otlezka_sheet(registry_fs):
    wb_path = registry_fs / "with_otlezka.xlsx"
    with pd.ExcelWriter(wb_path, engine="openpyxl") as writer:
        pd.DataFrame(columns=ALL_RESULTS_COLUMNS).to_excel(
            writer, sheet_name=SHEET_ALL_RESULTS, index=False
        )
        pd.DataFrame(columns=RUNS_COLUMNS).to_excel(writer, sheet_name=SHEET_RUNS, index=False)
        pd.DataFrame(
            [{"partner": "UserPartner", "Полные дни": 99, "comment": "manual"}]
        ).to_excel(writer, sheet_name=SHEET_OTLEZKA, index=False)
    data = _compose_once(registry_fs, existing=wb_path.read_bytes())
    sheets = _read_registry(data)
    assert sheets[SHEET_OTLEZKA].iloc[0]["partner"] == "UserPartner"
    assert int(sheets[SHEET_OTLEZKA].iloc[0]["Полные дни"]) == 99
    assert sheets[SHEET_OTLEZKA].iloc[0]["comment"] == "manual"


def _style_all_results_data_row(ws, row_idx: int) -> None:
    from openpyxl.styles import Alignment, Border, Side

    card_col = ALL_RESULTS_COLUMNS.index("card") + 1
    status_col = ALL_RESULTS_COLUMNS.index("status") + 1
    comment_col = ALL_RESULTS_COLUMNS.index("comment") + 1
    thin = Side(style="thin")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center")
    for col_idx in range(1, len(ALL_RESULTS_COLUMNS) + 1):
        cell = ws.cell(row=row_idx, column=col_idx)
        cell.border = border
        if col_idx in {card_col, status_col, comment_col}:
            cell.alignment = center
    ws.cell(row=row_idx, column=card_col).number_format = "@"


def _style_runs_data_row(ws, row_idx: int) -> None:
    from openpyxl.styles import Border, Side

    thin = Side(style="thin")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for col_idx in range(1, len(RUNS_COLUMNS) + 1):
        ws.cell(row=row_idx, column=col_idx).border = border


def _store_workbook_bytes(wb) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


def test_registry_save_copies_all_results_row_style(registry_fs):
    styled = registry_fs / "styled.xlsx"
    create_styled_registry_workbook(styled)
    first = _compose_once(registry_fs, existing=styled.read_bytes())
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(first))
    _style_all_results_data_row(wb[SHEET_ALL_RESULTS], 2)
    styled_bytes = _store_workbook_bytes(wb)
    second = _compose_once(registry_fs, existing=styled_bytes)
    wb2 = load_workbook(io.BytesIO(second))
    ws2 = wb2[SHEET_ALL_RESULTS]
    card_col = ALL_RESULTS_COLUMNS.index("card") + 1
    status_col = ALL_RESULTS_COLUMNS.index("status") + 1
    new_row = 3
    assert ws2.cell(row=new_row, column=card_col).border.left.style == "thin"
    assert ws2.cell(row=new_row, column=status_col).alignment.horizontal == "center"
    assert ws2.cell(row=new_row, column=card_col).number_format == "@"
    assert ws2.column_dimensions["A"].width == 22.5
    assert ws2.freeze_panes == "A2"
    wb2.close()


def test_registry_save_copies_runs_row_style(registry_fs):
    styled = registry_fs / "styled.xlsx"
    create_styled_registry_workbook(styled)
    first = _compose_once(registry_fs, existing=styled.read_bytes())
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(first))
    _style_runs_data_row(wb[SHEET_RUNS], 2)
    styled_bytes = _store_workbook_bytes(wb)
    second = _compose_once(registry_fs, existing=styled_bytes)
    wb2 = load_workbook(io.BytesIO(second))
    ws_runs = wb2[SHEET_RUNS]
    assert ws_runs.cell(row=3, column=1).border.left.style == "thin"
    assert ws_runs.cell(row=3, column=len(RUNS_COLUMNS)).border.left.style == "thin"
    wb2.close()


def _operation_date_cell(ws, row_idx: int = 2):
    col = ALL_RESULTS_COLUMNS.index(OPERATION_DATE_COLUMN) + 1
    return ws.cell(row=row_idx, column=col)


def _disable_date_cell(ws, row_idx: int = 2):
    col = ALL_RESULTS_COLUMNS.index(DISABLE_DATE_COLUMN) + 1
    return ws.cell(row=row_idx, column=col)


def _all_results_row(**overrides) -> dict:
    row = {col: "" for col in ALL_RESULTS_COLUMNS}
    row.update(overrides)
    return row


def test_operation_date_written_as_excel_date(registry_fs):
    data = _compose_once(registry_fs)
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data))
    cell = _operation_date_cell(wb[SHEET_ALL_RESULTS])
    assert isinstance(cell.value, (date, datetime))
    assert not isinstance(cell.value, str)
    assert cell.number_format == OPERATION_DATE_NUMBER_FORMAT
    wb.close()


def test_operation_date_preserved_on_save(registry_fs):
    first = _compose_once(registry_fs)
    second = _compose_once(registry_fs, existing=first)
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(second))
    ws = wb[SHEET_ALL_RESULTS]
    for row_idx in (2, 3):
        cell = _operation_date_cell(ws, row_idx)
        assert isinstance(cell.value, (date, datetime))
        assert cell.number_format == OPERATION_DATE_NUMBER_FORMAT
    wb.close()


@pytest.mark.parametrize(
    "legacy_value",
    ["08.06.2026", "08.06.2026 00:00:00"],
)
def test_legacy_string_operation_date_normalized_on_save(tmp_path, legacy_value: str):
    from openpyxl import Workbook

    wb_path = tmp_path / "legacy_op_date.xlsx"
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = SHEET_ALL_RESULTS
    for col_idx, name in enumerate(ALL_RESULTS_COLUMNS, 1):
        ws.cell(row=1, column=col_idx, value=name)
    ws.cell(row=2, column=1, value=legacy_value)
    ws.cell(row=2, column=ALL_RESULTS_COLUMNS.index(DISABLE_DATE_COLUMN) + 1, value="01.06.2026 10:00:00")
    ws.cell(row=2, column=ALL_RESULTS_COLUMNS.index("card") + 1, value="4111")
    ws.cell(row=2, column=ALL_RESULTS_COLUMNS.index("action") + 1, value="remove_partner")
    ws.cell(row=2, column=ALL_RESULTS_COLUMNS.index("partner") + 1, value="Ostin")
    ws.cell(row=2, column=ALL_RESULTS_COLUMNS.index("status") + 1, value="OK")
    wb.create_sheet(SHEET_RUNS)
    wb.save(wb_path)
    wb.close()

    all_df = pd.DataFrame(
        [
            _all_results_row(
                **{
                    OPERATION_DATE_COLUMN: legacy_value,
                    DISABLE_DATE_COLUMN: "01.06.2026 10:00:00",
                    "card": "4111",
                    "partner": "Ostin",
                    "action": "remove_partner",
                    "status": "OK",
                }
            )
        ]
    )
    save_registry_workbook(
        wb_path,
        all_results=all_df,
        runs=pd.DataFrame(columns=RUNS_COLUMNS),
        hold_exists=True,
        otlezka_exists=True,
        is_new_file=False,
    )
    from openpyxl import load_workbook

    wb2 = load_workbook(wb_path)
    cell = _operation_date_cell(wb2[SHEET_ALL_RESULTS])
    assert isinstance(cell.value, (date, datetime))
    assert not isinstance(cell.value, str)
    assert cell.number_format == OPERATION_DATE_NUMBER_FORMAT
    if isinstance(cell.value, datetime):
        assert cell.value.time() == datetime.min.time()
    wb2.close()


def test_remove_partner_ok_disable_date_remains_timestamp(registry_fs):
    data = _compose_once(registry_fs)
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data))
    cell = _disable_date_cell(wb[SHEET_ALL_RESULTS])
    assert cell.value is not None
    rendered = (
        cell.value.strftime("%d.%m.%Y %H:%M:%S")
        if isinstance(cell.value, datetime)
        else str(cell.value)
    )
    assert "09:00:00" in rendered
    assert cell.number_format != OPERATION_DATE_NUMBER_FORMAT
    wb.close()


def test_add_partner_result_has_empty_disable_date_in_registry(registry_fs):
    result_path = registry_fs / "add_partner.xlsx"
    processed = datetime(2026, 6, 8, 12, 0, 0, tzinfo=MSK)
    operation_date, disable_date = result_row_dates("add_partner", "OK", processed)
    assert disable_date == ""
    pd.DataFrame(
        {
            OPERATION_DATE_COLUMN: [operation_date],
            DISABLE_DATE_COLUMN: [disable_date],
            "card": ["4111111111111111"],
            "action": ["add_partner"],
            "value": ["Ostin"],
            "status": ["OK"],
            "comment": ["added"],
        }
    ).to_excel(result_path, index=False)
    dest = registry_fs / "registry.xlsx"
    data = _compose_registry_workbook(dest, result_path)
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data))
    ws = wb[SHEET_ALL_RESULTS]
    row_idx = ws.max_row
    op_cell = _operation_date_cell(ws, row_idx)
    disable_cell = _disable_date_cell(ws, row_idx)
    assert isinstance(op_cell.value, (date, datetime))
    assert op_cell.number_format == OPERATION_DATE_NUMBER_FORMAT
    assert disable_cell.value in (None, "")
    wb.close()
