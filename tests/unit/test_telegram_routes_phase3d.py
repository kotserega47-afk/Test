"""Phase 3D — bakai_rate_current / bakai_rate_alert migration behind TELEGRAM_ROUTES_FROM_RULES_V2."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from core.rules_v2.models import MetaInfo, RulesSnapshotV2, TelegramRoute
from integrations import bakai_monitor_playwright as bakai
from integrations.telegram_routes import (
    ENV_BAKAI_RATE_ALERT_LEGACY,
    ENV_BAKAI_RATE_CURRENT_LEGACY,
    ENV_TELEGRAM_ROUTES_FROM_RULES_V2,
    ROUTE_BAKAI_RATE_ALERT,
    ROUTE_BAKAI_RATE_CURRENT,
    ROUTE_CONVERSION_WALLET_EDITOR,
    ROUTE_PLATFORM_HOURLY_REPORT,
    ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT,
    MIGRATED_RUNTIME_ROUTES,
    compare_telegram_routes_env_vs_rules,
    resolve_route_chat_id,
    send_file_to_route,
    send_message_to_route,
)


def _meta() -> MetaInfo:
    return MetaInfo(
        ruleset_version="test",
        updated_at=datetime(2026, 6, 3, 12, 0, 0),
        updated_by="test",
    )


def _snapshot_bakai(
    current_chat: str,
    alert_chat: str,
    *,
    current_enabled: bool = True,
    alert_enabled: bool = True,
) -> RulesSnapshotV2:
    return RulesSnapshotV2(
        meta=_meta(),
        telegram_routes={
            ROUTE_BAKAI_RATE_CURRENT: TelegramRoute(
                route_key=ROUTE_BAKAI_RATE_CURRENT,
                chat_id=current_chat,
                enabled=current_enabled,
                description="bakai current",
            ),
            ROUTE_BAKAI_RATE_ALERT: TelegramRoute(
                route_key=ROUTE_BAKAI_RATE_ALERT,
                chat_id=alert_chat,
                enabled=alert_enabled,
                description="bakai alert",
            ),
            ROUTE_PLATFORM_HOURLY_REPORT: TelegramRoute(
                route_key=ROUTE_PLATFORM_HOURLY_REPORT,
                chat_id="-hourly",
                enabled=True,
                description="hourly",
            ),
        },
    )


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID_EMERGENCY", raising=False)


def test_bakai_routes_in_migrated_registry() -> None:
    assert ROUTE_BAKAI_RATE_CURRENT in MIGRATED_RUNTIME_ROUTES
    assert ROUTE_BAKAI_RATE_ALERT in MIGRATED_RUNTIME_ROUTES
    assert len(MIGRATED_RUNTIME_ROUTES) == 5


def test_flag_off_current_uses_legacy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BAKAI_RATE_CURRENT_LEGACY, "-100-current")
    res = resolve_route_chat_id(ROUTE_BAKAI_RATE_CURRENT)
    assert res.source == "legacy_env"
    assert res.chat_id == "-100-current"


def test_flag_off_alert_uses_legacy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BAKAI_RATE_ALERT_LEGACY, "-100-alert")
    res = resolve_route_chat_id(ROUTE_BAKAI_RATE_ALERT)
    assert res.source == "legacy_env"
    assert res.chat_id == "-100-alert"


def test_flag_off_missing_rules_sheet_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BAKAI_RATE_CURRENT_LEGACY, "-current")
    monkeypatch.setenv(ENV_BAKAI_RATE_ALERT_LEGACY, "-alert")
    with patch(
        "core.rules_provider.get_snapshot_v2",
        side_effect=RuntimeError("no rules"),
    ):
        cur = resolve_route_chat_id(ROUTE_BAKAI_RATE_CURRENT)
        alt = resolve_route_chat_id(ROUTE_BAKAI_RATE_ALERT)
    assert cur.source == "legacy_env"
    assert alt.source == "legacy_env"


def test_flag_on_current_uses_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = _snapshot_bakai("-rules-current", "-rules-alert")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        res = resolve_route_chat_id(ROUTE_BAKAI_RATE_CURRENT)
    assert res.source == "rules_v2"
    assert res.chat_id == "-rules-current"


def test_flag_on_alert_uses_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = _snapshot_bakai("-rules-current", "-rules-alert")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        res = resolve_route_chat_id(ROUTE_BAKAI_RATE_ALERT)
    assert res.source == "rules_v2"
    assert res.chat_id == "-rules-alert"


def test_flag_on_missing_current_skips_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = RulesSnapshotV2(
        meta=_meta(),
        telegram_routes={
            ROUTE_BAKAI_RATE_ALERT: TelegramRoute(
                route_key=ROUTE_BAKAI_RATE_ALERT,
                chat_id="-1",
                enabled=True,
                description="alert only",
            )
        },
    )
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        with patch("integrations.telegram_bot.send_message_sync") as send_sync:
            ok = send_message_to_route(ROUTE_BAKAI_RATE_CURRENT, "text")
    assert ok is False
    send_sync.assert_not_called()


def test_flag_on_missing_alert_skips_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = RulesSnapshotV2(
        meta=_meta(),
        telegram_routes={
            ROUTE_BAKAI_RATE_CURRENT: TelegramRoute(
                route_key=ROUTE_BAKAI_RATE_CURRENT,
                chat_id="-1",
                enabled=True,
                description="current only",
            )
        },
    )
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        with patch("integrations.telegram_bot.send_message_sync") as send_sync:
            ok = send_message_to_route(ROUTE_BAKAI_RATE_ALERT, "alert")
    assert ok is False
    send_sync.assert_not_called()


def test_flag_on_disabled_current_skips_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = _snapshot_bakai("-1", "-2", current_enabled=False)
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        with patch("integrations.telegram_bot.send_message_sync") as send_sync:
            ok = send_message_to_route(ROUTE_BAKAI_RATE_CURRENT, "text")
    assert ok is False
    send_sync.assert_not_called()


def test_flag_on_disabled_alert_skips_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = _snapshot_bakai("-1", "-2", alert_enabled=False)
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        with patch("integrations.telegram_bot.send_message_sync") as send_sync:
            ok = send_message_to_route(ROUTE_BAKAI_RATE_ALERT, "text")
    assert ok is False
    send_sync.assert_not_called()


def test_flag_on_emergency_not_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMERGENCY", "-911")
    snap = RulesSnapshotV2(meta=_meta(), telegram_routes={})
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        res = resolve_route_chat_id(ROUTE_BAKAI_RATE_ALERT)
    assert res.chat_id is None
    assert res.source == "missing_route"


def test_flag_on_bakai_diff_in_migrated_route_differences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv(ENV_BAKAI_RATE_CURRENT_LEGACY, "-env-current")
    monkeypatch.setenv(ENV_BAKAI_RATE_ALERT_LEGACY, "-env-alert")
    snap = _snapshot_bakai("-rules-current", "-rules-alert")
    result = compare_telegram_routes_env_vs_rules(snap, sheet_present=True)
    assert result.mismatched == 0
    assert result.migrated_route_differences >= 2


def test_hourly_still_migrated_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = _snapshot_bakai("-c", "-a")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        res = resolve_route_chat_id(ROUTE_PLATFORM_HOURLY_REPORT)
    assert res.source == "rules_v2"


def test_unmigrated_analiz_legacy_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_ANALIZ", "-analiz")
    snap = _snapshot_bakai("-c", "-a")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        res = resolve_route_chat_id("analiz_main_pipeline")
    assert res.source == "legacy_env"


def test_bakai_send_current_flag_off_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_BAKAI_RATE_CURRENT_LEGACY, "-legacy-current")
    with patch(
        "integrations.bakai_monitor_playwright.send_message_to_route",
    ) as send_route:
        with patch(
            "integrations.bakai_monitor_playwright.send_message_sync",
        ) as send_sync:
            bakai._send_to_current_route("current msg")
    send_sync.assert_called_once_with("current msg", chat_id="-legacy-current")
    send_route.assert_not_called()


def test_bakai_send_alert_flag_on_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = _snapshot_bakai("-c", "-rules-alert")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        with patch(
            "integrations.bakai_monitor_playwright.send_message_to_route",
        ) as send_route:
            with patch(
                "integrations.bakai_monitor_playwright.send_message_sync",
            ) as send_sync:
                bakai._send_to_alert_route("alert msg")
    send_route.assert_called_once_with(ROUTE_BAKAI_RATE_ALERT, "alert msg")
    send_sync.assert_not_called()


def test_bakai_send_file_flag_on_current_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = _snapshot_bakai("-c", "-a")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        with patch(
            "integrations.bakai_monitor_playwright.send_file_to_route",
        ) as send_file:
            bakai._send_file_to_current_route("/tmp/x.png", "cap")
    send_file.assert_called_once_with(ROUTE_BAKAI_RATE_CURRENT, "/tmp/x.png", "cap")


def test_conversion_route_still_in_registry() -> None:
    assert ROUTE_CONVERSION_WALLET_EDITOR in MIGRATED_RUNTIME_ROUTES
