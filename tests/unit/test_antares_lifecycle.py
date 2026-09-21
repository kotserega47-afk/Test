from __future__ import annotations

import tempfile
from pathlib import Path

from tests.unit.antares_lifecycle_child_runner import run_antares_lifecycle
from tests.unit.test_antares_boot import _accepted_local_xlsx, _PTB_TOKEN

_ROOT = Path(__file__).resolve().parents[2]


def _kinds(result) -> list[str]:
    return [str(item.get("kind")) for item in result.events]


def _run(scenario: str):
    with tempfile.TemporaryDirectory() as tmp:
        sandbox = Path(tmp)
        workbook = _accepted_local_xlsx(sandbox / "rules.xlsx")
        return run_antares_lifecycle(
            sandbox,
            scenario=scenario,
            workbook=workbook,
            process_env={
                "PROJECT_PROFILE": "antares",
                "TELEGRAM_BOT_TOKEN": _PTB_TOKEN,
            },
        )


def _assert_isolated(result) -> None:
    assert result.harness_ready is True
    assert result.import_attempts == [], result.import_attempts
    kinds = _kinds(result)
    assert "blocked_external" not in kinds, result.events
    assert "forbidden_lifecycle" not in kinds, result.events
    assert "assembly_called" in kinds, result.events
    assert "application_build_ok" in kinds, result.events


def test_whoami_queue_stop_shutdown() -> None:
    result = _run("whoami")
    assert result.returncode == 0, result.stderr + result.stdout
    _assert_isolated(result)
    kinds = _kinds(result)
    assert "update_queued" in kinds
    assert "whoami_completed" in kinds
    assert "send_message_synthetic" in kinds
    assert "intake_stopped" in kinds
    assert "application_start_ok" in kinds
    assert "application_stop_ok" in kinds
    assert "application_shutdown_ok" in kinds
    assert "antares lifecycle ok scenario=whoami leftover=0" in result.stdout
    assert result.report.get("exc_type") is None
    assert result.report.get("leftover") == []
    assert result.report.get("fetcher_done") is True
    assert result.report.get("app_running") is False
    assert result.report.get("job_queue") is False
    actions = result.report.get("cleanup_actions") or []
    assert "app.stop" in actions
    assert "app.shutdown" in actions


def test_callback_error_reaches_error_handler() -> None:
    result = _run("callback_error")
    assert result.returncode == 0, result.stderr + result.stdout
    _assert_isolated(result)
    kinds = _kinds(result)
    assert "whoami_entered" in kinds
    assert "whoami_raising" in kinds
    assert "whoami_completed" not in kinds
    assert "error_handler" in kinds
    err = [item for item in result.events if item.get("kind") == "error_handler"]
    assert err and err[0].get("exc_name") == "RuntimeError"
    assert "injected whoami callback failure" in str(err[0].get("message"))
    assert "antares lifecycle ok scenario=whoami" not in result.stdout
    assert result.report.get("leftover") == []


def test_enable_polling_rejected_before_initialize() -> None:
    result = _run("enable_polling")
    assert result.returncode == 2, result.stderr + result.stdout
    kinds = _kinds(result)
    assert "application_initialize_called" not in kinds
    assert result.report.get("exc_type") == "ValueError"
    leftover = result.report.get("leftover") or []
    assert any(item.endswith(".httpx_open") for item in leftover), leftover


def test_fail_requests_closes_httpx_without_reinitialize() -> None:
    result = _run("fail_requests")
    assert result.returncode == 2, result.stderr + result.stdout
    kinds = _kinds(result)
    assert "httpx_initialize_injected_failure" in kinds
    assert "bot_initialize_failed" in kinds
    assert "application_initialize_ok" not in kinds
    assert "httpx_shutdown_ok" in kinds
    assert result.report.get("bot_requests_initialized") is False
    assert result.report.get("app_initialized") is False
    actions = result.report.get("cleanup_actions") or []
    assert "bot.shutdown" not in actions
    assert any(item.endswith(".shutdown") for item in actions), actions
    assert result.report.get("leftover") == []
    assert "antares lifecycle ok" not in result.stdout


def test_fail_requests_cleanup_error_preserves_primary() -> None:
    result = _run("fail_requests_and_cleanup")
    assert result.returncode == 2, result.stderr + result.stdout
    assert result.report.get("exc_type") == "RuntimeError"
    assert "injected HTTPXRequest.initialize failure" in str(result.report.get("exc"))
    assert result.report.get("cause_type") == "ExceptionGroup"
    leftover = result.report.get("leftover") or result.report.get("helper_leftover") or []
    assert any(item.endswith(".httpx_open") for item in leftover), leftover
    assert "antares lifecycle ok" not in result.stdout


def test_fail_get_me_uses_bot_shutdown() -> None:
    result = _run("fail_get_me")
    assert result.returncode == 2, result.stderr + result.stdout
    kinds = _kinds(result)
    assert "get_me_injected_failure" in kinds
    assert result.report.get("exc_type") == "InvalidToken"
    assert result.report.get("app_initialized") is False
    actions = result.report.get("cleanup_actions") or []
    assert "bot.shutdown" in actions
    assert "app.shutdown" not in actions
    assert result.report.get("leftover") == []
    assert "httpx_shutdown_ok" in kinds


def test_fail_after_bot_before_application_flag() -> None:
    result = _run("fail_after_bot")
    assert result.returncode == 2, result.stderr + result.stdout
    kinds = _kinds(result)
    assert "bot_initialize_ok" in kinds
    assert "processor_initialize_injected_failure" in kinds
    assert "application_initialize_ok" not in kinds
    assert result.report.get("app_initialized") is False
    actions = result.report.get("cleanup_actions") or []
    assert "bot.shutdown" in actions
    assert "app.shutdown" not in actions
    assert result.report.get("leftover") == []


def test_fail_start_shuts_down_initialized_app() -> None:
    result = _run("fail_start")
    assert result.returncode == 2, result.stderr + result.stdout
    kinds = _kinds(result)
    assert "application_initialize_ok" in kinds
    assert "application_start_injected_failure" in kinds
    assert "application_start_ok" not in kinds
    assert result.report.get("app_running") is False
    actions = result.report.get("cleanup_actions") or []
    assert "app.stop" not in actions
    assert "app.shutdown" in actions
    assert result.report.get("leftover") == []


def test_cancel_after_start_still_cleans_up() -> None:
    result = _run("cancel")
    assert result.returncode == 2, result.stderr + result.stdout
    kinds = _kinds(result)
    assert "application_start_ok" in kinds
    assert "cancelling_lifecycle" in kinds
    assert result.report.get("exc_type") == "CancelledError"
    actions = result.report.get("cleanup_actions") or []
    assert "app.stop" in actions
    assert "app.shutdown" in actions
    assert result.report.get("leftover") == []
    assert result.report.get("fetcher_done") is True
    assert "antares lifecycle ok" not in result.stdout


def test_cleanup_only_error_is_not_success() -> None:
    result = _run("fail_cleanup_only")
    assert result.returncode == 2, result.stderr + result.stdout
    kinds = _kinds(result)
    assert "whoami_completed" in kinds
    assert "application_shutdown_injected_failure" in kinds
    assert result.report.get("exc_type") == "RuntimeError"
    assert "injected application shutdown failure" in str(result.report.get("exc"))
    assert "antares lifecycle ok" not in result.stdout
    assert result.returncode != 0
