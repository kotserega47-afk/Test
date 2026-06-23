"""Script jobs — explicit Telegram delivery from ScriptExecutionContext."""

from __future__ import annotations

import logging
from pathlib import Path

from integrations.script_jobs.types import ScriptExecutionContext, ScriptResult
from integrations.telegram_bot import send_file_sync, send_message_sync
from integrations.telegram_routes import send_message_to_route

log = logging.getLogger(__name__)


def _deliver_text_to_chat(chat_id: int, text: str) -> bool:
  if not text:
    return False
  send_message_sync(text, chat_id=str(chat_id))
  return True


def _deliver_text_to_route(route_key: str, text: str) -> bool:
  if not text:
    return False
  return send_message_to_route(route_key, text)


def _deliver_files_to_chat(chat_id: int, files: tuple[str, ...], caption: str | None) -> int:
  sent = 0
  for index, file_path in enumerate(files):
    path = Path(file_path)
    if not path.is_file():
      log.warning("[script_jobs] file missing path=%s", path.name)
      continue
    file_caption = caption if index == 0 and caption else None
    send_file_sync(str(path), file_caption, chat_id=str(chat_id))
    sent += 1
  return sent


def _deliver_files_to_route(route_key: str, files: tuple[str, ...], caption: str | None) -> int:
  """Routes support text via send_message_to_route; files use resolved chat_id."""
  from integrations.telegram_routes import resolve_route_chat_id

  resolution = resolve_route_chat_id(route_key)
  if not resolution.chat_id:
    log.warning("[script_jobs] route unresolved route_key=%s source=%s", route_key, resolution.source)
    return 0
  return _deliver_files_to_chat(int(resolution.chat_id), files, caption)


def deliver_script_result(context: ScriptExecutionContext, result: ScriptResult) -> None:
  """Deliver text/files using explicit context only (no hidden globals)."""
  text = (result.text or "").strip()
  files = result.files
  caption = text if text and files else None

  if context.chat_id is not None:
    if text and not files:
      _deliver_text_to_chat(context.chat_id, text)
    elif files:
      if text and not caption:
        _deliver_text_to_chat(context.chat_id, text)
      _deliver_files_to_chat(context.chat_id, files, caption)
    return

  if context.route_key:
    if text:
      _deliver_text_to_route(context.route_key, text)
    if files:
      _deliver_files_to_route(context.route_key, files, caption if not text else None)
    return

  if text or files:
    log.warning(
      "[script_jobs] delivery skipped script_key=%s source=%s reason=no_destination",
      context.script_key,
      context.source,
    )
