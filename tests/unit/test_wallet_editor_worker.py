"""WE-6: per-profile WalletEditor worker queues."""
from __future__ import annotations

import importlib
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from automation.runtime import WalletEditorTask


def _make_task(
    *,
    profile: str = "DENIS",
    file_path: str = "/tmp/wallet_editor/test.xlsx",
    chat_id: int = -1,
    user_id: int = 111,
) -> WalletEditorTask:
    return WalletEditorTask(
        file_path=file_path,
        chat_id=chat_id,
        telegram_user_id=user_id,
        operator_profile=profile,
        source_file_name="batch.xlsx",
        login=f"{profile.lower()}-login",
        password=f"{profile.lower()}-pass",
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


def test_same_profile_tasks_share_one_queue_and_worker() -> None:
    import automation.worker as worker_mod

    with patch.object(worker_mod.threading, "Thread") as mock_thread:
        mock_thread.side_effect = lambda **kwargs: MagicMock(start=MagicMock())

        worker_mod.add_task(_make_task(profile="DENIS", file_path="/tmp/a.xlsx"))
        worker_mod.add_task(_make_task(profile="DENIS", file_path="/tmp/b.xlsx"))

        assert mock_thread.call_count == 1
        assert mock_thread.call_args.kwargs["name"] == "wallet-editor-worker-DENIS"

        worker = worker_mod._profile_workers["DENIS"]
        assert worker.queue.qsize() == 2


def test_different_profiles_create_separate_queues_and_workers() -> None:
    import automation.worker as worker_mod

    with patch.object(worker_mod.threading, "Thread") as mock_thread:
        mock_thread.side_effect = lambda **kwargs: MagicMock(start=MagicMock())

        worker_mod.add_task(_make_task(profile="DENIS"))
        worker_mod.add_task(_make_task(profile="IVAN", user_id=222))

        assert mock_thread.call_count == 2
        names = {c.kwargs["name"] for c in mock_thread.call_args_list}
        assert names == {"wallet-editor-worker-DENIS", "wallet-editor-worker-IVAN"}
        assert worker_mod._profile_workers["DENIS"].queue is not worker_mod._profile_workers["IVAN"].queue


def test_repeated_add_task_same_profile_does_not_spawn_second_worker() -> None:
    import automation.worker as worker_mod

    with patch.object(worker_mod.threading, "Thread") as mock_thread:
        mock_thread.side_effect = lambda **kwargs: MagicMock(start=MagicMock())

        for i in range(3):
            worker_mod.add_task(_make_task(profile="DENIS", file_path=f"/tmp/{i}.xlsx"))

        assert mock_thread.call_count == 1


def test_add_task_returns_profile_queue_size() -> None:
    import automation.worker as worker_mod

    with patch.object(worker_mod.threading, "Thread") as mock_thread:
        mock_thread.side_effect = lambda **kwargs: MagicMock(start=MagicMock())

        size1 = worker_mod.add_task(_make_task(profile="DENIS", file_path="/tmp/1.xlsx"))
        size2 = worker_mod.add_task(_make_task(profile="DENIS", file_path="/tmp/2.xlsx"))
        size_ivan = worker_mod.add_task(_make_task(profile="IVAN", file_path="/tmp/3.xlsx"))

    assert size1 == 1
    assert size2 == 2
    assert size_ivan == 1


def test_ensure_worker_started_is_no_op() -> None:
    import automation.worker as worker_mod

    with patch.object(worker_mod.threading, "Thread") as mock_thread:
        worker_mod.ensure_worker_started()
        worker_mod.ensure_worker_started()
        mock_thread.assert_not_called()


def test_same_profile_tasks_run_sequentially() -> None:
    import automation.worker as worker_mod

    lock = threading.Lock()
    in_run = 0
    max_concurrent = 0

    def fake_run(file_path: str, cfg):  # noqa: ARG001
        nonlocal in_run, max_concurrent
        with lock:
            in_run += 1
            max_concurrent = max(max_concurrent, in_run)
        time.sleep(0.1)
        with lock:
            in_run -= 1
        stats = MagicMock()
        stats.summary.return_value = "ok"
        return "/tmp/result.xlsx", stats

    with patch("automation.worker.run", side_effect=fake_run):
        with patch("automation.worker.send_text"):
            with patch("automation.worker.send_document"):
                worker_mod.add_task(_make_task(profile="DENIS", file_path="/tmp/1.xlsx"))
                worker_mod.add_task(_make_task(profile="DENIS", file_path="/tmp/2.xlsx"))

                deadline = time.time() + 3
                while worker_mod._profile_workers["DENIS"].queue.unfinished_tasks > 0:
                    if time.time() > deadline:
                        break
                    time.sleep(0.02)
                time.sleep(0.15)

    assert max_concurrent == 1


def test_different_profiles_can_run_in_parallel() -> None:
    import automation.worker as worker_mod

    lock = threading.Lock()
    in_run = 0
    max_concurrent = 0

    def fake_run(file_path: str, cfg):  # noqa: ARG001
        nonlocal in_run, max_concurrent
        with lock:
            in_run += 1
            max_concurrent = max(max_concurrent, in_run)
        time.sleep(0.15)
        with lock:
            in_run -= 1
        stats = MagicMock()
        stats.summary.return_value = "ok"
        return "/tmp/result.xlsx", stats

    with patch("automation.worker.run", side_effect=fake_run):
        with patch("automation.worker.send_text"):
            with patch("automation.worker.send_document"):
                worker_mod.add_task(
                    _make_task(profile="DENIS", file_path="/tmp/denis_job.xlsx")
                )
                worker_mod.add_task(
                    _make_task(profile="IVAN", file_path="/tmp/ivan_job.xlsx", user_id=222)
                )

                deadline = time.time() + 3
                while (
                    worker_mod._profile_workers["DENIS"].queue.unfinished_tasks > 0
                    or worker_mod._profile_workers["IVAN"].queue.unfinished_tasks > 0
                ):
                    if time.time() > deadline:
                        break
                    time.sleep(0.02)
                time.sleep(0.05)

    assert max_concurrent >= 2


def test_import_safe_without_workers() -> None:
    with patch.dict("os.environ", {}, clear=True):
        worker_mod = importlib.import_module("automation.worker")
        importlib.reload(worker_mod)
        assert worker_mod._profile_workers == {}
        worker_mod.ensure_worker_started()
