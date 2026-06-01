# integrations/wallet_editor_tg.py
from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from telegram import Update
from telegram.ext import ContextTypes

from automation.audit import log
from automation.runtime import (
    MSG_OPERATOR_INCOMPLETE,
    MSG_OPERATOR_UNMAPPED,
    WalletEditorTask,
    resolve_operator_for_user,
)
from automation.worker import add_task, task_queue

ALLOWED_EXTENSION = ".xlsx"
TMP_DIR = Path("/tmp/wallet_editor")
_WALLET_EDITOR_ALLOWED_CHAT_IDS_ENV = "WALLET_EDITOR_ALLOWED_CHAT_IDS"
_ALLOWLIST_STARTUP_LOGGED = False


def parse_allowed_chat_ids(env_value: str | None = None) -> frozenset[int]:
    raw = (
        env_value
        if env_value is not None
        else os.getenv(_WALLET_EDITOR_ALLOWED_CHAT_IDS_ENV, "")
    ).strip()
    if not raw:
        return frozenset()
    return frozenset(int(part.strip()) for part in raw.split(",") if part.strip())


def log_wallet_editor_allowlist_startup_warning() -> None:
    global _ALLOWLIST_STARTUP_LOGGED
    if _ALLOWLIST_STARTUP_LOGGED:
        return
    _ALLOWLIST_STARTUP_LOGGED = True

    ids = parse_allowed_chat_ids()
    if not ids:
        log.warning(
            "⚠️ [WalletEditor] WALLET_EDITOR_ALLOWED_CHAT_IDS пуст или не задан — "
            "ingest .xlsx отключён (fail-closed)"
        )
        return

    log.info(f"🟢 [WalletEditor] allowed chats configured: {sorted(ids)}")


def is_wallet_editor_chat_allowed(
    chat_id: int,
    *,
    allowed: frozenset[int] | None = None,
) -> bool:
    ids = allowed if allowed is not None else parse_allowed_chat_ids()
    if not ids:
        return False
    return chat_id in ids


def is_xlsx_file_name(file_name: str | None) -> bool:
    name = (file_name or "").strip()
    if not name:
        return False
    return name.lower().endswith(".xlsx")


def _telegram_user_id(update: Update) -> int | None:
    user = update.effective_user
    if user is not None:
        return int(user.id)
    message = update.message
    if message is not None and message.from_user is not None:
        return int(message.from_user.id)
    return None


def _ensure_tmp_dir() -> None:
    TMP_DIR.mkdir(parents=True, exist_ok=True)


async def handle_wallet_editor_document(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    message = update.message
    if not message or not message.document:
        return

    chat_id = int(update.effective_chat.id)
    document = message.document

    if not is_wallet_editor_chat_allowed(chat_id):
        log.info(f"⛔ [WalletEditor] chat denied chat_id={chat_id}")
        await message.reply_text("⛔ Чат не разрешён для WalletEditor.")
        return

    if not is_xlsx_file_name(document.file_name):
        log.info(
            f"❌ [WalletEditor] rejected file_name={document.file_name!r} chat_id={chat_id}"
        )
        await message.reply_text("❌ Принимаются только файлы .xlsx")
        return

    telegram_user_id = _telegram_user_id(update)
    if telegram_user_id is None:
        log.warning(f"⚠️ [WalletEditor] missing sender user_id chat_id={chat_id}")
        await message.reply_text(MSG_OPERATOR_UNMAPPED)
        return

    operator, error_message = resolve_operator_for_user(telegram_user_id)
    if operator is None:
        log.info(
            f"⛔ [WalletEditor] operator denied user_id={telegram_user_id} chat_id={chat_id}"
        )
        await message.reply_text(error_message or MSG_OPERATOR_UNMAPPED)
        return

    try:
        await message.reply_text("📥 Файл получен")
        _ensure_tmp_dir()
        local_path = TMP_DIR / f"wallet_editor_{uuid4().hex}{ALLOWED_EXTENSION}"

        tg_file = await context.bot.get_file(document.file_id)
        await tg_file.download_to_drive(custom_path=str(local_path))

        add_task(
            WalletEditorTask(
                file_path=str(local_path),
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
                operator_profile=operator.profile_key,
                login=operator.login,
                password=operator.password,
                auth_state_path=operator.auth_state_path,
            )
        )
        position = task_queue.qsize()

        log.info(
            f"📌 [WalletEditor] queued profile={operator.profile_key} "
            f"user_id={telegram_user_id} chat_id={chat_id} "
            f"queue_size={position} file={local_path}"
        )
        await message.reply_text(
            f"📌 Файл добавлен в очередь. Текущий размер очереди: {position}"
        )
    except Exception as e:
        log.exception(f"❌ [WalletEditor] ingest failed chat_id={chat_id}: {e}")
        await message.reply_text(f"❌ Ошибка при приёме файла: {e}")


log_wallet_editor_allowlist_startup_warning()
