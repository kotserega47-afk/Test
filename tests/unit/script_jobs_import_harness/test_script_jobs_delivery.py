from __future__ import annotations

from unittest.mock import MagicMock, patch

from core.job_runner import Actor
from integrations.script_jobs.delivery import deliver_script_result
from integrations.script_jobs.types import ScriptExecutionContext, ScriptResult


def _context(*, chat_id: int | None = None, route_key: str | None = None) -> ScriptExecutionContext:
    return ScriptExecutionContext(
        actor=Actor(kind="tg" if chat_id else "scheduler", chat_id=chat_id),
        script_key="hello_world",
        job_type="script_job:hello_world",
        source="manual" if chat_id else "scheduled",
        chat_id=chat_id,
        route_key=route_key,
    )


@patch("integrations.script_jobs.delivery.send_message_sync")
def test_deliver_text_only_to_manual_chat(mock_send):
    deliver_script_result(
        _context(chat_id=12345),
        ScriptResult(status="ok", text="hello"),
    )
    mock_send.assert_called_once_with("hello", chat_id="12345")


@patch("integrations.script_jobs.delivery.send_message_to_route")
def test_deliver_text_only_to_route(mock_route):
    deliver_script_result(
        _context(route_key="platform_hourly_report"),
        ScriptResult(status="ok", text="scheduled text"),
    )
    mock_route.assert_called_once_with("platform_hourly_report", "scheduled text")


@patch("integrations.script_jobs.delivery.send_file_sync")
@patch("integrations.script_jobs.delivery.send_message_sync")
def test_deliver_text_and_file_to_manual_chat(mock_send, mock_file, tmp_path):
    file_path = tmp_path / "out.txt"
    file_path.write_text("payload", encoding="utf-8")

    deliver_script_result(
        _context(chat_id=999),
        ScriptResult(status="ok", text="caption", files=(str(file_path),)),
    )

    mock_send.assert_not_called()
    mock_file.assert_called_once_with(str(file_path), "caption", chat_id="999")


@patch("integrations.script_jobs.delivery.send_file_sync")
@patch("integrations.script_jobs.delivery.send_message_sync")
def test_deliver_file_only_to_manual_chat(mock_send, mock_file, tmp_path):
    file_path = tmp_path / "only.bin"
    file_path.write_bytes(b"x")

    deliver_script_result(
        _context(chat_id=42),
        ScriptResult(status="ok", files=(str(file_path),)),
    )

    mock_send.assert_not_called()
    mock_file.assert_called_once_with(str(file_path), None, chat_id="42")


@patch("integrations.script_jobs.delivery._deliver_files_to_route")
@patch("integrations.script_jobs.delivery.send_message_to_route")
def test_deliver_text_and_file_to_route(mock_route, mock_files):
    deliver_script_result(
        _context(route_key="wallet_editor_registry_refresh"),
        ScriptResult(status="ok", text="report", files=("/tmp/a.xlsx",)),
    )
    mock_route.assert_called_once_with("wallet_editor_registry_refresh", "report")
    mock_files.assert_called_once_with("wallet_editor_registry_refresh", ("/tmp/a.xlsx",), None)


@patch("integrations.script_jobs.delivery.send_message_sync")
def test_manual_chat_preferred_over_route(mock_send):
    deliver_script_result(
        _context(chat_id=1, route_key="platform_hourly_report"),
        ScriptResult(status="ok", text="to chat"),
    )
    mock_send.assert_called_once_with("to chat", chat_id="1")
