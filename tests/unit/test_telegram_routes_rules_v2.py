"""Phase 2 — telegram_routes Rules V2 model, validator, accessor, shadow compare."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import pytest

from core.rules_v2.accessors import BaseRulesAccessor
from core.rules_v2.bridge_legacy import build_snapshot_v2_from_legacy
from core.rules_v2.constants import (
    ALLOWED_TELEGRAM_ROUTE_KEYS,
    LEGACY_ENV_TO_TELEGRAM_ROUTE_KEYS,
    TELEGRAM_CHAT_ID_EMERGENCY_ENV,
)
from core.rules_v2.indexes import RulesIndexes
from core.rules_v2.models import TelegramRoute
from core.rules_v2.validators import (
    is_valid_telegram_chat_id,
    validate_telegram_routes_dict,
)
from integrations.telegram_routes import (
    compare_telegram_routes_env_vs_rules,
    run_telegram_routes_shadow_compare,
)

C5_BASELINE = (
    Path(__file__).resolve().parents[1] / "rules_v2" / "c5" / "workbooks" / "baseline_prod_synthetic.xlsx"
)


def _route_row(
    route_key: str,
    chat_id: str,
    *,
    enabled: int = 1,
    description: str = "test route",
) -> dict:
    return {
        "route_key": route_key,
        "chat_id": chat_id,
        "enabled": enabled,
        "description": description,
    }


def _write_workbook_with_routes(
    path: Path,
    routes: list[dict] | None,
    *,
    include_sheet: bool = True,
) -> None:
    """Copy baseline sheets and add/replace ``telegram_routes``."""

    xls = pd.ExcelFile(C5_BASELINE)
    frames: dict[str, pd.DataFrame] = {}
    for sheet in xls.sheet_names:
        frames[sheet] = pd.read_excel(C5_BASELINE, sheet_name=sheet)

    if include_sheet:
        if routes is None:
            frames["telegram_routes"] = pd.DataFrame(
                columns=["route_key", "chat_id", "enabled", "description"]
            )
        else:
            frames["telegram_routes"] = pd.DataFrame(routes)
    elif "telegram_routes" in frames:
        del frames["telegram_routes"]

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet, df in frames.items():
            df.to_excel(writer, sheet_name=sheet, index=False)


def _telegram_only_indexes(snap) -> RulesIndexes:
    """Minimal indexes for route accessor tests (avoids baseline access row quirks)."""

    return RulesIndexes(telegram_routes_by_key=dict(snap.telegram_routes))


def _snapshot_from_routes(routes: list[dict], tmp_path: Path) -> tuple:
    path = tmp_path / "rules_routes.xlsx"
    _write_workbook_with_routes(path, routes)
    snap = build_snapshot_v2_from_legacy(path)
    return snap, _telegram_only_indexes(snap), path


@pytest.fixture
def analiz_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_CHAT_ID_ANALIZ", "-9001")
    monkeypatch.setenv("TELEGRAM_CHAT_ID_HOURLY", "-9002")
    monkeypatch.delenv(TELEGRAM_CHAT_ID_EMERGENCY_ENV, raising=False)


def test_valid_telegram_routes_sheet_parses(tmp_path: Path) -> None:
    snap, idx, _ = _snapshot_from_routes(
        [_route_row("analiz_main_pipeline", "-100123")],
        tmp_path,
    )
    route = snap.telegram_routes["analiz_main_pipeline"]
    assert route.chat_id == "-100123"
    assert route.enabled is True
    assert idx.telegram_routes_by_key["analiz_main_pipeline"].description == "test route"


def test_duplicate_route_key_rejected(tmp_path: Path) -> None:
    path = tmp_path / "dup.xlsx"
    _write_workbook_with_routes(
        path,
        [
            _route_row("analiz_main_pipeline", "-1"),
            _route_row("analiz_main_pipeline", "-2"),
        ],
    )
    with pytest.raises(ValueError, match="duplicate route_key"):
        build_snapshot_v2_from_legacy(path)


def test_unknown_route_key_rejected(tmp_path: Path) -> None:
    path = tmp_path / "unknown.xlsx"
    _write_workbook_with_routes(path, [_route_row("not_a_real_route", "-1")])
    with pytest.raises(ValueError, match="unknown route_key"):
        build_snapshot_v2_from_legacy(path)


def test_empty_chat_id_rejected(tmp_path: Path) -> None:
    path = tmp_path / "empty_chat.xlsx"
    _write_workbook_with_routes(path, [_route_row("analiz_main_pipeline", "")])
    with pytest.raises(ValueError, match="invalid chat_id"):
        build_snapshot_v2_from_legacy(path)


def test_invalid_chat_id_rejected(tmp_path: Path) -> None:
    assert is_valid_telegram_chat_id("abc") is False
    path = tmp_path / "bad_chat.xlsx"
    _write_workbook_with_routes(path, [_route_row("analiz_main_pipeline", "not-int")])
    with pytest.raises(ValueError, match="invalid chat_id"):
        build_snapshot_v2_from_legacy(path)


def test_disabled_route_returns_none_from_accessor(tmp_path: Path) -> None:
    snap, idx, _ = _snapshot_from_routes(
        [_route_row("analiz_main_pipeline", "-1", enabled=0)],
        tmp_path,
    )
    acc = BaseRulesAccessor(snapshot=snap, indexes=idx)
    assert acc.get_telegram_route("analiz_main_pipeline") is not None
    assert acc.get_telegram_chat_id("analiz_main_pipeline") is None


def test_enabled_route_returns_chat_id(tmp_path: Path) -> None:
    snap, idx, _ = _snapshot_from_routes(
        [_route_row("platform_hourly_report", "-555001")],
        tmp_path,
    )
    acc = BaseRulesAccessor(snapshot=snap, indexes=idx)
    assert acc.get_telegram_chat_id("platform_hourly_report") == "-555001"


def test_missing_sheet_does_not_break_baseline_build() -> None:
    snap = build_snapshot_v2_from_legacy(C5_BASELINE)
    assert snap.telegram_routes == {}


def test_shadow_compare_match(analiz_env: None, tmp_path: Path) -> None:
    snap, _, _ = _snapshot_from_routes(
        [_route_row("analiz_main_pipeline", "-9001")],
        tmp_path,
    )
    result = compare_telegram_routes_env_vs_rules(snap, sheet_present=True)
    assert result.matched >= 1
    assert result.mismatched == 0


def test_shadow_missing_route_in_rules(analiz_env: None) -> None:
    snap = build_snapshot_v2_from_legacy(C5_BASELINE)
    result = compare_telegram_routes_env_vs_rules(snap, sheet_present=False)
    assert result.missing_rules >= 1


def test_shadow_missing_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_CHAT_ID_HOURLY", raising=False)
    snap, _, _ = _snapshot_from_routes(
        [_route_row("platform_hourly_report", "-42")],
        tmp_path,
    )
    result = compare_telegram_routes_env_vs_rules(snap, sheet_present=True)
    assert result.missing_env >= 1


def test_shadow_mismatch(analiz_env: None, tmp_path: Path) -> None:
    snap, _, _ = _snapshot_from_routes(
        [_route_row("analiz_main_pipeline", "-9999")],
        tmp_path,
    )
    result = compare_telegram_routes_env_vs_rules(snap, sheet_present=True)
    assert result.mismatched >= 1


def test_accessor_does_not_read_env_as_primary(
    analiz_env: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_CHAT_ID_ANALIZ", "-env-only")
    snap, idx, _ = _snapshot_from_routes(
        [_route_row("analiz_main_pipeline", "-10001")],
        tmp_path,
    )
    acc = BaseRulesAccessor(snapshot=snap, indexes=idx)
    assert acc.get_telegram_chat_id("analiz_main_pipeline") == "-10001"
    assert os.getenv("TELEGRAM_CHAT_ID_ANALIZ") == "-env-only"


def test_no_emergency_fallback_for_business_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(TELEGRAM_CHAT_ID_EMERGENCY_ENV, "-911")
    snap, idx, _ = _snapshot_from_routes(
        [_route_row("bakai_rate_current", "-1")],
        tmp_path,
    )
    acc = BaseRulesAccessor(snapshot=snap, indexes=idx)
    assert acc.get_telegram_chat_id("bakai_rate_current") == "-1"
    assert acc.get_telegram_chat_id("bakai_rate_alert") is None


def test_validate_telegram_routes_dict_ok_on_valid_route() -> None:
    routes = {
        "analiz_main_pipeline": TelegramRoute(
            route_key="analiz_main_pipeline",
            chat_id="-1",
            enabled=True,
            description="ok",
        )
    }
    assert validate_telegram_routes_dict(routes) == []


def test_allowed_registry_covers_legacy_mapping() -> None:
    for _env, route_key in LEGACY_ENV_TO_TELEGRAM_ROUTE_KEYS:
        assert route_key in ALLOWED_TELEGRAM_ROUTE_KEYS


def test_shadow_compare_logs_summary(analiz_env: None, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    snap, _, _ = _snapshot_from_routes(
        [_route_row("analiz_main_pipeline", "-9001")],
        tmp_path,
    )
    logger = logging.getLogger("test.telegram_routes")
    with caplog.at_level(logging.INFO, logger="test.telegram_routes"):
        run_telegram_routes_shadow_compare(logger, snapshot=snap, force=True)
    assert any("[telegram_routes][shadow]" in r.message for r in caplog.records)


def test_import_telegram_routes_module_no_resolver_env_required() -> None:
    import integrations.telegram_routes as tr  # noqa: F401

    assert tr.TELEGRAM_CHAT_ID_EMERGENCY_ENV == "TELEGRAM_CHAT_ID_EMERGENCY"


def test_telegram_route_model_fields() -> None:
    r = TelegramRoute(
        route_key="analiz_main_pipeline",
        chat_id="-1",
        enabled=True,
        description="d",
    )
    assert r.route_key == "analiz_main_pipeline"
