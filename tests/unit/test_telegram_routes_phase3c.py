"""Phase 3C — conversion_wallet_editor migration behind TELEGRAM_ROUTES_FROM_RULES_V2."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pandas as pd
import pytest

from core.rules_v2.models import MetaInfo, RulesSnapshotV2, TelegramRoute
from integrations.conversion_wallet_editor_bridge import (
    ENV_CHAT_ID,
    ENV_LOGIN,
    ENV_PASSWORD,
    maybe_enqueue_wallet_editor_from_problem_cards,
    resolve_conversion_we_config,
)
from integrations.telegram_routes import (
    ENV_CONVERSION_WALLET_EDITOR_LEGACY,
    ENV_TELEGRAM_ROUTES_FROM_RULES_V2,
    ROUTE_CONVERSION_WALLET_EDITOR,
    ROUTE_PLATFORM_HOURLY_REPORT,
    ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT,
    MIGRATED_RUNTIME_ROUTES,
    compare_telegram_routes_env_vs_rules,
    resolve_route_chat_id,
    send_message_to_route,
)


def _meta() -> MetaInfo:
    return MetaInfo(
        ruleset_version="test",
        updated_at=datetime(2026, 6, 3, 12, 0, 0),
        updated_by="test",
    )


def _snapshot_with_conv_route(
    chat_id: str,
    *,
    enabled: bool = True,
) -> RulesSnapshotV2:
    return RulesSnapshotV2(
        meta=_meta(),
        telegram_routes={
            ROUTE_CONVERSION_WALLET_EDITOR: TelegramRoute(
                route_key=ROUTE_CONVERSION_WALLET_EDITOR,
                chat_id=chat_id,
                enabled=enabled,
                description="conversion WE",
            ),
            ROUTE_PLATFORM_HOURLY_REPORT: TelegramRoute(
                route_key=ROUTE_PLATFORM_HOURLY_REPORT,
                chat_id="-hourly",
                enabled=True,
                description="hourly",
            ),
            ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT: TelegramRoute(
                route_key=ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT,
                chat_id="-wallet",
                enabled=True,
                description="wallet",
            ),
        },
    )


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID_EMERGENCY", raising=False)


def test_conversion_route_in_migrated_registry() -> None:
    assert ROUTE_CONVERSION_WALLET_EDITOR in MIGRATED_RUNTIME_ROUTES
    assert len(MIGRATED_RUNTIME_ROUTES) == 3


def test_flag_off_uses_legacy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_CONVERSION_WALLET_EDITOR_LEGACY, "-100123")
    res = resolve_route_chat_id(ROUTE_CONVERSION_WALLET_EDITOR)
    assert res.source == "legacy_env"
    assert res.chat_id == "-100123"


def test_flag_off_missing_rules_sheet_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_CONVERSION_WALLET_EDITOR_LEGACY, "-100123")
    with patch(
        "core.rules_provider.get_snapshot_v2",
        side_effect=RuntimeError("no rules"),
    ):
        res = resolve_route_chat_id(ROUTE_CONVERSION_WALLET_EDITOR)
    assert res.source == "legacy_env"
    assert res.chat_id == "-100123"


def test_flag_off_config_ignores_missing_rules_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_CHAT_ID, "-100555")
    monkeypatch.setenv(ENV_LOGIN, "login")
    monkeypatch.setenv(ENV_PASSWORD, "pass")
    with patch(
        "core.rules_provider.get_snapshot_v2",
        side_effect=RuntimeError("no rules"),
    ):
        config, err = resolve_conversion_we_config()
    assert err is None
    assert config is not None
    assert config.chat_id == -100555


def test_flag_on_uses_rules_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv(ENV_CONVERSION_WALLET_EDITOR_LEGACY, "-env-unused")
    snap = _snapshot_with_conv_route("-100777")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        res = resolve_route_chat_id(ROUTE_CONVERSION_WALLET_EDITOR)
    assert res.source == "rules_v2"
    assert res.chat_id == "-100777"


def test_flag_on_config_from_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv(ENV_LOGIN, "login")
    monkeypatch.setenv(ENV_PASSWORD, "pass")
    snap = _snapshot_with_conv_route("-100888")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        config, err = resolve_conversion_we_config()
    assert err is None
    assert config is not None
    assert config.chat_id == -100888


def test_flag_on_missing_route_skips_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = RulesSnapshotV2(meta=_meta(), telegram_routes={})
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        with patch("integrations.telegram_bot.send_message_sync") as send_sync:
            ok = send_message_to_route(ROUTE_CONVERSION_WALLET_EDITOR, "text")
    assert ok is False
    send_sync.assert_not_called()


def test_flag_on_disabled_route_skips_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = _snapshot_with_conv_route("-1", enabled=False)
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        with patch("integrations.telegram_bot.send_message_sync") as send_sync:
            ok = send_message_to_route(ROUTE_CONVERSION_WALLET_EDITOR, "text")
    assert ok is False
    send_sync.assert_not_called()


def test_flag_on_emergency_not_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMERGENCY", "-911")
    snap = RulesSnapshotV2(meta=_meta(), telegram_routes={})
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        res = resolve_route_chat_id(ROUTE_CONVERSION_WALLET_EDITOR)
    assert res.chat_id is None
    assert res.source == "missing_route"


def test_flag_on_conv_env_rules_diff_is_migrated_difference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv(ENV_CONVERSION_WALLET_EDITOR_LEGACY, "-100-env")
    snap = _snapshot_with_conv_route("-100-rules")
    result = compare_telegram_routes_env_vs_rules(snap, sheet_present=True)
    assert result.mismatched == 0
    assert result.migrated_route_differences >= 1


def test_flag_on_hourly_and_wallet_still_rules_v2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = _snapshot_with_conv_route("-conv")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        hourly = resolve_route_chat_id(ROUTE_PLATFORM_HOURLY_REPORT)
        wallet = resolve_route_chat_id(ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT)
    assert hourly.source == "rules_v2"
    assert wallet.source == "rules_v2"


def test_unmigrated_analiz_stays_legacy_when_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_ANALIZ", "-analiz")
    snap = _snapshot_with_conv_route("-conv")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        res = resolve_route_chat_id("analiz_main_pipeline")
    assert res.source == "legacy_env"
    assert res.chat_id == "-analiz"


def test_bridge_flag_on_uses_send_message_to_route(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv(ENV_LOGIN, "login")
    monkeypatch.setenv(ENV_PASSWORD, "pass")
    monkeypatch.setattr(
        "integrations.conversion_wallet_editor_bridge.WALLET_EDITOR_INPUT_DIR",
        tmp_path,
    )
    snap = _snapshot_with_conv_route("-100321")
    problem = pd.DataFrame([{"card": "123", "original_partner": "Ostin"}])

    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        with patch(
            "integrations.conversion_wallet_editor_bridge.add_task",
            return_value=1,
        ):
            with patch(
                "integrations.conversion_wallet_editor_bridge.send_message_to_route",
            ) as send_route:
                with patch(
                    "integrations.conversion_wallet_editor_bridge.send_message_sync",
                ) as send_sync:
                    maybe_enqueue_wallet_editor_from_problem_cards(problem)

    send_route.assert_called()
    send_sync.assert_not_called()
    assert send_route.call_args.args[0] == ROUTE_CONVERSION_WALLET_EDITOR


def test_bridge_flag_on_missing_route_no_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv(ENV_LOGIN, "login")
    monkeypatch.setenv(ENV_PASSWORD, "pass")
    snap = RulesSnapshotV2(meta=_meta(), telegram_routes={})
    problem = pd.DataFrame([{"card": "123", "original_partner": "Ostin"}])

    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        with patch(
            "integrations.conversion_wallet_editor_bridge.add_task",
        ) as add_task:
            maybe_enqueue_wallet_editor_from_problem_cards(problem)

    add_task.assert_not_called()
