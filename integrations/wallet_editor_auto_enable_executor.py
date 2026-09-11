"""Wallet Editor auto-enable Antares executor (Phase B1, no registry patch)."""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import pandas as pd
from playwright.sync_api import Page, sync_playwright

from automation.engine import (
    _ensure_logged_in,
    _get_current_card_status,
    _partner_already_selected,
    _reset_wallet_form_for_next_card,
    ensure_partner_added,
    ensure_status_set,
    get_partner_chips,
    open_card,
    save,
)
from automation.wallet_terminal_field import (
    FAIL_ADD_NOT_CONFIRMED,
    FAIL_PARTNER_OPTION_NOT_FOUND,
    FAIL_SAVE_NOT_CONFIRMED,
    FAIL_TERMINAL_FIELD_AMBIGUOUS,
    FAIL_TERMINAL_FIELD_NOT_FOUND,
    FAIL_TERMINAL_FIELD_NOT_LOADED,
    TerminalFieldError,
)
from automation.audit import (
    ERROR_CARD_NOT_FOUND,
    ERROR_MODAL_CARD_MISMATCH,
    ERROR_MODAL_CONTAINER_TIMEOUT,
    ERROR_MODAL_DATA_TIMEOUT,
    ERROR_PLAYWRIGHT_TIMEOUT,
    ERROR_RETRY_LIMIT_REACHED,
    ERROR_ROW_MATCH_TIMEOUT,
    ERROR_TECHNICAL,
    RetryClassification,
    classify_enable_exception,
    log_step_duration,
    next_retry_fail_comment,
)
from automation.runtime import (
    CONVERSION_AUTO_PROFILE,
    RunConfig,
    operator_auth_state_path,
    require_wallet_editor_antares_credentials,
    wallet_editor_playwright_slow_mo_ms,
    wallet_editor_retryable_max_attempts,
)
from core.playwright_cleanup import close_playwright_stack
from integrations.conversion_wallet_editor_bridge import (
    ENV_LOGIN,
    ENV_PASSWORD,
    OPERATOR_PROFILE,
)
from integrations.wallet_editor_auto_enable_eligibility import CandidateRow
from integrations.wallet_editor_auto_enable_eligibility import is_run_limited
from integrations.wallet_editor_auto_enable_settings import AutoEnableSettings
from integrations.wallet_editor_hold import (
    ERROR_HOLD,
    ERROR_HOLD_CHECK_FAILED,
    HOLD_CHECK_FAILED_AUTO_COMMENT,
    HOLD_SKIP_COMMENT,
    HoldPairsSnapshot,
    is_card_partner_on_hold,
    load_hold_pairs_snapshot,
)
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["AUTOMATION"]
log = get_logger(name, icon)

SERVICE_WORKS_STATUS = "Не готов. Сервисные работы"

REGISTRY_OK = "OK"
REGISTRY_SKIP = "SKIP"
REGISTRY_FAIL = "FAIL"

ERROR_UNKNOWN_STATUS = "UNKNOWN_STATUS"
ERROR_ALREADY_ADDED = "ALREADY_ADDED"
ERROR_PARTNER_NOT_AVAILABLE = "PARTNER_NOT_AVAILABLE"
ERROR_SERVICE_WORKS = "SERVICE_WORKS"

_TECHNICAL_OUTCOME_CODES = frozenset(
    {
        ERROR_TECHNICAL,
        ERROR_MODAL_CARD_MISMATCH,
        ERROR_MODAL_DATA_TIMEOUT,
        ERROR_MODAL_CONTAINER_TIMEOUT,
        ERROR_ROW_MATCH_TIMEOUT,
        ERROR_PLAYWRIGHT_TIMEOUT,
        FAIL_TERMINAL_FIELD_NOT_FOUND,
        FAIL_TERMINAL_FIELD_NOT_LOADED,
        FAIL_TERMINAL_FIELD_AMBIGUOUS,
        FAIL_ADD_NOT_CONFIRMED,
        FAIL_SAVE_NOT_CONFIRMED,
    }
)

_OUTCOME_COLUMNS = [
    "card",
    "partner",
    "disable_date",
    "registry_value",
    "registry_comment",
    "status_before",
    "status_after",
    "partner_present_before",
    "partner_present_after",
    "mutated",
    "saved",
    "error_code",
    "raw_error",
]


def normalize_status_text(status: str) -> str:
    return re.sub(r"\s+", " ", (status or "").strip()).casefold()


def build_status_set(statuses: Sequence[str]) -> frozenset[str]:
    return frozenset(normalize_status_text(item) for item in statuses if str(item).strip())


def is_status_in_set(status: str, statuses: frozenset[str]) -> bool:
    return normalize_status_text(status) in statuses


def build_allowed_status_set(allowed: Sequence[str]) -> frozenset[str]:
    """Backward-compatible alias for tests."""
    return build_status_set(allowed)


def is_whitelisted_status(status: str, allowed: frozenset[str]) -> bool:
    """Backward-compatible alias for tests."""
    return is_status_in_set(status, allowed)


def build_run_config_from_conversion_env() -> RunConfig:
    login = os.getenv(ENV_LOGIN, "").strip()
    password = os.getenv(ENV_PASSWORD, "").strip()
    return RunConfig(
        login=login,
        password=password,
        auth_state_path=operator_auth_state_path(OPERATOR_PROFILE),
        operator_profile=OPERATOR_PROFILE,
    )


@dataclass(frozen=True, slots=True)
class EnableOutcome:
    card: str
    partner: str
    disable_date: str
    registry_value: str
    registry_comment: str
    status_before: str
    status_after: str
    partner_present_before: bool
    partner_present_after: bool
    mutated: bool
    saved: bool
    error_code: str
    raw_error: str | None = None
    source_row_index: int = -1


def _outcome(
    candidate: CandidateRow,
    *,
    registry_value: str,
    registry_comment: str,
    status_before: str = "",
    status_after: str = "",
    partner_present_before: bool = False,
    partner_present_after: bool = False,
    mutated: bool = False,
    saved: bool = False,
    error_code: str = "",
    raw_error: str | None = None,
) -> EnableOutcome:
    return EnableOutcome(
        card=candidate.card,
        partner=candidate.partner,
        disable_date=candidate.disable_at,
        registry_value=registry_value,
        registry_comment=registry_comment,
        status_before=status_before,
        status_after=status_after,
        partner_present_before=partner_present_before,
        partner_present_after=partner_present_after,
        mutated=mutated,
        saved=saved,
        error_code=error_code,
        raw_error=raw_error,
        source_row_index=candidate.source_row_index,
    )


def _chip_texts(page: Page) -> list[str]:
    return [text for _, text in get_partner_chips(page)]


def _technical_fail_outcome(
    candidate: CandidateRow,
    classification: RetryClassification,
    *,
    status_before: str = "",
    status_after: str = "",
    partner_present_before: bool = False,
    partner_present_after: bool = False,
    mutated: bool = False,
    saved: bool = False,
    raw_error: str | None = None,
) -> EnableOutcome:
    registry_comment, error_code = next_retry_fail_comment(
        prior_comment=candidate.enable_comment,
        error_code=classification.error_code,
        message=classification.message,
        max_attempts=wallet_editor_retryable_max_attempts(),
    )
    return _outcome(
        candidate,
        registry_value=REGISTRY_FAIL,
        registry_comment=registry_comment,
        status_before=status_before,
        status_after=status_after,
        partner_present_before=partner_present_before,
        partner_present_after=partner_present_after,
        mutated=mutated,
        saved=saved,
        error_code=error_code,
        raw_error=raw_error,
    )


def _status_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _status_for_comment(status_after: str, status_before: str) -> str | None:
    """Prefer post-save status; fall back to pre-mutation read when modal is closed."""
    after = _status_text(status_after)
    if after:
        return after
    before = _status_text(status_before)
    if before:
        return before
    return None


def _build_partner_added_comment(status_after: str, status_before: str) -> str:
    status = _status_for_comment(status_after, status_before)
    if status:
        return f"Партнёр добавлен; статус карты: {status}"
    return "Партнёр добавлен; статус карты не определён"


def _build_already_added_comment(status: str) -> str:
    text = _status_text(status)
    if text:
        return f"Партнёр уже был добавлен; статус карты рабочий: {text}"
    return "Партнёр уже был добавлен; статус карты рабочий: не определён"


def _build_status_changed_comment(
    status_before: str,
    target_status: str,
    *,
    partner_already: bool,
) -> str:
    old_status = _status_text(status_before) or "не определён"
    new_status = _status_text(target_status) or "не определён"
    if partner_already:
        return (
            f"Партнёр уже был добавлен; статус изменён: {old_status} → {new_status}"
        )
    return f"Партнёр добавлен; статус изменён: {old_status} → {new_status}"


def _unknown_status_comment(status: str) -> str:
    text = _status_text(status)
    if text:
        return f"UNKNOWN_STATUS: {text}; ручной разбор"
    return "UNKNOWN_STATUS: статус карты не определён; ручной разбор"


@dataclass(frozen=True, slots=True)
class _PartnerPresence:
    present: bool
    unread: bool = False
    error: str = ""


def _read_partner_presence(
    get_chips_fn: Callable[[Page], list[str]],
    page: Page,
    partner: str,
) -> _PartnerPresence:
    try:
        chips = get_chips_fn(page)
    except TerminalFieldError as exc:
        return _PartnerPresence(present=False, unread=True, error=exc.code)
    except Exception as exc:
        return _PartnerPresence(
            present=False,
            unread=True,
            error=str(exc) or ERROR_TECHNICAL,
        )
    return _PartnerPresence(present=_partner_already_selected(chips, partner))


def _unread_chip_fail(
    candidate: CandidateRow,
    presence: _PartnerPresence,
    *,
    status_before: str,
    status_after: str,
    partner_present_before: bool,
    mutated: bool = False,
) -> EnableOutcome:
    classification = RetryClassification(
        error_code=presence.error or FAIL_TERMINAL_FIELD_NOT_LOADED,
        retryable=True,
        message=presence.error or "не удалось прочитать выбранные терминалы",
    )
    return _technical_fail_outcome(
        candidate,
        classification,
        status_before=status_before,
        status_after=status_after,
        partner_present_before=partner_present_before,
        partner_present_after=False,
        mutated=mutated,
        saved=False,
        raw_error=presence.error,
    )


def _add_partner_or_outcome(
    page: Page,
    candidate: CandidateRow,
    *,
    status_before: str,
    partner_present_before: bool,
    cfg: RunConfig,
    get_status_fn: Callable[[Page], str],
    get_chips_fn: Callable[[Page], list[str]],
    add_partner_fn: Callable[[Page, str, RunConfig], str],
) -> tuple[bool, EnableOutcome | None]:
    try:
        add_result = add_partner_fn(page, candidate.partner, cfg)
    except Exception as exc:
        classification = classify_enable_exception(exc)
        presence = _read_partner_presence(get_chips_fn, page, candidate.partner)
        if presence.unread:
            return False, _unread_chip_fail(
                candidate,
                presence,
                status_before=status_before,
                status_after=get_status_fn(page),
                partner_present_before=partner_present_before,
            )
        return False, _technical_fail_outcome(
            candidate,
            classification,
            status_before=status_before,
            status_after=get_status_fn(page),
            partner_present_before=partner_present_before,
            partner_present_after=presence.present,
            raw_error=str(exc),
        )

    if (add_result or "").upper().startswith("FAIL"):
        if add_result == FAIL_PARTNER_OPTION_NOT_FOUND:
            presence = _read_partner_presence(get_chips_fn, page, candidate.partner)
            if presence.unread:
                return False, _unread_chip_fail(
                    candidate,
                    presence,
                    status_before=status_before,
                    status_after=get_status_fn(page),
                    partner_present_before=partner_present_before,
                )
            return False, _outcome(
                candidate,
                registry_value=REGISTRY_SKIP,
                registry_comment=(
                    f"PARTNER_NOT_AVAILABLE: {candidate.partner}; ручной разбор"
                ),
                status_before=status_before,
                status_after=get_status_fn(page),
                partner_present_before=partner_present_before,
                partner_present_after=presence.present,
                error_code=ERROR_PARTNER_NOT_AVAILABLE,
                raw_error=add_result,
            )
        classification = RetryClassification(
            error_code=add_result,
            retryable=add_result
            in {
                FAIL_TERMINAL_FIELD_NOT_FOUND,
                FAIL_TERMINAL_FIELD_NOT_LOADED,
                FAIL_ADD_NOT_CONFIRMED,
            },
            message=add_result,
        )
        return False, _technical_fail_outcome(
            candidate,
            classification,
            status_before=status_before,
            status_after=get_status_fn(page),
            partner_present_before=partner_present_before,
            partner_present_after=False,
            raw_error=add_result,
        )

    if add_result.startswith("skip"):
        presence = _read_partner_presence(get_chips_fn, page, candidate.partner)
        if presence.unread:
            return False, _unread_chip_fail(
                candidate,
                presence,
                status_before=status_before,
                status_after=get_status_fn(page),
                partner_present_before=partner_present_before,
            )
        if "option not found" in add_result:
            return False, _outcome(
                candidate,
                registry_value=REGISTRY_SKIP,
                registry_comment=(
                    f"PARTNER_NOT_AVAILABLE: {candidate.partner}; ручной разбор"
                ),
                status_before=status_before,
                status_after=get_status_fn(page),
                partner_present_before=partner_present_before,
                partner_present_after=presence.present,
                error_code=ERROR_PARTNER_NOT_AVAILABLE,
                raw_error=add_result,
            )
        return False, _technical_fail_outcome(
            candidate,
            RetryClassification(
                error_code=ERROR_TECHNICAL,
                retryable=True,
                message=add_result,
            ),
            status_before=status_before,
            status_after=get_status_fn(page),
            partner_present_before=partner_present_before,
            partner_present_after=presence.present,
            raw_error=add_result,
        )

    presence = _read_partner_presence(get_chips_fn, page, candidate.partner)
    if presence.unread:
        return False, _unread_chip_fail(
            candidate,
            presence,
            status_before=status_before,
            status_after=get_status_fn(page),
            partner_present_before=partner_present_before,
            mutated=True,
        )
    if not presence.present:
        return False, _technical_fail_outcome(
            candidate,
            RetryClassification(
                error_code=ERROR_TECHNICAL,
                retryable=True,
                message="partner add did not stick",
            ),
            status_before=status_before,
            status_after=get_status_fn(page),
            partner_present_before=partner_present_before,
            partner_present_after=False,
            mutated=True,
            saved=False,
        )
    return True, None


def _save_or_outcome(
    page: Page,
    candidate: CandidateRow,
    *,
    status_before: str,
    partner_present_before: bool,
    partner_present_after: bool,
    cfg: RunConfig,
    get_status_fn: Callable[[Page], str],
    save_fn: Callable[[Page, RunConfig], str],
) -> tuple[bool, EnableOutcome | None]:
    try:
        save_fn(page, cfg)
    except Exception as exc:
        classification = classify_enable_exception(exc)
        return False, _technical_fail_outcome(
            candidate,
            classification,
            status_before=status_before,
            status_after=get_status_fn(page),
            partner_present_before=partner_present_before,
            partner_present_after=partner_present_after,
            mutated=True,
            saved=False,
            raw_error=str(exc),
        )
    return True, None


def _save_not_confirmed_outcome(
    candidate: CandidateRow,
    *,
    status_before: str,
    partner_present_before: bool,
    status_after: str = "",
    partner_present_after: bool = False,
    message: str = FAIL_SAVE_NOT_CONFIRMED,
) -> EnableOutcome:
    return _technical_fail_outcome(
        candidate,
        RetryClassification(
            error_code=FAIL_SAVE_NOT_CONFIRMED,
            retryable=True,
            message=message,
        ),
        status_before=status_before,
        status_after=status_after or status_before,
        partner_present_before=partner_present_before,
        partner_present_after=partner_present_after,
        mutated=True,
        saved=False,
        raw_error=message,
    )


def _verify_saved_enable(
    page: Page,
    candidate: CandidateRow,
    *,
    status_before: str,
    partner_present_before: bool,
    expected_status: str,
    open_card_fn: Callable[[Page, str], None],
    get_status_fn: Callable[[Page], str],
    get_chips_fn: Callable[[Page], list[str]],
    comment_fn: Callable[[str], str],
) -> EnableOutcome:
    try:
        open_card_fn(page, candidate.card)
        presence = _read_partner_presence(get_chips_fn, page, candidate.partner)
        if presence.unread:
            return _save_not_confirmed_outcome(
                candidate,
                status_before=status_before,
                partner_present_before=partner_present_before,
                message=presence.error or FAIL_SAVE_NOT_CONFIRMED,
            )
        if not presence.present:
            return _save_not_confirmed_outcome(
                candidate,
                status_before=status_before,
                partner_present_before=partner_present_before,
                message="partner missing after save",
            )
        try:
            status_after = get_status_fn(page)
        except Exception as exc:
            return _save_not_confirmed_outcome(
                candidate,
                status_before=status_before,
                partner_present_before=partner_present_before,
                partner_present_after=presence.present,
                message=str(exc) or FAIL_SAVE_NOT_CONFIRMED,
            )
        if not _status_text(status_after):
            return _save_not_confirmed_outcome(
                candidate,
                status_before=status_before,
                partner_present_before=partner_present_before,
                partner_present_after=presence.present,
                message="status unread after save",
            )
        if normalize_status_text(status_after) != normalize_status_text(expected_status):
            return _save_not_confirmed_outcome(
                candidate,
                status_before=status_before,
                partner_present_before=partner_present_before,
                status_after=status_after,
                partner_present_after=presence.present,
                message=(
                    f"status after save {status_after!r} != {expected_status!r}"
                ),
            )
        return _outcome(
            candidate,
            registry_value=REGISTRY_OK,
            registry_comment=comment_fn(status_after),
            status_before=status_before,
            status_after=status_after,
            partner_present_before=partner_present_before,
            partner_present_after=True,
            mutated=True,
            saved=True,
        )
    except Exception as exc:
        return _save_not_confirmed_outcome(
            candidate,
            status_before=status_before,
            partner_present_before=partner_present_before,
            message=str(exc) or FAIL_SAVE_NOT_CONFIRMED,
        )


def process_enable_candidate(
    page: Page,
    candidate: CandidateRow,
    *,
    settings: AutoEnableSettings,
    cfg: RunConfig,
    working_statuses: frozenset[str] | None = None,
    auto_return_statuses: frozenset[str] | None = None,
    hold_snapshot: HoldPairsSnapshot | None = None,
    open_card_fn: Callable[[Page, str], None] = open_card,
    get_status_fn: Callable[[Page], str] = _get_current_card_status,
    get_chips_fn: Callable[[Page], list[str]] = _chip_texts,
    add_partner_fn: Callable[[Page, str, RunConfig], str] = ensure_partner_added,
    set_status_fn: Callable[[Page, str, RunConfig], str] = ensure_status_set,
    save_fn: Callable[[Page, RunConfig], str] = save,
    reset_form_fn: Callable[[Page], None] = _reset_wallet_form_for_next_card,
) -> EnableOutcome:
    """Single-card enable pass: open, mutate, save, then re-open to confirm."""
    try:
        return _process_enable_candidate(
            page,
            candidate,
            settings=settings,
            cfg=cfg,
            working_statuses=working_statuses,
            auto_return_statuses=auto_return_statuses,
            hold_snapshot=hold_snapshot,
            open_card_fn=open_card_fn,
            get_status_fn=get_status_fn,
            get_chips_fn=get_chips_fn,
            add_partner_fn=add_partner_fn,
            set_status_fn=set_status_fn,
            save_fn=save_fn,
        )
    finally:
        try:
            reset_form_fn(page)
        except Exception:
            pass


def _process_enable_candidate(
    page: Page,
    candidate: CandidateRow,
    *,
    settings: AutoEnableSettings,
    cfg: RunConfig,
    working_statuses: frozenset[str] | None,
    auto_return_statuses: frozenset[str] | None,
    hold_snapshot: HoldPairsSnapshot | None,
    open_card_fn: Callable[[Page, str], None],
    get_status_fn: Callable[[Page], str],
    get_chips_fn: Callable[[Page], list[str]],
    add_partner_fn: Callable[[Page, str, RunConfig], str],
    set_status_fn: Callable[[Page, str, RunConfig], str],
    save_fn: Callable[[Page, RunConfig], str],
) -> EnableOutcome:
    working_statuses = working_statuses or build_status_set(settings.working_statuses)
    auto_return_statuses = auto_return_statuses or build_status_set(
        settings.auto_return_statuses
    )
    target_status = settings.auto_return_target_status
    hold_snapshot = hold_snapshot or HoldPairsSnapshot.empty_available()

    if not hold_snapshot.available:
        log.error(
            "[AutoEnable] hold check failed card=%s partner=%s error=%s",
            candidate.card,
            candidate.partner,
            hold_snapshot.error,
        )
        return _outcome(
            candidate,
            registry_value=REGISTRY_FAIL,
            registry_comment=HOLD_CHECK_FAILED_AUTO_COMMENT,
            error_code=ERROR_HOLD_CHECK_FAILED,
            raw_error=hold_snapshot.error,
        )

    if is_card_partner_on_hold(candidate.card, candidate.partner, hold_snapshot.pairs):
        log.info(
            "[AutoEnable] hold blocked card=%s partner=%s",
            candidate.card,
            candidate.partner,
        )
        return _outcome(
            candidate,
            registry_value=REGISTRY_SKIP,
            registry_comment=HOLD_SKIP_COMMENT,
            error_code=ERROR_HOLD,
        )

    try:
        open_card_fn(page, candidate.card)
    except Exception as exc:
        classification = classify_enable_exception(exc)
        if not classification.retryable:
            return _outcome(
                candidate,
                registry_value=REGISTRY_SKIP,
                registry_comment="CARD_NOT_FOUND; ручной разбор",
                error_code=ERROR_CARD_NOT_FOUND,
                raw_error=str(exc),
            )
        return _technical_fail_outcome(
            candidate,
            classification,
            raw_error=str(exc),
        )

    status_before = get_status_fn(page)
    if not _status_text(status_before):
        return _outcome(
            candidate,
            registry_value=REGISTRY_SKIP,
            registry_comment=_unknown_status_comment(status_before),
            status_before=status_before,
            status_after=status_before,
            error_code=ERROR_UNKNOWN_STATUS,
        )

    try:
        chips_before = get_chips_fn(page)
    except TerminalFieldError as exc:
        classification = classify_enable_exception(exc)
        return _technical_fail_outcome(
            candidate,
            classification,
            status_before=status_before,
            status_after=status_before,
            raw_error=str(exc),
        )
    partner_present_before = _partner_already_selected(chips_before, candidate.partner)

    is_working = is_status_in_set(status_before, working_statuses)
    is_auto_return = is_status_in_set(status_before, auto_return_statuses)

    if not is_working and not is_auto_return:
        return _outcome(
            candidate,
            registry_value=REGISTRY_SKIP,
            registry_comment=_unknown_status_comment(status_before),
            status_before=status_before,
            status_after=status_before,
            partner_present_before=partner_present_before,
            partner_present_after=partner_present_before,
            error_code=ERROR_UNKNOWN_STATUS,
        )

    if is_working:
        if partner_present_before:
            return _outcome(
                candidate,
                registry_value=REGISTRY_OK,
                registry_comment=_build_already_added_comment(status_before),
                status_before=status_before,
                status_after=status_before,
                partner_present_before=True,
                partner_present_after=True,
                mutated=False,
                saved=False,
                error_code=ERROR_ALREADY_ADDED,
            )

        ok, outcome = _add_partner_or_outcome(
            page,
            candidate,
            status_before=status_before,
            partner_present_before=partner_present_before,
            cfg=cfg,
            get_status_fn=get_status_fn,
            get_chips_fn=get_chips_fn,
            add_partner_fn=add_partner_fn,
        )
        if not ok:
            return outcome

        saved_ok, outcome = _save_or_outcome(
            page,
            candidate,
            status_before=status_before,
            partner_present_before=partner_present_before,
            partner_present_after=True,
            cfg=cfg,
            get_status_fn=get_status_fn,
            save_fn=save_fn,
        )
        if not saved_ok:
            return outcome

        return _verify_saved_enable(
            page,
            candidate,
            status_before=status_before,
            partner_present_before=partner_present_before,
            expected_status=status_before,
            open_card_fn=open_card_fn,
            get_status_fn=get_status_fn,
            get_chips_fn=get_chips_fn,
            comment_fn=lambda status_after: _build_partner_added_comment(
                status_after, status_before
            ),
        )

    if not partner_present_before:
        ok, outcome = _add_partner_or_outcome(
            page,
            candidate,
            status_before=status_before,
            partner_present_before=partner_present_before,
            cfg=cfg,
            get_status_fn=get_status_fn,
            get_chips_fn=get_chips_fn,
            add_partner_fn=add_partner_fn,
        )
        if not ok:
            return outcome

    try:
        set_status_fn(page, target_status, cfg)
    except Exception as exc:
        classification = classify_enable_exception(exc)
        presence = _read_partner_presence(get_chips_fn, page, candidate.partner)
        if presence.unread:
            return _unread_chip_fail(
                candidate,
                presence,
                status_before=status_before,
                status_after=get_status_fn(page),
                partner_present_before=partner_present_before,
                mutated=True,
            )
        return _technical_fail_outcome(
            candidate,
            classification,
            status_before=status_before,
            status_after=get_status_fn(page),
            partner_present_before=partner_present_before,
            partner_present_after=presence.present,
            mutated=True,
            saved=False,
            raw_error=str(exc),
        )

    saved_ok, outcome = _save_or_outcome(
        page,
        candidate,
        status_before=status_before,
        partner_present_before=partner_present_before,
        partner_present_after=True,
        cfg=cfg,
        get_status_fn=get_status_fn,
        save_fn=save_fn,
    )
    if not saved_ok:
        return outcome

    return _verify_saved_enable(
        page,
        candidate,
        status_before=status_before,
        partner_present_before=partner_present_before,
        expected_status=target_status,
        open_card_fn=open_card_fn,
        get_status_fn=get_status_fn,
        get_chips_fn=get_chips_fn,
        comment_fn=lambda status_after: _build_status_changed_comment(
            status_before,
            status_after,
            partner_already=partner_present_before,
        ),
    )


def execute_enable_batch(
    candidates: Sequence[CandidateRow],
    settings: AutoEnableSettings,
    *,
    cfg: RunConfig | None = None,
) -> list[EnableOutcome]:
    """Run Antares enable for one batch (sequential, one open_card per candidate)."""
    if not candidates:
        return []

    cfg = cfg or build_run_config_from_conversion_env()
    require_wallet_editor_antares_credentials(cfg)
    profile = (cfg.operator_profile or CONVERSION_AUTO_PROFILE).strip()

    outcomes: list[EnableOutcome] = []
    working = build_status_set(settings.working_statuses)
    auto_return = build_status_set(settings.auto_return_statuses)

    slow_mo = wallet_editor_playwright_slow_mo_ms()
    log.info(
        "[WalletEditor] playwright slow_mo_ms=%s profile=%s scope=auto_enable",
        slow_mo,
        profile,
    )

    with log_step_duration(profile=profile, scope="auto_enable", step="batch"):
        hold_snapshot = load_hold_pairs_snapshot()
        with sync_playwright() as playwright:
            browser = None
            context = None
            page = None
            try:
                browser = playwright.chromium.launch(
                    headless=cfg.headless,
                    slow_mo=slow_mo,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                if os.path.exists(cfg.auth_state_path):
                    context = browser.new_context(storage_state=cfg.auth_state_path)
                else:
                    context = browser.new_context()
                page = context.new_page()
                _ensure_logged_in(page, context, cfg)

                for candidate in candidates:
                    log.info(
                        "[AutoEnable] processing card=%s partner=%s",
                        candidate.card,
                        candidate.partner,
                    )
                    with log_step_duration(
                        profile=profile,
                        scope="auto_enable",
                        step="candidate",
                        card=candidate.card,
                    ):
                        outcomes.append(
                            process_enable_candidate(
                                page,
                                candidate,
                                settings=settings,
                                cfg=cfg,
                                working_statuses=working,
                                auto_return_statuses=auto_return,
                                hold_snapshot=hold_snapshot,
                            )
                        )
            finally:
                close_playwright_stack(page=page, context=context, browser=browser)

    return outcomes


def outcomes_to_dataframe(outcomes: Sequence[EnableOutcome]) -> pd.DataFrame:
    rows = [
        {
            "card": o.card,
            "partner": o.partner,
            "disable_date": o.disable_date,
            "registry_value": o.registry_value,
            "registry_comment": o.registry_comment,
            "status_before": o.status_before,
            "status_after": o.status_after,
            "partner_present_before": o.partner_present_before,
            "partner_present_after": o.partner_present_after,
            "mutated": o.mutated,
            "saved": o.saved,
            "error_code": o.error_code,
            "raw_error": o.raw_error or "",
        }
        for o in outcomes
    ]
    if not rows:
        return pd.DataFrame(columns=_OUTCOME_COLUMNS)
    return pd.DataFrame(rows, columns=_OUTCOME_COLUMNS)


def write_outcomes_report(path: str | Path, outcomes: Sequence[EnableOutcome]) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    outcomes_to_dataframe(outcomes).to_excel(path, index=False)
    return str(path)


def build_batch_execution_report(
    outcomes: Sequence[EnableOutcome],
    *,
    batch_index: int,
    batch_total: int,
    settings: AutoEnableSettings,
    selected_after_dedup: int | None = None,
    selected_for_run: int | None = None,
    registry_updated: bool | None = None,
    patched_rows: int | None = None,
    requested_patch_rows: int | None = None,
    patch_failed_reason: str | None = None,
) -> str:
    ok = sum(1 for o in outcomes if o.registry_value == REGISTRY_OK)
    skip = sum(1 for o in outcomes if o.registry_value == REGISTRY_SKIP)
    fail = sum(1 for o in outcomes if o.registry_value == REGISTRY_FAIL)

    def _count(code: str) -> int:
        return sum(1 for o in outcomes if o.error_code == code)

    def _count_technical() -> int:
        return sum(1 for o in outcomes if o.error_code in _TECHNICAL_OUTCOME_CODES)

    run_limit_lines: list[str] = []
    if selected_after_dedup is not None and selected_for_run is not None:
        limited = is_run_limited(
            selected_after_dedup=selected_after_dedup,
            selected_for_run=selected_for_run,
            max_rows_per_run=settings.max_rows_per_run,
        )
        run_limit_lines = [
            "",
            "run selection:",
            f"- selected after dedup: {selected_after_dedup}",
            f"- max_rows_per_run: {settings.max_rows_per_run}",
            f"- selected for this run: {selected_for_run}",
            f"- limited by max_rows_per_run: {'yes' if limited else 'no'}",
        ]
        if limited:
            run_limit_lines.append(
                f"⚠️ Run limited by max_rows_per_run: selected {selected_for_run} of {selected_after_dedup} candidates."
            )

    registry_lines: list[str] = ["", "registry:"]
    if registry_updated is True:
        registry_lines.extend(
            [
                "- registry_updated: True",
                f"- patched rows: {patched_rows or 0}",
                f"- requested rows: {requested_patch_rows or len(outcomes)}",
            ]
        )
    elif registry_updated is False:
        registry_lines.extend(
            [
                "- registry_updated: False",
                f"- patched rows: {patched_rows or 0}",
                f"- requested rows: {requested_patch_rows or len(outcomes)}",
            ]
        )
        if patch_failed_reason:
            registry_lines.append(f"- patch failed reason: {patch_failed_reason}")
        registry_lines.append("⚠️ Antares changes were NOT rolled back.")
    else:
        registry_lines.extend(
            [
                "- registry_updated: False",
                "- reason: no outcomes to patch",
            ]
        )

    lines = [
        "🧩 WalletEditor Auto-Enable",
        "mode: Phase B2 execution + registry patch",
        f"batch: {batch_index}/{batch_total}",
        f"rows in batch: {len(outcomes)}",
        *run_limit_lines,
        "",
        "results:",
        f"- OK: {ok}",
        f"- SKIP: {skip}",
        f"- FAIL: {fail}",
        "",
        "breakdown:",
        f"- already_added: {_count(ERROR_ALREADY_ADDED)}",
        f"- service_works: {_count(ERROR_SERVICE_WORKS)}",
        f"- unknown_status: {_count(ERROR_UNKNOWN_STATUS)}",
        f"- card_not_found: {_count(ERROR_CARD_NOT_FOUND)}",
        f"- partner_not_available: {_count(ERROR_PARTNER_NOT_AVAILABLE)}",
        f"- hold_blocked: {_count(ERROR_HOLD)}",
        f"- hold_check_failed: {_count(ERROR_HOLD_CHECK_FAILED)}",
        f"- technical: {_count_technical()}",
        *registry_lines,
        "",
        "⚠️ Antares execution completed for this batch.",
    ]
    return "\n".join(lines)


def make_batch_result_path(batch_index: int) -> str:
    fd, path = tempfile.mkstemp(
        prefix=f"we_auto_enable_batch_{batch_index}_",
        suffix=".xlsx",
    )
    os.close(fd)
    return path
