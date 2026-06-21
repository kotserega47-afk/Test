from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
import time
from pathlib import Path
from typing import Callable, TypeVar
from uuid import uuid4

from automation.audit import log


T = TypeVar("T")

DEFAULT_WALLET_EDITOR_AUTH_STATE_PATH = "/tmp/auth_state_wallet_editor.json"
_WALLET_EDITOR_AUTH_STATE_ENV = "WALLET_EDITOR_AUTH_STATE_PATH"
_WALLET_EDITOR_ANTARES_LOGIN_ENV = "WALLET_EDITOR_ANTARES_LOGIN"
_WALLET_EDITOR_ANTARES_PASSWORD_ENV = "WALLET_EDITOR_ANTARES_PASSWORD"
_WALLET_EDITOR_OPERATOR_MAP_ENV = "WALLET_EDITOR_OPERATOR_MAP"
_WALLET_EDITOR_PLAYWRIGHT_SLOW_MO_ENV = "WALLET_EDITOR_PLAYWRIGHT_SLOW_MO_MS"
_WALLET_EDITOR_OPEN_CARD_SETTLE_ENV = "WALLET_EDITOR_OPEN_CARD_SETTLE_MS"
_WALLET_EDITOR_ROW_MATCH_TIMEOUT_ENV = "WALLET_EDITOR_ROW_MATCH_TIMEOUT_MS"
_WALLET_EDITOR_RETRYABLE_MAX_ATTEMPTS_ENV = "WALLET_EDITOR_RETRYABLE_MAX_ATTEMPTS"
_DEFAULT_PLAYWRIGHT_SLOW_MO_MS = 0
_DEFAULT_OPEN_CARD_SETTLE_MS = 500
_DEFAULT_ROW_MATCH_TIMEOUT_MS = 3000
_DEFAULT_RETRYABLE_MAX_ATTEMPTS = 3
_PROFILE_KEY_RE = re.compile(r"^[A-Z0-9_]+$")
_INPUT_NAME_SAFE_RE = re.compile(r"[^A-Za-z0-9_\-.]+")
WALLET_EDITOR_RESULT_DIR = "/tmp/wallet_editor"
CONVERSION_AUTO_PROFILE = "CONVERSION_AUTO"
MAX_INPUT_NAME_LEN = 80
RESULT_NAME_PREFIX = "wallet_editor_result_"
ADD_WALLET_RESULT_PREFIX = "wallet_add_result_"
_WALLET_EDITOR_ADD_WALLET_DRY_RUN_ENV = "WALLET_EDITOR_ADD_WALLET_DRY_RUN"

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


def wallet_editor_playwright_slow_mo_ms() -> int:
    """Playwright launch slow_mo in ms. Default 0; rollback via WALLET_EDITOR_PLAYWRIGHT_SLOW_MO_MS=600."""
    raw = os.getenv(_WALLET_EDITOR_PLAYWRIGHT_SLOW_MO_ENV, "").strip()
    if not raw:
        return _DEFAULT_PLAYWRIGHT_SLOW_MO_MS
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        log.warning(
            "[WalletEditor] invalid %s=%r, using default=%s",
            _WALLET_EDITOR_PLAYWRIGHT_SLOW_MO_ENV,
            raw,
            _DEFAULT_PLAYWRIGHT_SLOW_MO_MS,
        )
        return _DEFAULT_PLAYWRIGHT_SLOW_MO_MS
    if value < 0:
        log.warning(
            "[WalletEditor] invalid %s=%s, using default=%s",
            _WALLET_EDITOR_PLAYWRIGHT_SLOW_MO_ENV,
            value,
            _DEFAULT_PLAYWRIGHT_SLOW_MO_MS,
        )
        return _DEFAULT_PLAYWRIGHT_SLOW_MO_MS
    return value


def wallet_editor_open_card_settle_ms() -> int:
    """
    Bounded settle wait after modal container becomes visible while card data loads.

    Default 500 ms. Set WALLET_EDITOR_OPEN_CARD_SETTLE_MS=0 to disable.
    Invalid values fall back to 500.
    """
    raw = os.getenv(_WALLET_EDITOR_OPEN_CARD_SETTLE_ENV, "").strip()
    if not raw:
        return _DEFAULT_OPEN_CARD_SETTLE_MS
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        log.warning(
            "[WalletEditor] invalid %s=%r, using default=%s",
            _WALLET_EDITOR_OPEN_CARD_SETTLE_ENV,
            raw,
            _DEFAULT_OPEN_CARD_SETTLE_MS,
        )
        return _DEFAULT_OPEN_CARD_SETTLE_MS
    if value < 0:
        log.warning(
            "[WalletEditor] invalid %s=%s, using default=%s",
            _WALLET_EDITOR_OPEN_CARD_SETTLE_ENV,
            value,
            _DEFAULT_OPEN_CARD_SETTLE_MS,
        )
        return _DEFAULT_OPEN_CARD_SETTLE_MS
    return value


def wallet_editor_row_match_timeout_ms() -> int:
    """
    Extra poll window for table row text to load after filter rows appear.

    Default 3000 ms. Set WALLET_EDITOR_ROW_MATCH_TIMEOUT_MS=0 for a single attempt only.
    Invalid values fall back to 3000.
    """
    raw = os.getenv(_WALLET_EDITOR_ROW_MATCH_TIMEOUT_ENV, "").strip()
    if not raw:
        return _DEFAULT_ROW_MATCH_TIMEOUT_MS
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        log.warning(
            "[WalletEditor] invalid %s=%r, using default=%s",
            _WALLET_EDITOR_ROW_MATCH_TIMEOUT_ENV,
            raw,
            _DEFAULT_ROW_MATCH_TIMEOUT_MS,
        )
        return _DEFAULT_ROW_MATCH_TIMEOUT_MS
    if value < 0:
        log.warning(
            "[WalletEditor] invalid %s=%s, using default=%s",
            _WALLET_EDITOR_ROW_MATCH_TIMEOUT_ENV,
            value,
            _DEFAULT_ROW_MATCH_TIMEOUT_MS,
        )
        return _DEFAULT_ROW_MATCH_TIMEOUT_MS
    return value


def wallet_editor_retryable_max_attempts() -> int:
    """
    Auto-enable retry limit for retryable technical FAIL rows.

    Default 3. Set WALLET_EDITOR_RETRYABLE_MAX_ATTEMPTS=0 to disable auto-pickup.
    Invalid values fall back to 3.
    """
    raw = os.getenv(_WALLET_EDITOR_RETRYABLE_MAX_ATTEMPTS_ENV, "").strip()
    if not raw:
        return _DEFAULT_RETRYABLE_MAX_ATTEMPTS
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        log.warning(
            "[WalletEditor] invalid %s=%r, using default=%s",
            _WALLET_EDITOR_RETRYABLE_MAX_ATTEMPTS_ENV,
            raw,
            _DEFAULT_RETRYABLE_MAX_ATTEMPTS,
        )
        return _DEFAULT_RETRYABLE_MAX_ATTEMPTS
    if value < 0:
        log.warning(
            "[WalletEditor] invalid %s=%s, using default=%s",
            _WALLET_EDITOR_RETRYABLE_MAX_ATTEMPTS_ENV,
            value,
            _DEFAULT_RETRYABLE_MAX_ATTEMPTS,
        )
        return _DEFAULT_RETRYABLE_MAX_ATTEMPTS
    return value


def sanitize_input_name(file_name: str, *, max_len: int = MAX_INPUT_NAME_LEN) -> str:
    name = (file_name or "").strip()
    name = os.path.basename(name.replace("\\", "/"))
    if name.lower().endswith(".xlsx"):
        name = name[:-5]
    name = name.replace(" ", "_")
    name = _INPUT_NAME_SAFE_RE.sub("_", name)
    name = re.sub(r"_+", "_", name).strip("._")
    if not name or name in {".", ".."}:
        name = "input"
    if len(name) > max_len:
        name = name[:max_len].rstrip("._") or "input"
    return name


def build_wallet_editor_result_path(
    source_file_name: str,
    operator_profile: str,
    *,
    base_dir: str = WALLET_EDITOR_RESULT_DIR,
) -> str:
    input_name = sanitize_input_name(source_file_name)
    operator = normalize_profile_key(operator_profile) or "UNKNOWN"
    file_name = f"{RESULT_NAME_PREFIX}{input_name}_{operator}.xlsx"
    directory = Path(base_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / file_name

    if not path.exists():
        return str(path)

    stem = path.stem
    for suffix in range(2, 100):
        candidate = directory / f"{stem}_{suffix}.xlsx"
        if not candidate.exists():
            return str(candidate)

    from uuid import uuid4

    return str(directory / f"{stem}_{uuid4().hex[:8]}.xlsx")


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
    source_file_name: str
    login: str
    password: str
    auth_state_path: str
    run_id: str = field(default_factory=lambda: uuid4().hex)
    queued_at: float = field(default_factory=time.perf_counter)


@dataclass(frozen=True)
class WalletEditorAddWalletTask:
    file_path: str
    original_filename: str
    operator_profile: str
    chat_id: int
    user_id: int
    login: str
    password: str
    auth_state_path: str
    dry_run: bool = False
    created_at: float = field(default_factory=time.perf_counter)
    queued_at: float = field(default_factory=time.perf_counter)


def wallet_editor_add_wallet_dry_run_enabled() -> bool:
    raw = os.getenv(_WALLET_EDITOR_ADD_WALLET_DRY_RUN_ENV, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def build_add_wallet_result_path(
    source_file_name: str,
    operator_profile: str,
    *,
    base_dir: str = WALLET_EDITOR_RESULT_DIR,
) -> str:
    input_name = sanitize_input_name(source_file_name)
    operator = normalize_profile_key(operator_profile) or "UNKNOWN"
    file_name = f"{ADD_WALLET_RESULT_PREFIX}{input_name}_{operator}.xlsx"
    directory = Path(base_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / file_name

    if not path.exists():
        return str(path)

    stem = path.stem
    for suffix in range(2, 100):
        candidate = directory / f"{stem}_{suffix}.xlsx"
        if not candidate.exists():
            return str(candidate)

    return str(directory / f"{stem}_{uuid4().hex[:8]}.xlsx")


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
    result_file_path: str | None = None
    operator_profile: str | None = None


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
