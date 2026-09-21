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
    assert "harness_installed" in kinds, result.events
    assert "blocked_external" not in kinds, result.events
    assert "forbidden_lifecycle" not in kinds, result.events
    assert "assembly_called" in kinds, result.events
    assert "application_build_ok" in kinds, result.events
    assert "in_loop_state_recorded" in kinds, result.events


def _in_loop(result) -> dict:
    live = result.report.get("in_loop")
    assert isinstance(live, dict), result.report
    assert live.get("recorded_before_loop_close") is True, live
    assert live.get("loop_running") is True, live
    return live


def _assert_in_loop_clean(result) -> None:
    live = _in_loop(result)
    assert live.get("app_running") is False, live
    assert live.get("updater_running") is False, live
    assert live.get("httpx_closed") == [True, True], live
    assert live.get("ptb_unfinished_tasks") == [], live
    assert live.get("create_task_unfinished") == [], live
    assert live.get("runner_unfinished_tasks") == [], live
    fetcher_done = live.get("fetcher_done")
    assert fetcher_done in {True, None}, live


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
    _assert_in_loop_clean(result)
    assert result.report.get("job_queue_set") is None or _in_loop(result).get("job_queue_set") is False
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
    _assert_in_loop_clean(result)


def test_enable_polling_rejected_before_initialize() -> None:
    result = _run("enable_polling")
    assert result.returncode == 2, result.stderr + result.stdout
    _assert_isolated(result)
    kinds = _kinds(result)
    assert "application_initialize_called" not in kinds
    assert result.report.get("exc_type") == "ValueError"
    live = _in_loop(result)
    assert any(item is False for item in (live.get("httpx_closed") or [])), live


def test_fail_requests_closes_httpx_without_reinitialize() -> None:
    result = _run("fail_requests")
    assert result.returncode == 2, result.stderr + result.stdout
    _assert_isolated(result)
    kinds = _kinds(result)
    assert "httpx_initialize_injected_failure" in kinds
    assert "bot_initialize_failed" in kinds
    assert "application_initialize_ok" not in kinds
    assert "httpx_shutdown_ok" in kinds
    live = _in_loop(result)
    assert live.get("bot_requests_initialized") is False
    assert live.get("app_initialized") is False
    actions = result.report.get("cleanup_actions") or []
    assert "bot.shutdown" not in actions
    assert any(item.endswith(".shutdown") for item in actions), actions
    assert live.get("httpx_closed") == [True, True], live
    assert "antares lifecycle ok" not in result.stdout


def test_fail_requests_cleanup_error_preserves_primary() -> None:
    result = _run("fail_requests_and_cleanup")
    assert result.returncode == 2, result.stderr + result.stdout
    _assert_isolated(result)
    assert result.report.get("exc_type") == "RuntimeError"
    assert "injected HTTPXRequest.initialize failure" in str(result.report.get("exc"))
    assert result.report.get("cause_type") == "ExceptionGroup"
    errors = result.report.get("cleanup_errors") or []
    assert any("injected HTTPXRequest.shutdown failure" in str(item) for item in errors), errors
    kinds = _kinds(result)
    assert "httpx_shutdown_injected_failure" in kinds
    assert "httpx_shutdown_ok" in kinds
    live = _in_loop(result)
    closed = live.get("httpx_closed") or []
    assert closed == [False, True], live
    assert "antares lifecycle ok" not in result.stdout


def test_fail_get_me_uses_bot_shutdown() -> None:
    result = _run("fail_get_me")
    assert result.returncode == 2, result.stderr + result.stdout
    _assert_isolated(result)
    kinds = _kinds(result)
    assert "get_me_injected_failure" in kinds
    assert result.report.get("exc_type") == "InvalidToken"
    live = _in_loop(result)
    assert live.get("app_initialized") is False
    actions = result.report.get("cleanup_actions") or []
    assert "bot.shutdown" in actions
    assert "app.shutdown" not in actions
    assert live.get("httpx_closed") == [True, True], live
    assert "httpx_shutdown_ok" in kinds


def test_fail_after_bot_before_application_flag() -> None:
    result = _run("fail_after_bot")
    assert result.returncode == 2, result.stderr + result.stdout
    _assert_isolated(result)
    kinds = _kinds(result)
    assert "bot_initialize_ok" in kinds
    assert "processor_initialize_injected_failure" in kinds
    assert "application_initialize_ok" not in kinds
    live = _in_loop(result)
    assert live.get("app_initialized") is False
    actions = result.report.get("cleanup_actions") or []
    assert "bot.shutdown" in actions
    assert "app.shutdown" not in actions
    assert live.get("httpx_closed") == [True, True], live


def test_fail_start_shuts_down_initialized_app() -> None:
    result = _run("fail_start")
    assert result.returncode == 2, result.stderr + result.stdout
    _assert_isolated(result)
    kinds = _kinds(result)
    assert "application_initialize_ok" in kinds
    assert "application_start_injected_failure" in kinds
    assert "application_start_ok" not in kinds
    live = _in_loop(result)
    assert live.get("app_running") is False
    actions = result.report.get("cleanup_actions") or []
    assert "app.stop" not in actions
    assert "app.shutdown" in actions
    assert live.get("httpx_closed") == [True, True], live


def test_cancel_after_start_still_cleans_up() -> None:
    result = _run("cancel")
    assert result.returncode == 2, result.stderr + result.stdout
    _assert_isolated(result)
    kinds = _kinds(result)
    assert "application_start_ok" in kinds
    assert "cancelling_lifecycle" in kinds
    assert result.report.get("exc_type") == "CancelledError"
    actions = result.report.get("cleanup_actions") or []
    assert "app.stop" in actions
    assert "app.shutdown" in actions
    _assert_in_loop_clean(result)
    assert _in_loop(result).get("fetcher_done") is True
    assert "antares lifecycle ok" not in result.stdout


def test_cancel_during_cleanup_still_finishes_remaining_steps() -> None:
    result = _run("cancel_during_cleanup")
    assert result.returncode == 2, result.stderr + result.stdout
    _assert_isolated(result)
    kinds = _kinds(result)
    assert "cancelling_during_cleanup" in kinds
    assert result.report.get("exc_type") == "CancelledError"
    assert result.report.get("cancelled_during_cleanup") is True
    actions = result.report.get("cleanup_actions") or []
    assert "app.stop" in actions
    assert "app.shutdown" in actions
    _assert_in_loop_clean(result)
    assert "antares lifecycle ok" not in result.stdout


def test_cleanup_only_error_still_closes_httpx() -> None:
    result = _run("fail_cleanup_only")
    assert result.returncode == 2, result.stderr + result.stdout
    _assert_isolated(result)
    kinds = _kinds(result)
    assert "whoami_completed" in kinds
    assert "application_shutdown_injected_failure" in kinds
    assert "application_shutdown_ok" not in kinds
    assert result.report.get("exc_type") == "RuntimeError"
    assert "injected application shutdown failure" in str(result.report.get("exc"))
    actions = result.report.get("cleanup_actions") or []
    assert any(item.startswith("app.shutdown.failed") for item in actions), actions
    assert "get_updates_request.shutdown" in actions
    assert "request.shutdown" in actions
    live = _in_loop(result)
    assert live.get("httpx_closed") == [True, True], live
    assert live.get("app_running") is False, live
    assert "antares lifecycle ok" not in result.stdout


def test_unsupported_job_queue_rejected_before_initialize() -> None:
    result = _run("unsupported_job_queue")
    assert result.returncode == 2, result.stderr + result.stdout
    _assert_isolated(result)
    kinds = _kinds(result)
    assert "application_initialize_called" not in kinds
    assert result.report.get("exc_type") == "ValueError"
    assert "job_queue" in str(result.report.get("exc"))


def test_unsupported_processor_rejected_before_initialize() -> None:
    result = _run("unsupported_processor")
    assert result.returncode == 2, result.stderr + result.stdout
    _assert_isolated(result)
    kinds = _kinds(result)
    assert "application_initialize_called" not in kinds
    assert result.report.get("exc_type") == "ValueError"
    assert "processor=" in str(result.report.get("exc"))
