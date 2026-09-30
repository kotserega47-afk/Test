# integrations/telegram_bot.py

from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from dataclasses import dataclass
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
_TELEGRAM_API_BOT_URL_RE = re.compile(
    r"https://api\.telegram\.org/bot[^/\s\"'<>]+",
    re.IGNORECASE,
)
_TELEGRAM_API_FILE_BOT_URL_RE = re.compile(
    r"https://api\.telegram\.org/file/bot[^/\s\"'<>]+",
    re.IGNORECASE,
)
_TOKEN_ENV_NAMES = ("TELEGRAM_BOT_TOKEN", "TG_BOT_TOKEN")

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


def _known_telegram_tokens() -> tuple[str, ...]:
    seen: set[str] = set()
    tokens: list[str] = []
    for env_name in _TOKEN_ENV_NAMES:
        value = (os.getenv(env_name) or "").strip()
        if value and value not in seen:
            seen.add(value)
            tokens.append(value)
    module_token = (TELEGRAM_TOKEN or "").strip()
    if module_token and module_token not in seen:
        tokens.append(module_token)
    return tuple(tokens)


def _sanitize_error_message(message: str) -> str:
    text = (message or "").strip()
    for token in _known_telegram_tokens():
        text = text.replace(token, "<redacted>")
    text = _TELEGRAM_API_FILE_BOT_URL_RE.sub(
        "https://api.telegram.org/file/bot<redacted>",
        text,
    )
    text = _TELEGRAM_API_BOT_URL_RE.sub(
        "https://api.telegram.org/bot<redacted>",
        text,
    )
    text = _TOKEN_URL_RE.sub("bot<redacted>", text)
    if len(text) > 500:
        text = text[:500] + "..."
    return text or "unknown"


def sanitize_telegram_error(message: str) -> str:
    """Public wrapper for redacting Telegram bot tokens from log/alert text."""
    return _sanitize_error_message(message)


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

_WORKER_READY_TIMEOUT_SECONDS = 5.0
_RECENT_INTAKE_FAILURE_LIMIT = 32

_lifecycle_lock = threading.Lock()
# RUNNING | DRAINING | WORKER_STOPPED | HTTP_STOPPING | HTTP_STOPPED | LOOP_STOPPING | STOPPED
_lifecycle_state = "RUNNING"
_intake_sealed = False
_pending_loop_handoffs = 0  # S1
_active_user_sends = 0  # S3
_terminal_intake_failure_total = 0
_recent_intake_failures: list[str] = []
_s1_idle = threading.Event()
_s1_idle.set()
_s3_idle = threading.Event()
_s3_idle.set()
_worker_task: asyncio.Task | None = None
_worker_owned_queue: asyncio.Queue | None = None
_worker_ready = threading.Event()
_drain_owner_proof: object | None = None

# Full-stop HTTP / loop phase (TASK-48); owner is `_drain_owner_proof`.
_http_close_plan: tuple[tuple[Any, ...], tuple[Any, ...]] | None = None  # (roles, close_targets)
_http_request_results: dict[int, Any] = {}  # id(target) → SenderRequestCloseResult
_bot_shutdown_attempted = False
_bot_shutdown_ok = False
_bot_shutdown_error_type: str | None = None
_bot_shutdown_error_text: str | None = None
_http_phase_ack = threading.Event()  # set when leaving HTTP_STOPPING
_http_close_session_task: asyncio.Task | None = None  # owner-loop HTTP close session
_loop_stop_requested = False
_loop_stopped = threading.Event()

_POST_WORKER_STATES = frozenset(
    {
        "WORKER_STOPPED",
        "HTTP_STOPPING",
        "HTTP_STOPPED",
        "LOOP_STOPPING",
        "STOPPED",
    }
)

# Sentinel protocol: NOT_SUBMITTED → SCHEDULED → ENQUEUED | FAILED
_SENTINEL_NOT_SUBMITTED = "NOT_SUBMITTED"
_SENTINEL_SCHEDULED = "SCHEDULED"
_SENTINEL_ENQUEUED = "ENQUEUED"
_SENTINEL_FAILED = "FAILED"
_sentinel_state = _SENTINEL_NOT_SUBMITTED
_sentinel_ack = threading.Event()  # set on ENQUEUED or FAILED
_sentinel_failure: BaseException | None = None

_ATTR_ABSENT = object()


class TelegramSenderIntakeClosedError(RuntimeError):
    """Raised when send_* is attempted after intake seal."""


class _WorkerStopSentinel:
    """Private control item; not a user (func, args) payload."""

    __slots__ = ()


_WORKER_STOP_SENTINEL = _WorkerStopSentinel()


def _loop_runner():
    """Фоновый поток, который крутит event loop постоянно."""
    try:
        asyncio.set_event_loop(loop)
        loop.run_forever()
    finally:
        _loop_stopped.set()


_loop_thread = threading.Thread(target=_loop_runner, daemon=True, name="telegram-sender-loop")
_loop_thread.start()
_real_call_soon_threadsafe = loop.call_soon_threadsafe


def _record_terminal_intake_failure_locked(kind: str, detail: str) -> None:
    global _terminal_intake_failure_total
    _terminal_intake_failure_total += 1
    entry = f"{kind}:{_sanitize_error_message(detail)}"
    _recent_intake_failures.append(entry)
    if len(_recent_intake_failures) > _RECENT_INTAKE_FAILURE_LIMIT:
        del _recent_intake_failures[: len(_recent_intake_failures) - _RECENT_INTAKE_FAILURE_LIMIT]


def _close_s1_handoff(*, failure: BaseException | None, kind: str) -> None:
    global _pending_loop_handoffs
    with _lifecycle_lock:
        if _pending_loop_handoffs > 0:
            _pending_loop_handoffs -= 1
        if _pending_loop_handoffs == 0:
            _s1_idle.set()
        if failure is not None:
            _record_terminal_intake_failure_locked(kind, str(failure))


def _loop_side_enqueue(payload: object, message_kind: str) -> None:
    """Owner-loop enqueue: close S1 after put attempt (D28)."""

    target = _worker_owned_queue if _worker_owned_queue is not None else queue
    try:
        target.put_nowait(payload)
    except Exception as exc:
        _close_s1_handoff(failure=exc, kind="d28_put")
        return

    _close_s1_handoff(failure=None, kind="d28_put")
    # Health/diagnostics are best-effort after successful queue put.
    try:
        _record_enqueue(message_kind)
    except Exception:
        pass


def _enqueue_sentinel_on_loop() -> None:
    """Owner-loop: put sentinel and publish ENQUEUED/FAILED ack (never silent)."""

    global _sentinel_state, _sentinel_failure
    target = _worker_owned_queue if _worker_owned_queue is not None else queue
    try:
        target.put_nowait(_WORKER_STOP_SENTINEL)
    except Exception as exc:
        with _lifecycle_lock:
            _sentinel_state = _SENTINEL_FAILED
            _sentinel_failure = exc
            # Control/resource failure — NOT a D27/D28 user intake failure.
            _sentinel_ack.set()
        return
    with _lifecycle_lock:
        _sentinel_state = _SENTINEL_ENQUEUED
        _sentinel_failure = None
        _sentinel_ack.set()


async def _queue_join_watching_worker() -> None:
    """Join owned queue on sender loop; fail fast if worker dies mid-drain."""

    target = _worker_owned_queue if _worker_owned_queue is not None else queue
    join_task = asyncio.create_task(
        target.join(),
        name="telegram-sender-queue-join-waiter",
    )

    async def _cancel_join_waiter() -> None:
        if join_task.done():
            return
        join_task.cancel()
        try:
            await join_task
        except asyncio.CancelledError:
            pass

    try:
        while True:
            worker = _worker_task
            if worker is not None and worker.done() and not join_task.done():
                # Worker may already be cleanly terminal with queue fully accounted.
                unfinished = getattr(target, "_unfinished_tasks", 0)
                if target.qsize() > 0 or unfinished > 0:
                    await _cancel_join_waiter()
                    raise RuntimeError("unexpected_dead_worker")
            if join_task.done():
                await join_task
                return
            done, _pending = await asyncio.wait({join_task}, timeout=0.05)
            if done:
                await join_task
                return
    except asyncio.CancelledError:
        await _cancel_join_waiter()
        raise
    except Exception:
        await _cancel_join_waiter()
        raise


async def _worker_status_on_sender_loop() -> dict[str, bool]:
    task = _worker_task
    return {
        "task_missing": task is None,
        "task_done": task is None or task.done(),
        "queue_ok": queue is not None,
    }


def _admit_user_send(func, args: tuple, message_kind: str) -> None:
    """Atomic seal check + S1 increment, then schedule loop-side enqueue."""

    payload = (func, args)
    with _lifecycle_lock:
        if _intake_sealed:
            raise TelegramSenderIntakeClosedError(
                "telegram sender intake is sealed; send rejected"
            )
        global _pending_loop_handoffs
        _pending_loop_handoffs += 1
        _s1_idle.clear()

    try:
        loop.call_soon_threadsafe(_loop_side_enqueue, payload, message_kind)
    except Exception as exc:
        # D27: scheduling failed after +handoff — decrement exactly once.
        _close_s1_handoff(failure=exc, kind="d27_schedule")
        raise


def _start_worker_on_loop() -> None:
    global _worker_task
    _worker_task = loop.create_task(_worker(), name="telegram-sender-worker")
    _worker_ready.set()


# =====================================================
#   Worker
# =====================================================

def _message_kind_for_func(func) -> str:
    return "text" if func is _send_message else "document"


async def _worker():
    # Bind queue for this worker lifetime so test resets replacing ``queue``
    # cannot make task_done hit a different Queue object.
    global _worker_owned_queue
    owned_queue = queue
    _worker_owned_queue = owned_queue
    while True:
        item = await owned_queue.get()
        try:
            if isinstance(item, _WorkerStopSentinel):
                return

            func, args = item
            try:
                _record_dequeue()
            except Exception:
                pass

            global _active_user_sends
            with _lifecycle_lock:
                _active_user_sends += 1
                _s3_idle.clear()

            message_kind = _message_kind_for_func(func)
            chat_id = str(args[0])
            try:
                await func(*args)

                try:
                    if func is _send_message:
                        _, text = args
                        logger.info(
                            f"📤 Отправлено сообщение (chat_id={chat_id}): {text[:80]}"
                        )
                    else:
                        _, path, _caption = args
                        logger.info(f"📁 Отправлен файл (chat_id={chat_id}): {path}")
                except Exception:
                    pass

                try:
                    _record_delivery_success(chat_id, message_kind)
                except Exception:
                    pass

            except Exception as e:
                try:
                    _record_delivery_failure(chat_id, message_kind, e)
                except Exception:
                    pass
            finally:
                with _lifecycle_lock:
                    if _active_user_sends > 0:
                        _active_user_sends -= 1
                    if _active_user_sends == 0:
                        _s3_idle.set()
        finally:
            # Exact-once unfinished accounting for every successful queue.get().
            owned_queue.task_done()


loop.call_soon_threadsafe(_start_worker_on_loop)


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
        _admit_user_send(_send_message, (chat_id, text), "text")
        logger.info(f"📨 Добавлено в очередь сообщение ({chat_id}): {text[:60]}")
    except TelegramSenderIntakeClosedError:
        raise
    except Exception as e:
        logger.error(
            f"❌ Ошибка постановки в очередь send_message: "
            f"{_sanitize_error_message(str(e))}"
        )


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
        safe_error = _sanitize_error_message(str(e))
        print(f"[telegram] Ошибка отправки фото: {safe_error}")
        send_message_sync(
            f"⚠️ Ошибка при отправке скриншота: {safe_error}",
            chat_id,
        )


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
        _admit_user_send(_send_file, (chat_id, path, caption), "document")
        logger.info(f"📨 Файл поставлен в очередь ({chat_id}): {path}")
    except TelegramSenderIntakeClosedError:
        raise
    except Exception as e:
        logger.error(
            f"❌ Ошибка постановки в очередь send_file: "
            f"{_sanitize_error_message(str(e))}"
        )


# =====================================================
#   Worker drain (TASK-47) — NOT full resource stop
# =====================================================

from core.antares_sender_ownership import validate_antares_sender_ownership  # noqa: E402
from integrations.telegram_sender_gates import inspect_sender_ptb_compatibility  # noqa: E402


@dataclass(frozen=True, slots=True)
class SenderWorkerDrainResult:
    """Structured worker-drain outcome. Never claims full sender resource stop."""

    ok: bool
    reason: str | None
    ownership_passed: bool
    ptb_passed: bool
    structural_passed: bool
    intake_sealed: bool
    lifecycle_state: str
    pending_loop_handoffs: int
    active_user_sends: int
    queue_drained: bool
    sentinel_submitted: bool
    worker_terminal: bool
    loop_running: bool
    loop_thread_alive: bool
    worker_stopped: bool
    full_resource_stopped: bool
    terminal_intake_failure_total: int
    recent_intake_failures: tuple[str, ...]


def _snapshot_drain_fields(
    *,
    ok: bool,
    reason: str | None,
    ownership_passed: bool,
    ptb_passed: bool,
    structural_passed: bool,
    queue_drained: bool = False,
    sentinel_submitted: bool | None = None,
    worker_terminal: bool | None = None,
) -> SenderWorkerDrainResult:
    with _lifecycle_lock:
        sealed = _intake_sealed
        state = _lifecycle_state
        s1 = _pending_loop_handoffs
        s3 = _active_user_sends
        total_fail = _terminal_intake_failure_total
        recent = tuple(_recent_intake_failures)
        # Truthful: only ENQUEUED means sentinel is on the queue.
        sent_flag = (
            (_sentinel_state == _SENTINEL_ENQUEUED)
            if sentinel_submitted is None
            else sentinel_submitted
        )
        task = _worker_task
    terminal = worker_terminal
    if terminal is None:
        if state in _POST_WORKER_STATES:
            terminal = True
        else:
            terminal = task is not None and task.done()
    loop_running = bool(loop.is_running())
    thread_alive = bool(_loop_thread.is_alive())
    worker_stopped = state in _POST_WORKER_STATES and bool(terminal)
    full_resource_stopped = (
        state == "STOPPED" and (not loop_running) and (not thread_alive)
    )
    return SenderWorkerDrainResult(
        ok=ok,
        reason=reason,
        ownership_passed=ownership_passed,
        ptb_passed=ptb_passed,
        structural_passed=structural_passed,
        intake_sealed=sealed,
        lifecycle_state=state,
        pending_loop_handoffs=s1,
        active_user_sends=s3,
        queue_drained=queue_drained,
        sentinel_submitted=sent_flag,
        worker_terminal=bool(terminal),
        loop_running=loop_running,
        loop_thread_alive=thread_alive,
        worker_stopped=worker_stopped,
        full_resource_stopped=full_resource_stopped,
        terminal_intake_failure_total=total_fail,
        recent_intake_failures=recent,
    )


def _structural_observations() -> tuple[bool, str | None]:
    """Non-blocking structural checks (lifecycle/loop only; no Event.wait)."""

    if loop is None or not loop.is_running():
        return False, "sender_loop_unavailable"
    if _loop_thread is None or not _loop_thread.is_alive():
        return False, "loop_thread_unavailable"
    if not _worker_ready.is_set():
        return False, "worker_not_ready"
    if queue is None:
        return False, "queue_unavailable"
    with _lifecycle_lock:
        if _lifecycle_state not in ("RUNNING", "DRAINING"):
            if _lifecycle_state in _POST_WORKER_STATES:
                return True, None
            return False, "lifecycle_refuses_drain"
    return True, None


async def _structural_preflight_async(deadline: float) -> tuple[bool, str | None]:
    """Async structural preflight: never blocks the caller event loop."""

    if loop is None or not loop.is_running():
        return False, "sender_loop_unavailable"
    if _loop_thread is None or not _loop_thread.is_alive():
        return False, "loop_thread_unavailable"
    # Use the ONE overall drain deadline (no independent wait budget).
    if not await _wait_event(_worker_ready, deadline):
        return False, "worker_not_ready"
    try:
        status = await _run_on_sender_loop(_worker_status_on_sender_loop, deadline)
    except TimeoutError:
        return False, "worker_status_deadline"
    if status["task_missing"]:
        return False, "worker_task_unavailable"
    if status["task_done"]:
        return False, "worker_already_dead"
    if not status["queue_ok"]:
        return False, "queue_unavailable"
    with _lifecycle_lock:
        if _lifecycle_state not in ("RUNNING", "DRAINING"):
            if _lifecycle_state in _POST_WORKER_STATES:
                return True, None
            return False, "lifecycle_refuses_drain"
    return True, None


async def _wait_event(event: threading.Event, deadline: float) -> bool:
    while not event.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.02, remaining))
    return True


async def _run_on_sender_loop(coro_factory, deadline: float):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("sender drain deadline exceeded")
    fut = asyncio.run_coroutine_threadsafe(coro_factory(), loop)
    return await asyncio.wait_for(asyncio.wrap_future(fut), timeout=remaining)


@dataclass(frozen=True, slots=True)
class WorkerTerminalOutcome:
    """Result of observing sender-loop owned `_worker_task` termination."""

    terminal: bool
    clean: bool
    reason: str | None = None
    diagnostic: str | None = None


async def _await_worker_terminal() -> WorkerTerminalOutcome:
    """Observe worker Task completion without cancelling it (shield).

    Distinguishes caller-waiter cancellation (re-raise) from unexpected
    worker cancel/exception (structured unclean outcome).
    """

    task = _worker_task
    if task is None:
        return WorkerTerminalOutcome(
            terminal=True,
            clean=False,
            reason="unexpected_dead_worker",
            diagnostic="worker_task_unavailable",
        )

    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # Worker itself cancelled → structured SND13 remainder.
        # Waiter cancelled while worker still alive → re-raise for caller.
        if task.cancelled():
            return WorkerTerminalOutcome(
                terminal=True,
                clean=False,
                reason="unexpected_dead_worker",
                diagnostic="worker_cancelled",
            )
        raise
    except Exception as exc:
        return WorkerTerminalOutcome(
            terminal=True,
            clean=False,
            reason="unexpected_dead_worker",
            diagnostic=type(exc).__name__,
        )

    return WorkerTerminalOutcome(terminal=True, clean=True, reason=None)


def _apply_worker_terminal_outcome(
    outcome: object,
    *,
    ptb_passed: bool,
    structural_passed: bool,
    sentinel_submitted: bool,
) -> SenderWorkerDrainResult:
    """Map WorkerTerminalOutcome to drain snapshot; sole DRAINING→WORKER_STOPPED gate."""

    global _lifecycle_state

    if not isinstance(outcome, WorkerTerminalOutcome) or not outcome.clean:
        reason = (
            outcome.reason
            if isinstance(outcome, WorkerTerminalOutcome) and outcome.reason
            else "unexpected_dead_worker"
        )
        return _snapshot_drain_fields(
            ok=False,
            reason=reason,
            ownership_passed=True,
            ptb_passed=ptb_passed,
            structural_passed=structural_passed,
            queue_drained=True,
            sentinel_submitted=sentinel_submitted,
            worker_terminal=True,
        )

    with _lifecycle_lock:
        _lifecycle_state = "WORKER_STOPPED"

    return _snapshot_drain_fields(
        ok=True,
        reason=None,
        ownership_passed=True,
        ptb_passed=ptb_passed,
        structural_passed=structural_passed,
        queue_drained=True,
        sentinel_submitted=sentinel_submitted,
        worker_terminal=True,
    )


async def _observe_worker_terminal_after_sentinel(
    deadline: float,
    *,
    ptb_passed: bool,
    structural_passed: bool,
    sentinel_submitted: bool,
) -> SenderWorkerDrainResult:
    """Await (or immediately classify) worker terminal after sentinel ENQUEUED."""

    try:
        outcome = await _run_on_sender_loop(_await_worker_terminal, deadline)
    except TimeoutError:
        return _snapshot_drain_fields(
            ok=False,
            reason="deadline_worker_terminal",
            ownership_passed=True,
            ptb_passed=ptb_passed,
            structural_passed=structural_passed,
            queue_drained=True,
            sentinel_submitted=sentinel_submitted,
            worker_terminal=False,
        )

    return _apply_worker_terminal_outcome(
        outcome,
        ptb_passed=ptb_passed,
        structural_passed=structural_passed,
        sentinel_submitted=sentinel_submitted,
    )


async def _ensure_sentinel_enqueued(deadline: float) -> BaseException | None:
    """Schedule sentinel put once; wait for loop-side ENQUEUED/FAILED ack.

    Returns None on ENQUEUED, exception on failure/timeout. Never reports
    enqueued merely because call_soon accepted the callback.
    """

    global _sentinel_state, _sentinel_failure

    with _lifecycle_lock:
        state = _sentinel_state
        if state == _SENTINEL_ENQUEUED:
            return None
        if state == _SENTINEL_FAILED:
            # Terminal put failure already known — clear for same-owner retry.
            err = _sentinel_failure or RuntimeError("sentinel_put_failed")
            _sentinel_state = _SENTINEL_NOT_SUBMITTED
            _sentinel_failure = None
            _sentinel_ack.clear()
            # Fall through to schedule a new attempt.
            state = _SENTINEL_NOT_SUBMITTED
        if state == _SENTINEL_NOT_SUBMITTED:
            _sentinel_ack.clear()
            _sentinel_failure = None
            try:
                _real_call_soon_threadsafe(_enqueue_sentinel_on_loop)
            except Exception as exc:
                _sentinel_state = _SENTINEL_FAILED
                _sentinel_failure = exc
                # Control/resource failure — NOT a D27/D28 user intake failure.
                _sentinel_ack.set()
                return exc
            _sentinel_state = _SENTINEL_SCHEDULED
        # SCHEDULED: wait for existing/new acknowledgement only.

    if not await _wait_event(_sentinel_ack, deadline):
        return TimeoutError("deadline_sentinel_ack")

    with _lifecycle_lock:
        if _sentinel_state == _SENTINEL_ENQUEUED:
            return None
        if _sentinel_state == _SENTINEL_FAILED:
            return _sentinel_failure or RuntimeError("sentinel_put_failed")
        return RuntimeError(f"sentinel_ack_inconsistent:{_sentinel_state}")


async def drain_and_stop_sender_worker(
    ownership_proof: object,
    *,
    timeout: float = 30.0,
) -> SenderWorkerDrainResult:
    """Seal intake, drain accepted queue work, stop worker Task.

    Does **not** close Bot/HTTP/loop/thread. Success ⇒ WORKER_STOPPED only.
    """

    global _intake_sealed, _lifecycle_state, _drain_owner_proof

    deadline = time.monotonic() + max(0.0, float(timeout))
    ownership = validate_antares_sender_ownership(ownership_proof)
    if not ownership.ok:
        return _snapshot_drain_fields(
            ok=False,
            reason=f"ownership_{ownership.reason or 'refused'}",
            ownership_passed=False,
            ptb_passed=False,
            structural_passed=False,
        )

    with _lifecycle_lock:
        state = _lifecycle_state
        sealed = _intake_sealed
        owner = _drain_owner_proof

    # Post-worker states: worker already stopped; no sentinel / PTB / restart.
    if state in _POST_WORKER_STATES:
        if owner is not None and ownership_proof is not owner:
            return _snapshot_drain_fields(
                ok=False,
                reason="ownership_foreign_after_worker_stopped",
                ownership_passed=False,
                ptb_passed=False,
                structural_passed=False,
                queue_drained=True,
                sentinel_submitted=True,
                worker_terminal=True,
            )
        return _snapshot_drain_fields(
            ok=True,
            reason=None,
            ownership_passed=True,
            ptb_passed=True,
            structural_passed=True,
            queue_drained=True,
            sentinel_submitted=True,
            worker_terminal=True,
        )

    already_draining = state == "DRAINING" and sealed
    ptb_passed = True
    structural_passed = True

    if not already_draining:
        ptb = inspect_sender_ptb_compatibility(
            bot=bot,
            expected_general_request=request,
        )
        if not ptb.supported:
            return _snapshot_drain_fields(
                ok=False,
                reason=f"ptb_{ptb.reason or 'refused'}",
                ownership_passed=True,
                ptb_passed=False,
                structural_passed=False,
            )

        structural_ok, structural_reason = await _structural_preflight_async(deadline)
        if not structural_ok:
            return _snapshot_drain_fields(
                ok=False,
                reason=structural_reason or "structural_refused",
                ownership_passed=True,
                ptb_passed=True,
                structural_passed=False,
            )

        with _lifecycle_lock:
            if _lifecycle_state in _POST_WORKER_STATES:
                already_draining = False
                # Race: another waiter finished; fall through to idempotent path below.
            elif _lifecycle_state == "DRAINING" and _intake_sealed:
                already_draining = True
            else:
                # Atomic seal + enter DRAINING under lifecycle lock.
                _intake_sealed = True
                _lifecycle_state = "DRAINING"
                _drain_owner_proof = ownership_proof
                already_draining = True

        with _lifecycle_lock:
            if _lifecycle_state in _POST_WORKER_STATES:
                return _snapshot_drain_fields(
                    ok=True,
                    reason=None,
                    ownership_passed=True,
                    ptb_passed=True,
                    structural_passed=True,
                    queue_drained=True,
                    sentinel_submitted=True,
                    worker_terminal=True,
                )
    else:
        # Partial repeat: ownership already validated; do not unseal/rollback.
        if owner is not None and ownership_proof is not owner:
            return _snapshot_drain_fields(
                ok=False,
                reason="ownership_foreign_during_drain",
                ownership_passed=False,
                ptb_passed=False,
                structural_passed=False,
            )

    try:
        if not await _wait_event(_s1_idle, deadline):
            return _snapshot_drain_fields(
                ok=False,
                reason="deadline_s1_pending",
                ownership_passed=True,
                ptb_passed=ptb_passed,
                structural_passed=structural_passed,
                queue_drained=False,
            )

        try:
            await _run_on_sender_loop(_queue_join_watching_worker, deadline)
        except TimeoutError:
            return _snapshot_drain_fields(
                ok=False,
                reason="deadline_queue_join",
                ownership_passed=True,
                ptb_passed=ptb_passed,
                structural_passed=structural_passed,
                queue_drained=False,
            )
        except RuntimeError as exc:
            if str(exc) == "unexpected_dead_worker":
                return _snapshot_drain_fields(
                    ok=False,
                    reason="unexpected_dead_worker",
                    ownership_passed=True,
                    ptb_passed=ptb_passed,
                    structural_passed=structural_passed,
                    queue_drained=False,
                    worker_terminal=True,
                )
            raise

        if not await _wait_event(_s3_idle, deadline):
            return _snapshot_drain_fields(
                ok=False,
                reason="deadline_s3_active",
                ownership_passed=True,
                ptb_passed=ptb_passed,
                structural_passed=structural_passed,
                queue_drained=True,
            )

        with _lifecycle_lock:
            s3_nonzero = _active_user_sends != 0

        if s3_nonzero:
            return _snapshot_drain_fields(
                ok=False,
                reason="active_s3_nonzero",
                ownership_passed=True,
                ptb_passed=ptb_passed,
                structural_passed=structural_passed,
                queue_drained=True,
            )

        try:
            status = await _run_on_sender_loop(_worker_status_on_sender_loop, deadline)
        except TimeoutError:
            return _snapshot_drain_fields(
                ok=False,
                reason="deadline_worker_status",
                ownership_passed=True,
                ptb_passed=ptb_passed,
                structural_passed=structural_passed,
                queue_drained=True,
            )

        with _lifecycle_lock:
            sentinel_enqueued_already = _sentinel_state == _SENTINEL_ENQUEUED

        dead_before_sentinel = status["task_done"]
        if dead_before_sentinel and not sentinel_enqueued_already:
            return _snapshot_drain_fields(
                ok=False,
                reason="unexpected_dead_worker",
                ownership_passed=True,
                ptb_passed=ptb_passed,
                structural_passed=structural_passed,
                queue_drained=True,
                worker_terminal=True,
            )
        if dead_before_sentinel and sentinel_enqueued_already:
            return await _observe_worker_terminal_after_sentinel(
                deadline,
                ptb_passed=ptb_passed,
                structural_passed=structural_passed,
                sentinel_submitted=True,
            )

        sentinel_err = await _ensure_sentinel_enqueued(deadline)
        if sentinel_err is not None:
            reason = (
                "deadline_sentinel_ack"
                if isinstance(sentinel_err, TimeoutError)
                else f"sentinel_submit_failed:{type(sentinel_err).__name__}"
            )
            return _snapshot_drain_fields(
                ok=False,
                reason=reason,
                ownership_passed=True,
                ptb_passed=ptb_passed,
                structural_passed=structural_passed,
                queue_drained=True,
                sentinel_submitted=False,
                worker_terminal=False,
            )

        return await _observe_worker_terminal_after_sentinel(
            deadline,
            ptb_passed=ptb_passed,
            structural_passed=structural_passed,
            sentinel_submitted=True,
        )
    except asyncio.CancelledError:
        # SND8: leave sealed/DRAINING; accepted work continues.
        raise


# =====================================================
#   Full sender resource stop (TASK-48) — after WORKER_STOPPED
# =====================================================


@dataclass(frozen=True, slots=True)
class SenderRequestCloseResult:
    """Per close-target outcome for sender Bot request HTTP shutdown."""

    role: str
    target_index: int
    shutdown_attempted: bool
    shutdown_ok: bool
    already_closed: bool
    leftover_open: bool
    timed_out: bool
    error_type: str | None
    error_text: str | None


@dataclass(frozen=True, slots=True)
class SenderFullStopResult:
    """Structured full sender resource stop outcome (worker + HTTP + loop + thread)."""

    ok: bool
    reason: str | None
    ownership_passed: bool
    ptb_passed: bool
    structural_passed: bool
    lifecycle_state: str
    intake_sealed: bool
    worker_stopped: bool
    worker_terminal: bool
    bot_shutdown_attempted: bool
    bot_shutdown_ok: bool
    bot_shutdown_error_type: str | None
    bot_shutdown_error_text: str | None
    request_close_results: tuple[SenderRequestCloseResult, ...]
    http_stopped: bool
    loop_stop_requested: bool
    loop_running: bool
    loop_thread_alive: bool
    thread_joined: bool
    full_resource_stopped: bool
    terminal_intake_failure_total: int
    recent_intake_failures: tuple[str, ...]


def _probe_attr(obj: Any, name: str) -> tuple[Any, bool]:
    try:
        return getattr(obj, name, _ATTR_ABSENT), True
    except Exception:
        return None, False


def _request_leftover_open(req: Any) -> tuple[bool | None, bool]:
    """Return ``(leftover_open, diagnostic_known)``. Unknown → fail-closed."""

    client, client_ok = _probe_attr(req, "_client")
    if not client_ok or client is _ATTR_ABSENT:
        return None, False
    if client is None:
        return False, True
    closed, closed_ok = _probe_attr(client, "is_closed")
    if not closed_ok or closed is _ATTR_ABSENT:
        return None, False
    try:
        return (not bool(closed)), True
    except Exception:
        return None, False


def _role_name_for_target(roles: tuple[Any, ...], target: Any) -> str:
    for role in roles:
        if getattr(role, "request", None) is target:
            return str(getattr(role, "role", "unknown"))
    return "unknown"


def _ordered_close_results() -> tuple[SenderRequestCloseResult, ...]:
    with _lifecycle_lock:
        plan = _http_close_plan
        results_map = dict(_http_request_results)
    if plan is None:
        return tuple(results_map[k] for k in sorted(results_map))
    _roles, close_targets = plan
    ordered: list[SenderRequestCloseResult] = []
    for idx, target in enumerate(close_targets):
        item = results_map.get(id(target))
        if item is not None:
            ordered.append(item)
        else:
            ordered.append(
                SenderRequestCloseResult(
                    role=_role_name_for_target(_roles, target),
                    target_index=idx,
                    shutdown_attempted=False,
                    shutdown_ok=False,
                    already_closed=False,
                    leftover_open=False,
                    timed_out=False,
                    error_type=None,
                    error_text=None,
                )
            )
    return tuple(ordered)


def _snapshot_full_stop_fields(
    *,
    ok: bool,
    reason: str | None,
    ownership_passed: bool,
    ptb_passed: bool,
    structural_passed: bool,
    worker_terminal: bool | None = None,
) -> SenderFullStopResult:
    with _lifecycle_lock:
        sealed = _intake_sealed
        state = _lifecycle_state
        total_fail = _terminal_intake_failure_total
        recent = tuple(_recent_intake_failures)
        task = _worker_task
        bot_attempted = _bot_shutdown_attempted
        bot_ok = _bot_shutdown_ok
        bot_err_type = _bot_shutdown_error_type
        bot_err_text = _bot_shutdown_error_text
        loop_stop_req = _loop_stop_requested
    terminal = worker_terminal
    if terminal is None:
        if state in _POST_WORKER_STATES:
            terminal = True
        else:
            terminal = task is not None and task.done()
    loop_running = bool(loop.is_running())
    thread_alive = bool(_loop_thread.is_alive())
    worker_stopped = state in _POST_WORKER_STATES and bool(terminal)
    http_stopped = state in ("HTTP_STOPPED", "LOOP_STOPPING", "STOPPED")
    thread_joined = (not thread_alive) and state == "STOPPED"
    full_resource_stopped = (
        state == "STOPPED" and (not loop_running) and (not thread_alive)
    )
    return SenderFullStopResult(
        ok=ok,
        reason=reason,
        ownership_passed=ownership_passed,
        ptb_passed=ptb_passed,
        structural_passed=structural_passed,
        lifecycle_state=state,
        intake_sealed=sealed,
        worker_stopped=worker_stopped,
        worker_terminal=bool(terminal),
        bot_shutdown_attempted=bot_attempted,
        bot_shutdown_ok=bot_ok,
        bot_shutdown_error_type=bot_err_type,
        bot_shutdown_error_text=bot_err_text,
        request_close_results=_ordered_close_results(),
        http_stopped=http_stopped,
        loop_stop_requested=loop_stop_req,
        loop_running=loop_running,
        loop_thread_alive=thread_alive,
        thread_joined=thread_joined,
        full_resource_stopped=full_resource_stopped,
        terminal_intake_failure_total=total_fail,
        recent_intake_failures=recent,
    )


def _full_stop_from_drain(drain: SenderWorkerDrainResult) -> SenderFullStopResult:
    return SenderFullStopResult(
        ok=drain.ok,
        reason=drain.reason,
        ownership_passed=drain.ownership_passed,
        ptb_passed=drain.ptb_passed,
        structural_passed=drain.structural_passed,
        lifecycle_state=drain.lifecycle_state,
        intake_sealed=drain.intake_sealed,
        worker_stopped=drain.worker_stopped,
        worker_terminal=drain.worker_terminal,
        bot_shutdown_attempted=False,
        bot_shutdown_ok=False,
        bot_shutdown_error_type=None,
        bot_shutdown_error_text=None,
        request_close_results=(),
        http_stopped=False,
        loop_stop_requested=False,
        loop_running=drain.loop_running,
        loop_thread_alive=drain.loop_thread_alive,
        thread_joined=False,
        full_resource_stopped=False,
        terminal_intake_failure_total=drain.terminal_intake_failure_total,
        recent_intake_failures=drain.recent_intake_failures,
    )


def _structural_for_http_mutation() -> tuple[bool, str | None]:
    """Best-effort sync structural checks (non-authoritative for worker terminal)."""

    if loop is None or not loop.is_running():
        return False, "sender_loop_unavailable"
    if _loop_thread is None or not _loop_thread.is_alive():
        return False, "loop_thread_unavailable"
    with _lifecycle_lock:
        sealed = _intake_sealed
        state = _lifecycle_state
    if not sealed:
        return False, "intake_not_sealed"
    if state != "WORKER_STOPPED":
        return False, "lifecycle_refuses_http_stop"
    return True, None


async def _structural_for_http_mutation_async(
    deadline: float,
) -> tuple[bool, str | None]:
    """Authoritative HTTP structural preflight; worker terminal via sender loop."""

    sync_ok, sync_reason = _structural_for_http_mutation()
    if not sync_ok:
        return False, sync_reason
    try:
        status = await _run_on_sender_loop(_worker_status_on_sender_loop, deadline)
    except TimeoutError:
        return False, "worker_status_deadline"
    if status["task_missing"]:
        return False, "worker_task_unavailable"
    if not status["task_done"]:
        return False, "worker_not_terminal"
    return True, None


def _prior_target_fully_closed(prev: SenderRequestCloseResult | None) -> bool:
    if prev is None:
        return False
    if prev.timed_out or prev.leftover_open:
        return False
    if prev.error_type is not None and not prev.already_closed and not prev.shutdown_ok:
        return False
    return bool(prev.already_closed or prev.shutdown_ok) and not prev.leftover_open


async def _http_close_on_sender_loop(deadline: float) -> bool:
    """Run Bot + request shutdowns on the sender loop. Returns HTTP success."""

    global _bot_shutdown_attempted, _bot_shutdown_ok
    global _bot_shutdown_error_type, _bot_shutdown_error_text
    global _http_request_results

    with _lifecycle_lock:
        plan = _http_close_plan
        prior = dict(_http_request_results)
        prior_bot_ok = _bot_shutdown_ok
    if plan is None:
        return False
    roles, close_targets = plan

    bot_ok = False
    if prior_bot_ok:
        # Same close-plan session: do not re-call successful Bot.shutdown.
        bot_ok = True
    else:
        shutdown = None
        try:
            shutdown = getattr(bot, "shutdown", None) if bot is not None else None
        except Exception as exc:
            _bot_shutdown_attempted = True
            _bot_shutdown_ok = False
            _bot_shutdown_error_type = type(exc).__name__
            _bot_shutdown_error_text = _sanitize_error_message(str(exc))
            shutdown = None
        if callable(shutdown):
            _bot_shutdown_attempted = True
            try:
                await shutdown()
                bot_ok = True
                _bot_shutdown_ok = True
                _bot_shutdown_error_type = None
                _bot_shutdown_error_text = None
            except Exception as exc:
                bot_ok = False
                _bot_shutdown_ok = False
                _bot_shutdown_error_type = type(exc).__name__
                _bot_shutdown_error_text = _sanitize_error_message(str(exc))
        else:
            _bot_shutdown_attempted = bool(bot is not None)
            _bot_shutdown_ok = False
            if _bot_shutdown_error_type is None:
                _bot_shutdown_error_type = "bot_shutdown_unavailable"
                _bot_shutdown_error_text = None

    results: dict[int, SenderRequestCloseResult] = dict(prior)
    for idx, target in enumerate(close_targets):
        key = id(target)
        role_name = _role_name_for_target(roles, target)
        prev = results.get(key)
        if _prior_target_fully_closed(prev):
            # Repeat: skip already-closed / successfully closed targets.
            continue

        if time.monotonic() >= deadline:
            for j in range(idx, len(close_targets)):
                tgt = close_targets[j]
                results[id(tgt)] = SenderRequestCloseResult(
                    role=_role_name_for_target(roles, tgt),
                    target_index=j,
                    shutdown_attempted=False,
                    shutdown_ok=False,
                    already_closed=False,
                    leftover_open=True,
                    timed_out=True,
                    error_type="TimeoutError",
                    error_text="deadline_http_close",
                )
            break

        is_open, known = _request_leftover_open(target)
        if not known or is_open is None:
            results[key] = SenderRequestCloseResult(
                role=role_name,
                target_index=idx,
                shutdown_attempted=False,
                shutdown_ok=False,
                already_closed=False,
                leftover_open=True,
                timed_out=False,
                error_type="leftover_diagnostic_unavailable",
                error_text=None,
            )
            continue
        if not is_open:
            results[key] = SenderRequestCloseResult(
                role=role_name,
                target_index=idx,
                shutdown_attempted=False,
                shutdown_ok=True,
                already_closed=True,
                leftover_open=False,
                timed_out=False,
                error_type=None,
                error_text=None,
            )
            continue

        try:
            shutdown_fn = getattr(target, "shutdown", None)
        except Exception as exc:
            results[key] = SenderRequestCloseResult(
                role=role_name,
                target_index=idx,
                shutdown_attempted=False,
                shutdown_ok=False,
                already_closed=False,
                leftover_open=True,
                timed_out=False,
                error_type=type(exc).__name__,
                error_text=_sanitize_error_message(str(exc)),
            )
            continue
        if not callable(shutdown_fn):
            results[key] = SenderRequestCloseResult(
                role=role_name,
                target_index=idx,
                shutdown_attempted=False,
                shutdown_ok=False,
                already_closed=False,
                leftover_open=True,
                timed_out=False,
                error_type="request_shutdown_unavailable",
                error_text=None,
            )
            continue

        try:
            await shutdown_fn()
            post_open, post_known = _request_leftover_open(target)
            leftover = bool(post_open) if post_known and post_open is not None else True
            results[key] = SenderRequestCloseResult(
                role=role_name,
                target_index=idx,
                shutdown_attempted=True,
                shutdown_ok=post_known and not leftover,
                already_closed=False,
                leftover_open=leftover or not post_known,
                timed_out=False,
                error_type=(
                    None
                    if post_known and not leftover
                    else (
                        "leftover_diagnostic_unavailable"
                        if not post_known
                        else "leftover_open"
                    )
                ),
                error_text=None,
            )
        except Exception as exc:
            post_open, post_known = _request_leftover_open(target)
            leftover = True if not post_known or post_open is None else bool(post_open)
            results[key] = SenderRequestCloseResult(
                role=role_name,
                target_index=idx,
                shutdown_attempted=True,
                shutdown_ok=False,
                already_closed=False,
                leftover_open=leftover,
                timed_out=False,
                error_type=type(exc).__name__,
                error_text=_sanitize_error_message(str(exc)),
            )

    # Final leftover re-check for success claim.
    any_live = False
    any_unknown = False
    for target in close_targets:
        is_open, known = _request_leftover_open(target)
        if not known or is_open is None:
            any_unknown = True
            key = id(target)
            prev = results.get(key)
            if prev is not None and not prev.leftover_open:
                results[key] = SenderRequestCloseResult(
                    role=prev.role,
                    target_index=prev.target_index,
                    shutdown_attempted=prev.shutdown_attempted,
                    shutdown_ok=False,
                    already_closed=prev.already_closed,
                    leftover_open=True,
                    timed_out=prev.timed_out,
                    error_type="leftover_diagnostic_unavailable",
                    error_text=prev.error_text,
                )
            continue
        if is_open:
            any_live = True
            key = id(target)
            prev = results.get(key)
            if prev is not None:
                results[key] = SenderRequestCloseResult(
                    role=prev.role,
                    target_index=prev.target_index,
                    shutdown_attempted=prev.shutdown_attempted,
                    shutdown_ok=False,
                    already_closed=False,
                    leftover_open=True,
                    timed_out=prev.timed_out,
                    error_type=prev.error_type or "leftover_open",
                    error_text=prev.error_text,
                )

    with _lifecycle_lock:
        _http_request_results = results
        _bot_shutdown_ok = bot_ok

    return bool(bot_ok) and (not any_live) and (not any_unknown)


def _publish_http_phase_locked(*, success: bool) -> None:
    """Leave HTTP_STOPPING; success→HTTP_STOPPED else repeatable WORKER_STOPPED.

    Only the owner-loop HTTP close session may call this when the session is
    actually terminal.
    """

    global _lifecycle_state, _http_close_session_task
    if _lifecycle_state == "HTTP_STOPPING":
        _lifecycle_state = "HTTP_STOPPED" if success else "WORKER_STOPPED"
    _http_close_session_task = None
    _http_phase_ack.set()


async def _owner_http_close_session(deadline: float) -> bool:
    """Owner-loop HTTP close session. Publishes phase terminal on exit."""

    success = False
    try:
        success = bool(await _http_close_on_sender_loop(deadline))
    except asyncio.CancelledError:
        with _lifecycle_lock:
            _publish_http_phase_locked(success=False)
        raise
    except Exception as exc:
        global _bot_shutdown_error_type, _bot_shutdown_error_text
        with _lifecycle_lock:
            if _bot_shutdown_error_type is None:
                _bot_shutdown_error_type = type(exc).__name__
                _bot_shutdown_error_text = _sanitize_error_message(str(exc))
            _publish_http_phase_locked(success=False)
        return False
    else:
        with _lifecycle_lock:
            _publish_http_phase_locked(success=success)
        return success


async def _start_http_close_session_on_loop(deadline: float) -> asyncio.Task:
    """Create the single owner-loop HTTP close Task. Must run on sender loop."""

    global _http_close_session_task
    existing = _http_close_session_task
    if existing is not None and not existing.done():
        raise RuntimeError("http_close_session_already_running")
    task = asyncio.create_task(
        _owner_http_close_session(deadline),
        name="telegram-sender-http-close",
    )
    _http_close_session_task = task
    return task


async def _await_http_phase_terminal(
    deadline: float,
    *,
    cancel_owner_on_deadline: bool,
) -> SenderFullStopResult | None:
    """Wait for `_http_phase_ack`. None ⇒ HTTP_STOPPED (continue to loop phase).

    Caller CancelledError does **not** publish HTTP phase terminal and does
    **not** cancel the owner-loop session.
    """

    try:
        if await _wait_event(_http_phase_ack, deadline):
            with _lifecycle_lock:
                state = _lifecycle_state
            if state == "HTTP_STOPPED":
                return None
            return _snapshot_full_stop_fields(
                ok=False,
                reason="http_close_incomplete",
                ownership_passed=True,
                ptb_passed=True,
                structural_passed=True,
                worker_terminal=True,
            )
    except asyncio.CancelledError:
        # Detach waiter only; owner session continues and publishes later.
        raise

    # Deadline while waiting for terminal.
    if cancel_owner_on_deadline:
        with _lifecycle_lock:
            task = _http_close_session_task
        if task is not None and not task.done():
            try:
                loop.call_soon_threadsafe(task.cancel)
            except Exception:
                pass
            # Wait until owner session publishes terminal (must not strand HTTP_STOPPING).
            await _wait_event(_http_phase_ack, time.monotonic() + 30.0)
        elif not _http_phase_ack.is_set():
            # No live session and no ack — publish fail-closed terminal.
            with _lifecycle_lock:
                if _lifecycle_state == "HTTP_STOPPING":
                    _publish_http_phase_locked(success=False)

    with _lifecycle_lock:
        state = _lifecycle_state
    if state == "HTTP_STOPPED":
        return None
    return _snapshot_full_stop_fields(
        ok=False,
        reason="deadline_http_close",
        ownership_passed=True,
        ptb_passed=True,
        structural_passed=True,
        worker_terminal=True,
    )


async def _run_http_close_phase(
    ownership_proof: object,
    deadline: float,
) -> SenderFullStopResult | None:
    """Claim or observe HTTP phase. None means continue to loop phase (HTTP_STOPPED)."""

    global _lifecycle_state

    claimed = False
    # ``_lifecycle_lock`` is non-reentrant; never call ``_snapshot_full_stop_fields``
    # while holding it.
    refuse_reason: str | None = None
    refuse_ownership = True
    refuse_ptb = True
    refuse_structural = True
    already_stopped = False
    skip_to_loop = False
    with _lifecycle_lock:
        state = _lifecycle_state
        owner = _drain_owner_proof
        if owner is not None and ownership_proof is not owner:
            refuse_reason = "ownership_foreign_during_http_stop"
            refuse_ownership = False
            refuse_ptb = False
            refuse_structural = False
        elif state == "STOPPED":
            already_stopped = True
        elif state in ("HTTP_STOPPED", "LOOP_STOPPING"):
            skip_to_loop = True
        elif state == "HTTP_STOPPING":
            claimed = False
        elif state == "WORKER_STOPPED":
            if _http_close_plan is None:
                # Plan stored by caller before claim; refuse if missing.
                refuse_reason = "http_close_plan_missing"
                refuse_ptb = False
                refuse_structural = False
            else:
                existing = _http_close_session_task
                if existing is not None and not existing.done():
                    refuse_reason = "http_close_session_already_running"
                    refuse_structural = False
                else:
                    _lifecycle_state = "HTTP_STOPPING"
                    _http_phase_ack.clear()
                    claimed = True
        else:
            refuse_reason = f"lifecycle_refuses_http_stop:{state}"
            refuse_structural = False

    if already_stopped:
        return _snapshot_full_stop_fields(
            ok=True,
            reason=None,
            ownership_passed=True,
            ptb_passed=True,
            structural_passed=True,
            worker_terminal=True,
        )
    if refuse_reason is not None:
        return _snapshot_full_stop_fields(
            ok=False,
            reason=refuse_reason,
            ownership_passed=refuse_ownership,
            ptb_passed=refuse_ptb,
            structural_passed=refuse_structural,
            worker_terminal=True,
        )
    if skip_to_loop:
        return None

    if claimed:
        # Start owner session on sender loop; do not bind waiter cancel to session.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            with _lifecycle_lock:
                if _lifecycle_state == "HTTP_STOPPING":
                    _publish_http_phase_locked(success=False)
            return _snapshot_full_stop_fields(
                ok=False,
                reason="deadline_http_close",
                ownership_passed=True,
                ptb_passed=True,
                structural_passed=True,
                worker_terminal=True,
            )
        try:
            start_fut = asyncio.run_coroutine_threadsafe(
                _start_http_close_session_on_loop(deadline),
                loop,
            )
            await asyncio.wait_for(asyncio.wrap_future(start_fut), timeout=remaining)
        except TimeoutError:
            with _lifecycle_lock:
                if _lifecycle_state == "HTTP_STOPPING" and (
                    _http_close_session_task is None or _http_close_session_task.done()
                ):
                    _publish_http_phase_locked(success=False)
            return _snapshot_full_stop_fields(
                ok=False,
                reason="deadline_http_close",
                ownership_passed=True,
                ptb_passed=True,
                structural_passed=True,
                worker_terminal=True,
            )
        except asyncio.CancelledError:
            # Session may already be running; leave HTTP_STOPPING for owner publish.
            raise
        except Exception as exc:
            with _lifecycle_lock:
                global _bot_shutdown_error_type, _bot_shutdown_error_text
                if _bot_shutdown_error_type is None:
                    _bot_shutdown_error_type = type(exc).__name__
                    _bot_shutdown_error_text = _sanitize_error_message(str(exc))
                if _lifecycle_state == "HTTP_STOPPING":
                    _publish_http_phase_locked(success=False)
            return _snapshot_full_stop_fields(
                ok=False,
                reason=f"http_close_session_start_failed:{type(exc).__name__}",
                ownership_passed=True,
                ptb_passed=True,
                structural_passed=True,
                worker_terminal=True,
            )

    return await _await_http_phase_terminal(
        deadline,
        cancel_owner_on_deadline=claimed,
    )

async def _run_loop_stop_phase(
    ownership_proof: object,
    deadline: float,
) -> SenderFullStopResult:
    """HTTP_STOPPED → LOOP_STOPPING → STOPPED (or remain LOOP_STOPPING)."""

    global _lifecycle_state, _loop_stop_requested

    # ``_lifecycle_lock`` is non-reentrant; never call ``_snapshot_full_stop_fields``
    # while holding it.
    refuse_reason: str | None = None
    refuse_ownership = True
    refuse_ptb = True
    refuse_structural = True
    already_stopped = False
    schedule_failed = False
    with _lifecycle_lock:
        state = _lifecycle_state
        owner = _drain_owner_proof
        if owner is not None and ownership_proof is not owner:
            refuse_reason = "ownership_foreign_during_loop_stop"
            refuse_ownership = False
            refuse_ptb = False
            refuse_structural = False
        elif state == "STOPPED":
            already_stopped = True
        elif state not in ("HTTP_STOPPED", "LOOP_STOPPING"):
            refuse_reason = f"lifecycle_refuses_loop_stop:{state}"
            refuse_structural = False
        else:
            if state == "HTTP_STOPPED":
                _lifecycle_state = "LOOP_STOPPING"
            if not _loop_stop_requested:
                _loop_stop_requested = True
                try:
                    _real_call_soon_threadsafe(loop.stop)
                except Exception:
                    _loop_stop_requested = False
                    # Remain LOOP_STOPPING for repeat; do not claim STOPPED.
                    schedule_failed = True

    if already_stopped:
        return _snapshot_full_stop_fields(
            ok=True,
            reason=None,
            ownership_passed=True,
            ptb_passed=True,
            structural_passed=True,
            worker_terminal=True,
        )
    if refuse_reason is not None:
        return _snapshot_full_stop_fields(
            ok=False,
            reason=refuse_reason,
            ownership_passed=refuse_ownership,
            ptb_passed=refuse_ptb,
            structural_passed=refuse_structural,
            worker_terminal=True,
        )
    if schedule_failed:
        return _snapshot_full_stop_fields(
            ok=False,
            reason="loop_stop_schedule_failed",
            ownership_passed=True,
            ptb_passed=True,
            structural_passed=True,
            worker_terminal=True,
        )

    try:
        if not await _wait_event(_loop_stopped, deadline):
            return _snapshot_full_stop_fields(
                ok=False,
                reason="deadline_loop_stopped",
                ownership_passed=True,
                ptb_passed=True,
                structural_passed=True,
                worker_terminal=True,
            )

        remaining = deadline - time.monotonic()
        if remaining < 0:
            remaining = 0.0
        await asyncio.to_thread(_loop_thread.join, remaining)

        thread_alive = bool(_loop_thread.is_alive())
        loop_running = bool(loop.is_running())
        with _lifecycle_lock:
            if (not thread_alive) and (not loop_running):
                _lifecycle_state = "STOPPED"
            # else remain LOOP_STOPPING

        if thread_alive or loop_running:
            return _snapshot_full_stop_fields(
                ok=False,
                reason="deadline_thread_join" if thread_alive else "loop_still_running",
                ownership_passed=True,
                ptb_passed=True,
                structural_passed=True,
                worker_terminal=True,
            )
        return _snapshot_full_stop_fields(
            ok=True,
            reason=None,
            ownership_passed=True,
            ptb_passed=True,
            structural_passed=True,
            worker_terminal=True,
        )
    except asyncio.CancelledError:
        # Loop stop may already be requested; leave LOOP_STOPPING for repeat.
        raise


async def _prepare_http_plan_and_structural(
    ownership_proof: object,
    deadline: float,
) -> SenderFullStopResult | None:
    """PTB inspect + store plan + structural for first HTTP entry. None = proceed."""

    global _http_close_plan

    with _lifecycle_lock:
        owner = _drain_owner_proof
        state = _lifecycle_state
        plan = _http_close_plan
    if owner is not None and ownership_proof is not owner:
        return _snapshot_full_stop_fields(
            ok=False,
            reason="ownership_foreign_after_worker_stopped",
            ownership_passed=False,
            ptb_passed=False,
            structural_passed=False,
            worker_terminal=True,
        )
    if state != "WORKER_STOPPED":
        # HTTP_STOPPING/HTTP_STOPPED handled by caller branches.
        return None

    if plan is None:
        ptb = inspect_sender_ptb_compatibility(
            bot=bot,
            expected_general_request=request,
        )
        if not ptb.supported:
            return _snapshot_full_stop_fields(
                ok=False,
                reason=f"ptb_{ptb.reason or 'refused'}",
                ownership_passed=True,
                ptb_passed=False,
                structural_passed=False,
                worker_terminal=True,
            )
        with _lifecycle_lock:
            # Store exact roles/close_targets; refuse if raced away from WORKER_STOPPED.
            if _lifecycle_state != "WORKER_STOPPED":
                return None
            if _http_close_plan is None:
                _http_close_plan = (tuple(ptb.roles), tuple(ptb.close_targets))

    structural_ok, structural_reason = await _structural_for_http_mutation_async(
        deadline
    )
    if not structural_ok:
        with _lifecycle_lock:
            advanced = _lifecycle_state in (
                "HTTP_STOPPING",
                "HTTP_STOPPED",
                "LOOP_STOPPING",
                "STOPPED",
            )
        if advanced:
            return None
        return _snapshot_full_stop_fields(
            ok=False,
            reason=structural_reason or "structural_refused",
            ownership_passed=True,
            ptb_passed=True,
            structural_passed=False,
            worker_terminal=True,
        )
    return None


async def stop_isolated_sender(
    ownership_proof: object,
    *,
    timeout: float = 30.0,
) -> SenderFullStopResult:
    """Full sender resource stop: worker drain (if needed) → HTTP → loop → thread.

    Async/non-blocking for the caller asyncio loop. One overall monotonic deadline.
    """

    deadline = time.monotonic() + max(0.0, float(timeout))
    ownership = validate_antares_sender_ownership(ownership_proof)
    if not ownership.ok:
        return _snapshot_full_stop_fields(
            ok=False,
            reason=f"ownership_{ownership.reason or 'refused'}",
            ownership_passed=False,
            ptb_passed=False,
            structural_passed=False,
        )

    with _lifecycle_lock:
        state = _lifecycle_state
        owner = _drain_owner_proof

    # --- Terminal / in-flight branches ---
    if state == "STOPPED":
        if owner is not None and ownership_proof is not owner:
            return _snapshot_full_stop_fields(
                ok=False,
                reason="ownership_foreign_after_stopped",
                ownership_passed=False,
                ptb_passed=False,
                structural_passed=False,
                worker_terminal=True,
            )
        return _snapshot_full_stop_fields(
            ok=True,
            reason=None,
            ownership_passed=True,
            ptb_passed=True,
            structural_passed=True,
            worker_terminal=True,
        )

    if state == "LOOP_STOPPING":
        if owner is not None and ownership_proof is not owner:
            return _snapshot_full_stop_fields(
                ok=False,
                reason="ownership_foreign_during_loop_stop",
                ownership_passed=False,
                ptb_passed=False,
                structural_passed=False,
                worker_terminal=True,
            )
        return await _run_loop_stop_phase(ownership_proof, deadline)

    if state == "HTTP_STOPPED":
        if owner is not None and ownership_proof is not owner:
            return _snapshot_full_stop_fields(
                ok=False,
                reason="ownership_foreign_during_loop_stop",
                ownership_passed=False,
                ptb_passed=False,
                structural_passed=False,
                worker_terminal=True,
            )
        return await _run_loop_stop_phase(ownership_proof, deadline)

    if state == "HTTP_STOPPING":
        if owner is not None and ownership_proof is not owner:
            return _snapshot_full_stop_fields(
                ok=False,
                reason="ownership_foreign_during_http_stop",
                ownership_passed=False,
                ptb_passed=False,
                structural_passed=False,
                worker_terminal=True,
            )
        http_result = await _run_http_close_phase(ownership_proof, deadline)
        if http_result is not None:
            return http_result
        return await _run_loop_stop_phase(ownership_proof, deadline)

    if state == "WORKER_STOPPED":
        if owner is not None and ownership_proof is not owner:
            return _snapshot_full_stop_fields(
                ok=False,
                reason="ownership_foreign_after_worker_stopped",
                ownership_passed=False,
                ptb_passed=False,
                structural_passed=False,
                worker_terminal=True,
            )
        prep = await _prepare_http_plan_and_structural(ownership_proof, deadline)
        if prep is not None:
            return prep
        http_result = await _run_http_close_phase(ownership_proof, deadline)
        if http_result is not None:
            return http_result
        return await _run_loop_stop_phase(ownership_proof, deadline)

    # RUNNING / DRAINING: PTB + structural, then worker drain, then HTTP onward.
    if state not in ("RUNNING", "DRAINING"):
        return _snapshot_full_stop_fields(
            ok=False,
            reason=f"lifecycle_refuses_full_stop:{state}",
            ownership_passed=True,
            ptb_passed=False,
            structural_passed=False,
        )

    with _lifecycle_lock:
        sealed_now = _intake_sealed
        owner_now = _drain_owner_proof
    already_draining = state == "DRAINING" and sealed_now
    if already_draining and owner_now is not None and ownership_proof is not owner_now:
        return _snapshot_full_stop_fields(
            ok=False,
            reason="ownership_foreign_during_drain",
            ownership_passed=False,
            ptb_passed=False,
            structural_passed=False,
        )

    if not already_draining:
        ptb = inspect_sender_ptb_compatibility(
            bot=bot,
            expected_general_request=request,
        )
        if not ptb.supported:
            return _snapshot_full_stop_fields(
                ok=False,
                reason=f"ptb_{ptb.reason or 'refused'}",
                ownership_passed=True,
                ptb_passed=False,
                structural_passed=False,
            )
        structural_ok, structural_reason = await _structural_preflight_async(deadline)
        if not structural_ok:
            return _snapshot_full_stop_fields(
                ok=False,
                reason=structural_reason or "structural_refused",
                ownership_passed=True,
                ptb_passed=True,
                structural_passed=False,
            )

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return _snapshot_full_stop_fields(
            ok=False,
            reason="deadline_before_drain",
            ownership_passed=True,
            ptb_passed=True,
            structural_passed=True,
        )

    drain = await drain_and_stop_sender_worker(
        ownership_proof,
        timeout=remaining,
    )

    if not drain.ok or not drain.worker_stopped:
        return _full_stop_from_drain(drain)

    with _lifecycle_lock:
        state = _lifecycle_state
    if state != "WORKER_STOPPED":
        # Concurrent same-owner stop may have advanced past WORKER_STOPPED.
        if state in ("HTTP_STOPPING", "HTTP_STOPPED", "LOOP_STOPPING", "STOPPED"):
            return await stop_isolated_sender(
                ownership_proof,
                timeout=max(0.0, deadline - time.monotonic()),
            )
        return _full_stop_from_drain(drain)

    prep = await _prepare_http_plan_and_structural(ownership_proof, deadline)
    if prep is not None:
        return prep
    http_result = await _run_http_close_phase(ownership_proof, deadline)
    if http_result is not None:
        return http_result
    return await _run_loop_stop_phase(ownership_proof, deadline)


_reset_lifecycle_gate = threading.Lock()


def _reset_sender_worker_lifecycle_for_tests() -> None:
    """Test-only: clear seal/accounting and restart worker Task if stopped."""

    global _lifecycle_state, _intake_sealed, _pending_loop_handoffs
    global _active_user_sends, _terminal_intake_failure_total, _recent_intake_failures
    global _sentinel_state, _sentinel_failure, _drain_owner_proof, _worker_task, queue
    global _http_close_plan, _http_request_results
    global _bot_shutdown_attempted, _bot_shutdown_ok
    global _bot_shutdown_error_type, _bot_shutdown_error_text
    global _loop_stop_requested, _http_close_session_task

    with _reset_lifecycle_gate:
        with _lifecycle_lock:
            need_restart = (
                _lifecycle_state != "RUNNING"
                or _intake_sealed
                or _sentinel_state != _SENTINEL_NOT_SUBMITTED
                or (_worker_task is not None and _worker_task.done())
            )
            _intake_sealed = False
            _lifecycle_state = "RUNNING"
            _pending_loop_handoffs = 0
            _active_user_sends = 0
            _terminal_intake_failure_total = 0
            _recent_intake_failures = []
            _sentinel_state = _SENTINEL_NOT_SUBMITTED
            _sentinel_failure = None
            _sentinel_ack.clear()
            _drain_owner_proof = None
            _s1_idle.set()
            _s3_idle.set()
            _http_close_plan = None
            _http_request_results = {}
            _bot_shutdown_attempted = False
            _bot_shutdown_ok = False
            _bot_shutdown_error_type = None
            _bot_shutdown_error_text = None
            _http_phase_ack.clear()
            _http_close_session_task = None
            _loop_stop_requested = False
            task = _worker_task

        if not need_restart:
            return
        if not loop.is_running():
            # Loop already stopped (e.g. after full STOPPED) — do not restart.
            return

        def _flush_and_restart() -> None:
            global _worker_task, queue, _worker_owned_queue
            # Abandon previous worker/queue for test isolation. Do not cancel the
            # prior Task (cancel races caused flaky unexpected_dead_worker).
            queue = asyncio.Queue()
            _worker_owned_queue = None
            _worker_ready.clear()
            _start_worker_on_loop()

        try:
            done = threading.Event()

            def _run() -> None:
                try:
                    _flush_and_restart()
                finally:
                    done.set()

            _real_call_soon_threadsafe(_run)
            done.wait(timeout=_WORKER_READY_TIMEOUT_SECONDS)
            _worker_ready.wait(timeout=_WORKER_READY_TIMEOUT_SECONDS)
            time.sleep(0.05)
        except Exception:
            # Tests may temporarily patch call_soon_threadsafe (D27); best-effort.
            pass


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

    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        raise RuntimeError(_sanitize_error_message(str(e))) from e


# Phase 3A — route-first delivery helpers (resolution in ``telegram_routes``).
from integrations.telegram_routes import (  # noqa: E402
    resolve_route_chat_id,
    send_file_to_route,
    send_message_to_route,
)
