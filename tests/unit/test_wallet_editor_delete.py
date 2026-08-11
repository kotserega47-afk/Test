"""Unit tests for Wallet Editor action=delete."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from automation.audit import Stats
from automation.engine import (
    ALLOWED_ACTIONS,
    DELETE_BUTTON_TEXT,
    DELETE_CONFIRM_OK_TEXT,
    DELETE_CONFIRM_TEXT,
    RESULT_DRY_RUN_WOULD_DELETE,
    RESULT_FAIL_CONFIRM_DIALOG_NOT_FOUND,
    RESULT_FAIL_DELETE_BUTTON_NOT_FOUND,
    RESULT_FAIL_DELETE_CONFLICT,
    RESULT_FAIL_DELETE_TIMEOUT,
    RESULT_FAIL_OPEN_CARD,
    RESULT_FAIL_STILL_EXISTS,
    RESULT_OK_DELETED,
    RESULT_SKIP_NOT_FOUND,
    RESULT_STOP_BEFORE_DELETE,
    OpenCardStageError,
    _click_delete_confirm_ok,
    _find_delete_confirm_dialog,
    _find_wallet_delete_button,
    _prepare_df,
    _validate_delete_conflicts,
    ensure_wallet_deleted,
    timing_outcome_from_result,
)
from automation.runtime import RunConfig
from automation.add_wallet_contract import ExcelRouting, detect_excel_routing
from integrations.wallet_editor_registry_lifecycle import (
    ACTION_DELETE,
    partner_from_row,
    result_row_dates,
)
from core.datetime_utils import now_msk


def _cfg(**kwargs) -> RunConfig:
    defaults = {
        "login": "u",
        "password": "p",
        "auth_state_path": "/tmp/we_auth.json",
        "dry_run": False,
        "stop_before_delete": False,
        "headless": True,
    }
    defaults.update(kwargs)
    return RunConfig(**defaults)


def test_delete_is_allowed_action():
    assert "delete" in ALLOWED_ACTIONS


def test_prepare_df_accepts_delete_without_value_column(tmp_path):
    path = tmp_path / "delete.xlsx"
    pd.DataFrame(
        {"card": ["9860246700001620"], "action": ["delete"]}
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "delete"
    assert df.iloc[0]["value"] == ""
    assert "value" in df.columns


def test_prepare_df_delete_case_and_whitespace(tmp_path):
    path = tmp_path / "delete_case.xlsx"
    pd.DataFrame(
        {
            "card": ["9860246700001620", "9860246700001621", "9860246700001622"],
            "action": ["DELETE", " Delete ", "DeLeTe"],
        }
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert list(df["action"]) == ["delete", "delete", "delete"]


def test_prepare_df_delete_ignores_optional_value(tmp_path):
    path = tmp_path / "delete_value.xlsx"
    pd.DataFrame(
        {
            "card": ["9860246700001620"],
            "action": ["delete"],
            "value": ["ignored"],
        }
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "delete"
    assert df.iloc[0]["value"] == "ignored"  # kept but unused by delete


def test_prepare_df_still_requires_value_for_legacy_compat_when_present(tmp_path):
    path = tmp_path / "legacy.xlsx"
    pd.DataFrame(
        {
            "card": ["4111111111111111"],
            "action": ["remove_partner"],
            "value": ["Ostin"],
        }
    ).to_excel(path, index=False)

    df = _prepare_df(str(path))
    assert df.iloc[0]["action"] == "remove_partner"
    assert df.iloc[0]["value"] == "Ostin"


def test_delete_conflict_with_other_action():
    df = pd.DataFrame(
        {
            "card": ["4111111111111111", "4111111111111111"],
            "action": ["delete", "remove_partner"],
            "value": ["", "Ostin"],
            "status": ["", ""],
            "comment": ["", ""],
        }
    )
    n = _validate_delete_conflicts(df)
    assert n == 2
    assert list(df["status"]) == [RESULT_FAIL_DELETE_CONFLICT, RESULT_FAIL_DELETE_CONFLICT]


def test_delete_conflict_duplicate_delete_rows():
    df = pd.DataFrame(
        {
            "card": ["4111111111111111", "4111111111111111"],
            "action": ["delete", "delete"],
            "value": ["", ""],
            "status": ["", ""],
            "comment": ["", ""],
        }
    )
    n = _validate_delete_conflicts(df)
    assert n == 2
    assert (df["status"] == RESULT_FAIL_DELETE_CONFLICT).all()


def test_delete_alone_no_conflict():
    df = pd.DataFrame(
        {
            "card": ["4111111111111111"],
            "action": ["delete"],
            "value": [""],
            "status": [""],
            "comment": [""],
        }
    )
    assert _validate_delete_conflicts(df) == 0
    assert df.iloc[0]["status"] == ""


def test_routing_card_action_delete_is_disable(tmp_path):
    path = tmp_path / "any_name.xlsx"
    pd.DataFrame(
        {"card": ["9860246700001620"], "action": ["delete"]}
    ).to_excel(path, index=False)

    routing, err = detect_excel_routing(str(path), original_filename="batch.xlsx")
    assert routing == ExcelRouting.DISABLE
    assert err is None


def test_ensure_wallet_deleted_skip_not_found():
    page = MagicMock()
    with patch("automation.engine.card_exists_strict", return_value=False):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())
    assert result == RESULT_SKIP_NOT_FOUND


def test_ensure_wallet_deleted_dry_run_does_not_open_or_click():
    page = MagicMock()
    with (
        patch("automation.engine.card_exists_strict", return_value=True) as exists,
        patch("automation.engine.open_card_strict") as open_card,
        patch("automation.engine._find_wallet_delete_button") as find_btn,
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg(dry_run=True))
    assert result == RESULT_DRY_RUN_WOULD_DELETE
    exists.assert_called_once()
    open_card.assert_not_called()
    find_btn.assert_not_called()


def test_ensure_wallet_deleted_fail_open_card():
    page = MagicMock()
    with (
        patch("automation.engine.card_exists_strict", return_value=True),
        patch(
            "automation.engine.open_card_strict",
            side_effect=OpenCardStageError("modal_container", "9860246700001620"),
        ),
        patch("automation.engine._close_stale_modal"),
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())
    assert result == RESULT_FAIL_OPEN_CARD


def test_ensure_wallet_deleted_button_not_found():
    page = MagicMock()
    with (
        patch("automation.engine.card_exists_strict", return_value=True),
        patch("automation.engine.open_card_strict"),
        patch("automation.engine._find_wallet_delete_button", return_value=None),
        patch("automation.engine._close_stale_modal"),
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())
    assert result == RESULT_FAIL_DELETE_BUTTON_NOT_FOUND


def test_ensure_wallet_deleted_confirm_dialog_not_found():
    page = MagicMock()
    delete_btn = MagicMock()
    with (
        patch("automation.engine.card_exists_strict", return_value=True),
        patch("automation.engine.open_card_strict"),
        patch("automation.engine._find_wallet_delete_button", return_value=delete_btn),
        patch("automation.engine._wait_delete_confirm_dialog", return_value=None),
        patch("automation.engine._close_stale_modal"),
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())
    assert result == RESULT_FAIL_CONFIRM_DIALOG_NOT_FOUND
    delete_btn.click.assert_called_once()


def test_ensure_wallet_deleted_ok_when_form_closes_and_card_gone():
    page = MagicMock()
    delete_btn = MagicMock()
    dialog = MagicMock()

    with (
        patch("automation.engine.card_exists_strict", side_effect=[True, False]),
        patch("automation.engine.open_card_strict"),
        patch("automation.engine._find_wallet_delete_button", return_value=delete_btn),
        patch("automation.engine._wait_delete_confirm_dialog", return_value=dialog),
        patch("automation.engine._click_delete_confirm_ok") as click_ok,
        patch("automation.engine._wait_form_hidden_after_delete", return_value=True),
        patch("automation.engine._wait_confirm_dialog_gone"),
        patch("automation.engine.ensure_wallet_search_ready", return_value=True),
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())

    assert result == RESULT_OK_DELETED
    click_ok.assert_called_once_with(dialog)
    delete_btn.click.assert_called_once()


def test_form_timeout_recovery_card_gone_returns_ok_deleted():
    page = MagicMock()
    delete_btn = MagicMock()
    dialog = MagicMock()
    recover = MagicMock()

    with (
        patch("automation.engine.card_exists_strict", side_effect=[True, False]),
        patch("automation.engine.open_card_strict"),
        patch("automation.engine._find_wallet_delete_button", return_value=delete_btn),
        patch("automation.engine._wait_delete_confirm_dialog", return_value=dialog),
        patch("automation.engine._click_delete_confirm_ok") as click_ok,
        patch("automation.engine._wait_form_hidden_after_delete", return_value=False),
        patch("automation.engine._find_delete_confirm_dialog", return_value=None),
        patch("automation.engine._wallet_form_visible", side_effect=[True, False, False]),
        patch("automation.engine._close_stale_modal", recover),
        patch("automation.engine.ensure_wallet_search_ready", return_value=True),
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())

    assert result == RESULT_OK_DELETED
    recover.assert_called()
    click_ok.assert_called_once()  # OK only once — no retry on recovery


def test_form_timeout_recovery_card_still_exists():
    page = MagicMock()
    delete_btn = MagicMock()
    dialog = MagicMock()

    with (
        patch("automation.engine.card_exists_strict", side_effect=[True, True]),
        patch("automation.engine.open_card_strict"),
        patch("automation.engine._find_wallet_delete_button", return_value=delete_btn),
        patch("automation.engine._wait_delete_confirm_dialog", return_value=dialog),
        patch("automation.engine._click_delete_confirm_ok") as click_ok,
        patch("automation.engine._wait_form_hidden_after_delete", return_value=False),
        patch("automation.engine._find_delete_confirm_dialog", return_value=None),
        patch("automation.engine._wallet_form_visible", return_value=False),
        patch("automation.engine.ensure_wallet_search_ready", return_value=True),
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())

    assert result == RESULT_FAIL_STILL_EXISTS
    click_ok.assert_called_once()


def test_form_timeout_search_impossible_returns_delete_timeout():
    page = MagicMock()
    delete_btn = MagicMock()
    dialog = MagicMock()

    with (
        patch("automation.engine.card_exists_strict", return_value=True),
        patch("automation.engine.open_card_strict"),
        patch("automation.engine._find_wallet_delete_button", return_value=delete_btn),
        patch("automation.engine._wait_delete_confirm_dialog", return_value=dialog),
        patch("automation.engine._click_delete_confirm_ok") as click_ok,
        patch("automation.engine._wait_form_hidden_after_delete", return_value=False),
        patch("automation.engine._find_delete_confirm_dialog", return_value=None),
        patch("automation.engine._wallet_form_visible", return_value=True),
        patch("automation.engine._close_stale_modal", side_effect=Exception("stuck")),
        patch("automation.engine.ensure_wallet_search_ready", return_value=False),
    ):
        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())

    assert result == RESULT_FAIL_DELETE_TIMEOUT
    click_ok.assert_called_once()


def test_recovery_does_not_click_ok_or_delete_again():
    page = MagicMock()
    from automation.engine import classify_after_delete_confirm

    click_ok = MagicMock()
    delete_btn = MagicMock()

    with (
        patch("automation.engine._wait_form_hidden_after_delete", return_value=False),
        patch("automation.engine._find_delete_confirm_dialog", side_effect=[MagicMock(), None, None]),
        patch("automation.engine._dismiss_confirm_dialog_without_ok") as dismiss,
        patch("automation.engine._wallet_form_visible", return_value=False),
        patch("automation.engine.ensure_wallet_search_ready", return_value=True),
        patch("automation.engine.card_exists_strict", return_value=False),
        patch("automation.engine._click_delete_confirm_ok", click_ok),
        patch("automation.engine._find_wallet_delete_button", return_value=delete_btn),
    ):
        result = classify_after_delete_confirm(page, "9860246700001620")

    assert result == RESULT_OK_DELETED
    dismiss.assert_called_once()
    click_ok.assert_not_called()
    delete_btn.click.assert_not_called()


def test_timeout_on_first_card_does_not_block_second_delete():
    """After stuck first delete, second card still gets a clean ensure_wallet_deleted call."""
    results = []

    def fake_delete(page_arg, card, cfg):
        if card.endswith("1620"):
            results.append(("first", card))
            return RESULT_FAIL_DELETE_TIMEOUT
        results.append(("second", card))
        return RESULT_OK_DELETED

    calls_ready = []

    def fake_ready(page_arg, *, allow_goto=True):
        calls_ready.append(allow_goto)
        return True

    df = pd.DataFrame(
        {
            "card": ["9860246700001620", "9860246700009999"],
            "action": ["delete", "delete"],
            "value": ["", ""],
            "status": ["", ""],
            "comment": ["", ""],
        }
    )

    pw = MagicMock()
    browser = MagicMock()
    context = MagicMock()
    page_obj = MagicMock()
    pw.chromium.launch.return_value = browser
    browser.new_context.return_value = context
    context.new_page.return_value = page_obj

    with (
        patch("automation.engine._prepare_df", return_value=df.copy()),
        patch("automation.engine._ensure_result_date_columns"),
        patch("automation.engine._validate_set_direction_pre_playwright", return_value=0),
        patch("automation.engine._validate_delete_conflicts", return_value=0),
        patch("automation.engine.load_hold_pairs_snapshot", return_value=MagicMock()),
        patch("automation.engine._apply_add_partner_hold_precheck"),
        patch("automation.engine.require_wallet_editor_antares_credentials"),
        patch("automation.engine.wallet_editor_playwright_slow_mo_ms", return_value=0),
        patch("automation.engine.sync_playwright") as sp,
        patch("automation.engine._ensure_logged_in"),
        patch("automation.engine.ensure_wallet_deleted", side_effect=fake_delete),
        patch("automation.engine.ensure_wallet_search_ready", side_effect=fake_ready),
        patch("automation.engine._write_result", return_value="/tmp/out.xlsx"),
        patch("automation.engine.close_playwright_stack"),
        patch("os.path.exists", return_value=False),
    ):
        sp.return_value.__enter__.return_value = pw
        sp.return_value.__exit__.return_value = None

        from automation.engine import run

        _out, stats = run("/tmp/in.xlsx", _cfg())

    assert [r[0] for r in results] == ["first", "second"]
    assert len(calls_ready) >= 2  # cleanup after each delete card
    assert stats.ok == 1
    assert stats.fail == 1


def test_ensure_wallet_deleted_technical_on_unexpected_error():
    page = MagicMock()
    with (
        patch("automation.engine.card_exists_strict", side_effect=RuntimeError("boom")),
    ):
        from automation.engine import RESULT_FAIL_TECHNICAL

        result = ensure_wallet_deleted(page, "9860246700001620", _cfg())
    assert result == RESULT_FAIL_TECHNICAL


def test_timing_outcome_from_delete_results():
    assert timing_outcome_from_result(RESULT_OK_DELETED) == "ok"
    assert timing_outcome_from_result(RESULT_SKIP_NOT_FOUND) == "skip"
    assert timing_outcome_from_result(RESULT_DRY_RUN_WOULD_DELETE) == "skip"
    assert timing_outcome_from_result(RESULT_STOP_BEFORE_DELETE) == "skip"
    assert timing_outcome_from_result(RESULT_FAIL_DELETE_TIMEOUT) == "fail"
    assert timing_outcome_from_result(RESULT_FAIL_STILL_EXISTS) == "fail"
    assert timing_outcome_from_result(RESULT_FAIL_OPEN_CARD) == "fail"
    assert timing_outcome_from_result("removed") == "ok"
    assert timing_outcome_from_result("skip: not selected") == "skip"


def test_log_step_duration_uses_caller_outcome_for_fail_delete(caplog):
    import logging

    from automation.audit import log_step_duration

    caplog.set_level(logging.INFO)
    with log_step_duration(profile="WALTER", scope="disable", step="action:delete") as timing:
        timing.outcome = timing_outcome_from_result(RESULT_FAIL_DELETE_TIMEOUT)
    assert "step=action:delete" in caplog.text
    assert "outcome=fail" in caplog.text


def test_log_step_duration_ok_deleted_and_skip(caplog):
    import logging

    from automation.audit import log_step_duration

    caplog.set_level(logging.INFO)
    with log_step_duration(profile="WALTER", scope="disable", step="action:delete") as timing:
        timing.outcome = timing_outcome_from_result(RESULT_OK_DELETED)
    assert "outcome=ok" in caplog.text

    with log_step_duration(profile="WALTER", scope="disable", step="action:delete") as timing:
        timing.outcome = timing_outcome_from_result(RESULT_SKIP_NOT_FOUND)
    assert "outcome=skip" in caplog.text


def test_click_delete_confirm_ok_exact_match_only():
    ok = MagicMock()
    ok.is_visible.return_value = True
    ok.inner_text.return_value = DELETE_CONFIRM_OK_TEXT
    cancel = MagicMock()
    cancel.is_visible.return_value = True
    cancel.inner_text.return_value = "Отмена"
    almost = MagicMock()
    almost.is_visible.return_value = True
    almost.inner_text.return_value = "Okay"

    buttons = MagicMock()
    buttons.count.return_value = 3
    buttons.nth.side_effect = [cancel, almost, ok]

    dialog = MagicMock()
    dialog.locator.return_value = buttons

    _click_delete_confirm_ok(dialog)
    ok.click.assert_called_once()
    cancel.click.assert_not_called()
    almost.click.assert_not_called()


def test_find_delete_confirm_dialog_requires_confirm_text():
    other = MagicMock()
    other.is_visible.return_value = True
    other.inner_text.return_value = "Сохранить изменения?"

    target = MagicMock()
    target.is_visible.return_value = True
    target.inner_text.return_value = f"Внимание\n{DELETE_CONFIRM_TEXT}\n"

    dialogs = MagicMock()
    dialogs.count.return_value = 2
    dialogs.nth.side_effect = [other, target]

    page = MagicMock()
    page.locator.return_value = dialogs

    found = _find_delete_confirm_dialog(page)
    assert found is target


def test_find_wallet_delete_button_requires_danger_and_exact_text():
    danger = MagicMock()
    danger.is_visible.return_value = True
    danger.inner_text.return_value = DELETE_BUTTON_TEXT
    danger.get_attribute.return_value = "btn btn-danger"

    plain = MagicMock()
    plain.is_visible.return_value = True
    plain.inner_text.return_value = DELETE_BUTTON_TEXT
    plain.get_attribute.return_value = "btn btn-secondary"
    plain.evaluate.return_value = "rgb(100,100,100)|rgb(0,0,0)"

    buttons = MagicMock()
    buttons.count.return_value = 2
    buttons.nth.side_effect = [plain, danger]

    modal = MagicMock()
    modal.is_visible.return_value = True
    modal.locator.return_value = buttons

    page = MagicMock()
    page.locator.return_value = modal

    found = _find_wallet_delete_button(page)
    assert found is danger


def test_stats_inc_delete_result_codes():
    s = Stats()
    s.inc(RESULT_OK_DELETED)
    s.inc(RESULT_SKIP_NOT_FOUND)
    s.inc(RESULT_DRY_RUN_WOULD_DELETE)
    s.inc(RESULT_FAIL_STILL_EXISTS)
    s.inc("removed")
    assert s.ok == 2
    assert s.skip == 2
    assert s.fail == 1


def test_registry_delete_does_not_set_disable_date_or_partner():
    processed = now_msk()
    op, disable = result_row_dates(ACTION_DELETE, RESULT_OK_DELETED, processed)
    assert op
    assert disable == ""
    assert partner_from_row(ACTION_DELETE, "") == ""
    assert partner_from_row("delete", "anything") == ""


def test_result_excel_and_telegram_path_for_delete(tmp_path, monkeypatch):
    """Delete routes through disable worker path: engine result + Stats summary."""
    from automation import worker as worker_mod
    from automation.runtime import WalletEditorTask

    result_xlsx = tmp_path / "wallet_editor_result_delete_WALTER.xlsx"
    pd.DataFrame(
        {
            "card": ["9860246700001620"],
            "action": ["delete"],
            "value": [""],
            "status": [RESULT_OK_DELETED],
            "comment": [RESULT_OK_DELETED],
        }
    ).to_excel(result_xlsx, index=False)

    sent = {}

    def fake_run(file_path, cfg):
        return str(result_xlsx), Stats(ok=1, fail=0, skip=0)

    monkeypatch.setattr(worker_mod, "run", fake_run)
    monkeypatch.setattr(worker_mod, "manual_sync_enabled", lambda: False)
    monkeypatch.setattr(
        worker_mod,
        "send_text",
        lambda **kw: sent.setdefault("text", kw.get("text")),
    )
    monkeypatch.setattr(
        worker_mod,
        "send_document",
        lambda **kw: sent.setdefault("doc", kw.get("path")),
    )
    monkeypatch.setattr(
        worker_mod,
        "prepare_registry_outbox_and_schedule",
        lambda *a, **k: sent.setdefault("registry", True),
    )
    monkeypatch.setattr(worker_mod, "delayed_cleanup", lambda *a, **k: None)
    monkeypatch.setattr(
        worker_mod,
        "build_wallet_editor_result_path",
        lambda *a, **k: str(result_xlsx),
    )

    task = WalletEditorTask(
        file_path=str(tmp_path / "in.xlsx"),
        chat_id=1,
        telegram_user_id=2,
        operator_profile="WALTER",
        source_file_name="delete.xlsx",
        login="u",
        password="p",
        auth_state_path=str(tmp_path / "auth.json"),
    )
    worker_mod._run_disable_task("WALTER", task)

    assert "OK=1" in sent["text"]
    assert sent["doc"] == str(result_xlsx)
    assert sent["registry"] is True
