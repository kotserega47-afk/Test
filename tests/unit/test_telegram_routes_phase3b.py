"""Phase 3B — platform_wallet_download_report migration behind TELEGRAM_ROUTES_FROM_RULES_V2."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from core.rules_v2.models import MetaInfo, RulesSnapshotV2, TelegramRoute
from integrations import downloader_wallets
from integrations.downloader_wallets import WalletJobParams
from integrations.telegram_routes import (
    ENV_PLATFORM_WALLET_LEGACY,
    ENV_TELEGRAM_ROUTES_FROM_RULES_V2,
    ROUTE_PLATFORM_HOURLY_REPORT,
    ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT,
    MIGRATED_RUNTIME_ROUTES,
    compare_telegram_routes_env_vs_rules,
    resolve_route_chat_id,
    send_message_to_route,
)
from reporters.wallet_reporter import RenderedReport


def _meta() -> MetaInfo:
    return MetaInfo(
        ruleset_version="test",
        updated_at=datetime(2026, 6, 3, 12, 0, 0),
        updated_by="test",
    )


def _snapshot_with_wallet_route(
    chat_id: str,
    *,
    enabled: bool = True,
    hourly_chat_id: str | None = None,
) -> RulesSnapshotV2:
    routes: dict[str, TelegramRoute] = {
        ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT: TelegramRoute(
            route_key=ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT,
            chat_id=chat_id,
            enabled=enabled,
            description="wallet download",
        )
    }
    if hourly_chat_id is not None:
        routes[ROUTE_PLATFORM_HOURLY_REPORT] = TelegramRoute(
            route_key=ROUTE_PLATFORM_HOURLY_REPORT,
            chat_id=hourly_chat_id,
            enabled=True,
            description="hourly",
        )
    return RulesSnapshotV2(meta=_meta(), telegram_routes=routes)


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID_EMERGENCY", raising=False)


def test_wallet_route_in_migrated_registry() -> None:
    assert ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT in MIGRATED_RUNTIME_ROUTES
    assert ROUTE_PLATFORM_HOURLY_REPORT in MIGRATED_RUNTIME_ROUTES


def test_flag_off_wallet_uses_legacy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PLATFORM_WALLET_LEGACY, "-wallet-legacy")
    res = resolve_route_chat_id(ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT)
    assert res.source == "legacy_env"
    assert res.chat_id == "-wallet-legacy"


def test_flag_off_missing_rules_sheet_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_PLATFORM_WALLET_LEGACY, "-wallet-legacy")
    with patch(
        "core.rules_provider.get_snapshot_v2",
        side_effect=RuntimeError("no rules"),
    ):
        res = resolve_route_chat_id(ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT)
    assert res.source == "legacy_env"
    assert res.chat_id == "-wallet-legacy"


def test_flag_on_wallet_uses_rules_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv(ENV_PLATFORM_WALLET_LEGACY, "-env-unused")
    snap = _snapshot_with_wallet_route("-rules-wallet")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        res = resolve_route_chat_id(ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT)
    assert res.source == "rules_v2"
    assert res.chat_id == "-rules-wallet"


def test_flag_on_missing_wallet_route_skips_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = RulesSnapshotV2(meta=_meta(), telegram_routes={})
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        with patch("integrations.telegram_bot.send_message_sync") as send_sync:
            ok = send_message_to_route(ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT, "text")
    assert ok is False
    send_sync.assert_not_called()


def test_flag_on_disabled_wallet_route_skips_send(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = _snapshot_with_wallet_route("-1", enabled=False)
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        with patch("integrations.telegram_bot.send_message_sync") as send_sync:
            ok = send_message_to_route(ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT, "text")
    assert ok is False
    send_sync.assert_not_called()


def test_flag_on_emergency_not_used_for_wallet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_EMERGENCY", "-911")
    snap = RulesSnapshotV2(meta=_meta(), telegram_routes={})
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        res = resolve_route_chat_id(ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT)
    assert res.chat_id is None
    assert res.source == "missing_route"


def test_flag_on_wallet_env_rules_diff_is_migrated_route_difference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv(ENV_PLATFORM_WALLET_LEGACY, "-env-wallet")
    snap = _snapshot_with_wallet_route("-rules-wallet")
    result = compare_telegram_routes_env_vs_rules(snap, sheet_present=True)
    assert result.mismatched == 0
    assert result.migrated_route_differences >= 1


def test_flag_on_hourly_still_rules_v2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = _snapshot_with_wallet_route("-w", hourly_chat_id="-hourly")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        res = resolve_route_chat_id(ROUTE_PLATFORM_HOURLY_REPORT)
    assert res.source == "rules_v2"
    assert res.chat_id == "-hourly"


def test_unmigrated_analiz_stays_legacy_when_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_ANALIZ", "-analiz")
    snap = _snapshot_with_wallet_route("-wallet")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        res = resolve_route_chat_id("analiz_main_pipeline")
    assert res.source == "legacy_env"
    assert res.chat_id == "-analiz"


def test_raccoon_hourly_not_using_send_message_to_route() -> None:
    import inspect
    import analyzers.raccoon_hourly_report as rh

    assert "send_message_to_route" not in inspect.getsource(rh)


def test_conversion_not_using_wallet_route() -> None:
    import inspect
    import analyzers.conversion as conv

    assert ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT not in inspect.getsource(conv)


def _patch_wallet_cycle_send_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTARES_LOGIN", "u")
    monkeypatch.setenv("ANTARES_PASSWORD", "p")


def _patch_wallet_params() -> object:
    return patch.object(
        downloader_wallets,
        "_load_wallet_params",
        return_value=WalletJobParams(),
    )


def test_wallet_cycle_flag_off_uses_send_text(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_wallet_cycle_send_deps(monkeypatch)
    monkeypatch.setenv(ENV_PLATFORM_WALLET_LEGACY, "-legacy-wallet")
    rendered = RenderedReport(main_text="main report", alerts_text="alert bit")
    with _patch_wallet_params():
        with patch.object(downloader_wallets, "_download_wallet_files", return_value=("a", "b")):
            with patch.object(downloader_wallets, "_calc_wallet_fingerprint", return_value="fp-new"):
                with patch.object(downloader_wallets, "state_get", return_value=None):
                    with patch.object(downloader_wallets, "state_update"):
                        with patch.object(downloader_wallets, "build_wallet_stats_dto", return_value=MagicMock()):
                            with patch.object(downloader_wallets, "render_wallet", return_value=rendered):
                                with patch.object(downloader_wallets, "send_text") as send_text:
                                    downloader_wallets.run_wallet_cycle()
    assert send_text.call_count == 2
    send_text.assert_any_call(text="main report", chat_id="-legacy-wallet")
    send_text.assert_any_call(text="alert bit", chat_id="-legacy-wallet")


def test_wallet_cycle_flag_off_without_rules_route_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_wallet_cycle_send_deps(monkeypatch)
    monkeypatch.setenv(ENV_PLATFORM_WALLET_LEGACY, "-legacy-wallet")
    rendered = RenderedReport(main_text="main only", alerts_text="")
    with _patch_wallet_params():
        with patch.object(downloader_wallets, "_download_wallet_files", return_value=("a", "b")):
            with patch.object(downloader_wallets, "_calc_wallet_fingerprint", return_value="fp-new"):
                with patch.object(downloader_wallets, "state_get", return_value=None):
                    with patch.object(downloader_wallets, "state_update"):
                        with patch.object(downloader_wallets, "build_wallet_stats_dto", return_value=MagicMock()):
                            with patch.object(downloader_wallets, "render_wallet", return_value=rendered):
                                with patch(
                                    "core.rules_provider.get_snapshot_v2",
                                    side_effect=RuntimeError("no sheet"),
                                ):
                                    with patch.object(downloader_wallets, "send_text") as send_text:
                                        downloader_wallets.run_wallet_cycle()
    send_text.assert_called_once_with(text="main only", chat_id="-legacy-wallet")


def test_wallet_cycle_flag_on_uses_send_message_to_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_wallet_cycle_send_deps(monkeypatch)
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = _snapshot_with_wallet_route("-rules-wallet")
    rendered = RenderedReport(main_text="main report", alerts_text="")
    with _patch_wallet_params():
        with patch.object(downloader_wallets, "_download_wallet_files", return_value=("a", "b")):
            with patch.object(downloader_wallets, "_calc_wallet_fingerprint", return_value="fp-new"):
                with patch.object(downloader_wallets, "state_get", return_value=None):
                    with patch.object(downloader_wallets, "state_update"):
                        with patch.object(downloader_wallets, "build_wallet_stats_dto", return_value=MagicMock()):
                            with patch.object(downloader_wallets, "render_wallet", return_value=rendered):
                                with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
                                    with patch.object(
                                        downloader_wallets,
                                        "send_message_to_route",
                                    ) as send_route:
                                        downloader_wallets.run_wallet_cycle()
    send_route.assert_called_once_with(
        ROUTE_PLATFORM_WALLET_DOWNLOAD_REPORT,
        "main report",
    )


def test_wallet_cycle_flag_on_missing_route_no_send(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_wallet_cycle_send_deps(monkeypatch)
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    snap = RulesSnapshotV2(meta=_meta(), telegram_routes={})
    rendered = RenderedReport(main_text="main report", alerts_text="")
    with _patch_wallet_params():
        with patch.object(downloader_wallets, "_download_wallet_files", return_value=("a", "b")):
            with patch.object(downloader_wallets, "_calc_wallet_fingerprint", return_value="fp-new"):
                with patch.object(downloader_wallets, "state_get", return_value=None):
                    with patch.object(downloader_wallets, "state_update"):
                        with patch.object(downloader_wallets, "build_wallet_stats_dto", return_value=MagicMock()):
                            with patch.object(downloader_wallets, "render_wallet", return_value=rendered):
                                with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
                                    with patch.object(
                                        downloader_wallets,
                                        "send_message_to_route",
                                    ) as send_route:
                                        with patch.object(downloader_wallets, "send_text") as send_text:
                                            downloader_wallets.run_wallet_cycle()
    send_route.assert_called_once()
    send_text.assert_not_called()
