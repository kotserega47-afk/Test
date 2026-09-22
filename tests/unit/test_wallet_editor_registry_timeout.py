"""Wallet Editor registry timeout/warning from Rules job_params."""
from __future__ import annotations

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
    _AppendOutcome,
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


def _blocked_pg(*_a, **_k):
    raise RuntimeError("postgres connect blocked in timeout tests")


@pytest.fixture
def isolated_append_env(monkeypatch, tmp_path):
    monkeypatch.delenv("DROPBOX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("DROPBOX_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)
    monkeypatch.setenv("WALLET_EDITOR_MANUAL_READERS_SOURCE", "dropbox")
    monkeypatch.setenv("WALLET_EDITOR_REGISTRY_SOURCE", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://registry-timeout-test.invalid:1/unused")
    monkeypatch.setattr(
        "integrations.wallet_editor_registry_db.connection.connect",
        _blocked_pg,
    )
    monkeypatch.setattr(
        "integrations.dropbox_watcher._get_dbx",
        _blocked_pg,
    )
    yield tmp_path


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
        "integrations.wallet_editor_registry_settings.get_published_state",
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
        "integrations.wallet_editor_registry_settings.get_published_state",
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
        "integrations.wallet_editor_registry_settings.get_published_state",
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


def test_registry_timeout_skips_append(isolated_append_env):
    result_path = isolated_append_env / "result.xlsx"
    _write_result(result_path)
    calls = {"n": 0}

    def transient(*_a, **_k):
        calls["n"] += 1
        return _AppendOutcome.TRANSIENT, None, None

    with patch("integrations.wallet_editor_registry._append_attempt", side_effect=transient):
        with patch("integrations.wallet_editor_registry._send_timeout_warning"):
            with patch("integrations.wallet_editor_registry._send_slow_append_warning"):
                append_run_to_dropbox_registry(
                    _make_task(run_id="timeout-skip"),
                    str(result_path),
                    Stats(ok=1, fail=0, skip=0),
                    run_started_at=RUN_STARTED,
                    run_finished_at=RUN_FINISHED,
                    settings=_fast_settings(warning=10, timeout=2, retry=1),
                )
    assert calls["n"] >= 1


def test_registry_retry_interval_used(isolated_append_env):
    result_path = isolated_append_env / "result.xlsx"
    _write_result(result_path)
    attempts: list[float] = []

    def transient(*_a, **_k):
        attempts.append(time.monotonic())
        return _AppendOutcome.TRANSIENT, None, None

    with patch("integrations.wallet_editor_registry._append_attempt", side_effect=transient):
        with patch("integrations.wallet_editor_registry._send_timeout_warning"):
            with patch("integrations.wallet_editor_registry._send_slow_append_warning"):
                append_run_to_dropbox_registry(
                    _make_task(run_id="retry-interval"),
                    str(result_path),
                    Stats(ok=1, fail=0, skip=0),
                    run_started_at=RUN_STARTED,
                    run_finished_at=RUN_FINISHED,
                    settings=_fast_settings(warning=30, timeout=5, retry=2),
                )
    assert len(attempts) >= 2
    assert attempts[1] - attempts[0] >= 1.5


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
                    "automation.worker.prepare_registry_outbox_and_schedule",
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


def test_schedule_registry_append_forwards_output_file(tmp_path):
    from integrations.wallet_editor_registry_async import schedule_registry_append

    staged_path = tmp_path / "we_registry_result_xyz.xlsx"
    staged_path.write_text("x", encoding="utf-8")
    user_visible = "wallet_editor_result_input_DENIS.xlsx"
    captured: dict[str, object] = {}

    def fake_append(task, result_path, stats, **kwargs):
        captured["result_path"] = result_path
        captured["output_file"] = kwargs.get("output_file")

    task = _make_task(run_id="async-output-file")
    with patch(
        "integrations.wallet_editor_registry.append_run_to_dropbox_registry",
        side_effect=fake_append,
    ):
        schedule_registry_append(
            task,
            str(staged_path),
            Stats(ok=1, fail=0, skip=0),
            run_started_at=RUN_STARTED,
            run_finished_at=RUN_FINISHED,
            is_staged_copy=True,
            output_file=user_visible,
        )
        deadline = time.time() + 2
        while not captured and time.time() < deadline:
            time.sleep(0.02)

    assert captured["result_path"] == str(staged_path)
    assert captured["output_file"] == user_visible
