"""Antares Wallet Editor Telegram document ingest. Import does not bind handlers."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

from telegram import Update
from telegram.ext import ContextTypes

from automation.audit import log

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


def _best_effort_unlink(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _cleanup_owned(path: Path | None, pending: BaseException) -> None:
    try:
        _best_effort_unlink(path)
    except Exception:
        pass
    raise pending


def _route_task(routing, *, local_path: Path, document, operator, chat_id, telegram_user_id):
    from automation.edit_wallet_contract import ExcelRouting
    from automation.runtime import (
        WalletEditorAddWalletTask,
        WalletEditorEditWalletTask,
        WalletEditorTask,
        wallet_editor_add_wallet_dry_run_enabled,
    )

    filename = document.file_name or "input.xlsx"
    if routing == ExcelRouting.ADD_WALLET:
        dry_run = wallet_editor_add_wallet_dry_run_enabled()
        return (
            "add",
            WalletEditorAddWalletTask(
                file_path=str(local_path),
                original_filename=filename,
                operator_profile=operator.profile_key,
                chat_id=chat_id,
                user_id=telegram_user_id,
                login=operator.login,
                password=operator.password,
                auth_state_path=operator.auth_state_path,
                dry_run=dry_run,
            ),
            dry_run,
        )
    if routing == ExcelRouting.EDIT_WALLET:
        return (
            "edit",
            WalletEditorEditWalletTask(
                file_path=str(local_path),
                original_filename=filename,
                operator_profile=operator.profile_key,
                chat_id=chat_id,
                user_id=telegram_user_id,
                login=operator.login,
                password=operator.password,
                auth_state_path=operator.auth_state_path,
            ),
            None,
        )
    return (
        "disable",
        WalletEditorTask(
            file_path=str(local_path),
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
            operator_profile=operator.profile_key,
            source_file_name=filename,
            login=operator.login,
            password=operator.password,
            auth_state_path=operator.auth_state_path,
        ),
        None,
    )


def _queued_log_and_text(kind: str, task, *, dry_run, queue_size) -> str:
    profile = task.operator_profile
    if kind == "add":
        log.info(
            f"📌 [WalletEditorAdd] queued profile={profile} "
            f"user_id={task.user_id} chat_id={task.chat_id} "
            f"queue_size={queue_size} dry_run={dry_run} file={task.file_path}"
        )
        return (
            f"📌 Add Wallet: файл в очереди профиля {profile}. "
            f"Очередь: {queue_size}. dry_run={dry_run}"
        )
    if kind == "edit":
        log.info(
            f"📌 [WalletEditorEdit] queued profile={profile} "
            f"user_id={task.user_id} chat_id={task.chat_id} "
            f"queue_size={queue_size} file={task.file_path}"
        )
        return (
            f"📌 Edit Wallet: файл в очереди профиля {profile}. "
            f"Очередь: {queue_size}."
        )
    log.info(
        f"📌 [WalletEditor] queued profile={profile} "
        f"user_id={task.telegram_user_id} chat_id={task.chat_id} "
        f"queue_size={queue_size} file={task.file_path}"
    )
    return (
        f"📌 Файл добавлен в очередь профиля {profile}. "
        f"Текущий размер очереди: {queue_size}"
    )


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

    from automation.runtime import MSG_OPERATOR_UNMAPPED, resolve_operator_for_user

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

    from modules.antares.work_admission import (
        ADMISSION_CLOSED_REPLY,
        AdmissionQueued,
        AdmissionRejected,
        AdmissionState,
        bound_admission,
    )

    admission = bound_admission()
    if admission is not None and admission.state is not AdmissionState.OPEN:
        await message.reply_text(ADMISSION_CLOSED_REPLY)
        return

    from automation.edit_wallet_contract import ExcelRouting, detect_excel_routing
    from automation.worker import add_add_wallet_task, add_edit_wallet_task, add_task

    if admission is None:
        try:
            await message.reply_text("📥 Файл получен")
            _ensure_tmp_dir()
            local_path = TMP_DIR / f"wallet_editor_{uuid4().hex}{ALLOWED_EXTENSION}"

            tg_file = await context.bot.get_file(document.file_id)
            await tg_file.download_to_drive(custom_path=str(local_path))

            routing, routing_error = detect_excel_routing(
                str(local_path),
                original_filename=document.file_name,
            )
            if routing == ExcelRouting.AMBIGUOUS:
                log.info(
                    f"❌ [WalletEditor] ambiguous contract chat_id={chat_id} error={routing_error}"
                )
                try:
                    local_path.unlink(missing_ok=True)
                except OSError:
                    pass
                await message.reply_text(
                    f"❌ Не удалось определить тип Excel: {routing_error or 'ambiguous'}"
                )
                return

            kind, task, dry_run = _route_task(
                routing,
                local_path=local_path,
                document=document,
                operator=operator,
                chat_id=chat_id,
                telegram_user_id=telegram_user_id,
            )
            if kind == "add":
                queue_size = add_add_wallet_task(task)
            elif kind == "edit":
                queue_size = add_edit_wallet_task(task)
            else:
                queue_size = add_task(task)
            text = _queued_log_and_text(kind, task, dry_run=dry_run, queue_size=queue_size)
            await message.reply_text(text)
        except Exception as e:
            log.exception(f"❌ [WalletEditor] ingest failed chat_id={chat_id}: {e}")
            await message.reply_text(f"❌ Ошибка при приёме файла: {e}")
        return

    local_path: Path | None = None
    handed_off = False
    try:
        await message.reply_text("📥 Файл получен")
        _ensure_tmp_dir()
        local_path = TMP_DIR / f"wallet_editor_{uuid4().hex}{ALLOWED_EXTENSION}"

        tg_file = await context.bot.get_file(document.file_id)
        await tg_file.download_to_drive(custom_path=str(local_path))

        routing, routing_error = detect_excel_routing(
            str(local_path),
            original_filename=document.file_name,
        )
        if routing == ExcelRouting.AMBIGUOUS:
            log.info(
                f"❌ [WalletEditor] ambiguous contract chat_id={chat_id} error={routing_error}"
            )
            _best_effort_unlink(local_path)
            await message.reply_text(
                f"❌ Не удалось определить тип Excel: {routing_error or 'ambiguous'}"
            )
            return

        kind, task, dry_run = _route_task(
            routing,
            local_path=local_path,
            document=document,
            operator=operator,
            chat_id=chat_id,
            telegram_user_id=telegram_user_id,
        )
        from automation.worker import ensure_profile_queue

        queue = ensure_profile_queue(operator.profile_key)
        outcome = admission.put_nowait_if_open(queue, task)
        if isinstance(outcome, AdmissionRejected):
            _best_effort_unlink(local_path)
            await message.reply_text(ADMISSION_CLOSED_REPLY)
            return
        if not isinstance(outcome, AdmissionQueued):
            _best_effort_unlink(local_path)
            raise RuntimeError(f"unexpected ingest admit outcome {type(outcome)!r}")
        handed_off = True
        queue_size = None
        try:
            queue_size = queue.qsize()
        except Exception:
            queue_size = None
        try:
            text = _queued_log_and_text(
                kind, task, dry_run=dry_run, queue_size=queue_size
            )
        except Exception:
            log.exception("isolated ingest queued log failed")
            text = f"📌 Файл добавлен в очередь профиля {operator.profile_key}."
        try:
            await message.reply_text(text)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("isolated ingest confirmation reply failed")
    except asyncio.CancelledError as pending:
        if not handed_off:
            _cleanup_owned(local_path, pending)
        raise
    except Exception as e:
        if handed_off:
            log.exception(f"❌ [WalletEditor] ingest after accept chat_id={chat_id}: {e}")
            return
        try:
            _best_effort_unlink(local_path)
        except Exception:
            pass
        log.exception(f"❌ [WalletEditor] ingest failed chat_id={chat_id}: {e}")
        try:
            await message.reply_text(f"❌ Ошибка при приёме файла: {e}")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("isolated ingest error reply failed")


log_wallet_editor_allowlist_startup_warning()
