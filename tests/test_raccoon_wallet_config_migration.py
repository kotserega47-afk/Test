"""Tests for raccoon_wallet_config.yaml → Rules V2 job_params (Phase 3A)."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from analyzers.raccoon_wallet_config_loader import (
    DEFAULT_YAML_PATH,
    RaccoonWalletScalarParams,
    apply_scalars_to_cfg,
    extract_scalars_from_yaml,
    load_legacy_yaml_config,
    resolve_raccoon_wallet_config,
    run_raccoon_wallet_config_shadow_compare,
    scalar_params_diff,
)
from core.config_manager import ALLOWED_JOB_PARAMS
from core.rules_v2.raccoon_wallet_rules_accessor import build_roster_from_yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

_GOLDEN_SCALARS = RaccoonWalletScalarParams(
    window_minutes=60,
    offset_minutes=0,
    min_events=5,
    pending_payin_minutes=10,
    payin_days_back=0,
)


def _write_minimal_raccoon_yaml(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        DEFAULT_YAML_PATH.read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def _build_raccoon_job_params_xlsx(path: Path, *, window_minutes: int = 60) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "id": "JP-RW-001",
            "enabled": 1,
            "job": "raccoon_wallet",
            "scope": "job",
            "scope_value": "raccoon_wallet",
            "key": "window_minutes",
            "value_type": "int",
            "value": window_minutes,
        },
        {
            "id": "JP-RW-002",
            "enabled": 1,
            "job": "raccoon_wallet",
            "scope": "job",
            "scope_value": "raccoon_wallet",
            "key": "offset_minutes",
            "value_type": "int",
            "value": 0,
        },
        {
            "id": "JP-RW-003",
            "enabled": 1,
            "job": "raccoon_wallet",
            "scope": "job",
            "scope_value": "raccoon_wallet",
            "key": "min_events",
            "value_type": "int",
            "value": 5,
        },
        {
            "id": "JP-RW-004",
            "enabled": 1,
            "job": "raccoon_wallet",
            "scope": "job",
            "scope_value": "raccoon_wallet",
            "key": "pending_payin_minutes",
            "value_type": "int",
            "value": 10,
        },
        {
            "id": "JP-RW-005",
            "enabled": 1,
            "job": "raccoon_wallet",
            "scope": "job",
            "scope_value": "raccoon_wallet",
            "key": "payin_days_back",
            "value_type": "int",
            "value": 0,
        },
    ]
    df = pd.DataFrame(rows)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="job_params", index=False)
    return path


@pytest.fixture
def raccoon_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "raccoon_wallet_config.yaml"
    _write_minimal_raccoon_yaml(p)
    return p


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_job_params_allows_raccoon_wallet():
    assert "raccoon_wallet" in ALLOWED_JOB_PARAMS
    assert set(ALLOWED_JOB_PARAMS["raccoon_wallet"]) == {
        "window_minutes",
        "offset_minutes",
        "min_events",
        "pending_payin_minutes",
        "payin_days_back",
    }


# ---------------------------------------------------------------------------
# Loader modes
# ---------------------------------------------------------------------------


def test_raccoon_wallet_config_loader_yaml_mode(raccoon_yaml: Path, monkeypatch, caplog):
    monkeypatch.delenv("RACCOON_WALLET_CONFIG_FROM_RULES_V2", raising=False)
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("test.raccoon.yaml")

    cfg = resolve_raccoon_wallet_config(logger, yaml_path=raccoon_yaml)

    assert cfg["window_minutes"] == 60
    assert cfg["offset_minutes"] == 0
    assert cfg["min_events"] == 5
    assert cfg["pending_thresholds"]["payin_minutes"] == 10
    assert cfg["download_periods"]["payin_days_back"] == 0
    assert "partners" in cfg and cfg["partners"]
    assert not any("[raccoon_wallet_config] source=rules_v2" in r.message for r in caplog.records)


def test_raccoon_wallet_config_loader_rules_mode(raccoon_yaml: Path, tmp_path: Path, monkeypatch, caplog):
    monkeypatch.setenv("RACCOON_WALLET_CONFIG_FROM_RULES_V2", "1")
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("test.raccoon.rules")

    rules_xlsx = _build_raccoon_job_params_xlsx(tmp_path / "rules_raccoon.xlsx", window_minutes=45)
    rules_roster = build_roster_from_yaml(load_legacy_yaml_config(raccoon_yaml))

    with patch(
        "analyzers.raccoon_wallet_config_loader.load_scalars_from_rules",
        return_value=RaccoonWalletScalarParams(
            window_minutes=45,
            offset_minutes=0,
            min_events=5,
            pending_payin_minutes=10,
            payin_days_back=0,
        ),
    ), patch(
        "analyzers.raccoon_wallet_config_loader.load_roster_from_rules",
        return_value=rules_roster,
    ), patch(
        "analyzers.raccoon_wallet_config_loader.load_groups_cfg_from_rules",
        return_value={},
    ):
        cfg = resolve_raccoon_wallet_config(logger, yaml_path=raccoon_yaml, rules_path=rules_xlsx)

    assert cfg["window_minutes"] == 45
    assert cfg["partners"]
    assert any(
        "[raccoon_wallet_config] source=rules_v2 fields=" in r.message
        and "scalars" in r.message
        and "roster" in r.message
        and "groups" in r.message
        for r in caplog.records
    )


def test_raccoon_wallet_config_loader_rules_fallback(raccoon_yaml: Path, monkeypatch, caplog):
    monkeypatch.setenv("RACCOON_WALLET_CONFIG_FROM_RULES_V2", "1")
    caplog.set_level(logging.INFO)
    logger = logging.getLogger("test.raccoon.fallback")

    with patch("analyzers.raccoon_wallet_config_loader.load_scalars_from_rules", return_value=None):
        cfg = resolve_raccoon_wallet_config(logger, yaml_path=raccoon_yaml)

    assert cfg["window_minutes"] == 60
    assert any("[raccoon_wallet_config] source=yaml reason=" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Shadow
# ---------------------------------------------------------------------------


def test_raccoon_wallet_shadow_no_diff(raccoon_yaml: Path, caplog):
    yaml_cfg = load_legacy_yaml_config(raccoon_yaml)
    yaml_scalars = extract_scalars_from_yaml(yaml_cfg)
    rules_scalars = _GOLDEN_SCALARS
    assert scalar_params_diff(yaml_scalars, rules_scalars) == {}

    caplog.set_level(logging.WARNING)
    with patch(
        "analyzers.raccoon_wallet_config_loader.load_scalars_from_rules",
        return_value=_GOLDEN_SCALARS,
    ), patch(
        "analyzers.raccoon_wallet_config_loader._resolve_rules_path",
        return_value=Path("dummy.xlsx"),
    ):
        run_raccoon_wallet_config_shadow_compare(
            logging.getLogger("test.raccoon.shadow"),
            yaml_path=raccoon_yaml,
        )

    assert not any("[config_shadow] raccoon_wallet mismatch" in r.message for r in caplog.records)


def test_raccoon_wallet_shadow_detects_diff(raccoon_yaml: Path, caplog):
    caplog.set_level(logging.WARNING)
    mutated = RaccoonWalletScalarParams(
        window_minutes=30,
        offset_minutes=0,
        min_events=5,
        pending_payin_minutes=10,
        payin_days_back=0,
    )

    with patch(
        "analyzers.raccoon_wallet_config_loader.load_scalars_from_rules",
        return_value=mutated,
    ), patch(
        "analyzers.raccoon_wallet_config_loader._resolve_rules_path",
        return_value=Path("dummy.xlsx"),
    ):
        run_raccoon_wallet_config_shadow_compare(
            logging.getLogger("test.raccoon.shadow.diff"),
            yaml_path=raccoon_yaml,
        )

    assert any("[config_shadow] raccoon_wallet mismatch" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Runtime regression
# ---------------------------------------------------------------------------


def test_raccoon_wallet_analyzer_uses_loader_without_behavior_change(raccoon_yaml: Path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID_RACCOON_WALLET", "123")
    monkeypatch.delenv("RACCOON_WALLET_CONFIG_FROM_RULES_V2", raising=False)

    from analyzers import raccoon_wallet_analyzer as analyzer_mod

    cfg = analyzer_mod._load_cfg()
    assert cfg["window_minutes"] == 60
    assert cfg["min_events"] == 5
    assert len(cfg.get("partners") or {}) >= 10


def test_raccoon_wallet_downloader_uses_loader_for_payin_days_back(raccoon_yaml: Path, monkeypatch):
    monkeypatch.setenv("RACCOON_LOGIN", "u")
    monkeypatch.setenv("RACCOON_PASSWORD", "p")
    monkeypatch.delenv("RACCOON_WALLET_CONFIG_FROM_RULES_V2", raising=False)

    captured: dict = {}

    def fake_resolve(logger, *, yaml_path=None, rules_path=None):
        cfg = load_legacy_yaml_config(yaml_path or DEFAULT_YAML_PATH)
        return apply_scalars_to_cfg(cfg, _GOLDEN_SCALARS)

    def fake_download(page, ts, days_back):
        captured["days_back"] = days_back
        return "/tmp/fake_payin.xlsx"

    import integrations.raccoon_wallet_downloader as dl_mod

    monkeypatch.setattr(dl_mod, "resolve_raccoon_wallet_config", fake_resolve)
    monkeypatch.setattr(dl_mod, "_download_payin", fake_download)
    monkeypatch.setattr(dl_mod, "analyze_raccoon_wallets", lambda *a, **k: None)

    class FakePage:
        pass

    class FakeContext:
        def storage_state(self, **kwargs):
            return None

        def new_page(self):
            return FakePage()

    class FakeBrowser:
        def new_context(self, **kwargs):
            return FakeContext()

        def close(self):
            return None

    class FakeChromium:
        def launch(self, **kwargs):
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class FakeSyncPlaywright:
        def __enter__(self):
            return FakePlaywright()

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(dl_mod, "sync_playwright", lambda: FakeSyncPlaywright())
    monkeypatch.setattr(dl_mod, "_ensure_logged_in", lambda page, ctx: None)
    monkeypatch.setattr(dl_mod.os.path, "exists", lambda p: True)

    dl_mod.run_raccoon_wallet_cycle()

    assert captured["days_back"] == 0


def test_golden_yaml_scalar_extraction_matches_repo_config():
    cfg = load_legacy_yaml_config(DEFAULT_YAML_PATH)
    scalars = extract_scalars_from_yaml(cfg)
    assert scalars == _GOLDEN_SCALARS
