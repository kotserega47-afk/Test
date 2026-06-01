import os
import time
import traceback
from pathlib import Path
from uuid import uuid4

import requests

from automation.worker import add_task, task_queue
from automation.audit import log
from transport.telegram_transport import send_text


def _telegram_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("Не задан TELEGRAM_BOT_TOKEN")
    return token


def _telegram_base() -> str:
    return f"https://api.telegram.org/bot{_telegram_token()}"

ALLOWED_EXTENSIONS = {".xlsx"}
ALLOWED_CHAT_IDS = {
    int(x) for x in os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").split(",") if x
}

TMP_DIR = Path("/tmp/wallet_editor")
TMP_DIR.mkdir(parents=True, exist_ok=True)


# =========================
# Telegram API
# =========================

def get_updates(offset: int | None = None) -> list[dict]:
    log.info(f"📡 [Receiver] polling telegram offset={offset}")

    response = requests.get(
        f"{_telegram_base()}/getUpdates",
        params={"timeout": 30, "offset": offset},
        timeout=40,
    )
    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(f"Telegram getUpdates error: {data}")

    result = data["result"]
    log.info(f"📥 [Receiver] updates received count={len(result)}")

    return result


def _get_file_path(file_id: str) -> str:
    log.info(f"📄 [Receiver] requesting telegram file_path file_id={file_id}")

    response = requests.get(
        f"{_telegram_base()}/getFile",
        params={"file_id": file_id},
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(f"Telegram getFile error: {data}")

    return data["result"]["file_path"]


# =========================
# Download
# =========================

def download_file(file_id: str, original_name: str | None) -> str:
    log.info(f"📥 [Receiver] start download file_id={file_id}")

    tg_file_path = _get_file_path(file_id)
    ext = Path(original_name or tg_file_path).suffix.lower()

    if ext not in ALLOWED_EXTENSIONS:
        log.error(f"❌ [Receiver] invalid extension: {ext}")
        raise ValueError(f"Недопустимый тип файла: {ext or 'unknown'}")

    unique_name = f"wallet_editor_{uuid4().hex}{ext}"
    local_path = TMP_DIR / unique_name

    url = f"https://api.telegram.org/file/bot{_telegram_token()}/{tg_file_path}"

    log.info(f"⬇️ [Receiver] downloading: name={original_name} → {local_path}")

    response = requests.get(url, timeout=120)
    response.raise_for_status()

    local_path.write_bytes(response.content)

    log.info(f"✅ [Receiver] downloaded: path={local_path} size={len(response.content)} bytes")

    return str(local_path)


# =========================
# Filters
# =========================

def _is_allowed_chat(chat_id: int) -> bool:
    if not ALLOWED_CHAT_IDS:
        log.warning("⚠️ [Receiver] TELEGRAM_ALLOWED_CHAT_IDS пуст — разрешены все чаты")
        return True
    return chat_id in ALLOWED_CHAT_IDS


# =========================
# Main loop
# =========================

def run_receiver() -> None:
    log.info(
        f"🚀 [Receiver] started allowed_chats={sorted(ALLOWED_CHAT_IDS) if ALLOWED_CHAT_IDS else 'ALL'}"
    )

    offset = None

    while True:
        try:
            updates = get_updates(offset)

            for update in updates:
                offset = update["update_id"] + 1

                msg = update.get("message", {})
                document = msg.get("document")
                chat = msg.get("chat", {})
                chat_id = int(chat.get("id", 0))

                log.info(
                    f"📨 [Receiver] update_id={update.get('update_id')} chat_id={chat_id} has_document={bool(document)}"
                )

                if not document:
                    continue

                if not _is_allowed_chat(chat_id):
                    log.info(f"⛔ [Receiver] chat ignored chat_id={chat_id}")
                    continue

                file_id = document["file_id"]
                file_name = document.get("file_name")

                try:
                    send_text(chat_id=str(chat_id), text="📥 Файл получен")

                    local_path = download_file(file_id, file_name)
                    add_task(local_path, chat_id)

                    position = task_queue.qsize()

                    log.info(
                        f"📌 [Receiver] task queued chat_id={chat_id} queue_size={position} file={local_path}"
                    )

                    send_text(
                        chat_id=str(chat_id),
                        text=f"📌 Файл добавлен в очередь. Текущий размер очереди: {position}",
                    )

                except Exception as e:
                    log.exception(f"❌ [Receiver] failed to receive file chat_id={chat_id}: {e}")

                    send_text(
                        chat_id=str(chat_id),
                        text=f"❌ Ошибка при приёме файла: {e}",
                    )

        except Exception as e:
            log.error(f"❌ [Receiver] loop crash: {e}")
            log.error(traceback.format_exc())
            time.sleep(5)