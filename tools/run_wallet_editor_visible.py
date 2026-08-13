#!/usr/bin/env python3
"""Local visible Wallet Editor (disable / delete / set_aggregate) runner — no Telegram polling.

Safe delete observation (opens form, stops before «Удалить»):

  .venv\\Scripts\\python.exe tools\\run_wallet_editor_visible.py ^
    --file .local\\wallet_editor\\input\\delete_sample.xlsx ^
    --profile WALTER ^
    --slow-mo 500 ^
    --stop-before-delete

Safe set_aggregate observation (switch + fill, no Save):

  .venv\\Scripts\\python.exe tools\\run_wallet_editor_visible.py ^
    --file .local\\wallet_editor\\input\\set_aggregate_sample.xlsx ^
    --profile WALTER ^
    --slow-mo 500 ^
    --stop-before-save

Real save for set_aggregate (only after explicit confirmation):

  .venv\\Scripts\\python.exe tools\\run_wallet_editor_visible.py ^
    --file .local\\wallet_editor\\input\\set_aggregate_sample.xlsx ^
    --profile WALTER ^
    --confirm-save

Real delete (only after explicit confirmation):

  .venv\\Scripts\\python.exe tools\\run_wallet_editor_visible.py ^
    --file .local\\wallet_editor\\input\\delete_sample.xlsx ^
    --profile WALTER ^
    --confirm-delete

Credentials are never printed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(_REPO_ROOT / ".env")

from automation.engine import (  # noqa: E402
    RESULT_STOP_BEFORE_DELETE,
    RESULT_STOP_BEFORE_SAVE,
    _prepare_df,
    run as run_wallet_editor,
)
from automation.runtime import (  # noqa: E402
    RunConfig,
    build_wallet_editor_result_path,
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
        auth_path = next(
            (str(p) for p in auth_candidates if p.exists()),
            str(auth_candidates[0]),
        )
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
        description="Visible local Wallet Editor disable/delete/set_aggregate (no Telegram).",
    )
    p.add_argument("--file", required=True, help="Path to Excel (.xlsx) with card+action")
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
        "--stop-before-delete",
        action="store_true",
        help="Open wallet form and stop before clicking «Удалить» (safe mode)",
    )
    p.add_argument(
        "--confirm-delete",
        action="store_true",
        help="Required for real delete when Excel contains action=delete",
    )
    p.add_argument(
        "--stop-before-save",
        action="store_true",
        help=(
            "For set_aggregate: switch aggregate and fill fields, "
            "but do not click «Сохранить» (safe mode)"
        ),
    )
    p.add_argument(
        "--confirm-save",
        action="store_true",
        help="Required for real Save when Excel contains action=set_aggregate",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Search only for delete/set_aggregate rows; never mutate",
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
            "After stop-before-delete / stop-before-save: wait N ms then close browser "
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

    LOCAL_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_AUTH_DIR.mkdir(parents=True, exist_ok=True)

    df = _prepare_df(str(file_path))
    has_delete = (df["action"] == "delete").any()
    has_set_aggregate = (df["action"] == "set_aggregate").any()

    if has_delete and not args.dry_run:
        if not args.stop_before_delete and not args.confirm_delete:
            raise SystemExit(
                "В Excel есть action=delete. Для реального удаления нужны:\n"
                "  1) без --stop-before-delete\n"
                "  2) с --confirm-delete\n"
                "Сейчас запуск заблокирован. Для наблюдения используйте "
                "--stop-before-delete (или --dry-run)."
            )
        if args.stop_before_delete and args.confirm_delete:
            raise SystemExit(
                "Нельзя одновременно --stop-before-delete и --confirm-delete."
            )

    if has_set_aggregate and not args.dry_run:
        if not args.stop_before_save and not args.confirm_save:
            raise SystemExit(
                "В Excel есть action=set_aggregate. Для реального Save нужны:\n"
                "  1) без --stop-before-save\n"
                "  2) с --confirm-save\n"
                "Сейчас запуск заблокирован. Для наблюдения используйте "
                "--stop-before-save (или --dry-run)."
            )
        if args.stop_before_save and args.confirm_save:
            raise SystemExit(
                "Нельзя одновременно --stop-before-save и --confirm-save."
            )

    profile_label, login, password, auth_state_path = _resolve_profile_credentials(
        args.profile
    )

    result_path = build_wallet_editor_result_path(
        file_path.name,
        profile_label,
        base_dir=str(LOCAL_RESULTS_DIR),
    )

    print("=== Wallet Editor visible run (preview) ===", flush=True)
    print(f"Excel:              {file_path}", flush=True)
    print(f"Rows:               {len(df)}", flush=True)
    print(f"Has delete:         {bool(has_delete)}", flush=True)
    print(f"Has set_aggregate:  {bool(has_set_aggregate)}", flush=True)
    print(f"Profile:            {profile_label}", flush=True)
    print(f"Auth state exists:  {Path(auth_state_path).exists()}", flush=True)
    print(f"Auth state path:    {auth_state_path}", flush=True)
    print(f"Login set:          {'yes' if login else 'no'}", flush=True)
    print(f"Password set:       {'yes' if password else 'no'}", flush=True)
    print(f"headless:           False", flush=True)
    print(f"slow_mo_ms:         {args.slow_mo}", flush=True)
    print(f"dry_run:            {args.dry_run}", flush=True)
    print(f"stop_before_delete: {args.stop_before_delete}", flush=True)
    print(f"confirm_delete:     {args.confirm_delete}", flush=True)
    print(f"stop_before_save:   {args.stop_before_save}", flush=True)
    print(f"confirm_save:       {args.confirm_save}", flush=True)
    print(f"Result Excel:       {result_path}", flush=True)
    print("Telegram:            not started (local CLI only)", flush=True)
    print("============================================", flush=True)

    if len(df) == 0:
        raise SystemExit("В Excel нет строк для обработки.")

    if not args.yes:
        answer = input("Продолжить? [y/N]: ").strip().lower()
        if answer not in {"y", "yes", "д", "да"}:
            print("Отменено.")
            return 1

    pause_ms = None
    if args.stop_before_delete or args.stop_before_save:
        pause_ms = args.pause_ms

    cfg = RunConfig(
        headless=False,
        dry_run=bool(args.dry_run),
        stop_before_delete=bool(args.stop_before_delete),
        stop_before_save=bool(args.stop_before_save),
        slow_mo_ms=int(args.slow_mo),
        stop_before_save_pause_ms=pause_ms,
        login=login,
        password=password,
        auth_state_path=auth_state_path,
        operator_profile=profile_label,
        result_file_path=result_path,
    )

    out_path, stats = run_wallet_editor(str(file_path), cfg)
    print("=== Done ===")
    print(stats.summary())
    print(f"Result Excel: {out_path}")
    if args.stop_before_delete:
        print(f"(safe mode result code: {RESULT_STOP_BEFORE_DELETE})")
    if args.stop_before_save:
        print(f"(safe mode result code: {RESULT_STOP_BEFORE_SAVE})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
