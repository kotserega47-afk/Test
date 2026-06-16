"""Bakai rate state persistence under STATE_DIR/bakai/."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from integrations import bakai_monitor_playwright as bakai


@pytest.fixture
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "state"
    monkeypatch.setenv("STATE_DIR", str(root))
    return root


def _mock_playwright_scrape(
    monkeypatch: pytest.MonkeyPatch, buy_rate: float,
) -> None:
    monkeypatch.setattr(bakai, "_in_time_window", lambda: True)
    mock_page = MagicMock()
    mock_context = MagicMock()
    mock_browser = MagicMock()
    mock_p = MagicMock()
    monkeypatch.setattr(
        bakai,
        "sync_playwright",
        MagicMock(return_value=MagicMock(__enter__=MagicMock(return_value=mock_p))),
    )
    mock_p.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page
    monkeypatch.setattr(bakai, "close_playwright_stack", lambda **_kwargs: None)
    monkeypatch.setattr(
        bakai, "_scrape_rub_buy_rate_from_page", lambda _page: buy_rate,
    )


def test_bakai_state_path_uses_state_dir(state_dir: Path) -> None:
    path = bakai._last_rate_file_path()
    assert path == state_dir / "bakai" / "last_buy_rate.txt"
    assert "/tmp" not in str(path)


def test_bakai_first_run_saves_rate_to_state_dir(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_playwright_scrape(monkeypatch, 1.145)
    monkeypatch.setenv("CURRENT_RATE_BAKAI_CHAT_ID", "-100-current")

    with patch.object(bakai, "_send_to_current_route") as send_current:
        with patch.object(bakai, "_send_to_alert_route") as send_alert:
            bakai.check_bakai_rate()

    rate_file = state_dir / "bakai" / "last_buy_rate.txt"
    assert rate_file.is_file()
    assert rate_file.read_text(encoding="utf-8") == "1.145"
    send_current.assert_called_once_with(
        "💱 Текущий курс покупки RUB: 1.145",
        override_chat_id=None,
    )
    send_alert.assert_not_called()


def test_bakai_changed_rate_updates_state_dir(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bakai_dir = state_dir / "bakai"
    bakai_dir.mkdir(parents=True)
    (bakai_dir / "last_buy_rate.txt").write_text("1.100", encoding="utf-8")

    _mock_playwright_scrape(monkeypatch, 1.145)
    monkeypatch.setenv("NEW_RATE_BAKAI_CHAT_ID", "-100-alert")

    with patch.object(bakai, "_send_to_current_route") as send_current:
        with patch.object(bakai, "_send_to_alert_route") as send_alert:
            bakai.check_bakai_rate()

    assert (bakai_dir / "last_buy_rate.txt").read_text(encoding="utf-8") == "1.145"
    send_alert.assert_called_once()
    alert_msg = send_alert.call_args[0][0]
    assert "⚡ *Внимание!* Новый курс покупки RUB: 1.145" in alert_msg
    assert "1.1" in alert_msg
    send_current.assert_not_called()


def test_bakai_unchanged_rate_keeps_existing_behavior(
    state_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bakai_dir = state_dir / "bakai"
    bakai_dir.mkdir(parents=True)
    (bakai_dir / "last_buy_rate.txt").write_text("1.145", encoding="utf-8")

    _mock_playwright_scrape(monkeypatch, 1.145)
    monkeypatch.setenv("CURRENT_RATE_BAKAI_CHAT_ID", "-100-current")

    with patch.object(bakai, "_send_to_current_route") as send_current:
        with patch.object(bakai, "_send_to_alert_route") as send_alert:
            bakai.check_bakai_rate()

    assert (bakai_dir / "last_buy_rate.txt").read_text(encoding="utf-8") == "1.145"
    send_current.assert_called_once_with(
        "💤 Курс без изменений: 1.145",
        override_chat_id=None,
    )
    send_alert.assert_not_called()
