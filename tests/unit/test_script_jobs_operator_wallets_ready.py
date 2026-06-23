from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

import integrations.script_jobs  # noqa: F401 — bootstrap JOB_REGISTRY
from core.config_manager import ALLOWED_JOB_PARAMS
from core.job_runner import Actor, JOB_REGISTRY
from core.rules_v2.constants import ALLOWED_JOB_PARAMS as RULES_V2_JOB_PARAMS
from integrations.script_jobs import SCRIPT_REGISTRY
from integrations.script_jobs.antares_wallets_export import format_login_failure_message
from integrations.script_jobs.scripts.operator_wallets_ready import (
    READY_STATUSES_NORM,
    apply_priority_partner_order,
    count_ready_wallets_by_partner,
    format_report_text,
    normalize_status,
    parse_priority_partners,
    run_operator_wallets_ready,
)
from integrations.script_jobs.types import ScriptExecutionContext
from integrations.tg_commands import cmd_operator_wallets_ready


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Готов к работе", "готов к работе"),
        ("готов к работе", "готов к работе"),
        ("  Активный   вход ", "активный вход"),
        ("Активный выход", "активный выход"),
        ("Не готов. Sim", "не готов. sim"),
    ],
)
def test_normalize_status(raw: str, expected: str):
    assert normalize_status(raw) == expected


def test_unknown_status_not_in_ready_set():
    assert normalize_status("Тест") not in READY_STATUSES_NORM


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Карта": ["111", "111", "222", "333", "444"],
            "Партнёр": ["Beta", "Beta", "Alpha", "Alpha", "Gamma"],
            "Статус": [
                "Готов к работе",
                "Активный вход",
                "активный выход",
                "Не готов. Sim",
                "Готов к работе",
            ],
        }
    )


def test_partner_grouping_and_sort():
    rows = count_ready_wallets_by_partner(_sample_df())
    assert rows == [("Alpha", 1), ("Beta", 1), ("Gamma", 1)]


def test_empty_result_text():
    text = format_report_text([], total=0, truncated=False, shown_count=0)
    assert "Нет кошельков" in text
    assert "Итого: 0" in text


@patch("integrations.script_jobs.scripts.operator_wallets_ready.build_report_xlsx")
@patch("integrations.script_jobs.scripts.operator_wallets_ready.download_antares_wallets_export")
def test_long_result_truncates_text_and_attaches_file(mock_download, mock_xlsx):
    mock_download.return_value = "ignored.xlsx"
    mock_xlsx.return_value = "/tmp/full.xlsx"

    many_rows = [(f"Partner-{i:03d}", i + 1) for i in range(40)]
    with patch(
        "integrations.script_jobs.scripts.operator_wallets_ready.pd.read_excel",
        return_value=pd.DataFrame({"Карта": ["1"], "Партнёр": ["A"], "Статус": ["Готов к работе"]}),
    ):
        with patch(
            "integrations.script_jobs.scripts.operator_wallets_ready.count_ready_wallets_by_partner",
            return_value=many_rows,
        ):
            with patch(
                "integrations.script_jobs.scripts.operator_wallets_ready.get_job_params",
                return_value={"text_limit_chars": 200, "top_n": 3},
            ):
                ctx = ScriptExecutionContext(
                    actor=Actor(kind="tg", chat_id=1, user_id=2),
                    script_key="operator_wallets_ready",
                    job_type="script_job:operator_wallets_ready",
                    source="manual",
                    chat_id=1,
                )
                result = run_operator_wallets_ready(ctx)

    assert result.status == "ok"
    assert "топ-3" in result.text
    assert len(result.files) == 1
    assert result.files[0].endswith(".xlsx")
    mock_xlsx.assert_called_once()


@patch("integrations.script_jobs.scripts.operator_wallets_ready.download_antares_wallets_export")
def test_run_failure_includes_exception_class_and_message(mock_download):
    mock_download.side_effect = RuntimeError("Antares login failed")
    ctx = ScriptExecutionContext(
        actor=Actor(kind="tg", chat_id=9),
        script_key="operator_wallets_ready",
        job_type="script_job:operator_wallets_ready",
        source="manual",
        chat_id=9,
    )
    with patch(
        "integrations.script_jobs.scripts.operator_wallets_ready.get_job_params",
        return_value={},
    ):
        result = run_operator_wallets_ready(ctx)
    assert result.status == "failed"
    assert result.text == "❌ Не удалось сформировать отчёт: RuntimeError: Antares login failed"
    assert "RuntimeError" in result.text
    assert "Antares login failed" in result.text


def test_registry_contains_operator_wallets_ready():
    assert "operator_wallets_ready" in SCRIPT_REGISTRY
    assert SCRIPT_REGISTRY["operator_wallets_ready"].command_name == "operator_wallets_ready"
    assert "script_job:operator_wallets_ready" in JOB_REGISTRY


def test_job_params_whitelist_synced():
    keys = {
        "enabled",
        "telegram_route_report",
        "telegram_route_alert",
        "text_limit_chars",
        "top_n",
        "priority_partners",
    }
    assert "script_job:operator_wallets_ready" in ALLOWED_JOB_PARAMS
    assert set(ALLOWED_JOB_PARAMS["script_job:operator_wallets_ready"]) == keys
    assert "script_job:operator_wallets_ready" in RULES_V2_JOB_PARAMS
    assert set(RULES_V2_JOB_PARAMS["script_job:operator_wallets_ready"]) == keys


def test_operator_wallets_ready_command_is_guarded():
    update = AsyncMock()
    update.effective_chat.id = 1
    update.effective_user.id = 2
    context = AsyncMock()

    async def run() -> None:
        with patch("integrations.tg_commands._guard_or_deny", new_callable=AsyncMock) as guard:
            guard.return_value = False
            await cmd_operator_wallets_ready(update, context)
            guard.assert_awaited_once_with(update, "operator_wallets_ready")

    asyncio.run(run())


def test_operator_wallets_ready_dispatches_script_job():
    update = AsyncMock()
    update.effective_chat.id = 1
    update.effective_user.id = 2
    context = AsyncMock()

    async def run() -> None:
        with patch("integrations.tg_commands._guard_or_deny", new_callable=AsyncMock) as guard:
            with patch("integrations.tg_commands._run_job_async", new_callable=AsyncMock) as run_job:
                guard.return_value = True
                await cmd_operator_wallets_ready(update, context)
                run_job.assert_awaited_once_with(update, "script_job:operator_wallets_ready")

    asyncio.run(run())


def test_format_login_failure_message_includes_diagnostics_paths():
    message = format_login_failure_message(
        url="https://antares.plus/lkcard/#/login",
        login_env_set=True,
        password_env_set=True,
        page_hint="title=Antares",
        screenshot="/tmp/script_jobs/operator_wallets_ready/debug/login_failed_20260101_120000.png",
        html="/tmp/script_jobs/operator_wallets_ready/debug/login_failed_20260101_120000.html",
        reason="export button not visible after login",
    )
    assert "Antares login failed" in message
    assert "url=https://antares.plus/lkcard/#/login" in message
    assert "login_env_set=True" in message
    assert "password_env_set=True" in message
    assert "screenshot=/tmp/script_jobs/operator_wallets_ready/debug/login_failed" in message
    assert "html=/tmp/script_jobs/operator_wallets_ready/debug/login_failed" in message
    assert "reason=export button not visible after login" in message


def test_format_login_failure_message_never_includes_credential_values():
    secret_login = "operator@example.com"
    secret_password = "super-secret-password"
    message = format_login_failure_message(
        url="https://antares.plus/lkcard/#/login",
        login_env_set=bool(secret_login),
        password_env_set=bool(secret_password),
        page_hint="title=Login",
        reason="export button not visible after login",
    )
    assert secret_login not in message
    assert secret_password not in message
    assert "login_env_set=True" in message
    assert "password_env_set=True" in message


def test_parse_priority_partners_multiline_value():
    raw = "Амобайл Юмани\nКибит Юмани\n\n  Аврора (Юмани карты)  \n"
    assert parse_priority_partners(raw) == [
        "Амобайл Юмани",
        "Кибит Юмани",
        "Аврора (Юмани карты)",
    ]


def test_parse_priority_partners_empty_and_missing():
    assert parse_priority_partners(None) == []
    assert parse_priority_partners("") == []
    assert parse_priority_partners("\n  \n") == []


def test_parse_priority_partners_deduplicates_safely():
    raw = "Амобайл Юмани\nКибит Юмани\nАмобайл Юмани\n"
    assert parse_priority_partners(raw) == ["Амобайл Юмани", "Кибит Юмани"]


def test_apply_priority_partner_order_preserves_rules_order():
    rows = [
        ("ЧБР Тбанк c2c", 10),
        ("Gamma", 5),
        ("Амобайл Юмани", 3),
        ("Кибит Юмани", 7),
        ("Аврора (Юмани карты)", 2),
    ]
    priority = ["Амобайл Юмани", "Кибит Юмани", "Аврора (Юмани карты)"]
    ordered = apply_priority_partner_order(rows, priority)
    assert [partner for partner, _ in ordered] == [
        "Амобайл Юмани",
        "Кибит Юмани",
        "Аврора (Юмани карты)",
        "ЧБР Тбанк c2c",
        "Gamma",
    ]


def test_apply_priority_partner_order_ignores_missing_partners():
    rows = [("Alpha", 4), ("Beta", 2)]
    priority = ["Missing Partner", "Alpha", "Also Missing"]
    ordered = apply_priority_partner_order(rows, priority)
    assert ordered == [("Alpha", 4), ("Beta", 2)]


def test_apply_priority_partner_order_no_config_keeps_default_sort():
    rows = [("Alpha", 1), ("Beta", 1), ("Gamma", 1)]
    assert apply_priority_partner_order(rows, []) == rows


def test_apply_priority_partner_order_fallback_sort_for_remaining():
    rows = [("Alpha", 9), ("Bravo", 9), ("Zulu", 9), ("Mike", 3)]
    priority = ["Mike"]
    ordered = apply_priority_partner_order(rows, priority)
    assert ordered == [("Mike", 3), ("Alpha", 9), ("Bravo", 9), ("Zulu", 9)]
