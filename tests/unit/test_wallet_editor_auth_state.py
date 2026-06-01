"""WE-2: isolated WalletEditor Playwright auth-state path."""
from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

from automation.runtime import (
    DEFAULT_WALLET_EDITOR_AUTH_STATE_PATH,
    RunConfig,
    wallet_editor_auth_state_path,
)


def test_default_wallet_editor_auth_state_path() -> None:
    with patch.dict("os.environ", {}, clear=True):
        assert wallet_editor_auth_state_path() == DEFAULT_WALLET_EDITOR_AUTH_STATE_PATH
        assert DEFAULT_WALLET_EDITOR_AUTH_STATE_PATH == "/tmp/auth_state_wallet_editor.json"


def test_run_config_default_auth_state_path() -> None:
    with patch.dict("os.environ", {}, clear=True):
        cfg = RunConfig()
        assert cfg.auth_state_path == "/tmp/auth_state_wallet_editor.json"


def test_env_wallet_editor_auth_state_path_overrides_default() -> None:
    custom = "/data/wallet_editor_session.json"
    with patch.dict("os.environ", {"WALLET_EDITOR_AUTH_STATE_PATH": custom}, clear=True):
        assert wallet_editor_auth_state_path() == custom
        assert RunConfig().auth_state_path == custom


def test_downloader_wallets_auth_path_unchanged() -> None:
    src = Path("integrations/downloader_wallets.py").read_text(encoding="utf-8")
    assert 'AUTH_STATE_FILE = os.path.join(BASE_DIR, "auth_state_wallets.json")' in src
    assert "auth_state_wallet_editor" not in src
    assert "WALLET_EDITOR_AUTH_STATE_PATH" not in src


def test_engine_no_shared_auth_state_constant() -> None:
    src = Path("automation/engine.py").read_text(encoding="utf-8")
    assert "auth_state_wallets.json" not in src
    assert "cfg.auth_state_path" in src


def test_import_safe_without_antares_credentials() -> None:
    with patch.dict("os.environ", {}, clear=True):
        import automation.engine as engine_mod
        import automation.runtime as runtime_mod

        importlib.reload(runtime_mod)
        importlib.reload(engine_mod)

        cfg = runtime_mod.RunConfig()
        assert cfg.login == ""
        assert cfg.password == ""
        assert runtime_mod.RunConfig().auth_state_path == DEFAULT_WALLET_EDITOR_AUTH_STATE_PATH
