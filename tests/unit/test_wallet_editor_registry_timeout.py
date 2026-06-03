"""Wallet Editor registry timeout/warning from Rules job_params."""
from __future__ import annotations

import io
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from automation.audit import Stats
from automation.runtime import WalletEditorTask
from integrations.wallet_editor_registry import (
    SLOW_APPEND_MESSAGE,
    TIMEOUT_MESSAGE,
    append_run_to_dropbox_registry,
)
from integrations.wallet_editor_registry_async import (
    remove_staged_result,
    stage_registry_result_copy,
)
from integrations.wallet_editor_registry_settings import (
    DEFAULT_REGISTRY_RETRY_INTERVAL_SECONDS,
    DEFAULT_REGISTRY_TIMEOUT_SECONDS,
    DEFAULT_REGISTRY_WARNING_SECONDS,
    RegistrySettings,
    load_registry_settings,
)
MSK = ZoneInfo("Europe/Moscow")
RUN_STARTED = datetime(2026, 6, 3, 9, 0, 0, tzinfo=MSK)
RUN_FINISHED = datetime(2026, 6, 3, 9, 5, 0, tzinfo=MSK)
DROPBOX_PATH = "/Ostin/platform/Tests/wallet_editor.xlsx"


def _make_task(*, run_id: str = "timeout-run", chat_id: int = -900) -> WalletEditorTask:
    return WalletEditorTask(
        file_path="/tmp/wallet_editor/in.xlsx",
        chat_id=chat_id,
        telegram_user_id=1,
        operator_profile="DENIS",
        source_file_name="batch.xlsx",
        login="l",
        password="p",
        auth_state_path="/tmp/auth.json",
        run_id=run_id,
    )


def _write_result(path: Path) -> None:
    pd.DataFrame(
        {
            "Дата отключения": ["03.06.2026 09:00:00"],
            "card": ["4111111111111111"],
            "action": ["remove_partner"],
            "value": ["Ostin"],
            "status": ["OK"],
            "comment": [""],
        }
    ).to_excel(path, index=False)


def _fast_settings(
    *,
    warning: int = 1,
    timeout: int = 3,
    retry: int = 1,
) -> RegistrySettings:
    return RegistrySettings(
        registry_warning_seconds=warning,
        registry_timeout_seconds=timeout,
        registry_retry_interval_seconds=retry,
    )


@pytest.fixture
def registry_store(monkeypatch, tmp_path):
    store: dict[str, bytes] = {}
    revs: dict[str, str] = {"rev": "rev-1"}

    def fake_download(dropbox_path: str, local_path: str) -> tuple[str, str | None]:
        if dropbox_path not in store:
            return "not_found", None
        Path(local_path).write_bytes(store[dropbox_path])
        return "ok", revs.get(dropbox_path, "rev-1")

    def fake_upload(local_path: str, dropbox_path: str, expected_rev: str | None) -> str:
        from integrations import dropbox_watcher

        if expected_rev is not None:
            current = dropbox_watcher.get_dropbox_file_rev(dropbox_path)
            if current != expected_rev:
                return "rev_conflict"
        store[dropbox_path] = Path(local_path).read_bytes()
        revs[dropbox_path] = f"rev-{len(store)}"
        return "uploaded"

    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)

    with patch(
        "integrations.wallet_editor_registry.download_file_with_rev",
        side_effect=fake_download,
    ):
        with patch(
            "integrations.wallet_editor_registry.upload_file_if_rev",
            side_effect=fake_upload,
        ):
            with patch(
                "integrations.dropbox_watcher.get_dropbox_file_rev",
                return_value=revs.get(DROPBOX_PATH, "rev-1"),
            ):
                with patch(
                    "integrations.wallet_editor_registry_lifecycle.now_msk",
                    return_value=datetime(2026, 6, 3, 12, 0, 0, tzinfo=MSK),
                ):
                    yield store, revs, tmp_path


def test_registry_settings_loaded_from_job_params():
    accessor = MagicMock()
    def _param(job_key: str, param_key: str, **kw):
        return {
            "registry_warning_seconds": 45,
            "registry_timeout_seconds": 120,
            "registry_retry_interval_seconds": 5,
        }.get(param_key, kw.get("default"))

    accessor.get_job_param.side_effect = _param

    with patch(
        "integrations.wallet_editor_registry_settings.get_snapshot_v2",
        return_value=MagicMock(),
    ):
        with patch(
            "integrations.wallet_editor_registry_settings.get_indexes_v2",
            return_value=MagicMock(),
        ):
            with patch(
                "integrations.wallet_editor_registry_settings.BaseRulesAccessor",
                return_value=accessor,
            ):
                settings = load_registry_settings()

    assert settings.registry_warning_seconds == 45
    assert settings.registry_timeout_seconds == 120
    assert settings.registry_retry_interval_seconds == 5


def test_registry_settings_defaults_when_missing():
    accessor = MagicMock()
    accessor.get_job_param.return_value = None

    with patch(
        "integrations.wallet_editor_registry_settings.get_snapshot_v2",
        return_value=MagicMock(),
    ):
        with patch(
            "integrations.wallet_editor_registry_settings.get_indexes_v2",
            return_value=MagicMock(),
        ):
            with patch(
                "integrations.wallet_editor_registry_settings.BaseRulesAccessor",
                return_value=accessor,
            ):
                settings = load_registry_settings()

    assert settings.registry_warning_seconds == DEFAULT_REGISTRY_WARNING_SECONDS
    assert settings.registry_timeout_seconds == DEFAULT_REGISTRY_TIMEOUT_SECONDS
    assert settings.registry_retry_interval_seconds == DEFAULT_REGISTRY_RETRY_INTERVAL_SECONDS


def test_registry_invalid_settings_fallback():
    accessor = MagicMock()
    def _param(job_key: str, param_key: str, **kw):
        return {
            "registry_warning_seconds": 200,
            "registry_timeout_seconds": 5,
            "registry_retry_interval_seconds": "bad",
        }.get(param_key, kw.get("default"))

    accessor.get_job_param.side_effect = _param

    with patch(
        "integrations.wallet_editor_registry_settings.get_snapshot_v2",
        return_value=MagicMock(),
    ):
        with patch(
            "integrations.wallet_editor_registry_settings.get_indexes_v2",
            return_value=MagicMock(),
        ):
            with patch(
                "integrations.wallet_editor_registry_settings.BaseRulesAccessor",
                return_value=accessor,
            ):
                settings = load_registry_settings()

    assert settings.registry_timeout_seconds == DEFAULT_REGISTRY_TIMEOUT_SECONDS
    assert settings.registry_warning_seconds < settings.registry_timeout_seconds
    assert settings.registry_retry_interval_seconds == DEFAULT_REGISTRY_RETRY_INTERVAL_SECONDS


def test_registry_warning_before_timeout(registry_store, tmp_path):
    store, revs, root = registry_store
    revs[DROPBOX_PATH] = "rev-block"
    messages: list[str] = []
    call_count = {"n": 0}

    def slow_upload(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            time.sleep(1.5)
        return "rev_conflict"

    result_path = root / "result.xlsx"
    _write_result(result_path)

    with patch(
        "integrations.wallet_editor_registry.upload_file_if_rev",
        side_effect=slow_upload,
    ):
        with patch(
            "integrations.dropbox_watcher.get_dropbox_file_rev",
            return_value="rev-other",
        ):
            with patch(
                "integrations.wallet_editor_registry.send_message_sync",
                side_effect=lambda text, **kw: messages.append(text),
            ):
                append_run_to_dropbox_registry(
                    _make_task(run_id="warn-before-timeout"),
                    str(result_path),
                    Stats(ok=1, fail=0, skip=0),
                    run_started_at=RUN_STARTED,
                    run_finished_at=RUN_FINISHED,
                    settings=_fast_settings(warning=1, timeout=4, retry=1),
                )

    assert any(SLOW_APPEND_MESSAGE in m for m in messages)
    assert any(TIMEOUT_MESSAGE in m for m in messages)


def test_registry_timeout_skips_append(registry_store, tmp_path):
    store, revs, root = registry_store
    before = b""
    store[DROPBOX_PATH] = before
    revs[DROPBOX_PATH] = "rev-stale"

    result_path = root / "result.xlsx"
    _write_result(result_path)

    with patch(
        "integrations.dropbox_watcher.get_dropbox_file_rev",
        return_value="rev-changed",
    ):
        with patch("integrations.wallet_editor_registry.send_message_sync"):
            append_run_to_dropbox_registry(
                _make_task(run_id="timeout-skip"),
                str(result_path),
                Stats(ok=1, fail=0, skip=0),
                run_started_at=RUN_STARTED,
                run_finished_at=RUN_FINISHED,
                settings=_fast_settings(warning=10, timeout=2, retry=1),
            )

    assert store.get(DROPBOX_PATH, b"") == before


def test_registry_retry_interval_used(registry_store, tmp_path):
    store, revs, root = registry_store
    revs[DROPBOX_PATH] = "rev-a"
    attempts: list[float] = []

    def track_upload(*args, **kwargs):
        attempts.append(time.monotonic())
        return "rev_conflict"

    result_path = root / "result.xlsx"
    _write_result(result_path)

    with patch(
        "integrations.wallet_editor_registry.upload_file_if_rev",
        side_effect=track_upload,
    ):
        with patch(
            "integrations.dropbox_watcher.get_dropbox_file_rev",
            return_value="rev-b",
        ):
            with patch("integrations.wallet_editor_registry.send_message_sync"):
                append_run_to_dropbox_registry(
                    _make_task(run_id="retry-interval"),
                    str(result_path),
                    Stats(ok=1, fail=0, skip=0),
                    run_started_at=RUN_STARTED,
                    run_finished_at=RUN_FINISHED,
                    settings=_fast_settings(warning=30, timeout=5, retry=2),
                )

    assert len(attempts) >= 2
    if len(attempts) >= 2:
        gap = attempts[1] - attempts[0]
        assert gap >= 1.5


def test_registry_result_sent_before_slow_registry(tmp_path):
    order: list[str] = []

    def fake_run(*args, **kwargs):
        result = tmp_path / "out.xlsx"
        _write_result(result)
        return str(result), Stats(ok=1, fail=0, skip=0)

    def track_send_text(**kwargs):
        order.append("text")

    def track_send_document(**kwargs):
        order.append("document")

    def track_schedule(*args, **kwargs):
        order.append("schedule")
        time.sleep(0.05)

    import automation.worker as worker_mod

    with worker_mod._registry_lock:
        worker_mod._profile_workers.clear()

    with patch("automation.worker.run", side_effect=fake_run):
        with patch("automation.worker.send_text", side_effect=track_send_text):
            with patch("automation.worker.send_document", side_effect=track_send_document):
                with patch(
                    "automation.worker.schedule_registry_append",
                    side_effect=track_schedule,
                ):
                    with patch("automation.worker.delayed_cleanup"):
                        worker_mod.add_task(_make_task(run_id="order-test"))
                        deadline = time.time() + 3
                        while (
                            worker_mod._profile_workers["DENIS"].queue.unfinished_tasks > 0
                            and time.time() < deadline
                        ):
                            time.sleep(0.02)

    assert order.index("text") < order.index("document") < order.index("schedule")


def test_registry_async_does_not_race_cleanup(tmp_path):
    result = tmp_path / "result.xlsx"
    _write_result(result)

    staged_path, is_copy = stage_registry_result_copy(str(result))
    assert is_copy
    assert Path(staged_path).is_file()

    import os

    os.remove(result)
    assert not result.exists()
    assert Path(staged_path).is_file()

    data = Path(staged_path).read_bytes()
    assert len(data) > 0

    remove_staged_result(staged_path, is_staged_copy=True)
    assert not Path(staged_path).exists()


def test_registry_success_fast_no_warning(registry_store, tmp_path):
    store, revs, root = registry_store
    messages: list[str] = []
    result_path = root / "result.xlsx"
    _write_result(result_path)

    with patch(
        "integrations.wallet_editor_registry.send_message_sync",
        side_effect=lambda text, **kw: messages.append(text),
    ):
        append_run_to_dropbox_registry(
            _make_task(run_id="fast-ok"),
            str(result_path),
            Stats(ok=1, fail=0, skip=0),
            run_started_at=RUN_STARTED,
            run_finished_at=RUN_FINISHED,
            settings=_fast_settings(warning=60, timeout=180, retry=10),
        )

    assert DROPBOX_PATH in store
    assert not any(SLOW_APPEND_MESSAGE in m for m in messages)
    assert not any(TIMEOUT_MESSAGE in m for m in messages)
