"""Risks of moving Antares Telegram dispatch commands out of tg_commands."""

from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import CommandHandler

from core.access_guard import deny_message
from core.access_rules import AccessRules, CommandRule
from core.job_runner import Actor
from core.rules_v2.indexes import build_indexes
from core.rules_v2.models import (
    AccessRule,
    CommandDef,
    CommandPolicy,
    MetaInfo,
    RoleDef,
    RulesSnapshotV2,
)
from core.tg_command_dispatch import build_access_context, guard_or_deny
from modules.antares import handlers
from tests.unit.isolated_child_env import isolated_child_env, missing_dependency_hint

ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = ROOT / "tests" / "fixtures" / "behavior_baseline"

_ANTARES_CMDS = (
    (handlers.cmd_run_wallet, "run_wallet", "wallet", "jid-wallet"),
    (handlers.cmd_run_hourly, "run_hourly", "hourly", "jid-hourly"),
    (handlers.cmd_run_download, "run_download", "download", "jid-download"),
    (handlers.cmd_run_rate, "run_rate", "rate", "jid-rate"),
    (
        handlers.cmd_operator_wallets_ready,
        "operator_wallets_ready",
        "script_job:operator_wallets_ready",
        "jid-operator-wallets-ready",
    ),
    (
        handlers.cmd_wallet_editor_refresh,
        "wallet_editor_refresh",
        "wallet_editor_registry_refresh",
        "jid-wallet-editor-refresh",
    ),
)

_ANTARES_CALLBACK_IDS = {
    id(handlers.cmd_run_wallet),
    id(handlers.cmd_run_hourly),
    id(handlers.cmd_run_download),
    id(handlers.cmd_run_rate),
    id(handlers.cmd_operator_wallets_ready),
    id(handlers.cmd_wallet_editor_refresh),
    id(handlers.cmd_registry_health),
    id(handlers.cmd_registry_replay),
    id(handlers.cmd_registry_export),
    id(handlers.cmd_auto_enable_plan),
    id(handlers.cmd_auto_enable_run),
}

_REGISTRY_CMDS = (
    (handlers.cmd_registry_health, "registry_health"),
    (handlers.cmd_registry_replay, "registry_replay"),
)

_FORBIDDEN_ON_HANDLERS_IMPORT = (
    "integrations.tg_commands",
    "integrations.telegram_bot",
    "integrations.wallet_editor_registry",
    "integrations.wallet_editor_registry_db.registry_export_builder",
    "integrations.wallet_editor_auto_enable",
    "playwright",
    "playwright.sync_api",
    "psycopg2",
    "psycopg",
    "asyncpg",
    "dotenv",
)


class _EqBox:
    def __eq__(self, other: object) -> bool:
        return isinstance(other, _EqBox)

    def __hash__(self) -> int:
        return 1


class _MutableRules:
    def __init__(self) -> None:
        self.commands_map: dict = {}
        self.access_map: dict = {}

    def get_snapshot(self, force_sync: bool = False) -> SimpleNamespace:
        return SimpleNamespace(commands_map=self.commands_map, access_map=self.access_map)


def _update(chat_id: int = 11, user_id: int = 22, chat_type: str = "private") -> MagicMock:
    update = MagicMock()
    update.effective_chat.type = chat_type
    update.effective_chat.id = chat_id
    update.effective_user.id = user_id
    replies: list[str] = []

    async def _reply(text: str, *args, **kwargs):  # noqa: ANN002, ANN003
        replies.append(text)

    update.message.reply_text = AsyncMock(side_effect=_reply)
    update._replies = replies
    return update


def _allow(rules: _MutableRules, command: str, *, chat_id: int = 11, user_id: int = 22) -> None:
    rules.commands_map[command] = CommandRule(
        required_level=1,
        allow_private=True,
        allow_groups=True,
        enabled=True,
    )
    rules.access_map[("private", user_id)] = 1
    rules.access_map[(chat_id, user_id)] = 1


def _run_wallet_snapshot_v2(*, allow: bool, version: str, updated_at: datetime) -> RulesSnapshotV2:
    roles = {"level_1": RoleDef(role_key="level_1", role_level=1, display_name="L1")}
    commands = {}
    policies = {}
    access = [
        AccessRule(chat_id="private", user_id="22", role_key="level_1", enabled=True),
    ]
    if allow:
        commands["run_wallet"] = CommandDef(
            command_key="run_wallet",
            command_text="run_wallet",
            job_key=None,
            display_name="run_wallet",
            enabled=True,
        )
        policies["run_wallet"] = CommandPolicy(
            command_key="run_wallet",
            min_role_key="level_1",
            allow_private=True,
            allow_groups=True,
            enabled=True,
        )
    return RulesSnapshotV2(
        meta=MetaInfo(
            ruleset_version=version,
            updated_at=updated_at,
            updated_by="test",
        ),
        roles=roles,
        commands=commands,
        command_policies=policies,
        access_rules=access,
    )


def _published_stub(snap: RulesSnapshotV2) -> SimpleNamespace:
    return SimpleNamespace(
        snapshot=snap,
        indexes=build_indexes(snap),
        generation=1,
        stat_key=(1.0, 1),
    )


@pytest.fixture
def unbound(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(handlers, "_rules", None)
    monkeypatch.setattr(handlers, "_logger", None)
    return handlers


def test_import_handlers_does_not_load_mixed_tg_commands() -> None:
    script = (
        "import json, sys\n"
        "import modules.antares.handlers as h\n"
        "print(json.dumps({\n"
        "  'file': h.__file__,\n"
        "  'loaded': sorted(m for m in sys.modules if m in "
        + repr(list(_FORBIDDEN_ON_HANDLERS_IMPORT))
        + "),\n"
        "  'rules': h._rules is None,\n"
        "  'logger': h._logger is None,\n"
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
    assert payload["file"].replace("\\", "/").endswith("modules/antares/handlers.py")
    assert payload["loaded"] == []
    assert payload["rules"] is True
    assert payload["logger"] is True


def test_core_dispatch_module_does_not_import_antares() -> None:
    tree = ast.parse((ROOT / "core" / "tg_command_dispatch.py").read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert all(not name.startswith("modules.antares") for name in imported)
    src = (ROOT / "modules" / "antares" / "handlers.py").read_text(encoding="utf-8")
    assert "integrations.tg_commands" not in src
    assert "AccessRules(" not in src
    assert "RULES_XLSX" not in src


def test_expected_tg_commands_json_unchanged() -> None:
    expected = json.loads((_FIXTURE / "expected_tg_commands.json").read_text(encoding="utf-8"))
    assert expected["commands"][:9] == [
        "start",
        "help",
        "status",
        "whoami",
        "reload_rules",
        "run_wallet",
        "run_hourly",
        "run_download",
        "run_rate",
    ]
    assert expected["commands"][12] == "operator_wallets_ready"
    assert expected["commands"][16] == "wallet_editor_refresh"
    assert expected["commands"][17] == "registry_health"
    assert expected["commands"][18] == "registry_replay"
    assert expected.get("also_registers_document_handler") is True


def test_bind_same_object_ok_different_object_rejected(unbound) -> None:
    rules_a = _EqBox()
    rules_b = _EqBox()
    log_a = _EqBox()
    log_b = _EqBox()
    assert rules_a == rules_b
    assert log_a == log_b
    assert rules_a is not rules_b

    unbound.bind_rules(rules_a)
    unbound.bind_rules(rules_a)
    with pytest.raises(unbound.HandlerBindError):
        unbound.bind_rules(rules_b)
    assert unbound._rules is rules_a

    unbound.bind_logger(log_a)
    unbound.bind_logger(log_a)
    with pytest.raises(unbound.HandlerBindError):
        unbound.bind_logger(log_b)
    assert unbound._logger is log_a


@pytest.mark.parametrize("callback,command,job_type,_jid", _ANTARES_CMDS)
def test_unbound_and_partial_bind_do_not_dispatch(unbound, callback, command, job_type, _jid) -> None:
    update = _update()

    async def _run() -> None:
        with patch("core.tg_command_dispatch.dispatch_job_async", new_callable=AsyncMock) as dispatch:
            with pytest.raises(unbound.HandlerNotBoundError):
                await callback(update, MagicMock())
            unbound.bind_rules(object())
            with pytest.raises(unbound.HandlerNotBoundError):
                await callback(update, MagicMock())
            unbound._rules = None
            unbound.bind_logger(MagicMock())
            with pytest.raises(unbound.HandlerNotBoundError):
                await callback(update, MagicMock())
            dispatch.assert_not_called()

    asyncio.run(_run())
    assert update._replies == []


@pytest.mark.parametrize("callback,command,job_type,job_id", _ANTARES_CMDS)
def test_deny_does_not_dispatch(unbound, callback, command, job_type, job_id) -> None:
    rules = _MutableRules()
    logger = MagicMock()
    unbound.bind_rules(rules)
    unbound.bind_logger(logger)
    update = _update()

    async def _run() -> None:
        with patch("core.tg_command_dispatch.dispatch_job_async", new_callable=AsyncMock) as dispatch:
            await callback(update, MagicMock())
            dispatch.assert_not_called()

    asyncio.run(_run())
    assert update._replies == [deny_message("unknown_command", {})]
    assert all("Запускаю" not in text for text in update._replies)
    logger.exception.assert_not_called()


@pytest.mark.parametrize("callback,command,job_type,job_id", _ANTARES_CMDS)
def test_allow_dispatches_named_job_and_actor(unbound, callback, command, job_type, job_id) -> None:
    rules = _MutableRules()
    _allow(rules, command)
    logger = MagicMock()
    unbound.bind_rules(rules)
    unbound.bind_logger(logger)
    update = _update()
    seen: list[str] = []

    async def _dispatch(jt: str, actor: Actor) -> str:
        seen.append(jt)
        assert update._replies == [f"🚀 Запускаю: {jt}"]
        assert jt == job_type
        assert isinstance(actor, Actor)
        assert actor.kind == "tg"
        assert actor.chat_id == 11
        assert actor.user_id == 22
        return job_id

    async def _run() -> None:
        from core.access_guard import check_access as real_check

        with patch("core.tg_command_dispatch.dispatch_job_async", side_effect=_dispatch) as dispatch:
            with patch("core.tg_command_dispatch.check_access", wraps=real_check) as check:
                await callback(update, MagicMock())
                check.assert_called_once()
                assert check.call_args.args[2] == command
                dispatch.assert_awaited_once()

    asyncio.run(_run())
    assert seen == [job_type]
    assert update._replies == [
        f"🚀 Запускаю: {job_type}",
        f"✅ Принято: {job_type}\njob_id={job_id}",
    ]


def test_reload_replaces_snapshot_without_rebind(unbound) -> None:
    deny_v2 = _run_wallet_snapshot_v2(
        allow=False,
        version="deny",
        updated_at=datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc),
    )
    allow_v2 = _run_wallet_snapshot_v2(
        allow=True,
        version="allow",
        updated_at=datetime(2026, 9, 17, 13, 0, tzinfo=timezone.utc),
    )
    source = {"current": deny_v2}

    def _load_state(*, force_sync: bool = False) -> SimpleNamespace:
        return _published_stub(source["current"])

    rules = AccessRules()
    logger = MagicMock()
    unbound.bind_rules(rules)
    unbound.bind_logger(logger)
    update = _update()

    async def _run() -> None:
        with patch("core.access_rules.get_published_state", side_effect=_load_state):
            with patch("core.access_rules.with_provider_lock", side_effect=lambda fn: fn(_load_state())):
                with patch("core.tg_command_dispatch.dispatch_job_async", new_callable=AsyncMock) as dispatch:
                    await handlers.cmd_run_wallet(update, MagicMock())
                    dispatch.assert_not_called()
                    snap_deny = rules.get_snapshot()
                    assert "run_wallet" not in snap_deny.commands_map

                    rules.invalidate()
                    source["current"] = allow_v2
                    dispatch.return_value = "jid-after-reload"
                    await handlers.cmd_run_wallet(update, MagicMock())
                    dispatch.assert_awaited_once()
                    assert dispatch.await_args.args[0] == "wallet"

                    snap_allow = rules.get_snapshot()
                    assert snap_allow is not snap_deny
                    assert snap_allow.commands_map is not snap_deny.commands_map
                    assert snap_allow.access_map is not snap_deny.access_map
                    assert "run_wallet" in snap_allow.commands_map
                    assert unbound._rules is rules
                    assert snap_allow is rules.get_snapshot()

    asyncio.run(_run())
    assert any(text.startswith("🚀 Запускаю: wallet") for text in update._replies)
    assert handlers._rules is rules


def test_dispatch_error_logs_and_sends_traceback_tail(unbound) -> None:
    rules = _MutableRules()
    _allow(rules, "run_hourly")
    logger = MagicMock()
    unbound.bind_rules(rules)
    unbound.bind_logger(logger)
    update = _update()

    async def _boom(job_type: str, actor: Actor) -> str:
        raise RuntimeError("hourly-dispatch-failed")

    async def _run() -> None:
        with patch("core.tg_command_dispatch.dispatch_job_async", side_effect=_boom):
            await handlers.cmd_run_hourly(update, MagicMock())

    asyncio.run(_run())
    logger.exception.assert_called_once()
    assert logger.exception.call_args.args[0] == "❌ TG job error: %s"
    assert logger.exception.call_args.args[1] == "hourly"
    assert update._replies[0] == "🚀 Запускаю: hourly"
    assert update._replies[1] == "❌ Ошибка при выполнении.\nХвост трейса:"
    assert "hourly-dispatch-failed" in update._replies[2]
    assert "RuntimeError" in update._replies[2]


def test_core_helpers_match_mixed_texts() -> None:
    update = _update()
    ctx = build_access_context(update)
    assert ctx.chat_type == "private"
    assert ctx.chat_id == 11
    assert ctx.user_id == 22

    rules = _MutableRules()

    async def _deny() -> None:
        ok = await guard_or_deny(update, "run_rate", rules)
        assert ok is False

    asyncio.run(_deny())
    assert update._replies == [deny_message("unknown_command", {})]


def test_mixed_reexport_and_get_handlers_identity() -> None:
    from integrations import tg_commands

    assert tg_commands.cmd_run_wallet is handlers.cmd_run_wallet
    assert tg_commands.cmd_run_hourly is handlers.cmd_run_hourly
    assert tg_commands.cmd_run_download is handlers.cmd_run_download
    assert tg_commands.cmd_run_rate is handlers.cmd_run_rate
    assert tg_commands.cmd_operator_wallets_ready is handlers.cmd_operator_wallets_ready
    assert tg_commands.cmd_wallet_editor_refresh is handlers.cmd_wallet_editor_refresh
    assert tg_commands.cmd_registry_health is handlers.cmd_registry_health
    assert tg_commands.cmd_registry_replay is handlers.cmd_registry_replay
    assert tg_commands.cmd_registry_export is handlers.cmd_registry_export
    assert tg_commands.cmd_auto_enable_plan is handlers.cmd_auto_enable_plan
    assert tg_commands.cmd_auto_enable_run is handlers.cmd_auto_enable_run
    assert tg_commands.cmd_whoami is handlers.cmd_whoami
    assert tg_commands.cmd_reload_rules is handlers.cmd_reload_rules
    assert tg_commands.cmd_rules_validate is handlers.cmd_rules_validate
    assert tg_commands.cmd_start is not handlers.cmd_start
    assert tg_commands.cmd_help is not handlers.cmd_help
    assert tg_commands.cmd_status is not handlers.cmd_status
    assert handlers._rules is tg_commands.RULES
    assert handlers._logger is tg_commands.log

    assembled: dict[str, object] = {}
    for handler in tg_commands.get_handlers():
        if isinstance(handler, CommandHandler):
            for name in handler.commands:
                assembled[name] = handler.callback
    assert assembled["run_wallet"] is handlers.cmd_run_wallet
    assert assembled["run_hourly"] is handlers.cmd_run_hourly
    assert assembled["run_download"] is handlers.cmd_run_download
    assert assembled["run_rate"] is handlers.cmd_run_rate
    assert assembled["operator_wallets_ready"] is handlers.cmd_operator_wallets_ready
    assert assembled["wallet_editor_refresh"] is handlers.cmd_wallet_editor_refresh
    assert assembled["registry_health"] is handlers.cmd_registry_health
    assert assembled["registry_replay"] is handlers.cmd_registry_replay
    assert assembled["registry_export"] is handlers.cmd_registry_export
    assert assembled["auto_enable_plan"] is handlers.cmd_auto_enable_plan
    assert assembled["auto_enable_run"] is handlers.cmd_auto_enable_run
    assert assembled["whoami"] is handlers.cmd_whoami
    assert assembled["reload_rules"] is handlers.cmd_reload_rules
    assert assembled["rules_validate"] is handlers.cmd_rules_validate
    assert assembled["start"] is tg_commands.cmd_start
    assert assembled["start"] is not handlers.cmd_start
    assert assembled["run_raccoon"] is tg_commands.cmd_run_raccoon
    assert assembled["run_raccoon"] is not handlers.cmd_run_hourly
    assert assembled["run_hourly_raccoon"] is tg_commands.cmd_run_hourly_raccoon
    assert assembled["registry_export"] is not handlers.cmd_registry_replay
    assert len(_ANTARES_CALLBACK_IDS) == 11


@pytest.mark.parametrize("callback,command", _REGISTRY_CMDS)
def test_registry_cmds_unbound_do_not_touch_registry(unbound, callback, command) -> None:
    update = _update()

    async def _run() -> None:
        with patch("integrations.wallet_editor_registry.build_registry_health_report") as health:
            with patch("integrations.wallet_editor_registry.format_registry_health_report") as fmt:
                with patch("integrations.wallet_editor_registry.replay_pending_outbox_records") as replay:
                    with patch("core.tg_command_dispatch.dispatch_job_async", new_callable=AsyncMock) as dispatch:
                        with pytest.raises(unbound.HandlerNotBoundError):
                            await callback(update, MagicMock())
                        unbound.bind_rules(object())
                        with pytest.raises(unbound.HandlerNotBoundError):
                            await callback(update, MagicMock())
                        unbound._rules = None
                        unbound.bind_logger(MagicMock())
                        with pytest.raises(unbound.HandlerNotBoundError):
                            await callback(update, MagicMock())
                        health.assert_not_called()
                        fmt.assert_not_called()
                        replay.assert_not_called()
                        dispatch.assert_not_called()

    asyncio.run(_run())
    assert update._replies == []


@pytest.mark.parametrize("callback,command", _REGISTRY_CMDS)
def test_registry_cmds_deny_skips_registry(unbound, callback, command) -> None:
    rules = _MutableRules()
    logger = MagicMock()
    unbound.bind_rules(rules)
    unbound.bind_logger(logger)
    update = _update()

    async def _run() -> None:
        with patch("integrations.wallet_editor_registry.build_registry_health_report") as health:
            with patch("integrations.wallet_editor_registry.format_registry_health_report") as fmt:
                with patch("integrations.wallet_editor_registry.replay_pending_outbox_records") as replay:
                    with patch("core.tg_command_dispatch.dispatch_job_async", new_callable=AsyncMock) as dispatch:
                        await callback(update, MagicMock())
                        health.assert_not_called()
                        fmt.assert_not_called()
                        replay.assert_not_called()
                        dispatch.assert_not_called()

    asyncio.run(_run())
    assert update._replies == [deny_message("unknown_command", {})]
    logger.exception.assert_not_called()


def test_registry_health_allow_passes_builder_to_formatter(unbound) -> None:
    rules = _MutableRules()
    _allow(rules, "registry_health")
    logger = MagicMock()
    unbound.bind_rules(rules)
    unbound.bind_logger(logger)
    update = _update()
    report = {"ok": True, "tag": "health-report"}

    async def _run() -> None:
        from core.access_guard import check_access as real_check

        with patch("integrations.wallet_editor_registry.build_registry_health_report", return_value=report) as builder:
            with patch(
                "integrations.wallet_editor_registry.format_registry_health_report",
                return_value="formatted-health",
            ) as formatter:
                with patch("core.tg_command_dispatch.check_access", wraps=real_check) as check:
                    with patch("core.tg_command_dispatch.dispatch_job_async", new_callable=AsyncMock) as dispatch:
                        await handlers.cmd_registry_health(update, MagicMock())
                        check.assert_called_once()
                        assert check.call_args.args[2] == "registry_health"
                        builder.assert_called_once_with()
                        formatter.assert_called_once_with(report)
                        dispatch.assert_not_called()

    asyncio.run(_run())
    assert update._replies == ["formatted-health"]


def test_registry_health_formatter_error_uses_same_handler(unbound) -> None:
    rules = _MutableRules()
    _allow(rules, "registry_health")
    logger = MagicMock()
    unbound.bind_rules(rules)
    unbound.bind_logger(logger)
    update = _update()

    async def _run() -> None:
        with patch("integrations.wallet_editor_registry.build_registry_health_report", return_value={"x": 1}):
            with patch(
                "integrations.wallet_editor_registry.format_registry_health_report",
                side_effect=RuntimeError("format-boom"),
            ):
                await handlers.cmd_registry_health(update, MagicMock())

    asyncio.run(_run())
    logger.exception.assert_called_once()
    assert logger.exception.call_args.args[0] == "cmd_registry_health failed"
    assert update._replies == ["❌ /registry_health failed: RuntimeError: format-boom"]


def test_registry_replay_order_and_summary_text(unbound) -> None:
    rules = _MutableRules()
    _allow(rules, "registry_replay")
    logger = MagicMock()
    unbound.bind_rules(rules)
    unbound.bind_logger(logger)
    update = _update()
    result = SimpleNamespace(attempted=4, synced=2, failed=1, skipped=1, errors=("e1", "e2"))
    seen: list[str] = []

    def _replay() -> SimpleNamespace:
        seen.append("replay")
        assert update._replies == ["🔄 Replaying pending/failed registry outbox..."]
        return result

    async def _run() -> None:
        from core.access_guard import check_access as real_check

        with patch("integrations.wallet_editor_registry.replay_pending_outbox_records", side_effect=_replay) as replay:
            with patch("core.tg_command_dispatch.check_access", wraps=real_check) as check:
                with patch("core.tg_command_dispatch.dispatch_job_async", new_callable=AsyncMock) as dispatch:
                    await handlers.cmd_registry_replay(update, MagicMock())
                    check.assert_called_once()
                    assert check.call_args.args[2] == "registry_replay"
                    replay.assert_called_once_with()
                    dispatch.assert_not_called()

    asyncio.run(_run())
    assert seen == ["replay"]
    assert update._replies == [
        "🔄 Replaying pending/failed registry outbox...",
        "Registry outbox replay\nattempted: 4\nsynced: 2\nfailed: 1\nskipped: 1\n\nerrors:\n- e1\n- e2",
    ]


def test_registry_replay_empty_errors_omits_section(unbound) -> None:
    rules = _MutableRules()
    _allow(rules, "registry_replay")
    logger = MagicMock()
    unbound.bind_rules(rules)
    unbound.bind_logger(logger)
    update = _update()
    result = SimpleNamespace(attempted=1, synced=1, failed=0, skipped=0, errors=())

    async def _run() -> None:
        with patch("integrations.wallet_editor_registry.replay_pending_outbox_records", return_value=result):
            await handlers.cmd_registry_replay(update, MagicMock())

    asyncio.run(_run())
    assert update._replies[1] == "Registry outbox replay\nattempted: 1\nsynced: 1\nfailed: 0\nskipped: 0"
    assert "errors:" not in update._replies[1]


def test_registry_replay_truncates_errors_to_ten(unbound) -> None:
    rules = _MutableRules()
    _allow(rules, "registry_replay")
    logger = MagicMock()
    unbound.bind_rules(rules)
    unbound.bind_logger(logger)
    update = _update()
    errors = tuple(f"err-{i}" for i in range(12))
    result = SimpleNamespace(attempted=12, synced=0, failed=12, skipped=0, errors=errors)

    async def _run() -> None:
        with patch("integrations.wallet_editor_registry.replay_pending_outbox_records", return_value=result):
            await handlers.cmd_registry_replay(update, MagicMock())

    asyncio.run(_run())
    body = update._replies[1]
    assert "- err-0" in body
    assert "- err-9" in body
    assert "- err-10" not in body
    assert body.count("- err-") == 10


def test_registry_replay_exception_keeps_initial_reply(unbound) -> None:
    rules = _MutableRules()
    _allow(rules, "registry_replay")
    logger = MagicMock()
    unbound.bind_rules(rules)
    unbound.bind_logger(logger)
    update = _update()

    async def _run() -> None:
        with patch(
            "integrations.wallet_editor_registry.replay_pending_outbox_records",
            side_effect=RuntimeError("replay-boom"),
        ):
            await handlers.cmd_registry_replay(update, MagicMock())

    asyncio.run(_run())
    logger.exception.assert_called_once()
    assert logger.exception.call_args.args[0] == "cmd_registry_replay failed"
    assert update._replies == [
        "🔄 Replaying pending/failed registry outbox...",
        "❌ /registry_replay failed: RuntimeError: replay-boom",
    ]
