from dataclasses import dataclass, field
import os
import time
from typing import Callable, TypeVar

from automation.audit import log


T = TypeVar("T")

DEFAULT_WALLET_EDITOR_AUTH_STATE_PATH = "/tmp/auth_state_wallet_editor.json"
_WALLET_EDITOR_AUTH_STATE_ENV = "WALLET_EDITOR_AUTH_STATE_PATH"
_WALLET_EDITOR_ANTARES_LOGIN_ENV = "WALLET_EDITOR_ANTARES_LOGIN"
_WALLET_EDITOR_ANTARES_PASSWORD_ENV = "WALLET_EDITOR_ANTARES_PASSWORD"


def wallet_editor_auth_state_path() -> str:
    value = os.getenv(_WALLET_EDITOR_AUTH_STATE_ENV, DEFAULT_WALLET_EDITOR_AUTH_STATE_PATH).strip()
    return value or DEFAULT_WALLET_EDITOR_AUTH_STATE_PATH


def wallet_editor_antares_login() -> str:
    return os.getenv(_WALLET_EDITOR_ANTARES_LOGIN_ENV, "").strip()


def wallet_editor_antares_password() -> str:
    return os.getenv(_WALLET_EDITOR_ANTARES_PASSWORD_ENV, "").strip()


@dataclass
class RunConfig:
    headless: bool = True
    dry_run: bool = False
    retries: int = 3
    delay: float = 1.0
    auth_state_path: str = field(default_factory=wallet_editor_auth_state_path)
    login: str = field(default_factory=wallet_editor_antares_login)
    password: str = field(default_factory=wallet_editor_antares_password)


def require_wallet_editor_antares_credentials(cfg: RunConfig) -> None:
    missing = []
    if not cfg.login:
        missing.append(_WALLET_EDITOR_ANTARES_LOGIN_ENV)
    if not cfg.password:
        missing.append(_WALLET_EDITOR_ANTARES_PASSWORD_ENV)
    if missing:
        raise RuntimeError(
            "Для WalletEditor не заданы переменные окружения: " + ", ".join(missing)
        )


def retry(fn: Callable[[], T], attempts: int = 3, delay: float = 1.0, step_name: str = "operation") -> T:
    last_error = None

    for i in range(attempts):
        try:
            log.info(f"🔁 [Retry] step={step_name} attempt={i + 1}/{attempts}")
            return fn()
        except Exception as e:
            last_error = e
            log.warning(f"⚠️ [Retry] step={step_name} attempt={i + 1}/{attempts} failed: {e}")
            if i == attempts - 1:
                log.error(f"❌ [Retry] step={step_name} exhausted retries")
                raise
            time.sleep(delay)

    raise last_error
