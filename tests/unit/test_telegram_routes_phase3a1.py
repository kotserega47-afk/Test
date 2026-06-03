"""Phase 3A.1 — shadow/status metrics split for runtime-migrated routes."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest

from core.rules_v2.models import MetaInfo, RulesSnapshotV2, TelegramRoute
from integrations.telegram_routes import (
    ENV_PLATFORM_HOURLY_LEGACY,
    ENV_TELEGRAM_ROUTES_FROM_RULES_V2,
    ROUTE_PLATFORM_HOURLY_REPORT,
    compare_telegram_routes_env_vs_rules,
    format_telegram_routes_status_lines,
    get_telegram_routes_status_dict,
    resolve_route_chat_id,
    run_telegram_routes_shadow_compare,
)


def _meta() -> MetaInfo:
    return MetaInfo(
        ruleset_version="test",
        updated_at=datetime(2026, 6, 3, 12, 0, 0),
        updated_by="test",
    )


def _snap_hourly(chat_id: str) -> RulesSnapshotV2:
    return RulesSnapshotV2(
        meta=_meta(),
        telegram_routes={
            ROUTE_PLATFORM_HOURLY_REPORT: TelegramRoute(
                route_key=ROUTE_PLATFORM_HOURLY_REPORT,
                chat_id=chat_id,
                enabled=True,
                description="hourly",
            )
        },
    )


def _snap_analiz(chat_id: str) -> RulesSnapshotV2:
    return RulesSnapshotV2(
        meta=_meta(),
        telegram_routes={
            "analiz_main_pipeline": TelegramRoute(
                route_key="analiz_main_pipeline",
                chat_id=chat_id,
                enabled=True,
                description="analiz",
            )
        },
    )


@pytest.fixture(autouse=True)
def _clear_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, raising=False)


def test_flag_off_hourly_env_rules_diff_counts_shadow_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_PLATFORM_HOURLY_LEGACY, "-env-hourly")
    snap = _snap_hourly("-rules-hourly")
    result = compare_telegram_routes_env_vs_rules(snap, sheet_present=True)
    assert result.mismatched == 1
    assert result.migrated_route_differences == 0


def test_flag_on_migrated_hourly_diff_is_informational(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv(ENV_PLATFORM_HOURLY_LEGACY, "-env-hourly")
    snap = _snap_hourly("-rules-hourly")
    result = compare_telegram_routes_env_vs_rules(snap, sheet_present=True)
    assert result.mismatched == 0
    assert result.migrated_route_differences == 1


def test_flag_on_migrated_hourly_match_zero_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv(ENV_PLATFORM_HOURLY_LEGACY, "-same")
    snap = _snap_hourly("-same")
    result = compare_telegram_routes_env_vs_rules(snap, sheet_present=True)
    assert result.mismatched == 0
    assert result.migrated_route_differences == 0


def test_flag_on_unmigrated_route_still_shadow_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_ANALIZ", "-env-analiz")
    snap = _snap_analiz("-rules-analiz")
    result = compare_telegram_routes_env_vs_rules(snap, sheet_present=True)
    assert result.mismatched == 1
    assert result.migrated_route_differences == 0


def test_status_dict_flag_on_migrated_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv(ENV_PLATFORM_HOURLY_LEGACY, "-env")
    snap = _snap_hourly("-rules")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        status = get_telegram_routes_status_dict(snapshot=snap)
    assert status["mode"] == "rules_runtime"
    assert status["feature_flag"] == "1"
    assert status["source_for_platform_hourly_report"] == "rules_v2"
    assert status["shadow_mismatches"] == 0
    assert status["migrated_route_differences"] == 1


def test_format_status_includes_migrated_route_differences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv(ENV_PLATFORM_HOURLY_LEGACY, "-env")
    snap = _snap_hourly("-rules")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        lines = format_telegram_routes_status_lines()
    assert any(line == "- shadow_mismatches=0" for line in lines)
    assert any(line == "- migrated_route_differences=1" for line in lines)


def test_shadow_log_summary_includes_split_metrics(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv(ENV_PLATFORM_HOURLY_LEGACY, "-env")
    snap = _snap_hourly("-rules")
    logger = logging.getLogger("test.telegram_routes.phase3a1")
    with caplog.at_level(logging.INFO, logger="test.telegram_routes.phase3a1"):
        run_telegram_routes_shadow_compare(logger, snapshot=snap, force=True)
    msg = next(r.message for r in caplog.records if "[telegram_routes][shadow]" in r.message)
    assert "shadow_mismatches=0" in msg
    assert "migrated_route_differences=1" in msg
    assert "-env" not in msg
    assert "-rules" not in msg


def test_runtime_resolve_unchanged_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_TELEGRAM_ROUTES_FROM_RULES_V2, "1")
    monkeypatch.setenv(ENV_PLATFORM_HOURLY_LEGACY, "-legacy-unused")
    snap = _snap_hourly("-rules-runtime")
    with patch("core.rules_provider.get_snapshot_v2", return_value=snap):
        res = resolve_route_chat_id(ROUTE_PLATFORM_HOURLY_REPORT)
    assert res.source == "rules_v2"
    assert res.chat_id == "-rules-runtime"
