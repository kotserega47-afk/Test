"""Best-effort bridge: Conversion problem_cards → Wallet Editor queue."""

from __future__ import annotations

import os
import traceback
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pandas as pd

from automation.runtime import WalletEditorTask, operator_auth_state_path
from automation.worker import add_task
from integrations.telegram_bot import send_message_sync
from integrations.telegram_routes import (
    ROUTE_CONVERSION_WALLET_EDITOR,
    routes_from_rules_v2_enabled,
    resolve_route_chat_id,
    send_message_to_route,
)
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["CONVERT"]
logger = get_logger(name, icon)

ACTION_REMOVE_PARTNER = "remove_partner"
OPERATOR_PROFILE = "CONVERSION_AUTO"
CONVERSION_WE_TELEGRAM_USER_ID = 0

ENV_CHAT_ID = "CONVERSION_WALLET_EDITOR"
ENV_LOGIN = "CONVERSION_WALLET_EDITOR_OPERATOR_PROFILE_LOGIN"
ENV_PASSWORD = "CONVERSION_WALLET_EDITOR_OPERATOR_PROFILE_PASSWORD"

WALLET_EDITOR_INPUT_DIR = Path("/tmp/wallet_editor")

INFO_MESSAGE_TEMPLATE = (
    "🧩 Conversion → Wallet Editor\n\n"
    "Найдено в problem_cards: {found_cards}\n"
    "Валидных: {valid_cards}\n"
    "Передано в Wallet Editor: {sent_cards}\n"
    "Отфильтровано (пустые card/partner): {filtered_invalid}"
)


@dataclass(frozen=True)
class ConversionWEBridgeStats:
    found_cards: int
    valid_cards: int
    sent_cards: int
    filtered_invalid: int


@dataclass(frozen=True)
class ConversionWEConfig:
    chat_id: int
    login: str
    password: str


def _non_empty_str(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    return text


def _row_is_valid(card: object, original_partner: object) -> bool:
    return bool(_non_empty_str(card)) and bool(_non_empty_str(original_partner))


def _count_problem_cards_rows(problem_cards: pd.DataFrame | None) -> int:
    if problem_cards is None or problem_cards.empty:
        return 0
    return len(problem_cards)


def map_problem_cards_to_wallet_editor_rows(problem_cards: pd.DataFrame) -> list[dict[str, str]]:
    """Filter invalid rows and map to Wallet Editor Excel contract. Preserves order."""
    if problem_cards is None or problem_cards.empty:
        return []

    rows: list[dict[str, str]] = []
    for _, row in problem_cards.iterrows():
        card = row.get("card", "")
        original_partner = row.get("original_partner", "")
        if not _row_is_valid(card, original_partner):
            continue
        rows.append(
            {
                "card": _non_empty_str(card),
                "action": ACTION_REMOVE_PARTNER,
                "value": _non_empty_str(original_partner),
            }
        )
    return rows


def compute_bridge_stats(found_cards: int, valid_cards: int) -> ConversionWEBridgeStats:
    return ConversionWEBridgeStats(
        found_cards=found_cards,
        valid_cards=valid_cards,
        sent_cards=valid_cards,
        filtered_invalid=found_cards - valid_cards,
    )


def build_wallet_editor_excel(rows: list[dict[str, str]], file_path: str) -> str:
    pd.DataFrame(rows, columns=["card", "action", "value"]).to_excel(file_path, index=False)
    return file_path


def _parse_chat_id(raw: str | None) -> int | None:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        logger.warning("[conversion_we] invalid CONVERSION_WALLET_EDITOR chat_id=%r", raw)
        return None


def _resolve_notification_chat_id() -> int | None:
    if routes_from_rules_v2_enabled():
        resolution = resolve_route_chat_id(ROUTE_CONVERSION_WALLET_EDITOR)
        return _parse_chat_id(resolution.chat_id)
    return _parse_chat_id(os.getenv(ENV_CHAT_ID, ""))


def resolve_conversion_we_config() -> tuple[ConversionWEConfig | None, str | None]:
    login = os.getenv(ENV_LOGIN, "").strip()
    password = os.getenv(ENV_PASSWORD, "").strip()
    chat_id = _resolve_notification_chat_id()

    missing: list[str] = []
    if chat_id is None:
        if routes_from_rules_v2_enabled():
            missing.append(ROUTE_CONVERSION_WALLET_EDITOR)
        else:
            missing.append(ENV_CHAT_ID)
    if not login:
        missing.append(ENV_LOGIN)
    if not password:
        missing.append(ENV_PASSWORD)

    if missing:
        return None, f"missing env: {', '.join(missing)}"

    return ConversionWEConfig(chat_id=chat_id, login=login, password=password), None


def _send_telegram_best_effort(chat_id: int | None, text: str) -> None:
    if routes_from_rules_v2_enabled():
        send_message_to_route(ROUTE_CONVERSION_WALLET_EDITOR, text)
        return

    if chat_id is None:
        logger.info("[conversion_we] telegram skipped (no chat_id): %s", text.replace("\n", " | "))
        return
    try:
        send_message_sync(text, chat_id=str(chat_id))
    except Exception:
        logger.exception("[conversion_we] failed to send telegram message")


def _build_source_file_name(conv_file: str | None) -> str:
    if conv_file:
        base = os.path.splitext(os.path.basename(conv_file))[0]
        return f"conversion_auto_{base}.xlsx"
    return f"conversion_auto_{uuid4().hex[:8]}.xlsx"


def _ensure_input_dir() -> None:
    WALLET_EDITOR_INPUT_DIR.mkdir(parents=True, exist_ok=True)


def maybe_enqueue_wallet_editor_from_problem_cards(
    problem_cards: pd.DataFrame,
    *,
    conv_file: str | None = None,
) -> None:
    """Best-effort hook after conversion analysis. Never raises."""
    try:
        found_cards = _count_problem_cards_rows(problem_cards)
        valid_rows = map_problem_cards_to_wallet_editor_rows(problem_cards)
        if not valid_rows:
            logger.debug("[conversion_we] no valid problem_cards rows — skip Wallet Editor")
            return

        stats = compute_bridge_stats(found_cards, len(valid_rows))
        config, config_error = resolve_conversion_we_config()
        if config is None:
            logger.warning("[conversion_we] Wallet Editor hook skipped: %s", config_error)
            chat_only = _resolve_notification_chat_id()
            if chat_only is not None:
                _send_telegram_best_effort(
                    chat_only,
                    f"⚠️ Conversion → Wallet Editor пропущен: {config_error}",
                )
            return

        info_message = INFO_MESSAGE_TEMPLATE.format(
            found_cards=stats.found_cards,
            valid_cards=stats.valid_cards,
            sent_cards=stats.sent_cards,
            filtered_invalid=stats.filtered_invalid,
        )
        logger.info(
            "[conversion_we] found=%s valid=%s sent=%s filtered_invalid=%s",
            stats.found_cards,
            stats.valid_cards,
            stats.sent_cards,
            stats.filtered_invalid,
        )
        _send_telegram_best_effort(config.chat_id, info_message)

        _ensure_input_dir()
        input_path = str(WALLET_EDITOR_INPUT_DIR / f"conversion_we_{uuid4().hex}.xlsx")
        build_wallet_editor_excel(valid_rows, input_path)

        task = WalletEditorTask(
            file_path=input_path,
            chat_id=config.chat_id,
            telegram_user_id=CONVERSION_WE_TELEGRAM_USER_ID,
            operator_profile=OPERATOR_PROFILE,
            source_file_name=_build_source_file_name(conv_file),
            login=config.login,
            password=config.password,
            auth_state_path=operator_auth_state_path(OPERATOR_PROFILE),
        )
        queue_size = add_task(task)
        logger.info(
            "[conversion_we] enqueued profile=%s chat_id=%s queue_size=%s rows=%s file=%s",
            task.operator_profile,
            task.chat_id,
            queue_size,
            stats.sent_cards,
            input_path,
        )
    except Exception as exc:
        logger.error("[conversion_we] hook failed: %s", exc)
        logger.debug(traceback.format_exc())
        _send_telegram_best_effort(
            _resolve_notification_chat_id(),
            f"❌ Conversion → Wallet Editor ошибка: {exc}",
        )
