"""Phase 3A — platform_hourly_report migration behind TELEGRAM_ROUTES_FROM_RULES_V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from unittest.mock import patch

import pytest

from core.rules_v2.models import MetaInfo, RulesSnapshotV2, TelegramRoute
from integrations import tg_commands
from integrations.telegram_routes import (
    ENV_PLATFORM_HOURLY_LEGACY,
    ENV_TELEGRAM_ROUTES_FROM_RULES_V2,
    ROUTE_PLATFORM_HOURLY_REPORT,
    MIGRATED_RUNTIME_ROUTES,
    RouteResolution,
    resolve_route_chat_id,
    routes_from_rules_v2_enabled,
    send_message_to_route,
)
from integrations.telegram_bot import get_telegram_sender_health_snapshot


@dataclass
class _FakeHourlyResult:
    text: str = "hourly report body"
    skipped_no_changes: bool = False
    fingerprint: str | None = "fp-1"


def _meta() -> MetaInfo:
    return MetaInfo(
        ruleset_version="test",
        updated_at=datetime(2026, 6, 3, 12, 0, 0),
        updated_by="test",
    )


def _snapshot_with_hourly_route(
    chat_id: str,
    *,
    enabled: bool = True,
) -> RulesSnapshotV2:
    return RulesSnapshotV2(
        meta=_meta(),
        telegram_routes={
            ROUTE_PLATFORM_HOURLY_REPORT: TelegramRoute(
                route_key=ROUTE_PLATFORM_HOURLY_REPORT,
                chat_id=chat_id,
                enabled=enabled,
                description="platform hourly",
            )
        },
    )


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID_EMERGENCY", raising=False)


def test_flag_off_by_default() -> None:
    assert routes_from_rules_v2_enabled() is False


def test_flag_off_uses_legacy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PLATFORM_HOURLY_LEGACY, "-hourly-legacy")
    res = resolve_route_chat_id(ROUTE_PLATFORM_HOURLY_REPORT)
    assert res.source == "legacy_env"
    assert res.chat_id == "-hourly-legacy"


def test_flag_off_missing_routes_sheet_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PLATFORM_HOURLY_LEGACY, "-hourly-legacy")
    with patch(
        "core.rules_provider.get_snapshot_v2",
        side_effect=RuntimeError("no rules"),
    ):
        res = resolve_route_chat_id(ROUTE_PLATFORM_HOURLY_REPORT)
    assert res.source == "legacy_env"
    assert res.chat_id == "-hourly-legacy"


def test_flag_off_hourly_job_uses_legacy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PLATFORM_HOURLY_LEGACY, "-legacy-hourly")
    with patch("integrations.tg_commands.run_hourly_report", return_value=_FakeHourlyResult()):
        with patch("integrations.tg_commands.state_update"):
            with patch("integrations.telegram_bot.send_message_sync") as send_sync:
                tg_commands.run_hourly_job()
    send_sync.assert_called_once_with("hourly report body", chat_id="-legacy-hourly")


def test_flag_off_missing_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_PLATFORM_HOURLY_LEGACY, raising=False)
    with patch("integrations.tg_commands.run_hourly_report", return_value=_FakeHourlyResult()):
        with patch("integrations.tg_commands.state_update"):
            with pytest.raises(RuntimeError, match=ENV_PLATFORM_HOURLY_LEGACY):
                tg_commands.run_hourly_job()


def test_flag_on_uses_rules_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv(ENV_PLATFORM_HOURLY_LEGACY, "-should-not-use")
    snap = _snapshot_with_hourly_route("-rules-hourly")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        res = resolve_route_chat_id(ROUTE_PLATFORM_HOURLY_REPORT)
    assert res.source == "rules_v2"
    assert res.chat_id == "-rules-hourly"


def test_flag_on_disabled_route_skips_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = _snapshot_with_hourly_route("-1", enabled=False)
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        with patch("integrations.telegram_bot.send_message_sync") as send_sync:
            ok = send_message_to_route(ROUTE_PLATFORM_HOURLY_REPORT, "text")
    assert ok is False
    send_sync.assert_not_called()


def test_flag_on_missing_route_skips_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = RulesSnapshotV2(meta=_meta(), telegram_routes={})
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        with patch("integrations.telegram_bot.send_message_sync") as send_sync:
            ok = send_message_to_route(ROUTE_PLATFORM_HOURLY_REPORT, "text")
    assert ok is False
    send_sync.assert_not_called()


def test_flag_on_hourly_job_skips_when_route_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = RulesSnapshotV2(meta=_meta(), telegram_routes={})
    with patch("integrations.tg_commands.run_hourly_report", return_value=_FakeHourlyResult()):
        with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
            with patch("integrations.telegram_bot.send_message_sync") as send_sync:
                with patch("integrations.tg_commands.state_update") as state_up:
                    tg_commands.run_hourly_job()
    send_sync.assert_not_called()
    state_up.assert_not_called()


def test_emergency_not_used_for_hourly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMERGENCY", "-911")
    snap = RulesSnapshotV2(meta=_meta(), telegram_routes={})
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        res = resolve_route_chat_id(ROUTE_PLATFORM_HOURLY_REPORT)
    assert res.chat_id is None
    assert res.source == "missing_route"


def test_platform_hourly_still_migrated() -> None:
    assert ROUTE_PLATFORM_HOURLY_REPORT in MIGRATED_RUNTIME_ROUTES


def test_unmigrated_route_stays_legacy_when_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_ANALIZ", "-analiz")
    snap = _snapshot_with_hourly_route("-rules")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        res = resolve_route_chat_id("analiz_main_pipeline")
    assert res.source == "legacy_env"
    assert res.chat_id == "-analiz"


def test_raccoon_hourly_still_uses_send_message_sync_directly() -> None:
    import inspect
    import analyzers.raccoon_hourly_report as rh

    source = inspect.getsource(rh)
    assert "send_message_to_route" not in source
    assert "send_message_sync" in source


def test_telegram_sender_health_snapshot_unchanged() -> None:
    snap = get_telegram_sender_health_snapshot()
    assert "status" in snap
    assert "queue_depth" in snap


def test_flag_on_sender_called_after_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = _snapshot_with_hourly_route("-rules-ok")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        with patch("integrations.telegram_bot.send_message_sync") as send_sync:
            assert send_message_to_route(ROUTE_PLATFORM_HOURLY_REPORT, "ok") is True
    send_sync.assert_called_once_with("ok", chat_id="-rules-ok")


def test_resolve_logs_rules_v2_source(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = _snapshot_with_hourly_route("-1")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        with caplog.at_level(logging.INFO, logger="integrations.telegram_routes"):
            send_message_to_route(ROUTE_PLATFORM_HOURLY_REPORT, "x")
    assert any("source=rules_v2" in r.message for r in caplog.records)
