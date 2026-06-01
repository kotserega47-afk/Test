"""WE-3: isolated Antares credentials for WalletEditor."""
from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest

from automation.runtime import (
    RunConfig,
    require_wallet_editor_antares_credentials,
    resolve_operator_for_user,
    wallet_editor_antares_login,
    wallet_editor_antares_password,
)


def test_wallet_editor_uses_dedicated_env_vars() -> None:
    env = {
        "WALLET_EDITOR_ANTARES_LOGIN": "editor-user",
        "WALLET_EDITOR_ANTARES_PASSWORD": "editor-pass",
    }
    with patch.dict("os.environ", env, clear=True):
        assert wallet_editor_antares_login() == "editor-user"
        assert wallet_editor_antares_password() == "editor-pass"
        cfg = RunConfig()
        assert cfg.login == "editor-user"
        assert cfg.password == "editor-pass"


def test_no_fallback_to_shared_antares_env() -> None:
    env = {
        "ANTARES_LOGIN": "shared-login",
        "ANTARES_PASSWORD": "shared-password",
    }
    with patch.dict("os.environ", env, clear=True):
        cfg = RunConfig()
        assert cfg.login == ""
        assert cfg.password == ""


def test_missing_credentials_import_safe() -> None:
    with patch.dict("os.environ", {}, clear=True):
        import automation.engine as engine_mod
        import automation.runtime as runtime_mod

        importlib.reload(runtime_mod)
        importlib.reload(engine_mod)

        cfg = runtime_mod.RunConfig()
        assert cfg.login == ""
        assert cfg.password == ""


def test_missing_credentials_runtime_error() -> None:
    with patch.dict("os.environ", {}, clear=True):
        cfg = RunConfig()
        with pytest.raises(RuntimeError, match="credentials оператора"):
            require_wallet_editor_antares_credentials(cfg)


def test_missing_password_runtime_error() -> None:
    with patch.dict(
        "os.environ",
        {"WALLET_EDITOR_ANTARES_LOGIN": "only-login"},
        clear=True,
    ):
        cfg = RunConfig()
        with pytest.raises(RuntimeError, match="credentials оператора"):
            require_wallet_editor_antares_credentials(cfg)


def test_engine_no_shared_antares_constants() -> None:
    src = Path("automation/engine.py").read_text(encoding="utf-8")
    assert "ANTARES_LOGIN" not in src
    assert "ANTARES_PASSWORD" not in src
    assert "cfg.login" in src
    assert "cfg.password" in src


def test_handler_does_not_use_legacy_antares_env_as_fallback() -> None:
    src = Path("integrations/wallet_editor_tg.py").read_text(encoding="utf-8")
    assert "WALLET_EDITOR_ANTARES_LOGIN" not in src
    assert "ANTARES_LOGIN" not in src


def test_resolve_operator_does_not_use_legacy_wallet_editor_antares_env() -> None:
    env = {
        "WALLET_EDITOR_ANTARES_LOGIN": "legacy-login",
        "WALLET_EDITOR_ANTARES_PASSWORD": "legacy-pass",
        "WALLET_EDITOR_OPERATOR_MAP": "555:DENIS",
        "WALLET_EDITOR_OPERATOR_DENIS_LOGIN": "denis-login",
        "WALLET_EDITOR_OPERATOR_DENIS_PASSWORD": "denis-pass",
    }
    with patch.dict("os.environ", env, clear=True):
        creds, _ = resolve_operator_for_user(555)
    assert creds is not None
    assert creds.login == "denis-login"
    assert creds.password == "denis-pass"


def test_downloader_wallets_still_uses_shared_antares_env() -> None:
    src = Path("integrations/downloader_wallets.py").read_text(encoding="utf-8")
    assert 'os.getenv("ANTARES_LOGIN")' in src
    assert 'os.getenv("ANTARES_PASSWORD")' in src
    assert "WALLET_EDITOR_ANTARES_LOGIN" not in src
    assert "WALLET_EDITOR_ANTARES_PASSWORD" not in src
