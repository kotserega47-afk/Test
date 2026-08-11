#!/usr/bin/env python3
"""Local visible Add Wallet runner (no Telegram polling).

Usage (from repo root, project venv):

  .venv\\Scripts\\python.exe tools\\run_add_wallet_visible.py ^
    --file .local\\wallet_editor\\input\\sample.xlsx ^
    --profile DENIS ^
    --slow-mo 500 ^
    --stop-before-save

Real save (only after explicit confirmation): omit --stop-before-save
and pass --confirm-save.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Repo root on sys.path when launched as tools/….py
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env")

from automation.add_wallet_contract import prepare_add_wallet_batch  # noqa: E402
from automation.add_wallet_engine import run as run_add_wallet  # noqa: E402
from automation.runtime import (  # noqa: E402
    RunConfig,
    build_add_wallet_result_path,
    normalize_profile_key,
    operator_auth_state_path,
    operator_login_env,
    operator_password_env,
    wallet_editor_antares_login,
    wallet_editor_antares_password,
    wallet_editor_auth_state_path,
)

LOCAL_WE_DIR = _REPO_ROOT / ".local" / "wallet_editor"
LOCAL_INPUT_DIR = LOCAL_WE_DIR / "input"
LOCAL_RESULTS_DIR = LOCAL_WE_DIR / "results"
LOCAL_AUTH_DIR = LOCAL_WE_DIR / "auth_state"


def _resolve_profile_credentials(profile: str | None) -> tuple[str, str, str, str]:
    """Return (profile_label, login, password, auth_state_path). Never print secrets."""
    if profile:
        key = normalize_profile_key(profile)
        if key is None:
            raise SystemExit(
                f"Некорректный --profile={profile!r}. Ожидается A-Z / 0-9 / _."
            )
        login = os.getenv(operator_login_env(key), "").strip()
        password = os.getenv(operator_password_env(key), "").strip()
        if not login or not password:
            raise SystemExit(
                f"Нет credentials для профиля {key}: "
                f"задайте {operator_login_env(key)} и {operator_password_env(key)} "
                f"(например в .env). Значения в консоль не выводятся."
            )
        auth_candidates = [
            Path(operator_auth_state_path(key)),
            LOCAL_AUTH_DIR / f"auth_state_wallet_editor_{key}.json",
        ]
        auth_path = next((str(p) for p in auth_candidates if p.exists()), str(auth_candidates[0]))
        return key, login, password, auth_path

    login = wallet_editor_antares_login()
    password = wallet_editor_antares_password()
    if not login or not password:
        raise SystemExit(
            "Не задан --profile и нет WALLET_EDITOR_ANTARES_LOGIN / "
            "WALLET_EDITOR_ANTARES_PASSWORD. Укажите --profile или env."
        )
    auth_path = wallet_editor_auth_state_path()
    local_default = LOCAL_AUTH_DIR / "auth_state_wallet_editor.json"
    if not Path(auth_path).exists() and local_default.exists():
        auth_path = str(local_default)
    return "DEFAULT", login, password, auth_path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Visible local Wallet Editor → Add Wallet (no Telegram).",
    )
    p.add_argument(
        "--file",
        required=True,
        help="Path to Add Wallet Excel (.xlsx)",
    )
    p.add_argument(
        "--profile",
        default=None,
        help="Operator profile key (WALLET_EDITOR_OPERATOR_<PROFILE>_*)",
    )
    p.add_argument(
        "--slow-mo",
        type=int,
        default=500,
        help="Playwright slow_mo in ms (default: 500)",
    )
    p.add_argument(
        "--stop-before-save",
        action="store_true",
        help="Fill the first form and stop before Save (safe observation mode)",
    )
    p.add_argument(
        "--confirm-save",
        action="store_true",
        help="Required together with real save (when --stop-before-save is off)",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive 'continue?' prompt after summary",
    )
    p.add_argument(
        "--pause-ms",
        type=int,
        default=None,
        help=(
            "After stop-before-save: wait N ms then close browser "
            "(default: wait for Enter in console)"
        ),
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    file_path = Path(args.file).expanduser().resolve()
    if not file_path.is_file():
        raise SystemExit(f"Excel не найден: {file_path}")

    if args.slow_mo < 0:
        raise SystemExit("--slow-mo must be >= 0")

    if not args.stop_before_save and not args.confirm_save:
        raise SystemExit(
            "Для реального сохранения нужны оба условия:\n"
            "  1) без --stop-before-save\n"
            "  2) с --confirm-save\n"
            "Сейчас запуск заблокирован. Для наблюдения используйте --stop-before-save."
        )

    LOCAL_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_AUTH_DIR.mkdir(parents=True, exist_ok=True)

    profile_label, login, password, auth_state_path = _resolve_profile_credentials(args.profile)

    # Preview batch without launching browser (dry_run flag only affects result labels).
    batch = prepare_add_wallet_batch(str(file_path), dry_run=False)
    valid_n = len(batch.rows)
    invalid_n = len(batch.invalid_rows)

    result_path = build_add_wallet_result_path(
        file_path.name,
        profile_label,
        base_dir=str(LOCAL_RESULTS_DIR),
    )

    print("=== Add Wallet visible run (preview) ===", flush=True)
    print(f"Excel:              {file_path}", flush=True)
    print(f"Valid rows:         {valid_n}", flush=True)
    print(f"Invalid/skip rows:  {invalid_n}", flush=True)
    print(f"Profile:            {profile_label}", flush=True)
    print(f"Auth state exists:  {Path(auth_state_path).exists()}", flush=True)
    print(f"Auth state path:    {auth_state_path}", flush=True)
    print(f"Login set:          {'yes' if login else 'no'}", flush=True)
    print(f"Password set:       {'yes' if password else 'no'}", flush=True)
    print(f"headless:           False", flush=True)
    print(f"slow_mo_ms:         {args.slow_mo}", flush=True)
    print(f"stop_before_save:   {args.stop_before_save}", flush=True)
    print(f"confirm_save:       {args.confirm_save}", flush=True)
    print(f"Result Excel:       {result_path}", flush=True)
    print("Telegram:            not started (local CLI only)", flush=True)
    print("========================================", flush=True)

    if valid_n == 0 and invalid_n == 0:
        raise SystemExit("В Excel нет строк для обработки.")

    if not args.yes:
        answer = input("Продолжить? [y/N]: ").strip().lower()
        if answer not in {"y", "yes", "д", "да"}:
            print("Отменено.")
            return 1

    pause_ms = None
    if args.stop_before_save:
        pause_ms = args.pause_ms  # None → interactive Enter

    cfg = RunConfig(
        headless=False,
        dry_run=False,
        stop_before_save=bool(args.stop_before_save),
        slow_mo_ms=int(args.slow_mo),
        stop_before_save_pause_ms=pause_ms,
        login=login,
        password=password,
        auth_state_path=auth_state_path,
        operator_profile=profile_label,
        result_file_path=result_path,
    )

    out_path, summary = run_add_wallet(
        str(file_path),
        cfg,
        result_file_path=result_path,
    )
    print("=== Done ===")
    print(summary.telegram_summary())
    print(f"Result Excel: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
