from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from automation.runtime import RunConfig
from integrations.wallet_editor_auto_enable_eligibility import CandidateRow
from integrations.wallet_editor_auto_enable_executor import (
    ERROR_ALREADY_ADDED,
    ERROR_CARD_NOT_FOUND,
    ERROR_PARTNER_NOT_AVAILABLE,
    ERROR_SERVICE_WORKS,
    ERROR_TECHNICAL,
    ERROR_UNKNOWN_STATUS,
    REGISTRY_FAIL,
    REGISTRY_OK,
    REGISTRY_SKIP,
    SERVICE_WORKS_STATUS,
    build_allowed_status_set,
    build_batch_execution_report,
    is_whitelisted_status,
    process_enable_candidate,
)
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
        allowed_statuses_for_enable=("готов к работе", "активный вход", "активный выход"),
        include_overdue=True,
        telegram_route_report="wallet_editor_auto_enable",
        telegram_route_alert="wallet_editor_auto_enable_alert",
    )


def _candidate(**overrides) -> CandidateRow:
    base = dict(
        card="4111",
        partner="Ostin",
        disable_at="01.06.2026 10:00:00",
        enable_status="К ВКЛЮЧЕНИЮ",
        vklyucheno="",
        source_row_index=0,
    )
    base.update(overrides)
    return CandidateRow(**base)


def _cfg() -> RunConfig:
    return RunConfig(login="u", password="p", auth_state_path="/tmp/auth.json")


@pytest.fixture
def page() -> MagicMock:
    return MagicMock()


def test_service_works_skip_no_mutation_no_save(page):
    open_calls: list[str] = []

    def open_card(_page, card):
        open_calls.append(card)

    outcome = process_enable_candidate(
        page,
        _candidate(),
        settings=_settings(),
        cfg=_cfg(),
        open_card_fn=open_card,
        get_status_fn=lambda _p: SERVICE_WORKS_STATUS,
        get_chips_fn=lambda _p: [],
        add_partner_fn=lambda *_a, **_k: pytest.fail("add_partner should not run"),
        save_fn=lambda *_a, **_k: pytest.fail("save should not run"),
    )

    assert outcome.registry_value == REGISTRY_SKIP
    assert outcome.error_code == ERROR_SERVICE_WORKS
    assert "SERVICE_WORKS" in outcome.registry_comment
    assert outcome.mutated is False
    assert outcome.saved is False
    assert open_calls == ["4111"]


def test_unknown_status_skip_no_mutation_no_save(page):
    outcome = process_enable_candidate(
        page,
        _candidate(),
        settings=_settings(),
        cfg=_cfg(),
        open_card_fn=lambda _p, _c: None,
        get_status_fn=lambda _p: "Не готов. Плановый прозвон",
        get_chips_fn=lambda _p: [],
        add_partner_fn=lambda *_a, **_k: pytest.fail("add_partner should not run"),
        save_fn=lambda *_a, **_k: pytest.fail("save should not run"),
    )

    assert outcome.registry_value == REGISTRY_SKIP
    assert outcome.error_code == ERROR_UNKNOWN_STATUS
    assert "UNKNOWN_STATUS" in outcome.registry_comment
    assert outcome.mutated is False
    assert outcome.saved is False


def test_partner_already_whitelist_ok_no_save(page):
    outcome = process_enable_candidate(
        page,
        _candidate(),
        settings=_settings(),
        cfg=_cfg(),
        open_card_fn=lambda _p, _c: None,
        get_status_fn=lambda _p: "Готов к работе",
        get_chips_fn=lambda _p: ["Ostin"],
        add_partner_fn=lambda *_a, **_k: pytest.fail("add_partner should not run"),
        save_fn=lambda *_a, **_k: pytest.fail("save should not run"),
    )

    assert outcome.registry_value == REGISTRY_OK
    assert outcome.error_code == ERROR_ALREADY_ADDED
    assert outcome.partner_present_before is True
    assert outcome.partner_present_after is True
    assert outcome.mutated is False
    assert outcome.saved is False


def test_partner_missing_whitelist_add_and_save_ok(page):
    save_calls: list[str] = []
    chips: list[str] = []

    def add_partner(_page, partner, _cfg):
        chips.append(partner)
        return f"added {partner}"

    def save(_page, _cfg):
        save_calls.append("save")
        return "ok"

    outcome = process_enable_candidate(
        page,
        _candidate(),
        settings=_settings(),
        cfg=_cfg(),
        open_card_fn=lambda _p, _c: None,
        get_status_fn=lambda _p: "Готов к работе",
        get_chips_fn=lambda _p: list(chips),
        add_partner_fn=add_partner,
        save_fn=save,
    )

    assert outcome.registry_value == REGISTRY_OK
    assert outcome.mutated is True
    assert outcome.saved is True
    assert outcome.partner_present_after is True
    assert "Партнёр добавлен" in outcome.registry_comment
    assert save_calls == ["save"]


def test_partner_option_not_found_skip(page):
    outcome = process_enable_candidate(
        page,
        _candidate(),
        settings=_settings(),
        cfg=_cfg(),
        open_card_fn=lambda _p, _c: None,
        get_status_fn=lambda _p: "Готов к работе",
        get_chips_fn=lambda _p: [],
        add_partner_fn=lambda *_a, **_k: "skip: option not found",
        save_fn=lambda *_a, **_k: pytest.fail("save should not run"),
    )

    assert outcome.registry_value == REGISTRY_SKIP
    assert outcome.error_code == ERROR_PARTNER_NOT_AVAILABLE
    assert outcome.mutated is False
    assert outcome.saved is False


def test_card_not_found_skip(page):
    def open_card(_page, _card):
        raise RuntimeError("Карта не найдена: 4111")

    outcome = process_enable_candidate(
        page,
        _candidate(),
        settings=_settings(),
        cfg=_cfg(),
        open_card_fn=open_card,
        get_status_fn=lambda _p: pytest.fail("status read should not run"),
        get_chips_fn=lambda _p: pytest.fail("chips read should not run"),
    )

    assert outcome.registry_value == REGISTRY_SKIP
    assert outcome.error_code == ERROR_CARD_NOT_FOUND
    assert outcome.mutated is False
    assert outcome.saved is False


def test_technical_exception_fail(page):
    def open_card_raises(_page, _card):
        raise TimeoutError("page timeout")

    outcome = process_enable_candidate(
        page,
        _candidate(),
        settings=_settings(),
        cfg=_cfg(),
        open_card_fn=open_card_raises,
        get_status_fn=lambda _p: "",
        get_chips_fn=lambda _p: [],
    )

    assert outcome.registry_value == REGISTRY_FAIL
    assert outcome.error_code == ERROR_TECHNICAL
    assert "TECHNICAL" in outcome.registry_comment


def test_save_failure_after_mutation_fail(page):
    chips: list[str] = []

    def add_partner(_page, partner, _cfg):
        chips.append(partner)
        return f"added {partner}"

    def save(_page, _cfg):
        raise RuntimeError("save modal mismatch")

    outcome = process_enable_candidate(
        page,
        _candidate(),
        settings=_settings(),
        cfg=_cfg(),
        open_card_fn=lambda _p, _c: None,
        get_status_fn=lambda _p: "Готов к работе",
        get_chips_fn=lambda _p: list(chips),
        add_partner_fn=add_partner,
        save_fn=save,
    )

    assert outcome.registry_value == REGISTRY_FAIL
    assert outcome.error_code == ERROR_TECHNICAL
    assert outcome.mutated is True
    assert outcome.saved is False


def test_one_open_card_per_candidate(page):
    open_calls: list[str] = []

    def open_card(_page, card):
        open_calls.append(card)

    process_enable_candidate(
        page,
        _candidate(),
        settings=_settings(),
        cfg=_cfg(),
        open_card_fn=open_card,
        get_status_fn=lambda _p: "Готов к работе",
        get_chips_fn=lambda _p: ["Ostin"],
        add_partner_fn=lambda *_a, **_k: pytest.fail("no add"),
        save_fn=lambda *_a, **_k: pytest.fail("no save"),
    )

    assert open_calls == ["4111"]


def test_no_separate_prescan_only_status_and_chips_after_open(page):
    sequence: list[str] = []

    def open_card(_page, _card):
        sequence.append("open")

    def get_status(_page):
        sequence.append("status")
        return "Готов к работе"

    def get_chips(_page):
        sequence.append("chips")
        return ["Ostin"]

    process_enable_candidate(
        page,
        _candidate(),
        settings=_settings(),
        cfg=_cfg(),
        open_card_fn=open_card,
        get_status_fn=get_status,
        get_chips_fn=get_chips,
        add_partner_fn=lambda *_a, **_k: pytest.fail("no add"),
        save_fn=lambda *_a, **_k: pytest.fail("no save"),
    )

    assert sequence == ["open", "status", "chips"]


def test_active_input_whitelist_normalization(page):
    allowed = build_allowed_status_set(_settings().allowed_statuses_for_enable)
    assert is_whitelisted_status("  Активный   вход ", allowed)

    outcome = process_enable_candidate(
        page,
        _candidate(),
        settings=_settings(),
        cfg=_cfg(),
        allowed_statuses=allowed,
        open_card_fn=lambda _p, _c: None,
        get_status_fn=lambda _p: "активный вход",
        get_chips_fn=lambda _p: ["Ostin"],
        add_partner_fn=lambda *_a, **_k: pytest.fail("no add"),
        save_fn=lambda *_a, **_k: pytest.fail("no save"),
    )

    assert outcome.registry_value == REGISTRY_OK
    assert outcome.error_code == ERROR_ALREADY_ADDED


def test_build_batch_execution_report_contains_registry_warning():
    outcomes = [
        process_enable_candidate(
            MagicMock(),
            _candidate(),
            settings=_settings(),
            cfg=_cfg(),
            open_card_fn=lambda _p, _c: None,
            get_status_fn=lambda _p: "Готов к работе",
            get_chips_fn=lambda _p: ["Ostin"],
            add_partner_fn=lambda *_a, **_k: pytest.fail("no add"),
            save_fn=lambda *_a, **_k: pytest.fail("no save"),
        )
    ]
    report = build_batch_execution_report(
        outcomes,
        batch_index=1,
        batch_total=2,
        settings=_settings(),
        registry_updated=True,
        patched_rows=1,
        requested_patch_rows=1,
    )
    assert "registry_updated: True" in report
    assert "batch: 1/2" in report
