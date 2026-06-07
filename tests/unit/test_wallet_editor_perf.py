"""WE-PERF-1: timing instrumentation, slow_mo env, auth/micro-sleep optimizations."""
from __future__ import annotations

import logging
import re
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from automation.audit import log_step_duration, log_timing, mask_card
from automation.engine import CARD_INPUT, _ensure_logged_in, run
from automation.runtime import RunConfig, wallet_editor_playwright_slow_mo_ms
from integrations.wallet_editor_auto_enable_executor import execute_enable_batch
from integrations.wallet_editor_auto_enable_eligibility import CandidateRow
from integrations.wallet_editor_auto_enable_settings import AutoEnableSettings


def _settings() -> AutoEnableSettings:
    return AutoEnableSettings(
        enabled=True,
        dry_run=False,
        approval_required=False,
        max_rows_per_batch=200,
        max_rows_per_run=0,
        seconds_per_card_timeout=10,
        batch_timeout_buffer_seconds=300,
        working_statuses=("готов к работе",),
        auto_return_statuses=(),
        auto_return_target_status="Готов к работе",
        allowed_statuses_for_enable=("готов к работе",),
        deprecated_working_statuses_fallback=False,
        include_overdue=True,
        telegram_route_report="wallet_editor_auto_enable",
        telegram_route_alert="wallet_editor_auto_enable_alert",
    )


def _candidate() -> CandidateRow:
    return CandidateRow(
        card="4111111111111111",
        partner="P1",
        disable_at="01.06.2026 10:00:00",
        enable_status="К ВКЛЮЧЕНИЮ",
        vklyucheno="",
        source_row_index=0,
    )


def test_slow_mo_env_default_zero(monkeypatch):
    monkeypatch.delenv("WALLET_EDITOR_PLAYWRIGHT_SLOW_MO_MS", raising=False)
    assert wallet_editor_playwright_slow_mo_ms() == 0


def test_slow_mo_env_rollback_600(monkeypatch):
    monkeypatch.setenv("WALLET_EDITOR_PLAYWRIGHT_SLOW_MO_MS", "600")
    assert wallet_editor_playwright_slow_mo_ms() == 600


def test_slow_mo_invalid_env_safe_default(monkeypatch, caplog):
    monkeypatch.setenv("WALLET_EDITOR_PLAYWRIGHT_SLOW_MO_MS", "not-a-number")
    assert wallet_editor_playwright_slow_mo_ms() == 0
    assert "invalid" in caplog.text.lower()


def test_we_timing_log_format(caplog):
    caplog.set_level(logging.INFO)
    log_timing(
        profile="DENIS",
        scope="disable",
        step="open_card",
        duration_ms=123,
        outcome="ok",
        card="4111111111111111",
    )
    assert re.search(
        r"\[WE/timing\] profile=DENIS scope=disable step=open_card card=\*\*\*1111 duration_ms=123 outcome=ok",
        caplog.text,
    )


def test_mask_card_masks_digits():
    assert mask_card("4111111111111111") == "***1111"
    assert mask_card("12") == "***"


def test_engine_run_emits_total_and_card_timing(tmp_path, caplog):
    caplog.set_level(logging.INFO)
    xlsx = tmp_path / "input.xlsx"
    pd.DataFrame(
        [{"card": "4111", "action": "remove_partner", "value": "P1"}]
    ).to_excel(xlsx, index=False)

    cfg = RunConfig(
        login="u",
        password="p",
        auth_state_path=str(tmp_path / "auth.json"),
        operator_profile="DENIS",
        result_file_path=str(tmp_path / "out.xlsx"),
    )

    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_browser = MagicMock()
    mock_playwright = MagicMock()
    mock_playwright.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    with patch("automation.engine.sync_playwright") as sp:
        sp.return_value.__enter__.return_value = mock_playwright
        with patch("automation.engine._ensure_logged_in"):
            with patch("automation.engine.open_card"):
                with patch(
                    "automation.engine.ensure_partner_removed",
                    return_value="skip: not selected",
                ):
                    run(str(xlsx), cfg)

    text = caplog.text
    assert "[WE/timing] profile=DENIS scope=disable step=run_total" in text
    assert "[WE/timing] profile=DENIS scope=disable step=card" in text


def test_auto_enable_batch_emits_timing(caplog):
    caplog.set_level(logging.INFO)
    cfg = RunConfig(
        login="u",
        password="p",
        auth_state_path="/tmp/auth.json",
        operator_profile="CONVERSION_AUTO",
    )

    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_browser = MagicMock()
    mock_playwright = MagicMock()
    mock_playwright.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    with patch("integrations.wallet_editor_auto_enable_executor.sync_playwright") as sp:
        sp.return_value.__enter__.return_value = mock_playwright
        with patch("integrations.wallet_editor_auto_enable_executor._ensure_logged_in"):
            with patch(
                "integrations.wallet_editor_auto_enable_executor.process_enable_candidate",
                return_value=MagicMock(registry_value="OK"),
            ):
                execute_enable_batch([_candidate()], _settings(), cfg=cfg)

    assert "[WE/timing] profile=CONVERSION_AUTO scope=auto_enable step=batch" in caplog.text


def test_slow_mo_passed_to_chromium_launch(monkeypatch):
    monkeypatch.setenv("WALLET_EDITOR_PLAYWRIGHT_SLOW_MO_MS", "600")
    cfg = RunConfig(login="u", password="p", operator_profile="DENIS")

    mock_playwright = MagicMock()
    mock_browser = MagicMock()
    mock_playwright.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value.new_page.return_value = MagicMock()

    with patch("integrations.wallet_editor_auto_enable_executor.sync_playwright") as sp:
        sp.return_value.__enter__.return_value = mock_playwright
        with patch("integrations.wallet_editor_auto_enable_executor._ensure_logged_in"):
            with patch(
                "integrations.wallet_editor_auto_enable_executor.process_enable_candidate",
                return_value=MagicMock(registry_value="OK"),
            ):
                execute_enable_batch([_candidate()], _settings(), cfg=cfg)

    assert mock_playwright.chromium.launch.call_args.kwargs["slow_mo"] == 600


def test_ensure_logged_in_no_fixed_sleep_when_ui_ready():
    page = MagicMock()
    page.url = "https://antares.plus/lkcard/#/wallet"
    context = MagicMock()
    cfg = RunConfig(login="u", password="p")

    card_input = MagicMock()
    page.locator.return_value = card_input

    _ensure_logged_in(page, context, cfg)

    page.wait_for_timeout.assert_not_called()
    card_input.wait_for.assert_called_once_with(state="visible", timeout=15000)


def test_ensure_logged_in_cold_login_uses_networkidle_not_fixed_sleep():
    page = MagicMock()
    page.url = "https://antares.plus/lkcard/#/wallet"
    context = MagicMock()
    cfg = RunConfig(login="u", password="p", auth_state_path="/tmp/auth.json")

    card_input = MagicMock()
    page.locator.return_value = card_input

    def goto_side_effect(url: str, *args, **kwargs):
        if "#/wallet" in url:
            page.url = "https://antares.plus/lkcard/#/login"
        return None

    def click_side_effect(*args, **kwargs):
        page.url = "https://antares.plus/lkcard/#/wallet"
        return None

    page.goto.side_effect = goto_side_effect
    page.click.side_effect = click_side_effect

    _ensure_logged_in(page, context, cfg)

    page.wait_for_timeout.assert_not_called()
    page.wait_for_load_state.assert_called_once()
    assert card_input.wait_for.call_count >= 1
    context.storage_state.assert_called_once()


def test_disable_flow_outcomes_unchanged(tmp_path):
    """Golden-style: skip remove_partner still yields SKIP row."""
    xlsx = tmp_path / "input.xlsx"
    pd.DataFrame(
        [{"card": "4111", "action": "remove_partner", "value": "P1"}]
    ).to_excel(xlsx, index=False)

    cfg = RunConfig(
        login="u",
        password="p",
        auth_state_path=str(tmp_path / "auth.json"),
        operator_profile="DENIS",
        result_file_path=str(tmp_path / "out.xlsx"),
    )

    mock_playwright = MagicMock()
    mock_browser = MagicMock()
    mock_playwright.chromium.launch.return_value = mock_browser
    mock_context = MagicMock()
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = MagicMock()

    with patch("automation.engine.sync_playwright") as sp:
        sp.return_value.__enter__.return_value = mock_playwright
        with patch("automation.engine._ensure_logged_in"):
            with patch("automation.engine.open_card"):
                with patch(
                    "automation.engine.ensure_partner_removed",
                    return_value="skip: not selected",
                ):
                    out_path, stats = run(str(xlsx), cfg)

    result = pd.read_excel(out_path)
    assert result.iloc[0]["status"] == "SKIP"
    assert stats.skip == 1
    assert stats.ok == 0


def test_auto_enable_outcomes_unchanged():
    from integrations.wallet_editor_auto_enable_executor import (
        ERROR_ALREADY_ADDED,
        REGISTRY_OK,
        process_enable_candidate,
    )

    page = MagicMock()

    outcome = process_enable_candidate(
        page,
        _candidate(),
        settings=_settings(),
        cfg=RunConfig(login="u", password="p"),
        open_card_fn=lambda _p, _c: None,
        get_status_fn=lambda _p: "Готов к работе",
        get_chips_fn=lambda _p: ["Ostin / P1"],
        working_statuses=frozenset({"готов к работе"}),
        auto_return_statuses=frozenset(),
    )

    assert outcome.registry_value == REGISTRY_OK
    assert outcome.error_code == ERROR_ALREADY_ADDED
    assert outcome.saved is False


def test_log_step_duration_records_fail_on_exception(caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(ValueError):
        with log_step_duration(profile="DENIS", scope="disable", step="save", card="4111"):
            raise ValueError("boom")
    assert "outcome=fail" in caplog.text


def _open_card_page_mock(card: str = "9990080818592862") -> MagicMock:
    page = MagicMock()
    modal = MagicMock()
    row = MagicMock()
    row.inner_text.return_value = card
    rows = MagicMock()
    rows.count.return_value = 1
    rows.nth.return_value = row

    def locator(selector: str) -> MagicMock:
        if selector == "#wallet-add-modal___BV_modal_body_":
            return modal
        if selector == "tr.pointer":
            return rows
        return MagicMock()

    page.locator.side_effect = locator
    modal.is_visible.return_value = True
    return page


def test_open_card_logs_modal_stages(caplog):
    from automation.engine import open_card

    caplog.set_level(logging.INFO)
    page = _open_card_page_mock()

    with patch("automation.engine._close_stale_modal"):
        with patch(
            "automation.engine._wait_for_matching_row",
            return_value=0,
        ):
            with patch(
                "automation.engine._wait_modal_card_data_ready",
                return_value="9990080818592862",
            ):
                open_card(page, "9990080818592862")

    text = caplog.text
    assert "[Card] row clicked card=9990080818592862" in text
    assert "[Card] modal container visible card=9990080818592862" in text
    assert "[Card] modal card verified card=9990080818592862" in text


def test_open_card_waits_for_modal_data_before_verify():
    from automation.engine import open_card

    page = _open_card_page_mock()
    order: list[str] = []

    def container(modal, card):  # noqa: ARG001
        order.append("container")

    def data(page_arg, card):  # noqa: ARG001
        order.append("data")
        return "9990080818592862"

    def verify(card, value):  # noqa: ARG001
        order.append("verify")

    with patch("automation.engine._close_stale_modal"):
        with patch("automation.engine._wait_for_matching_row", return_value=0):
            with patch("automation.engine._wait_modal_container_visible", side_effect=container):
                with patch("automation.engine._wait_modal_card_data_ready", side_effect=data):
                    with patch("automation.engine._verify_modal_card_number", side_effect=verify):
                        open_card(page, "9990080818592862")

    assert order == ["container", "data", "verify"]


def test_open_card_settle_ms_from_env(monkeypatch):
    from automation.engine import _wait_modal_card_data_ready

    monkeypatch.setenv("WALLET_EDITOR_OPEN_CARD_SETTLE_MS", "500")
    page = MagicMock()
    page.wait_for_timeout = MagicMock()

    with patch(
        "automation.engine._try_get_modal_card_value_fast",
        side_effect=[None, "9990080818592862"],
    ):
        value = _wait_modal_card_data_ready(page, "9990080818592862")

    assert value == "9990080818592862"
    page.wait_for_timeout.assert_any_call(500)


def test_open_card_settle_ms_can_be_zero(monkeypatch):
    from automation.engine import _wait_modal_card_data_ready

    monkeypatch.setenv("WALLET_EDITOR_OPEN_CARD_SETTLE_MS", "0")
    page = MagicMock()
    page.wait_for_timeout = MagicMock()

    with patch(
        "automation.engine._try_get_modal_card_value_fast",
        return_value="9990080818592862",
    ):
        _wait_modal_card_data_ready(page, "9990080818592862")

    settle_calls = [
        c for c in page.wait_for_timeout.call_args_list if c.args == (500,)
    ]
    assert not settle_calls


def test_open_card_timeout_reports_stage():
    from automation.engine import OpenCardStageError, open_card

    page = _open_card_page_mock()
    modal = page.locator("#wallet-add-modal___BV_modal_body_")
    modal.wait_for.side_effect = TimeoutError("visible timeout")

    with patch("automation.engine._close_stale_modal"):
        with patch("automation.engine._wait_for_matching_row", return_value=0):
            with pytest.raises(OpenCardStageError) as exc_info:
                open_card(page, "9990080818592862")

    assert exc_info.value.stage == "modal_container"


def test_open_card_settle_ms_invalid_env_defaults(monkeypatch):
    from automation.runtime import wallet_editor_open_card_settle_ms

    monkeypatch.setenv("WALLET_EDITOR_OPEN_CARD_SETTLE_MS", "bad")
    assert wallet_editor_open_card_settle_ms() == 500


def test_row_match_normalizes_spaces_and_nbsp():
    from automation.audit import normalize_card_digits, row_matches_card

    card_digits = normalize_card_digits("9990080812990492")
    row_text = "9990\u00a0080\u00a0812\u00a0990\u00a0492"
    assert row_matches_card(row_text, card_digits)


def test_row_match_normalizes_float_suffix_dot_zero():
    from automation.audit import normalize_card_digits, row_matches_card

    card_digits = normalize_card_digits("9990080812990492.0")
    row_text = "9990080812990492"
    assert row_matches_card(row_text, card_digits)
    assert normalize_card_digits("9990080812990492.0") == "9990080812990492"


def test_row_match_waits_until_row_text_loaded(monkeypatch):
    from automation.engine import _wait_for_matching_row

    monkeypatch.setenv("WALLET_EDITOR_ROW_MATCH_TIMEOUT_MS", "1000")
    page = MagicMock()
    page.wait_for_timeout = MagicMock()

    row = MagicMock()
    call_count = [0]

    def inner_text(**_kwargs):
        call_count[0] += 1
        if call_count[0] < 3:
            return ""
        return "9990080812990492"

    row.inner_text.side_effect = inner_text
    rows = MagicMock()
    rows.count.return_value = 1
    rows.nth.return_value = row

    index = _wait_for_matching_row(
        page,
        rows,
        "9990080812990492",
        "9990080812990492",
    )

    assert index == 0
    assert call_count[0] >= 3
    page.wait_for_timeout.assert_called()


def test_row_match_timeout_reports_diagnostics(caplog):
    from automation.engine import OpenCardStageError, _wait_for_matching_row

    caplog.set_level(logging.ERROR)
    page = MagicMock()
    page.wait_for_timeout = MagicMock()

    row = MagicMock()
    row.inner_text.return_value = "partial"
    rows = MagicMock()
    rows.count.return_value = 1
    rows.nth.return_value = row

    with patch("automation.engine.wallet_editor_row_match_timeout_ms", return_value=0):
        with pytest.raises(OpenCardStageError) as exc_info:
            _wait_for_matching_row(
                page,
                rows,
                "9990080812990492",
                "9990080812990492",
            )

    assert exc_info.value.stage == "row_match"
    assert "row match failed" in caplog.text
    assert "stage=row_match" in caplog.text
    assert "expected_tail=" in caplog.text


def test_row_match_timeout_env_default(monkeypatch):
    from automation.runtime import wallet_editor_row_match_timeout_ms

    monkeypatch.delenv("WALLET_EDITOR_ROW_MATCH_TIMEOUT_MS", raising=False)
    assert wallet_editor_row_match_timeout_ms() == 3000


def test_row_match_timeout_env_zero_disables_poll(monkeypatch):
    from automation.runtime import wallet_editor_row_match_timeout_ms

    monkeypatch.setenv("WALLET_EDITOR_ROW_MATCH_TIMEOUT_MS", "0")
    assert wallet_editor_row_match_timeout_ms() == 0


def test_row_match_inner_text_timeout_is_bounded():
    from automation.engine import _ROW_TEXT_READ_TIMEOUT_MS, _try_match_row_index
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    row = MagicMock()
    row.inner_text.side_effect = PlaywrightTimeoutError("Timeout 500ms exceeded")
    rows = MagicMock()
    rows.count.return_value = 3
    rows.nth.return_value = row

    match_index, rows_count, first_text = _try_match_row_index(
        rows,
        "9990080812990492",
        "9990080812990492",
    )

    assert match_index is None
    assert rows_count == 3
    assert first_text == ""
    assert row.inner_text.call_count == 3
    for call in row.inner_text.call_args_list:
        assert call.kwargs.get("timeout") == _ROW_TEXT_READ_TIMEOUT_MS


def test_row_match_skips_not_ready_row_and_later_matches(monkeypatch):
    from automation.engine import _wait_for_matching_row
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    monkeypatch.setenv("WALLET_EDITOR_ROW_MATCH_TIMEOUT_MS", "1000")
    page = MagicMock()
    page.wait_for_timeout = MagicMock()

    row = MagicMock()
    call_count = [0]

    def inner_text(**_kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            raise PlaywrightTimeoutError("Timeout 500ms exceeded")
        return "9990080812990492"

    row.inner_text.side_effect = inner_text
    rows = MagicMock()
    rows.count.return_value = 1
    rows.nth.return_value = row

    index = _wait_for_matching_row(
        page,
        rows,
        "9990080812990492",
        "9990080812990492",
    )

    assert index == 0
    assert call_count[0] >= 2
    page.wait_for_timeout.assert_called()


def test_row_match_all_rows_timeout_reports_row_match_stage(caplog):
    from automation.engine import OpenCardStageError, _wait_for_matching_row
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    caplog.set_level(logging.INFO)
    page = MagicMock()
    page.wait_for_timeout = MagicMock()

    row = MagicMock()
    row.inner_text.side_effect = PlaywrightTimeoutError("Timeout 500ms exceeded")
    rows = MagicMock()
    rows.count.return_value = 2
    rows.nth.return_value = row

    with patch("automation.engine.wallet_editor_row_match_timeout_ms", return_value=0):
        with pytest.raises(OpenCardStageError) as exc_info:
            _wait_for_matching_row(
                page,
                rows,
                "9990080812990492",
                "9990080812990492",
            )

    assert exc_info.value.stage == "row_match"
    assert "row text not ready" in caplog.text

