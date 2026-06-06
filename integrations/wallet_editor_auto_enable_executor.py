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
    ensure_partner_added,
    get_partner_chips,
    open_card,
    save,
)
from automation.runtime import RunConfig, operator_auth_state_path, require_wallet_editor_antares_credentials
from core.playwright_cleanup import close_playwright_stack
from integrations.conversion_wallet_editor_bridge import (
    ENV_LOGIN,
    ENV_PASSWORD,
    OPERATOR_PROFILE,
)
from integrations.wallet_editor_auto_enable_eligibility import CandidateRow
from integrations.wallet_editor_auto_enable_eligibility import is_run_limited
from integrations.wallet_editor_auto_enable_settings import AutoEnableSettings
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["AUTOMATION"]
log = get_logger(name, icon)

SERVICE_WORKS_STATUS = "Не готов. Сервисные работы"

REGISTRY_OK = "OK"
REGISTRY_SKIP = "SKIP"
REGISTRY_FAIL = "FAIL"

ERROR_CARD_NOT_FOUND = "CARD_NOT_FOUND"
ERROR_SERVICE_WORKS = "SERVICE_WORKS"
ERROR_UNKNOWN_STATUS = "UNKNOWN_STATUS"
ERROR_ALREADY_ADDED = "ALREADY_ADDED"
ERROR_PARTNER_NOT_AVAILABLE = "PARTNER_NOT_AVAILABLE"
ERROR_TECHNICAL = "TECHNICAL"

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


def build_allowed_status_set(allowed: Sequence[str]) -> frozenset[str]:
    return frozenset(normalize_status_text(item) for item in allowed if str(item).strip())


def is_service_works_status(status: str) -> bool:
    return normalize_status_text(status) == normalize_status_text(SERVICE_WORKS_STATUS)


def is_whitelisted_status(status: str, allowed: frozenset[str]) -> bool:
    return normalize_status_text(status) in allowed


def build_run_config_from_conversion_env() -> RunConfig:
    login = os.getenv(ENV_LOGIN, "").strip()
    password = os.getenv(ENV_PASSWORD, "").strip()
    return RunConfig(
        login=login,
        password=password,
        auth_state_path=operator_auth_state_path(OPERATOR_PROFILE),
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


def _is_card_not_found(exc: Exception) -> bool:
    return "Карта не найдена" in str(exc)


def process_enable_candidate(
    page: Page,
    candidate: CandidateRow,
    *,
    settings: AutoEnableSettings,
    cfg: RunConfig,
    allowed_statuses: frozenset[str] | None = None,
    open_card_fn: Callable[[Page, str], None] = open_card,
    get_status_fn: Callable[[Page], str] = _get_current_card_status,
    get_chips_fn: Callable[[Page], list[str]] = _chip_texts,
    add_partner_fn: Callable[[Page, str, RunConfig], str] = ensure_partner_added,
    save_fn: Callable[[Page, RunConfig], str] = save,
) -> EnableOutcome:
    """Single-card enable pass: one open_card, read status/partners, decide, act, save."""
    allowed_statuses = allowed_statuses or build_allowed_status_set(
        settings.allowed_statuses_for_enable
    )

    try:
        open_card_fn(page, candidate.card)
    except Exception as exc:
        if _is_card_not_found(exc):
            return _outcome(
                candidate,
                registry_value=REGISTRY_SKIP,
                registry_comment="CARD_NOT_FOUND; ручной разбор",
                error_code=ERROR_CARD_NOT_FOUND,
                raw_error=str(exc),
            )
        return _outcome(
            candidate,
            registry_value=REGISTRY_FAIL,
            registry_comment=f"TECHNICAL: {exc}; можно повторить",
            error_code=ERROR_TECHNICAL,
            raw_error=str(exc),
        )

    status_before = get_status_fn(page)

    if is_service_works_status(status_before):
        return _outcome(
            candidate,
            registry_value=REGISTRY_SKIP,
            registry_comment=(
                f"SERVICE_WORKS: {SERVICE_WORKS_STATUS}; ручное включение"
            ),
            status_before=status_before,
            status_after=status_before,
            error_code=ERROR_SERVICE_WORKS,
        )

    if not is_whitelisted_status(status_before, allowed_statuses):
        return _outcome(
            candidate,
            registry_value=REGISTRY_SKIP,
            registry_comment=f"UNKNOWN_STATUS: {status_before}; ручной разбор",
            status_before=status_before,
            status_after=status_before,
            error_code=ERROR_UNKNOWN_STATUS,
        )

    chips_before = get_chips_fn(page)
    partner_present_before = _partner_already_selected(chips_before, candidate.partner)

    if partner_present_before:
        return _outcome(
            candidate,
            registry_value=REGISTRY_OK,
            registry_comment=(
                f"Партнёр уже был добавлен; статус карты рабочий: {status_before}"
            ),
            status_before=status_before,
            status_after=status_before,
            partner_present_before=True,
            partner_present_after=True,
            mutated=False,
            saved=False,
            error_code=ERROR_ALREADY_ADDED,
        )

    try:
        add_result = add_partner_fn(page, candidate.partner, cfg)
    except Exception as exc:
        return _outcome(
            candidate,
            registry_value=REGISTRY_FAIL,
            registry_comment=f"TECHNICAL: {exc}; можно повторить",
            status_before=status_before,
            status_after=get_status_fn(page),
            partner_present_before=partner_present_before,
            partner_present_after=_partner_already_selected(
                get_chips_fn(page), candidate.partner
            ),
            error_code=ERROR_TECHNICAL,
            raw_error=str(exc),
        )

    if add_result.startswith("skip"):
        if "option not found" in add_result:
            return _outcome(
                candidate,
                registry_value=REGISTRY_SKIP,
                registry_comment=(
                    f"PARTNER_NOT_AVAILABLE: {candidate.partner}; ручной разбор"
                ),
                status_before=status_before,
                status_after=get_status_fn(page),
                partner_present_before=partner_present_before,
                partner_present_after=_partner_already_selected(
                    get_chips_fn(page), candidate.partner
                ),
                error_code=ERROR_PARTNER_NOT_AVAILABLE,
                raw_error=add_result,
            )
        return _outcome(
            candidate,
            registry_value=REGISTRY_FAIL,
            registry_comment=f"TECHNICAL: {add_result}; можно повторить",
            status_before=status_before,
            status_after=get_status_fn(page),
            partner_present_before=partner_present_before,
            partner_present_after=_partner_already_selected(
                get_chips_fn(page), candidate.partner
            ),
            error_code=ERROR_TECHNICAL,
            raw_error=add_result,
        )

    chips_after_add = get_chips_fn(page)
    partner_present_after = _partner_already_selected(chips_after_add, candidate.partner)
    if not partner_present_after:
        return _outcome(
            candidate,
            registry_value=REGISTRY_FAIL,
            registry_comment="TECHNICAL: partner add did not stick; можно повторить",
            status_before=status_before,
            status_after=get_status_fn(page),
            partner_present_before=partner_present_before,
            partner_present_after=False,
            mutated=True,
            saved=False,
            error_code=ERROR_TECHNICAL,
        )

    try:
        save_fn(page, cfg)
    except Exception as exc:
        return _outcome(
            candidate,
            registry_value=REGISTRY_FAIL,
            registry_comment=f"TECHNICAL: {exc}; можно повторить",
            status_before=status_before,
            status_after=get_status_fn(page),
            partner_present_before=partner_present_before,
            partner_present_after=partner_present_after,
            mutated=True,
            saved=False,
            error_code=ERROR_TECHNICAL,
            raw_error=str(exc),
        )

    status_after = get_status_fn(page)
    return _outcome(
        candidate,
        registry_value=REGISTRY_OK,
        registry_comment=f"Партнёр добавлен; статус карты: {status_after}",
        status_before=status_before,
        status_after=status_after,
        partner_present_before=partner_present_before,
        partner_present_after=partner_present_after,
        mutated=True,
        saved=True,
        error_code="",
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

    outcomes: list[EnableOutcome] = []
    allowed = build_allowed_status_set(settings.allowed_statuses_for_enable)

    with sync_playwright() as playwright:
        browser = None
        context = None
        page = None
        try:
            browser = playwright.chromium.launch(
                headless=cfg.headless,
                slow_mo=600,
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
                outcomes.append(
                    process_enable_candidate(
                        page,
                        candidate,
                        settings=settings,
                        cfg=cfg,
                        allowed_statuses=allowed,
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
) -> str:
    ok = sum(1 for o in outcomes if o.registry_value == REGISTRY_OK)
    skip = sum(1 for o in outcomes if o.registry_value == REGISTRY_SKIP)
    fail = sum(1 for o in outcomes if o.registry_value == REGISTRY_FAIL)

    def _count(code: str) -> int:
        return sum(1 for o in outcomes if o.error_code == code)

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

    lines = [
        "🧩 WalletEditor Auto-Enable",
        "mode: Phase B1 execution-only",
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
        f"- technical: {_count(ERROR_TECHNICAL)}",
        "",
        "⚠️ Registry не обновлялся. Это Phase B1 execution-only.",
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
