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
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["CONVERT"]
logger = get_logger(name, icon)

MAX_CONVERSION_WALLET_EDITOR_CARDS_PER_RUN = 10

ACTION_REMOVE_PARTNER = "remove_partner"
OPERATOR_PROFILE = "CONVERSION_AUTO"
CONVERSION_WE_TELEGRAM_USER_ID = 0

ENV_CHAT_ID = "CONVERSION_WALLET_EDITOR"
ENV_LOGIN = "CONVERSION_WALLET_EDITOR_OPERATOR_PROFILE_LOGIN"
ENV_PASSWORD = "CONVERSION_WALLET_EDITOR_OPERATOR_PROFILE_PASSWORD"

WALLET_EDITOR_INPUT_DIR = Path("/tmp/wallet_editor")

INFO_MESSAGE_TEMPLATE = (
    "🧩 Conversion → Wallet Editor\n\n"
    "Найдено карт для отключения: {total_found_cards}\n"
    "Передано в Wallet Editor: {processed_cards}\n"
    "Оставлено: {skipped_cards}"
)


@dataclass(frozen=True)
class ConversionWERolloutStats:
    total_found_cards: int
    processed_cards: int
    skipped_cards: int


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


def compute_rollout_stats(total_found_cards: int) -> ConversionWERolloutStats:
    processed_cards = min(total_found_cards, MAX_CONVERSION_WALLET_EDITOR_CARDS_PER_RUN)
    skipped_cards = total_found_cards - processed_cards
    return ConversionWERolloutStats(
        total_found_cards=total_found_cards,
        processed_cards=processed_cards,
        skipped_cards=skipped_cards,
    )


def apply_card_limit(rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], ConversionWERolloutStats]:
    stats = compute_rollout_stats(len(rows))
    return rows[: stats.processed_cards], stats


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


def resolve_conversion_we_config() -> tuple[ConversionWEConfig | None, str | None]:
    chat_raw = os.getenv(ENV_CHAT_ID, "")
    login = os.getenv(ENV_LOGIN, "").strip()
    password = os.getenv(ENV_PASSWORD, "").strip()
    chat_id = _parse_chat_id(chat_raw)

    missing: list[str] = []
    if chat_id is None:
        missing.append(ENV_CHAT_ID)
    if not login:
        missing.append(ENV_LOGIN)
    if not password:
        missing.append(ENV_PASSWORD)

    if missing:
        return None, f"missing env: {', '.join(missing)}"

    return ConversionWEConfig(chat_id=chat_id, login=login, password=password), None


def _send_telegram_best_effort(chat_id: int | None, text: str) -> None:
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
        valid_rows = map_problem_cards_to_wallet_editor_rows(problem_cards)
        if not valid_rows:
            logger.debug("[conversion_we] no valid problem_cards rows — skip Wallet Editor")
            return

        limited_rows, stats = apply_card_limit(valid_rows)
        config, config_error = resolve_conversion_we_config()
        if config is None:
            logger.warning("[conversion_we] Wallet Editor hook skipped: %s", config_error)
            chat_only = _parse_chat_id(os.getenv(ENV_CHAT_ID))
            if chat_only is not None:
                _send_telegram_best_effort(
                    chat_only,
                    f"⚠️ Conversion → Wallet Editor пропущен: {config_error}",
                )
            return

        info_message = INFO_MESSAGE_TEMPLATE.format(
            total_found_cards=stats.total_found_cards,
            processed_cards=stats.processed_cards,
            skipped_cards=stats.skipped_cards,
        )
        logger.info(
            "[conversion_we] rollout total=%s processed=%s skipped=%s",
            stats.total_found_cards,
            stats.processed_cards,
            stats.skipped_cards,
        )
        _send_telegram_best_effort(config.chat_id, info_message)

        _ensure_input_dir()
        input_path = str(WALLET_EDITOR_INPUT_DIR / f"conversion_we_{uuid4().hex}.xlsx")
        build_wallet_editor_excel(limited_rows, input_path)

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
            "[conversion_we] enqueued profile=%s chat_id=%s queue_size=%s file=%s",
            task.operator_profile,
            task.chat_id,
            queue_size,
            input_path,
        )
    except Exception as exc:
        logger.error("[conversion_we] hook failed: %s", exc)
        logger.debug(traceback.format_exc())
        chat_id = _parse_chat_id(os.getenv(ENV_CHAT_ID))
        _send_telegram_best_effort(
            chat_id,
            f"❌ Conversion → Wallet Editor ошибка: {exc}",
        )
