"""Tests for Raccoon Wallet Rules V2 config (Phases 3B-6 / 3B-7)."""

from __future__ import annotations

import ast
import inspect
import logging
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from analyzers import raccoon_wallet_config_loader as loader_mod
from analyzers.raccoon_wallet_config_loader import (
    RaccoonWalletScalarParams,
    resolve_raccoon_wallet_config,
)
from core.config_manager import ALLOWED_JOB_PARAMS
from core.rules_v2.raccoon_wallet_rules_accessor import (
    RaccoonWalletRoster,
    partners_cfg_from_roster,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNTIME_DIRS = ("analyzers", "core", "integrations")

_GOLDEN_SCALARS = RaccoonWalletScalarParams(
    window_minutes=60,
    offset_minutes=0,
    min_events=5,
    pending_payin_minutes=10,
    payin_days_back=0,
)

_RULES_ROSTER = RaccoonWalletRoster(
    norm_to_display={
        "cat.casino": "Cat.Casino (207)",
        "motor": "Motor (215)",
    }
)

_RESOLVE_PATCH_TARGET = "analyzers.raccoon_wallet_config_loader"


@contextmanager
def _patch_resolve(**overrides):
    scalars = overrides.get("load_scalars_from_rules", _GOLDEN_SCALARS)
    roster = overrides.get("load_roster_from_rules", _RULES_ROSTER)
    groups = overrides.get("load_groups_cfg_from_rules", {})
    rules_path = overrides.get("_resolve_rules_path", Path("dummy.xlsx"))

    with (
        patch(f"{_RESOLVE_PATCH_TARGET}.load_scalars_from_rules", return_value=scalars),
        patch(f"{_RESOLVE_PATCH_TARGET}.load_roster_from_rules", return_value=roster),
        patch(f"{_RESOLVE_PATCH_TARGET}.load_groups_cfg_from_rules", return_value=groups),
        patch(f"{_RESOLVE_PATCH_TARGET}._resolve_rules_path", return_value=rules_path),
    ):
        yield


@pytest.fixture(autouse=True)
def _no_raccoon_env_flag(monkeypatch):
    monkeypatch.delenv("RACCOON_WALLET_CONFIG_FROM_RULES_V2", raising=False)


def test_raccoon_config_resolves_without_env(caplog):
    caplog.set_level(logging.INFO)

    with _patch_resolve():
        cfg = resolve_raccoon_wallet_config(logging.getLogger("test.resolve.no_env"))

    assert cfg["window_minutes"] == 60
    assert cfg["columns"]["payin"]["partner"] == "Партнер"
    assert any(
        "[raccoon_wallet_config] source=rules_v2" in r.message for r in caplog.records
    )


def test_raccoon_config_fails_on_missing_required_job_params():
    with _patch_resolve(load_scalars_from_rules=None):
        with pytest.raises(RuntimeError, match="rules scalars incomplete"):
            resolve_raccoon_wallet_config(logging.getLogger("test.scalars.missing"))


def test_raccoon_config_fails_on_empty_roster():
    with _patch_resolve(load_roster_from_rules=RaccoonWalletRoster(norm_to_display={})):
        with pytest.raises(RuntimeError, match="rules roster empty or unavailable"):
            resolve_raccoon_wallet_config(logging.getLogger("test.roster.empty"))


def test_raccoon_rules_v2_only_scalars():
    mutated = RaccoonWalletScalarParams(
        window_minutes=45,
        offset_minutes=0,
        min_events=5,
        pending_payin_minutes=10,
        payin_days_back=0,
    )

    with _patch_resolve(load_scalars_from_rules=mutated):
        cfg = resolve_raccoon_wallet_config(logging.getLogger("test.scalars"))

    assert cfg["window_minutes"] == 45
    assert cfg["pending_thresholds"]["payin_minutes"] == 10
    assert cfg["download_periods"]["payin_days_back"] == 0


def test_raccoon_rules_v2_only_roster():
    roster = RaccoonWalletRoster(
        norm_to_display={
            "cat.casino": "Cat.Casino (207)",
            "r7.casino": "R7.Casino (212)",
        }
    )

    with _patch_resolve(load_roster_from_rules=roster):
        cfg = resolve_raccoon_wallet_config(logging.getLogger("test.roster"))

    assert set(cfg["partners"].keys()) == {"Cat.Casino (207)", "R7.Casino (212)"}


def test_raccoon_rules_v2_only_groups():
    groups = {"test_group": {"partners": ["Cat.Casino (207)"]}}

    with _patch_resolve(load_groups_cfg_from_rules=groups):
        cfg = resolve_raccoon_wallet_config(logging.getLogger("test.groups"))

    assert cfg["groups"] == groups


def test_raccoon_analyzer_runs_without_yaml_and_without_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID_RACCOON_WALLET", "123")

    with patch(
        "analyzers.raccoon_wallet_analyzer.resolve_raccoon_wallet_config",
        return_value={
            "window_minutes": 60,
            "offset_minutes": 0,
            "min_events": 5,
            "pending_thresholds": {"payin_minutes": 10},
            "download_periods": {"payin_days_back": 0},
            "partners": {"Cat.Casino (207)": {}},
            "groups": {},
            "columns": {
                "payin": {
                    "partner": "Партнер",
                    "status": "Статус",
                    "dt": "Дата/Время создания",
                    "amount": "Сумма",
                }
            },
        },
    ):
        from analyzers import raccoon_wallet_analyzer as analyzer_mod

        cfg = analyzer_mod._load_cfg()

    assert cfg["window_minutes"] == 60
    assert "Cat.Casino (207)" in cfg["partners"]


def test_raccoon_downloader_runs_without_yaml_and_without_env(monkeypatch):
    monkeypatch.setenv("RACCOON_LOGIN", "u")
    monkeypatch.setenv("RACCOON_PASSWORD", "p")
    captured: dict = {}

    def fake_resolve(logger, *, rules_path=None):
        return {
            "download_periods": {"payin_days_back": 0},
            "window_minutes": 60,
        }

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


def test_no_runtime_references_to_raccoon_config_env_flag():
    offenders: list[str] = []
    for rel_dir in _RUNTIME_DIRS:
        root = REPO_ROOT / rel_dir
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if "RACCOON_WALLET_CONFIG_FROM_RULES_V2" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == []


def test_no_yaml_loader_remaining():
    source = inspect.getsource(loader_mod)
    tree = ast.parse(source)

    forbidden_names = {
        "try_load_legacy_yaml_config",
        "load_legacy_yaml_config",
        "run_raccoon_wallet_config_shadow_compare",
        "run_raccoon_wallet_roster_shadow_compare",
        "run_raccoon_wallet_groups_shadow_compare",
        "run_raccoon_payin_columns_shadow_compare",
        "_require_rules_v2_enabled",
        "_truthy_env",
    }
    defined_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert forbidden_names.isdisjoint(defined_names)
    assert "yaml" not in loader_mod.__dict__
    assert "yaml.safe_load" not in source
    assert "RACCOON_WALLET_CONFIG_FROM_RULES_V2" not in source


def test_no_yaml_config_file_present():
    yaml_path = REPO_ROOT / "config" / "raccoon_wallet_config.yaml"
    assert not yaml_path.exists()


def test_job_params_allows_raccoon_wallet():
    assert "raccoon_wallet" in ALLOWED_JOB_PARAMS
    assert set(ALLOWED_JOB_PARAMS["raccoon_wallet"]) == {
        "window_minutes",
        "offset_minutes",
        "min_events",
        "pending_payin_minutes",
        "payin_days_back",
    }


def test_partners_cfg_from_roster_preserves_display_names():
    roster = RaccoonWalletRoster(
        norm_to_display={
            "cat.casino": "Cat.Casino (207)",
            "motor": "Motor (215)",
        }
    )
    partners = partners_cfg_from_roster(roster)
    assert partners == {"Cat.Casino (207)": {}, "Motor (215)": {}}
