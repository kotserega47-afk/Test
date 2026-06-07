"""WE-CONCURRENCY-GUARD-1A: auto-enable Antares via CONVERSION_AUTO worker queue."""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from automation.runtime import WalletEditorTask
from automation.worker import (
    ProfileQueueItem,
    WalletEditorAutoEnableBatchTask,
    enqueue_auto_enable_batch,
)
from integrations.conversion_wallet_editor_bridge import OPERATOR_PROFILE
from integrations.wallet_editor_auto_enable_eligibility import CandidateRow
from integrations.wallet_editor_auto_enable_settings import AutoEnableSettings


def _settings() -> AutoEnableSettings:
    return AutoEnableSettings(
        enabled=True,
        dry_run=False,
        approval_required=False,
        max_rows_per_batch=200,
        max_rows_per_run=0,
        seconds_per_card_timeout=10,
        batch_timeout_buffer_seconds=300,
        working_statuses=("готов к работе",),
        auto_return_statuses=(),
        auto_return_target_status="Готов к работе",
        allowed_statuses_for_enable=("готов к работе",),
        deprecated_working_statuses_fallback=False,
        include_overdue=True,
        telegram_route_report="wallet_editor_auto_enable",
        telegram_route_alert="wallet_editor_auto_enable_alert",
    )


def _candidate() -> CandidateRow:
    return CandidateRow(
        card="4111111111111111",
        partner="P1",
        disable_at="2026-01-01",
        enable_status="К ВКЛЮЧЕНИЮ",
        vklyucheno="",
        source_row_index=0,
    )


def _disable_task(*, profile: str = OPERATOR_PROFILE, file_path: str = "/tmp/we/disable.xlsx") -> WalletEditorTask:
    return WalletEditorTask(
        file_path=file_path,
        chat_id=-1,
        telegram_user_id=0,
        operator_profile=profile,
        source_file_name="disable.xlsx",
        login="conv-login",
        password="conv-pass",
        auth_state_path=f"/tmp/auth_state_wallet_editor_{profile}.json",
    )


@pytest.fixture(autouse=True)
def isolated_worker_registry():
    import automation.worker as worker_mod

    with worker_mod._registry_lock:
        worker_mod._profile_workers.clear()
    yield
    with worker_mod._registry_lock:
        worker_mod._profile_workers.clear()


def test_auto_enable_enqueued_to_conversion_auto_worker() -> None:
    import automation.worker as worker_mod

    captured: list[ProfileQueueItem] = []

    real_put = worker_mod.Queue.put

    def capture_put(self, item):  # noqa: ANN001
        captured.append(item)
        return real_put(self, item)

    with patch.object(worker_mod.Queue, "put", capture_put):
        with patch(
            "integrations.wallet_editor_auto_enable_executor.execute_enable_batch",
            return_value=[],
        ):
            with patch(
                "integrations.wallet_editor_auto_enable_executor.build_run_config_from_conversion_env",
                return_value=MagicMock(
                    login="l",
                    password="p",
                    auth_state_path="/tmp/auth_state_wallet_editor_CONVERSION_AUTO.json",
                ),
            ):
                with patch("automation.runtime.require_wallet_editor_antares_credentials"):
                    enqueue_auto_enable_batch([_candidate()], _settings())

    assert len(captured) == 1
    assert isinstance(captured[0], WalletEditorAutoEnableBatchTask)
    assert captured[0].operator_profile == OPERATOR_PROFILE
    assert worker_mod._profile_workers[OPERATOR_PROFILE].thread is not None
    assert worker_mod._profile_workers[OPERATOR_PROFILE].thread.name == (
        f"wallet-editor-worker-{OPERATOR_PROFILE}"
    )


def test_auto_enable_enqueued_puts_batch_task_on_queue() -> None:
    import automation.worker as worker_mod

    with patch.object(worker_mod.threading, "Thread") as mock_thread:
        mock_thread.side_effect = lambda **kwargs: MagicMock(start=MagicMock())

        batch_task = WalletEditorAutoEnableBatchTask(
            operator_profile=OPERATOR_PROFILE,
            login="l",
            password="p",
            auth_state_path="/tmp/auth_state_wallet_editor_CONVERSION_AUTO.json",
            candidates=(_candidate(),),
            settings=_settings(),
        )
        batch_task.result_future.set_result([])

        with patch(
            "integrations.wallet_editor_auto_enable_executor.build_run_config_from_conversion_env",
            return_value=MagicMock(
                login="l",
                password="p",
                auth_state_path="/tmp/auth_state_wallet_editor_CONVERSION_AUTO.json",
            ),
        ):
            with patch("automation.runtime.require_wallet_editor_antares_credentials"):
                with patch.object(
                    worker_mod,
                    "WalletEditorAutoEnableBatchTask",
                    return_value=batch_task,
                ):
                    enqueue_auto_enable_batch([_candidate()], _settings())

        worker = worker_mod._profile_workers[OPERATOR_PROFILE]
        assert worker.queue.qsize() == 1
        item = worker.queue.get_nowait()
        assert isinstance(item, WalletEditorAutoEnableBatchTask)


def test_auto_enable_and_disable_same_profile_serialized() -> None:
    import automation.worker as worker_mod

    lock = threading.Lock()
    in_antares = 0
    max_concurrent = 0

    def slow_disable(file_path: str, cfg):  # noqa: ARG001
        nonlocal in_antares, max_concurrent
        with lock:
            in_antares += 1
            max_concurrent = max(max_concurrent, in_antares)
        time.sleep(0.12)
        with lock:
            in_antares -= 1
        stats = MagicMock()
        stats.summary.return_value = "OK=1"
        return "/tmp/result.xlsx", stats

    def slow_enable(candidates, settings, *, cfg=None):  # noqa: ARG001
        nonlocal in_antares, max_concurrent
        with lock:
            in_antares += 1
            max_concurrent = max(max_concurrent, in_antares)
        time.sleep(0.12)
        with lock:
            in_antares -= 1
        return []

    with patch("automation.worker.run", side_effect=slow_disable):
        with patch(
            "integrations.wallet_editor_auto_enable_executor.execute_enable_batch",
            side_effect=slow_enable,
        ):
            with patch("automation.worker.send_text"):
                with patch("automation.worker.send_document"):
                    with patch(
                        "integrations.wallet_editor_auto_enable_executor.build_run_config_from_conversion_env",
                        return_value=MagicMock(
                            login="l",
                            password="p",
                            auth_state_path="/tmp/auth_state_wallet_editor_CONVERSION_AUTO.json",
                        ),
                    ):
                        with patch("automation.runtime.require_wallet_editor_antares_credentials"):
                            worker_mod.add_task(_disable_task())

                            enable_error: list[BaseException] = []

                            def run_enable() -> None:
                                try:
                                    enqueue_auto_enable_batch([_candidate()], _settings())
                                except BaseException as exc:
                                    enable_error.append(exc)

                            t = threading.Thread(target=run_enable)
                            t.start()

                            deadline = time.time() + 3
                            while worker_mod._profile_workers[OPERATOR_PROFILE].queue.unfinished_tasks > 0:
                                if time.time() > deadline:
                                    break
                                time.sleep(0.02)
                            t.join(timeout=3)
                            time.sleep(0.05)

    assert not enable_error
    assert max_concurrent == 1


def test_different_profiles_still_parallel() -> None:
    import automation.worker as worker_mod

    lock = threading.Lock()
    in_antares = 0
    max_concurrent = 0

    def slow_disable(file_path: str, cfg):  # noqa: ARG001
        nonlocal in_antares, max_concurrent
        with lock:
            in_antares += 1
            max_concurrent = max(max_concurrent, in_antares)
        time.sleep(0.15)
        with lock:
            in_antares -= 1
        stats = MagicMock()
        stats.summary.return_value = "OK=1"
        return "/tmp/result.xlsx", stats

    def slow_enable(candidates, settings, *, cfg=None):  # noqa: ARG001
        nonlocal in_antares, max_concurrent
        with lock:
            in_antares += 1
            max_concurrent = max(max_concurrent, in_antares)
        time.sleep(0.15)
        with lock:
            in_antares -= 1
        return []

    cfg_mock = MagicMock(
        login="l",
        password="p",
        auth_state_path="/tmp/auth_state_wallet_editor_CONVERSION_AUTO.json",
    )

    with patch("automation.worker.run", side_effect=slow_disable):
        with patch(
            "integrations.wallet_editor_auto_enable_executor.execute_enable_batch",
            side_effect=slow_enable,
        ):
            with patch("automation.worker.send_text"):
                with patch("automation.worker.send_document"):
                    with patch(
                        "integrations.wallet_editor_auto_enable_executor.build_run_config_from_conversion_env",
                        return_value=cfg_mock,
                    ):
                        with patch("automation.runtime.require_wallet_editor_antares_credentials"):
                            worker_mod.add_task(
                                _disable_task(profile="DENIS", file_path="/tmp/denis.xlsx")
                            )
                            enable_thread = threading.Thread(
                                target=lambda: enqueue_auto_enable_batch(
                                    [_candidate()], _settings()
                                )
                            )
                            enable_thread.start()

                            deadline = time.time() + 3
                            profiles = worker_mod._profile_workers
                            while (
                                profiles["DENIS"].queue.unfinished_tasks > 0
                                or profiles[OPERATOR_PROFILE].queue.unfinished_tasks > 0
                            ):
                                if time.time() > deadline:
                                    break
                                time.sleep(0.02)
                            enable_thread.join(timeout=3)
                            time.sleep(0.05)

    assert max_concurrent >= 2


def test_auto_enable_result_returned_to_runner() -> None:
    import automation.worker as worker_mod

    expected = MagicMock()
    expected.card = "4111111111111111"

    with patch(
        "integrations.wallet_editor_auto_enable_executor.execute_enable_batch",
        return_value=[expected],
    ):
        with patch(
            "integrations.wallet_editor_auto_enable_executor.build_run_config_from_conversion_env",
            return_value=MagicMock(
                login="l",
                password="p",
                auth_state_path="/tmp/auth_state_wallet_editor_CONVERSION_AUTO.json",
            ),
        ):
            with patch("automation.runtime.require_wallet_editor_antares_credentials"):
                outcomes = enqueue_auto_enable_batch([_candidate()], _settings())

    assert outcomes == [expected]
    assert worker_mod._profile_workers[OPERATOR_PROFILE].queue.unfinished_tasks == 0


def test_disable_flow_unchanged() -> None:
    import automation.worker as worker_mod

    with patch("automation.worker.run") as mock_run:
        mock_run.return_value = ("/tmp/result.xlsx", MagicMock(summary=lambda: "OK=1"))
        with patch("automation.worker.send_text"):
            with patch("automation.worker.send_document"):
                with patch("automation.worker.schedule_registry_append"):
                    with patch("automation.worker.stage_registry_result_copy", return_value=("/tmp/copy.xlsx", True)):
                        worker_mod.add_task(_disable_task(profile="DENIS"))

                        deadline = time.time() + 3
                        while worker_mod._profile_workers["DENIS"].queue.unfinished_tasks > 0:
                            if time.time() > deadline:
                                break
                            time.sleep(0.02)

    mock_run.assert_called_once()


def test_worker_logs_auto_enable_queue_lifecycle(caplog) -> None:
    import automation.worker as worker_mod
    import logging

    caplog.set_level(logging.INFO, logger=worker_mod.log.name)

    with patch(
        "integrations.wallet_editor_auto_enable_executor.execute_enable_batch",
        return_value=[],
    ):
        with patch(
            "integrations.wallet_editor_auto_enable_executor.build_run_config_from_conversion_env",
            return_value=MagicMock(
                login="l",
                password="p",
                auth_state_path="/tmp/auth_state_wallet_editor_CONVERSION_AUTO.json",
            ),
        ):
            with patch("automation.runtime.require_wallet_editor_antares_credentials"):
                enqueue_auto_enable_batch([_candidate()], _settings())

    messages = caplog.text
    assert "[AutoEnable] queued profile=CONVERSION_AUTO" in messages
    assert "[AutoEnable] started profile=CONVERSION_AUTO" in messages
    assert "[AutoEnable] finished profile=CONVERSION_AUTO" in messages


def test_run_auto_enable_uses_worker_enqueue() -> None:
    from integrations.wallet_editor_auto_enable import run_auto_enable
    from integrations.wallet_editor_auto_enable_executor import REGISTRY_OK, EnableOutcome
    from integrations.wallet_editor_registry_lifecycle import STATUS_K_VKLUCHENIYU

    settings = _settings()
    df = __import__("pandas").DataFrame(
        [
            {
                "Дата отключения": "01.06.2026 10:00:00",
                "Дата включения": "06.06.2026",
                "Статус включения": STATUS_K_VKLUCHENIYU,
                "Включено": "",
                "Комментарий включения": "",
                "card": "4111",
                "partner": "Ostin",
                "action": "remove_partner",
                "status": "OK",
                "comment": "",
                "hold": "",
            }
        ]
    )

    outcome = EnableOutcome(
        card="4111",
        partner="Ostin",
        disable_date="01.06.2026 10:00:00",
        registry_value=REGISTRY_OK,
        registry_comment="ok",
        status_before="",
        status_after="",
        partner_present_before=False,
        partner_present_after=False,
        mutated=False,
        saved=False,
        error_code="",
        source_row_index=0,
    )

    with patch(
        "integrations.wallet_editor_auto_enable.enqueue_auto_enable_batch",
        return_value=[outcome],
    ) as mock_enqueue:
        with patch("integrations.wallet_editor_auto_enable._send_to_route", return_value=True):
            with patch(
                "integrations.wallet_editor_auto_enable.patch_enable_results_in_dropbox_registry",
                return_value=__import__(
                    "integrations.wallet_editor_registry", fromlist=["EnablePatchResult"]
                ).EnablePatchResult(success=True, patched_count=1, requested_count=1),
            ):
                run_auto_enable(
                    settings=settings,
                    manual=True,
                    registry_frames=(df, df, df, df),
                )

    mock_enqueue.assert_called_once()
