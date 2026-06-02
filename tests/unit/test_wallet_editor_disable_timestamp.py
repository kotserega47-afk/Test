"""Wallet Editor result column «Дата отключения» uses MSK datetime."""
from __future__ import annotations

import re
from datetime import datetime
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from automation.engine import run
from automation.runtime import RunConfig
from core.datetime_utils import EXCEL_DATETIME_FORMAT

MSK = ZoneInfo("Europe/Moscow")
FIXED_NOW = datetime(2026, 6, 3, 0, 9, 15, tzinfo=MSK)
EXPECTED_TIMESTAMP = "03.06.2026 00:09:15"
DATETIME_PATTERN = re.compile(r"^\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}$")


@pytest.fixture
def mock_wallet_editor_playwright(monkeypatch):
    page = MagicMock()
    context = MagicMock()
    browser = MagicMock()
    browser.new_context.return_value = context
    context.new_page.return_value = page

    playwright = MagicMock()
    playwright.chromium.launch.return_value = browser

    context_manager = MagicMock()
    context_manager.__enter__ = MagicMock(return_value=playwright)
    context_manager.__exit__ = MagicMock(return_value=False)
    monkeypatch.setattr("automation.engine.sync_playwright", lambda: context_manager)
    monkeypatch.setattr("automation.engine._ensure_logged_in", MagicMock())
    monkeypatch.setattr("automation.engine.open_card", MagicMock())
    monkeypatch.setattr("automation.engine.ensure_partner_removed", MagicMock(return_value="removed"))
    monkeypatch.setattr("automation.engine.retry", lambda fn, *args, **kwargs: fn())
    monkeypatch.setattr(
        "automation.engine._apply_auto_no_partners_status_after_actions",
        MagicMock(return_value=False),
    )
    monkeypatch.setattr("automation.engine.save", MagicMock(return_value="saved"))


def _write_input_xlsx(path: str) -> None:
    pd.DataFrame(
        {"card": ["4111111111111111"], "action": ["remove_partner"], "value": ["Ostin"]}
    ).to_excel(path, index=False)


def _read_disable_timestamp(result_path: str) -> str:
    value = pd.read_excel(result_path).iloc[0]["Дата отключения"]
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.strftime(EXCEL_DATETIME_FORMAT)
        return value.astimezone(MSK).strftime(EXCEL_DATETIME_FORMAT)
    return str(value).strip()


def test_disable_timestamp_uses_msk_datetime(tmp_path, monkeypatch, mock_wallet_editor_playwright):
    monkeypatch.setattr("automation.engine.now_msk", lambda: FIXED_NOW)

    input_path = tmp_path / "input.xlsx"
    result_path = tmp_path / "result.xlsx"
    _write_input_xlsx(str(input_path))

    run(
        str(input_path),
        RunConfig(
            login="test-login",
            password="test-password",
            result_file_path=str(result_path),
        ),
    )

    assert _read_disable_timestamp(str(result_path)) == EXPECTED_TIMESTAMP


def test_disable_timestamp_format(tmp_path, monkeypatch, mock_wallet_editor_playwright):
    monkeypatch.setattr("automation.engine.now_msk", lambda: FIXED_NOW)

    input_path = tmp_path / "input.xlsx"
    result_path = tmp_path / "result.xlsx"
    _write_input_xlsx(str(input_path))

    run(
        str(input_path),
        RunConfig(
            login="test-login",
            password="test-password",
            result_file_path=str(result_path),
        ),
    )

    timestamp = _read_disable_timestamp(str(result_path))
    assert DATETIME_PATTERN.match(timestamp)
    assert timestamp == EXPECTED_TIMESTAMP


def test_remove_partner_still_writes_result_excel(tmp_path, monkeypatch, mock_wallet_editor_playwright):
    monkeypatch.setattr("automation.engine.now_msk", lambda: FIXED_NOW)

    input_path = tmp_path / "input.xlsx"
    result_path = tmp_path / "result.xlsx"
    _write_input_xlsx(str(input_path))

    _, stats = run(
        str(input_path),
        RunConfig(
            login="test-login",
            password="test-password",
            result_file_path=str(result_path),
        ),
    )

    assert result_path.exists()
    df = pd.read_excel(result_path)
    assert df.iloc[0]["action"] == "remove_partner"
    assert df.iloc[0]["status"] == "OK"
    assert "removed" in str(df.iloc[0]["comment"])
    assert stats.ok >= 1
