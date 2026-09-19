from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import job_runner
from core.access_rules import AccessRules
from core.job_health import _reset_job_health_for_tests
from core.job_progress import _reset_job_progress_for_tests, record_progress
from core.lock_status import KNOWN_JOB_TYPES
from modules.antares.assembly import assemble_antares
from modules.antares.handlers import (
    ANTARES_STATUS_JOB_TYPES,
    cmd_help,
    cmd_rules_validate,
    cmd_status,
    cmd_whoami,
)


@pytest.fixture(scope="module")
def assembled():
    rules = AccessRules("")
    logger = logging.getLogger("antares-assembly-cmds")
    assemble_antares(rules=rules, logger=logger)
    return rules, logger


def _update() -> MagicMock:
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    update.effective_chat = SimpleNamespace(id=11, type="private")
    update.effective_user = SimpleNamespace(id=22, username="alice")
    return update


def test_antares_help_and_status_and_whoami(assembled):
    rules, _logger = assembled
    update = _update()

    async def _help() -> None:
        with patch("modules.antares.handlers.guard_or_deny", new_callable=AsyncMock, return_value=True):
            await cmd_help(update, MagicMock())

    asyncio.run(_help())
    help_text = update.message.reply_text.await_args.args[0]
    assert "/run_wallet" in help_text
    assert "/operator_wallets_ready" in help_text
    assert "/run_raccoon" not in help_text
    assert "/run_hourly_raccoon" not in help_text
    assert "/run_script_hello" not in help_text

    update.message.reply_text.reset_mock()

    async def _status_off() -> None:
        with patch("modules.antares.handlers.guard_or_deny", new_callable=AsyncMock, return_value=True):
            with patch(
                "modules.antares.handlers.get_status",
                return_value={"raccoon_wallet": {"job_id": "x", "started_ts": 1, "runtime_sec": 1}},
            ):
                await cmd_status(update, MagicMock())

    asyncio.run(_status_off())
    assert update.message.reply_text.await_args.args[0] == "🟢 Сейчас ничего не выполняется."

    update.message.reply_text.reset_mock()
    snap = SimpleNamespace(access_map={("private", 22): 2})

    async def _whoami() -> None:
        with patch("modules.antares.handlers.guard_or_deny", new_callable=AsyncMock, return_value=True):
            with patch.object(rules, "get_snapshot", return_value=snap):
                await cmd_whoami(update, MagicMock())

    asyncio.run(_whoami())
    who = update.message.reply_text.await_args.args[0]
    assert "chat_id: 11" in who
    assert "user_id: 22" in who
    assert "level: 2" in who

    update.message.reply_text.reset_mock()

    async def _whoami_deny() -> None:
        with patch("modules.antares.handlers.guard_or_deny", new_callable=AsyncMock, return_value=False):
            with patch.object(rules, "get_snapshot") as get_snap:
                await cmd_whoami(update, MagicMock())
                get_snap.assert_not_called()

    asyncio.run(_whoami_deny())
    update.message.reply_text.assert_not_awaited()

    assert "raccoon_wallet" not in ANTARES_STATUS_JOB_TYPES
    assert "script_job:operator_wallets_ready" in ANTARES_STATUS_JOB_TYPES
    assert "wallet_editor_registry_replay" in ANTARES_STATUS_JOB_TYPES


def test_antares_status_observation_uses_real_job_health(assembled, monkeypatch):
    monkeypatch.setenv("OBSERVATION_ENABLED", "1")
    monkeypatch.setenv("JOB_HEALTH_GUARD_ENABLED", "1")
    monkeypatch.setenv("JOB_HEALTH_RECOVERY_MODE", "observe")
    monkeypatch.setenv("JOB_HEALTH_WARNING_SECONDS", "600")
    monkeypatch.setenv("JOB_HEALTH_TIMEOUT_SECONDS", "1800")
    monkeypatch.setenv("JOB_HEALTH_PROGRESS_TIMEOUT_SECONDS", "600")
    _reset_job_progress_for_tests()
    _reset_job_health_for_tests()
    job_runner._RUNNING.clear()

    rules, _logger = assembled
    update = _update()
    lock_calls: list[tuple[str, ...]] = []

    def _locks(job_types=KNOWN_JOB_TYPES):
        requested = tuple(job_types)
        lock_calls.append(requested)
        assert set(requested) == set(ANTARES_STATUS_JOB_TYPES)
        assert "raccoon_wallet" not in requested
        assert "script_job:hello_world" not in requested
        return {jt: {"lock_pid": "none", "lock_age_sec": "none"} for jt in requested}

    started = time.time() - 20
    job_runner._RUNNING["wallet_editor_registry_replay"] = ("jid-replay", started, {"kind": "tg"})
    job_runner._RUNNING["script_job:operator_wallets_ready"] = ("jid-op", started, {"kind": "tg"})
    job_runner._RUNNING["raccoon_wallet"] = ("jid-r", started, {"kind": "tg"})
    record_progress("wallet_editor_registry_replay", "replay_batch")
    record_progress("script_job:operator_wallets_ready", "export")

    async def _running() -> None:
        with patch("modules.antares.handlers.guard_or_deny", new_callable=AsyncMock, return_value=True):
            with patch("core.job_health.get_lock_status_for_job_types", side_effect=_locks):
                with patch("modules.antares.handlers.get_lock_status_for_job_types", side_effect=_locks):
                    with patch(
                        "core.scheduler_health.get_scheduler_health_snapshot",
                        return_value={},
                    ):
                        await cmd_status(update, MagicMock())

    asyncio.run(_running())
    running_text = update.message.reply_text.await_args.args[0]
    assert "📊 Status" in running_text
    assert "wallet_editor_registry_replay: state=running_ok" in running_text
    assert "script_job:operator_wallets_ready: state=running_ok" in running_text
    assert "raccoon_wallet" not in running_text
    assert "hello_world" not in running_text
    assert "scheduler:" in running_text
    assert lock_calls
    assert all(set(call) == set(ANTARES_STATUS_JOB_TYPES) for call in lock_calls)

    job_runner._RUNNING.clear()
    _reset_job_progress_for_tests()
    _reset_job_health_for_tests()
    lock_calls.clear()
    update.message.reply_text.reset_mock()

    async def _idle() -> None:
        with patch("modules.antares.handlers.guard_or_deny", new_callable=AsyncMock, return_value=True):
            with patch("core.job_health.get_lock_status_for_job_types", side_effect=_locks):
                with patch("modules.antares.handlers.get_lock_status_for_job_types", side_effect=_locks):
                    with patch(
                        "core.scheduler_health.get_scheduler_health_snapshot",
                        return_value={},
                    ):
                        await cmd_status(update, MagicMock())

    asyncio.run(_idle())
    idle_text = update.message.reply_text.await_args.args[0]
    assert "wallet_editor_registry_replay: state=idle" in idle_text
    assert "script_job:operator_wallets_ready: state=idle" in idle_text
    assert "raccoon_wallet" not in idle_text
    assert "🟢 idle" in idle_text
    job_runner._RUNNING.clear()
    _reset_job_progress_for_tests()
    _reset_job_health_for_tests()


def test_transferred_rules_validate_deny_parts_audit_and_error(assembled):
    _rules, _logger = assembled
    update = _update()

    async def _deny() -> None:
        with patch("modules.antares.handlers.guard_or_deny", new_callable=AsyncMock, return_value=False):
            with patch(
                "core.rules_v2.ops_rules_validate_summary.build_rules_validate_telegram_chunks_with_payload"
            ) as build:
                with patch(
                    "core.rules_v2.rules_validate_audit.try_append_manual_validate_audit_from_payload"
                ) as audit:
                    await cmd_rules_validate(update, MagicMock())
                    build.assert_not_called()
                    audit.assert_not_called()

    asyncio.run(_deny())
    update.message.reply_text.assert_not_awaited()

    payload = object()
    update.message.reply_text.reset_mock()

    async def _parts() -> None:
        with patch("modules.antares.handlers.guard_or_deny", new_callable=AsyncMock, return_value=True):
            with patch(
                "core.rules_v2.ops_rules_validate_summary.build_rules_validate_telegram_chunks_with_payload",
                return_value=(["chunk-a", "chunk-b"], payload),
            ) as build:
                with patch(
                    "core.rules_v2.rules_validate_audit.try_append_manual_validate_audit_from_payload"
                ) as audit:
                    await cmd_rules_validate(update, MagicMock())
                    build.assert_called_once_with()
                    audit.assert_called_once_with(payload)

    asyncio.run(_parts())
    texts = [call.args[0] for call in update.message.reply_text.await_args_list]
    assert texts == ["chunk-a", "(part 2/2)\nchunk-b"]

    update.message.reply_text.reset_mock()

    async def _error() -> None:
        with patch("modules.antares.handlers.guard_or_deny", new_callable=AsyncMock, return_value=True):
            with patch(
                "core.rules_v2.ops_rules_validate_summary.build_rules_validate_telegram_chunks_with_payload",
                side_effect=RuntimeError("validate-boom"),
            ):
                with patch(
                    "core.rules_v2.rules_validate_audit.try_append_manual_validate_audit_from_payload"
                ) as audit:
                    await cmd_rules_validate(update, MagicMock())
                    audit.assert_not_called()

    asyncio.run(_error())
    err = update.message.reply_text.await_args.args[0]
    assert err.startswith("❌ /rules_validate failed: RuntimeError: validate-boom")
