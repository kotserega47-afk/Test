from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
import time
from typing import Callable, TypeVar

from automation.audit import log


T = TypeVar("T")

DEFAULT_WALLET_EDITOR_AUTH_STATE_PATH = "/tmp/auth_state_wallet_editor.json"
_WALLET_EDITOR_AUTH_STATE_ENV = "WALLET_EDITOR_AUTH_STATE_PATH"
_WALLET_EDITOR_ANTARES_LOGIN_ENV = "WALLET_EDITOR_ANTARES_LOGIN"
_WALLET_EDITOR_ANTARES_PASSWORD_ENV = "WALLET_EDITOR_ANTARES_PASSWORD"
_WALLET_EDITOR_OPERATOR_MAP_ENV = "WALLET_EDITOR_OPERATOR_MAP"
_PROFILE_KEY_RE = re.compile(r"^[A-Z0-9_]+$")

MSG_OPERATOR_UNMAPPED = "⛔ Для вашего Telegram user_id не настроен профиль WalletEditor."
MSG_OPERATOR_INCOMPLETE = (
    "⛔ Профиль WalletEditor настроен неполностью. Обратитесь к администратору."
)


def wallet_editor_auth_state_path() -> str:
    value = os.getenv(_WALLET_EDITOR_AUTH_STATE_ENV, DEFAULT_WALLET_EDITOR_AUTH_STATE_PATH).strip()
    return value or DEFAULT_WALLET_EDITOR_AUTH_STATE_PATH


def wallet_editor_antares_login() -> str:
    return os.getenv(_WALLET_EDITOR_ANTARES_LOGIN_ENV, "").strip()


def wallet_editor_antares_password() -> str:
    return os.getenv(_WALLET_EDITOR_ANTARES_PASSWORD_ENV, "").strip()


def normalize_profile_key(raw: str) -> str | None:
    key = (raw or "").strip().upper()
    if not key or not _PROFILE_KEY_RE.fullmatch(key):
        return None
    return key


def parse_operator_map(env_value: str | None = None) -> dict[int, str]:
    raw = (
        env_value
        if env_value is not None
        else os.getenv(_WALLET_EDITOR_OPERATOR_MAP_ENV, "")
    ).strip()
    if not raw:
        return {}

    result: dict[int, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        user_s, profile_s = part.split(":", 1)
        user_part = user_s.strip()
        if not user_part:
            continue
        try:
            user_id = int(user_part)
        except ValueError:
            continue
        profile_key = normalize_profile_key(profile_s)
        if profile_key is None:
            log.warning(
                f"⚠️ [WalletEditor] invalid profile_key in operator map for user_id={user_part!r}"
            )
            continue
        result[user_id] = profile_key
    return result


def operator_login_env(profile_key: str) -> str:
    return f"WALLET_EDITOR_OPERATOR_{profile_key}_LOGIN"


def operator_password_env(profile_key: str) -> str:
    return f"WALLET_EDITOR_OPERATOR_{profile_key}_PASSWORD"


def operator_auth_state_path(profile_key: str) -> str:
    return f"/tmp/auth_state_wallet_editor_{profile_key}.json"


@dataclass(frozen=True)
class OperatorCredentials:
    profile_key: str
    login: str
    password: str
    auth_state_path: str


@dataclass(frozen=True)
class WalletEditorTask:
    file_path: str
    chat_id: int
    telegram_user_id: int
    operator_profile: str
    login: str
    password: str
    auth_state_path: str


def resolve_operator_for_user(telegram_user_id: int) -> tuple[OperatorCredentials | None, str | None]:
    profile_key = parse_operator_map().get(telegram_user_id)
    if profile_key is None:
        return None, MSG_OPERATOR_UNMAPPED

    login = os.getenv(operator_login_env(profile_key), "").strip()
    password = os.getenv(operator_password_env(profile_key), "").strip()
    if not login or not password:
        return None, MSG_OPERATOR_INCOMPLETE

    return (
        OperatorCredentials(
            profile_key=profile_key,
            login=login,
            password=password,
            auth_state_path=operator_auth_state_path(profile_key),
        ),
        None,
    )


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
        missing.append("login")
    if not cfg.password:
        missing.append("password")
    if missing:
        raise RuntimeError(
            "Для WalletEditor не заданы credentials оператора: " + ", ".join(missing)
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
