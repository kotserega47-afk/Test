from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation.audit import (
    ERROR_CARD_NOT_FOUND,
    ERROR_MODAL_CARD_MISMATCH,
    ERROR_MODAL_DATA_TIMEOUT,
    ERROR_MODAL_CONTAINER_TIMEOUT,
    ERROR_PLAYWRIGHT_TIMEOUT,
    ERROR_RETRY_LIMIT_REACHED,
    ERROR_ROW_MATCH_TIMEOUT,
    ERROR_TECHNICAL,
    RetryClassification,
    build_retry_limit_reached_comment,
    build_retryable_fail_comment,
    classify_enable_exception,
    is_retryable_fail_comment_for_eligibility,
    next_retry_fail_comment,
    parse_retry_count,
)
from automation.engine import OpenCardStageError
from automation.runtime import wallet_editor_retryable_max_attempts
from integrations.wallet_editor_auto_enable_eligibility import (
    select_auto_enable_candidates,
)
from integrations.wallet_editor_auto_enable_executor import (
    REGISTRY_FAIL,
    REGISTRY_SKIP,
    ERROR_CARD_NOT_FOUND as EXECUTOR_CARD_NOT_FOUND,
    process_enable_candidate,
)
from integrations.wallet_editor_auto_enable_settings import AutoEnableSettings
from integrations.wallet_editor_auto_enable_eligibility import CandidateRow
from integrations.wallet_editor_registry import (
    EnableRegistryUpdate,
    apply_enable_updates_to_all_results,
)
from integrations.wallet_editor_registry_lifecycle import (
    ACTION_REMOVE_PARTNER,
    ALL_RESULTS_COLUMNS,
    STATUS_K_VKLUCHENIYU,
    STATUS_OSHIBKA,
    recalculate_all_results,
)
from automation.runtime import RunConfig


def _settings(**overrides) -> AutoEnableSettings:
    base = dict(
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
        allowed_statuses_for_enable=(),
        deprecated_working_statuses_fallback=False,
        include_overdue=True,
        telegram_route_report="wallet_editor_auto_enable",
        telegram_route_alert="wallet_editor_auto_enable_alert",
    )
    base.update(overrides)
    return AutoEnableSettings(**base)


def _candidate(**overrides) -> CandidateRow:
    base = dict(
        card="4111",
        partner="Ostin",
        disable_at="01.06.2026 10:00:00",
        enable_status="К ВКЛЮЧЕНИЮ",
        vklyucheno="",
        source_row_index=0,
        enable_comment="",
    )
    base.update(overrides)
    return CandidateRow(**base)


def _cfg() -> RunConfig:
    return RunConfig(login="u", password="p", auth_state_path="/tmp/auth.json")


def _registry_row(*, vklyucheno: str = "", enable_comment: str = "") -> dict[str, str]:
    return {
        "Дата отключения": "01.06.2026 10:00:00",
        "Дата включения": "06.06.2026",
        "Статус включения": STATUS_OSHIBKA if vklyucheno == "FAIL" else STATUS_K_VKLUCHENIYU,
        "Включено": vklyucheno,
        "Комментарий включения": enable_comment,
        "card": "4111",
        "partner": "Ostin",
        "action": ACTION_REMOVE_PARTNER,
        "status": "OK",
        "comment": "",
        "hold": "",
    }


def _select_with_comment(enable_comment: str, *, vklyucheno: str = "FAIL") -> int:
    df = pd.DataFrame([_registry_row(vklyucheno=vklyucheno, enable_comment=enable_comment)])
    otlezka = pd.DataFrame([{"partner": "Ostin", "Полные дни": 5, "comment": ""}])
    recalculated, _ = recalculate_all_results(
        df,
        pd.DataFrame(),
        otlezka,
        today=date(2026, 6, 6),
    )
    result = select_auto_enable_candidates(recalculated, include_overdue=True)
    return len(result.selected)


@pytest.mark.parametrize(
    ("stage", "expected_code"),
    [
        ("card_verify", ERROR_MODAL_CARD_MISMATCH),
        ("modal_data", ERROR_MODAL_DATA_TIMEOUT),
        ("modal_container", ERROR_MODAL_CONTAINER_TIMEOUT),
        ("row_match", ERROR_ROW_MATCH_TIMEOUT),
    ],
)
def test_classify_open_card_stage_errors(stage: str, expected_code: str):
    exc = OpenCardStageError(stage, "4111", message=f"failed stage={stage}")
    classification = classify_enable_exception(exc)
    assert classification.error_code == expected_code
    assert classification.retryable is True


def test_row_match_not_classified_as_card_not_found():
    exc = OpenCardStageError(
        "row_match",
        "4111",
        message="Карта не найдена (stage=row_match): 4111",
    )
    classification = classify_enable_exception(exc)
    assert classification.error_code == ERROR_ROW_MATCH_TIMEOUT
    assert classification.retryable is True

    page = MagicMock()
    outcome = process_enable_candidate(
        page,
        _candidate(),
        settings=_settings(),
        cfg=_cfg(),
        open_card_fn=lambda _p, _c: (_ for _ in ()).throw(exc),
        get_status_fn=lambda _p: pytest.fail("status read should not run"),
        get_chips_fn=lambda _p: pytest.fail("chips read should not run"),
    )
    assert outcome.registry_value == REGISTRY_FAIL
    assert outcome.error_code == ERROR_ROW_MATCH_TIMEOUT
    assert "ROW_MATCH_TIMEOUT" in outcome.registry_comment


def test_modal_card_mismatch_retryable_fail():
    exc = OpenCardStageError(
        "card_verify",
        "4111",
        message="Модалка не соответствует карте: 4111",
    )
    page = MagicMock()
    outcome = process_enable_candidate(
        page,
        _candidate(),
        settings=_settings(),
        cfg=_cfg(),
        open_card_fn=lambda _p, _c: (_ for _ in ()).throw(exc),
        get_status_fn=lambda _p: "",
        get_chips_fn=lambda _p: [],
    )
    assert outcome.registry_value == REGISTRY_FAIL
    assert outcome.error_code == ERROR_MODAL_CARD_MISMATCH
    assert outcome.registry_comment.startswith("TECHNICAL:MODAL_CARD_MISMATCH:")
    assert "RETRY:1/3:" in outcome.registry_comment


def test_playwright_timeout_retryable_fail():
    page = MagicMock()
    outcome = process_enable_candidate(
        page,
        _candidate(),
        settings=_settings(),
        cfg=_cfg(),
        open_card_fn=lambda _p, _c: (_ for _ in ()).throw(
            PlaywrightTimeoutError("Locator.click: Timeout 5000ms exceeded")
        ),
        get_status_fn=lambda _p: "",
        get_chips_fn=lambda _p: [],
    )
    assert outcome.registry_value == REGISTRY_FAIL
    assert outcome.error_code == ERROR_PLAYWRIGHT_TIMEOUT
    assert "PLAYWRIGHT_TIMEOUT" in outcome.registry_comment


def test_retryable_fail_included_under_limit():
    comment = build_retryable_fail_comment(
        ERROR_MODAL_DATA_TIMEOUT,
        1,
        3,
        "modal timeout",
    )
    assert _select_with_comment(comment) == 1


def test_retryable_fail_excluded_at_limit():
    comment = build_retryable_fail_comment(
        ERROR_MODAL_DATA_TIMEOUT,
        3,
        3,
        "modal timeout",
    )
    assert _select_with_comment(comment) == 0


def test_retry_limit_reached_comment_not_selected():
    comment = build_retry_limit_reached_comment(ERROR_MODAL_CARD_MISMATCH)
    assert _select_with_comment(comment) == 0
    assert not is_retryable_fail_comment_for_eligibility(comment, max_attempts=3)


def test_business_skip_not_retryable():
    page = MagicMock()
    outcome = process_enable_candidate(
        page,
        _candidate(),
        settings=_settings(),
        cfg=_cfg(),
        open_card_fn=lambda _p, _c: (_ for _ in ()).throw(
            RuntimeError("Карта не найдена: 4111")
        ),
        get_status_fn=lambda _p: pytest.fail("status read should not run"),
        get_chips_fn=lambda _p: pytest.fail("chips read should not run"),
    )
    assert outcome.registry_value == REGISTRY_SKIP
    assert outcome.error_code == EXECUTOR_CARD_NOT_FOUND

    assert _select_with_comment("CARD_NOT_FOUND; ручной разбор", vklyucheno="SKIP") == 0


def test_max_attempts_env_default_invalid_zero(monkeypatch):
    monkeypatch.delenv("WALLET_EDITOR_RETRYABLE_MAX_ATTEMPTS", raising=False)
    assert wallet_editor_retryable_max_attempts() == 3

    monkeypatch.setenv("WALLET_EDITOR_RETRYABLE_MAX_ATTEMPTS", "bad")
    assert wallet_editor_retryable_max_attempts() == 3

    monkeypatch.setenv("WALLET_EDITOR_RETRYABLE_MAX_ATTEMPTS", "0")
    assert wallet_editor_retryable_max_attempts() == 0


def test_retry_count_increments_from_prior_comment():
    prior = build_retryable_fail_comment(
        ERROR_MODAL_CARD_MISMATCH,
        1,
        3,
        "first fail",
    )
    comment, code = next_retry_fail_comment(
        prior_comment=prior,
        error_code=ERROR_MODAL_CARD_MISMATCH,
        message="second fail",
        max_attempts=3,
    )
    assert code == ERROR_MODAL_CARD_MISMATCH
    assert parse_retry_count(comment) == 2
    assert "RETRY:2/3:" in comment


def test_third_fail_writes_retry_limit_reached():
    prior = build_retryable_fail_comment(
        ERROR_MODAL_CARD_MISMATCH,
        2,
        3,
        "second fail",
    )
    comment, code = next_retry_fail_comment(
        prior_comment=prior,
        error_code=ERROR_MODAL_CARD_MISMATCH,
        message="third fail",
        max_attempts=3,
    )
    assert code == ERROR_RETRY_LIMIT_REACHED
    assert comment.startswith("RETRY_LIMIT_REACHED:MODAL_CARD_MISMATCH:")


def test_legacy_technical_comment_still_retryable():
    assert _select_with_comment("TECHNICAL: timeout; можно повторить") == 1


def test_patched_structured_fail_included_in_next_selection():
    df = pd.DataFrame([_registry_row()], columns=ALL_RESULTS_COLUMNS)
    comment = build_retryable_fail_comment(
        ERROR_MODAL_CARD_MISMATCH,
        1,
        3,
        "modal mismatch",
    )
    patched, _ = apply_enable_updates_to_all_results(
        df,
        [
            EnableRegistryUpdate(
                card="4111",
                partner="Ostin",
                disable_date="01.06.2026 10:00:00",
                vklyucheno="FAIL",
                comment=comment,
                source_row_index=0,
            )
        ],
    )
    otlezka = pd.DataFrame([{"partner": "Ostin", "Полные дни": 5, "comment": ""}])
    recalculated, _ = recalculate_all_results(
        patched,
        pd.DataFrame(),
        otlezka,
        today=date(2026, 6, 6),
    )
    result = select_auto_enable_candidates(recalculated, include_overdue=True)
    assert len(result.selected) == 1
