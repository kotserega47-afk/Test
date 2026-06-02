# integrations/telegram_bot.py

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from typing import Any

import requests
from dotenv import load_dotenv
from telegram import Bot, InputFile
from telegram.request import HTTPXRequest

from utils.logger import logger

# Загружаем .env (для CLI и прямых запусков)
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

_HEALTH_LOG_PREFIX = "[TelegramSender/health]"
_DEFAULT_DEGRADED_THRESHOLD = 3
_DEFAULT_HEALTH_LOG_INTERVAL_SECONDS = 600
_TOKEN_URL_RE = re.compile(r"bot\d+:[A-Za-z0-9_-]+", re.IGNORECASE)

# =====================================================
#   Telegram sender health (Option B)
# =====================================================

_health_lock = threading.Lock()
_last_health_log_at: float | None = None
_was_degraded = False
_queue_depth = 0

_health_state: dict[str, Any] = {
    "last_success_at": None,
    "last_failure_at": None,
    "consecutive_failures": 0,
    "total_enqueued": 0,
    "total_sent": 0,
    "total_failed": 0,
    "last_error_class": None,
    "last_error_message_safe": None,
    "last_target_chat_id": None,
    "last_message_kind": None,
    "queue_depth": 0,
}


def _degraded_threshold() -> int:
    raw = os.getenv("TELEGRAM_HEALTH_DEGRADED_THRESHOLD", str(_DEFAULT_DEGRADED_THRESHOLD)).strip()
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_DEGRADED_THRESHOLD
    return max(1, value)


def _health_log_interval_seconds() -> int:
    raw = os.getenv("TELEGRAM_HEALTH_LOG_INTERVAL_SECONDS", str(_DEFAULT_HEALTH_LOG_INTERVAL_SECONDS)).strip()
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_HEALTH_LOG_INTERVAL_SECONDS
    return max(1, value)


def _sanitize_error_message(message: str) -> str:
    text = (message or "").strip()
    token = (TELEGRAM_TOKEN or "").strip()
    if token:
        text = text.replace(token, "***")
    text = _TOKEN_URL_RE.sub("bot***", text)
    if len(text) > 500:
        text = text[:500] + "..."
    return text or "unknown"


def _error_class_name(exc: BaseException) -> str:
    return type(exc).__name__


def _is_degraded_locked() -> bool:
    return int(_health_state["consecutive_failures"]) >= _degraded_threshold()


def _sync_queue_depth_locked() -> None:
    _health_state["queue_depth"] = _queue_depth


def _record_enqueue(message_kind: str) -> None:
    global _queue_depth
    with _health_lock:
        _queue_depth += 1
        _health_state["total_enqueued"] = int(_health_state["total_enqueued"]) + 1
        _health_state["last_message_kind"] = message_kind
        _sync_queue_depth_locked()


def _record_dequeue() -> None:
    global _queue_depth
    with _health_lock:
        _queue_depth = max(0, _queue_depth - 1)
        _sync_queue_depth_locked()


def _record_delivery_success(chat_id: str, message_kind: str) -> None:
    global _was_degraded
    now = time.time()
    failures_before = 0
    should_recover = False

    with _health_lock:
        failures_before = int(_health_state["consecutive_failures"])
        should_recover = _was_degraded and failures_before >= _degraded_threshold()
        _health_state["total_sent"] = int(_health_state["total_sent"]) + 1
        _health_state["last_success_at"] = now
        _health_state["consecutive_failures"] = 0
        _health_state["last_target_chat_id"] = str(chat_id)
        _health_state["last_message_kind"] = message_kind
        _was_degraded = False

    if should_recover:
        logger.info(
            f"{_HEALTH_LOG_PREFIX} RECOVERED after {failures_before} failures"
        )


def _record_delivery_failure(chat_id: str, message_kind: str, exc: BaseException) -> None:
    global _was_degraded
    now = time.time()
    error_class = _error_class_name(exc)
    error_message = _sanitize_error_message(str(exc))
    threshold = _degraded_threshold()
    log_degraded = False
    consecutive = 0

    with _health_lock:
        _health_state["total_failed"] = int(_health_state["total_failed"]) + 1
        _health_state["last_failure_at"] = now
        consecutive = int(_health_state["consecutive_failures"]) + 1
        _health_state["consecutive_failures"] = consecutive
        _health_state["last_error_class"] = error_class
        _health_state["last_error_message_safe"] = error_message
        _health_state["last_target_chat_id"] = str(chat_id)
        _health_state["last_message_kind"] = message_kind
        if consecutive >= threshold:
            _was_degraded = True
            log_degraded = consecutive == threshold or consecutive % threshold == 0

    logger.error(f"❌ Ошибка async отправки: {error_message}")

    if log_degraded:
        level = logger.critical if consecutive >= threshold * 2 else logger.error
        level(
            f"{_HEALTH_LOG_PREFIX} DEGRADED consecutive_failures={consecutive} "
            f"last_error={error_class} chat_id={chat_id} kind={message_kind}"
        )


def _compute_status_locked() -> str:
    if not TELEGRAM_TOKEN:
        return "NO_TOKEN"
    if _is_degraded_locked():
        return "DEGRADED"

    total_sent = int(_health_state["total_sent"])
    total_failed = int(_health_state["total_failed"])
    total_enqueued = int(_health_state["total_enqueued"])
    consecutive = int(_health_state["consecutive_failures"])

    if total_enqueued == 0 and total_failed == 0:
        return "HEALTHY"
    if total_sent > 0 and consecutive == 0:
        return "HEALTHY"
    if total_failed > 0 and consecutive > 0:
        return "UNKNOWN"
    return "UNKNOWN"


def get_telegram_sender_health_snapshot() -> dict[str, Any]:
    now = time.time()
    try:
        with _health_lock:
            last_success_at = _health_state["last_success_at"]
            last_failure_at = _health_state["last_failure_at"]
            snapshot = {
                "status": _compute_status_locked(),
                "consecutive_failures": int(_health_state["consecutive_failures"]),
                "total_enqueued": int(_health_state["total_enqueued"]),
                "total_sent": int(_health_state["total_sent"]),
                "total_failed": int(_health_state["total_failed"]),
                "last_success_at": last_success_at,
                "last_failure_at": last_failure_at,
                "last_error_class": _health_state["last_error_class"],
                "last_message_kind": _health_state["last_message_kind"],
                "queue_depth": int(_health_state["queue_depth"]),
                "last_target_chat_id": _health_state["last_target_chat_id"],
            }

        if isinstance(last_success_at, (int, float)):
            snapshot["last_success_age_sec"] = round(now - last_success_at, 1)
        else:
            snapshot["last_success_age_sec"] = None

        if isinstance(last_failure_at, (int, float)):
            snapshot["last_failure_age_sec"] = round(now - last_failure_at, 1)
        else:
            snapshot["last_failure_age_sec"] = None

        return snapshot
    except Exception:
        return {
            "status": "UNKNOWN",
            "consecutive_failures": "unknown",
            "total_enqueued": "unknown",
            "total_sent": "unknown",
            "total_failed": "unknown",
            "last_success_at": None,
            "last_failure_at": None,
            "last_error_class": None,
            "last_message_kind": None,
            "queue_depth": "unknown",
            "last_success_age_sec": None,
            "last_failure_age_sec": None,
        }


def log_telegram_health_if_due(*, now: float | None = None) -> None:
    global _last_health_log_at
    if not TELEGRAM_TOKEN:
        return

    ts = time.time() if now is None else now
    interval = _health_log_interval_seconds()

    with _health_lock:
        global _last_health_log_at
        if _last_health_log_at is not None and (ts - _last_health_log_at) < interval:
            return
        _last_health_log_at = ts
        status = _compute_status_locked()
        consecutive = int(_health_state["consecutive_failures"])
        total_sent = int(_health_state["total_sent"])
        total_failed = int(_health_state["total_failed"])
        queue_depth = int(_health_state["queue_depth"])
        last_success_at = _health_state["last_success_at"]
        last_error_class = _health_state["last_error_class"]

    if isinstance(last_success_at, (int, float)):
        last_success_age = round(ts - last_success_at, 1)
    else:
        last_success_age = "unknown"

    if status == "DEGRADED":
        logger.error(
            f"{_HEALTH_LOG_PREFIX} DEGRADED consecutive_failures={consecutive} "
            f"last_error={last_error_class} queue_depth={queue_depth} "
            f"sent={total_sent} failed={total_failed} last_success_age={last_success_age}s"
        )
        return

    logger.info(
        f"{_HEALTH_LOG_PREFIX} HEALTHY sent={total_sent} failed={total_failed} "
        f"queue_depth={queue_depth} last_success_age={last_success_age}s"
    )


def _reset_telegram_sender_health_for_tests() -> None:
    global _last_health_log_at, _was_degraded, _queue_depth
    with _health_lock:
        _last_health_log_at = None
        _was_degraded = False
        _queue_depth = 0
        for key in _health_state:
            if key.endswith("_at") or key in {
                "last_error_class",
                "last_error_message_safe",
                "last_target_chat_id",
                "last_message_kind",
            }:
                _health_state[key] = None
            elif key == "queue_depth":
                _health_state[key] = 0
            else:
                _health_state[key] = 0


# =====================================================
#   HTTP client
# =====================================================

request = HTTPXRequest(
    connection_pool_size=100,
    connect_timeout=20.0,
    read_timeout=40.0,
)

bot = Bot(token=TELEGRAM_TOKEN, request=request) if TELEGRAM_TOKEN else None


# =====================================================
#   GLOBAL EVENT LOOP + BACKGROUND THREAD
# =====================================================

loop = asyncio.new_event_loop()
queue = asyncio.Queue()


def _loop_runner():
    """Фоновый поток, который крутит event loop постоянно."""
    asyncio.set_event_loop(loop)
    loop.run_forever()


threading.Thread(target=_loop_runner, daemon=True).start()


# =====================================================
#   Worker
# =====================================================

def _message_kind_for_func(func) -> str:
    return "text" if func is _send_message else "document"


async def _worker():
    while True:
        func, args = await queue.get()
        _record_dequeue()
        message_kind = _message_kind_for_func(func)
        chat_id = str(args[0])

        try:
            if bot is None:
                raise RuntimeError("NO_TOKEN")

            await func(*args)

            if func is _send_message:
                _, text = args
                logger.info(f"📤 Отправлено сообщение (chat_id={chat_id}): {text[:80]}")
            else:
                _, path, _caption = args
                logger.info(f"📁 Отправлен файл (chat_id={chat_id}): {path}")

            _record_delivery_success(chat_id, message_kind)

        except Exception as e:
            _record_delivery_failure(chat_id, message_kind, e)

        queue.task_done()


loop.call_soon_threadsafe(loop.create_task, _worker())


# =====================================================
#   async send funcs
# =====================================================

async def _send_message(chat_id: str, text: str):
    if bot is None:
        raise RuntimeError("NO_TOKEN")
    await bot.send_message(chat_id=chat_id, text=text)


async def _send_file(chat_id: str, path: str, caption: str | None):
    if bot is None:
        raise RuntimeError("NO_TOKEN")
    with open(path, "rb") as f:
        await bot.send_document(chat_id=chat_id, document=InputFile(f), caption=caption)


# =====================================================
#   PUBLIC sync API — chat_id ОБЯЗАТЕЛЕН
# =====================================================

def send_message_sync(text: str, chat_id: str):
    """
    Отправка текста в очередь.
    chat_id должен передаваться ЯВНО!
    """
    if not chat_id:
        raise ValueError("chat_id обязателен для send_message_sync")

    if not TELEGRAM_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN не задан — сообщение не отправлено")
        return

    try:
        loop.call_soon_threadsafe(
            queue.put_nowait,
            (_send_message, (chat_id, text))
        )
        _record_enqueue("text")
        logger.info(f"📨 Добавлено в очередь сообщение ({chat_id}): {text[:60]}")

    except Exception as e:
        logger.error(f"❌ Ошибка постановки в очередь send_message: {e}")


def send_photo_sync(photo_path: str, caption: str, chat_id: str):
    """Отправляет фото (например, скриншот) с подписью"""
    try:
        if not TELEGRAM_TOKEN:
            logger.warning("TELEGRAM_BOT_TOKEN не задан — файл не отправлен")
            return
        with open(photo_path, "rb") as photo:
            requests.post(
                f"{BASE_URL}/sendPhoto",
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "Markdown"},
                files={"photo": photo},
                timeout=30
            )
    except Exception as e:
        print(f"[telegram] Ошибка отправки фото: {e}")
        send_message_sync(f"⚠️ Ошибка при отправке скриншота: {e}", chat_id)


def send_file_sync(path: str, caption: str | None, chat_id: str):
    """
    Отправка файла в очередь.
    chat_id должен передаваться ЯВНО!
    """
    if not chat_id:
        raise ValueError("chat_id обязателен для send_file_sync")

    if not TELEGRAM_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN не задан — файл не отправлен")
        return

    try:
        loop.call_soon_threadsafe(
            queue.put_nowait,
            (_send_file, (chat_id, path, caption))
        )
        _record_enqueue("document")
        logger.info(f"📨 Файл поставлен в очередь ({chat_id}): {path}")

    except Exception as e:
        logger.error(f"❌ Ошибка постановки в очередь send_file: {e}")


# =====================================================
#   DIRECT SEND (используется только тестами)
# =====================================================

def send_message_direct(text: str, chat_id: str):
    """
    Прямая отправка без очереди — chat_id обязателен.
    """
    if not chat_id:
        raise ValueError("chat_id обязателен для send_message_direct")
    if not TELEGRAM_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    resp = requests.post(url, json={
        "chat_id": chat_id,
        "text": text
    }, timeout=10)

    resp.raise_for_status()
    return resp.json()
