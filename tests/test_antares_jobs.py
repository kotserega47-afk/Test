"""Risks of moving Antares JOB_REGISTRY bindings out of tg_commands."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call

import pytest

from integrations.telegram_routes import ROUTE_PLATFORM_HOURLY_REPORT
from modules.antares.jobs import (
    ANTARES_JOB_KEYS,
    register_jobs,
    run_download_job,
    run_hourly_job,
)
from tests.unit.isolated_child_env import isolated_child_env, missing_dependency_hint

_FORBIDDEN_ON_IMPORT = (
    "integrations.telegram_bot",
    "core.rules_provider",
    "playwright",
    "playwright.sync_api",
    "psycopg2",
    "psycopg",
    "asyncpg",
    "integrations.dropbox_watcher",
    "dotenv",
    "integrations.tg_commands",
)


def test_import_jobs_module_does_not_touch_external_boundaries() -> None:
    script = (
        "import json, sys\n"
        "import modules.antares.jobs as jobs\n"
        "print(json.dumps({\n"
        "  'file': jobs.__file__,\n"
        "  'loaded': sorted(m for m in sys.modules if m in "
        + repr(list(_FORBIDDEN_ON_IMPORT))
        + "),\n"
        "}))\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        env = isolated_child_env(Path(tmp))
        proc = subprocess.run(
            [sys.executable, "-c", script],
            cwd=tmp,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    if proc.returncode != 0 and "ModuleNotFoundError" in (proc.stderr or ""):
        raise AssertionError(missing_dependency_hint(proc.stderr))
    assert proc.returncode == 0, proc.stderr + proc.stdout
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["file"].replace("\\", "/").endswith("modules/antares/jobs.py")
    assert payload["loaded"] == []


def test_register_jobs_adds_six_keys_keeps_foreign_and_does_not_run(monkeypatch) -> None:
    ran: list[str] = []

    def _mark(name: str):
        def _fn(*args, **kwargs):  # noqa: ANN002, ANN003
            ran.append(name)
            raise AssertionError(f"{name} executed")

        return _fn

    wallet = _mark("wallet")
    rate = _mark("rate")
    refresh = _mark("refresh")
    replay = _mark("replay")
    monkeypatch.setattr("integrations.downloader_wallets.run_wallet_cycle", wallet)
    monkeypatch.setattr("integrations.bakai_monitor_playwright.run_rate_monitor_safe", rate)
    monkeypatch.setattr(
        "integrations.wallet_editor_registry_refresh.run_wallet_editor_registry_refresh_job",
        refresh,
    )
    monkeypatch.setattr(
        "integrations.wallet_editor_registry.run_registry_outbox_replay_job",
        replay,
    )
    foreign = object()
    expected = {
        "keep": foreign,
        "wallet": wallet,
        "hourly": run_hourly_job,
        "rate": rate,
        "download": run_download_job,
        "wallet_editor_registry_refresh": refresh,
        "wallet_editor_registry_replay": replay,
    }
    stub_executors = (wallet, rate, refresh, replay)
    assert len({id(fn) for fn in stub_executors}) == 4
    registry = {"keep": foreign}
    register_jobs(registry)
    assert set(registry) == ANTARES_JOB_KEYS | {"keep"}
    for key, fn in expected.items():
        assert registry[key] is fn
    assert ran == []
    register_jobs(registry)
    assert set(registry) == ANTARES_JOB_KEYS | {"keep"}
    for key, fn in expected.items():
        assert registry[key] is fn
    assert ran == []


def test_hourly_skips_send_and_fingerprint_when_no_changes(monkeypatch) -> None:
    monkeypatch.setattr(
        "analyzers.hourly_report.run_hourly_report",
        lambda job="hourly": SimpleNamespace(
            skipped_no_changes=True, text="ignored", fingerprint="fp"
        ),
    )
    send = MagicMock(return_value=True)
    state = MagicMock()
    monkeypatch.setattr("integrations.telegram_routes.send_message_to_route", send)
    monkeypatch.setattr("core.state_store.state_update", state)
    run_hourly_job()
    send.assert_not_called()
    state.assert_not_called()


def test_hourly_skips_send_when_text_empty(monkeypatch) -> None:
    monkeypatch.setattr(
        "analyzers.hourly_report.run_hourly_report",
        lambda job="hourly": SimpleNamespace(
            skipped_no_changes=False, text="", fingerprint="fp"
        ),
    )
    send = MagicMock(return_value=True)
    state = MagicMock()
    monkeypatch.setattr("integrations.telegram_routes.send_message_to_route", send)
    monkeypatch.setattr("core.state_store.state_update", state)
    run_hourly_job()
    send.assert_not_called()
    state.assert_not_called()


def test_hourly_does_not_commit_fingerprint_when_send_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        "analyzers.hourly_report.run_hourly_report",
        lambda job="hourly": SimpleNamespace(
            skipped_no_changes=False, text="body", fingerprint="fp"
        ),
    )
    send = MagicMock(return_value=False)
    state = MagicMock()
    monkeypatch.setattr("integrations.telegram_routes.send_message_to_route", send)
    monkeypatch.setattr("core.state_store.state_update", state)
    run_hourly_job()
    send.assert_called_once()
    state.assert_not_called()


def test_hourly_does_not_commit_fingerprint_when_send_raises(monkeypatch) -> None:
    monkeypatch.setattr(
        "analyzers.hourly_report.run_hourly_report",
        lambda job="hourly": SimpleNamespace(
            skipped_no_changes=False, text="body", fingerprint="fp"
        ),
    )
    send = MagicMock(side_effect=RuntimeError("send failed"))
    state = MagicMock()
    monkeypatch.setattr("integrations.telegram_routes.send_message_to_route", send)
    monkeypatch.setattr("core.state_store.state_update", state)
    with pytest.raises(RuntimeError, match="send failed"):
        run_hourly_job()
    send.assert_called_once()
    state.assert_not_called()


def test_hourly_commits_fingerprint_only_after_successful_send(monkeypatch) -> None:
    monkeypatch.setattr(
        "analyzers.hourly_report.run_hourly_report",
        lambda job="hourly": SimpleNamespace(
            skipped_no_changes=False, text="body", fingerprint="fp-ok"
        ),
    )
    parent = Mock()
    send = Mock(return_value=True)
    state = Mock()
    parent.attach_mock(send, "send")
    parent.attach_mock(state, "state")
    monkeypatch.setattr("integrations.telegram_routes.send_message_to_route", send)
    monkeypatch.setattr("core.state_store.state_update", state)
    monkeypatch.setattr("modules.antares.jobs.time.time", lambda: 1_700_000_000)
    run_hourly_job()
    send.assert_called_once_with(ROUTE_PLATFORM_HOURLY_REPORT, "body")
    state.assert_called_once_with(
        "hourly",
        {"last_fingerprint": "fp-ok", "last_sent_ts": 1_700_000_000},
    )
    assert parent.mock_calls == [
        call.send(ROUTE_PLATFORM_HOURLY_REPORT, "body"),
        call.state(
            "hourly",
            {"last_fingerprint": "fp-ok", "last_sent_ts": 1_700_000_000},
        ),
    ]
