# integrations/wallet_editor_tg.py
from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from telegram import Update
from telegram.ext import ContextTypes

from automation.audit import log
from automation.worker import add_task, task_queue

ALLOWED_EXTENSION = ".xlsx"
TMP_DIR = Path("/tmp/wallet_editor")


def parse_allowed_chat_ids(env_value: str | None = None) -> frozenset[int]:
    raw = (
        env_value
        if env_value is not None
        else os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "")
    ).strip()
    if not raw:
        return frozenset()
    return frozenset(int(part.strip()) for part in raw.split(",") if part.strip())


def is_wallet_editor_chat_allowed(
    chat_id: int,
    *,
    allowed: frozenset[int] | None = None,
) -> bool:
    ids = allowed if allowed is not None else parse_allowed_chat_ids()
    if not ids:
        log.warning(
            "⚠️ [WalletEditor] TELEGRAM_ALLOWED_CHAT_IDS пуст — доступ запрещён (fail-closed)"
        )
        return False
    return chat_id in ids


def document_extension(file_name: str | None, file_path: str | None = None) -> str:
    for candidate in (file_name, file_path):
        if candidate:
            ext = Path(candidate).suffix.lower()
            if ext:
                return ext
    return ""


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

    ext = document_extension(document.file_name, document.file_path)
    if ext != ALLOWED_EXTENSION:
        log.info(f"❌ [WalletEditor] rejected extension={ext!r} chat_id={chat_id}")
        await message.reply_text("❌ Принимаются только файлы .xlsx")
        return

    try:
        await message.reply_text("📥 Файл получен")
        _ensure_tmp_dir()
        local_path = TMP_DIR / f"wallet_editor_{uuid4().hex}{ALLOWED_EXTENSION}"

        tg_file = await context.bot.get_file(document.file_id)
        await tg_file.download_to_drive(custom_path=str(local_path))

        add_task(str(local_path), chat_id)
        position = task_queue.qsize()

        log.info(
            f"📌 [WalletEditor] queued chat_id={chat_id} "
            f"queue_size={position} file={local_path}"
        )
        await message.reply_text(
            f"📌 Файл добавлен в очередь. Текущий размер очереди: {position}"
        )
    except Exception as e:
        log.exception(f"❌ [WalletEditor] ingest failed chat_id={chat_id}: {e}")
        await message.reply_text(f"❌ Ошибка при приёме файла: {e}")
