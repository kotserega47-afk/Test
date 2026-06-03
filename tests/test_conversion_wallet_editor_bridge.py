"""Tests for Conversion → Wallet Editor bridge."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from automation.engine import _prepare_df
from automation.runtime import WalletEditorTask
from integrations.conversion_wallet_editor_bridge import (
    ACTION_REMOVE_PARTNER,
    CONVERSION_WE_TELEGRAM_USER_ID,
    ENV_CHAT_ID,
    ENV_LOGIN,
    ENV_PASSWORD,
    INFO_MESSAGE_TEMPLATE,
    OPERATOR_PROFILE,
    build_wallet_editor_excel,
    compute_bridge_stats,
    map_problem_cards_to_wallet_editor_rows,
    maybe_enqueue_wallet_editor_from_problem_cards,
    resolve_conversion_we_config,
)


def _problem_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _conversion_we_env(*, chat_id: str = "-100123", login: str = "we-login", password: str = "we-pass") -> dict[str, str]:
    return {
        ENV_CHAT_ID: chat_id,
        ENV_LOGIN: login,
        ENV_PASSWORD: password,
    }


class TestConversionWERowsMapper:
    def test_conversion_we_rows_mapper_empty(self):
        assert map_problem_cards_to_wallet_editor_rows(pd.DataFrame()) == []
        assert map_problem_cards_to_wallet_editor_rows(_problem_df([])) == []

    def test_conversion_we_rows_mapper_filters_invalid(self):
        problem = _problem_df(
            [
                {"card": "", "original_partner": "Ostin"},
                {"card": "123", "original_partner": ""},
                {"card": None, "original_partner": "Teon"},
                {"card": "456", "original_partner": "Valid"},
            ]
        )
        rows = map_problem_cards_to_wallet_editor_rows(problem)
        assert len(rows) == 1
        assert rows[0]["card"] == "456"
        assert rows[0]["value"] == "Valid"

    def test_conversion_we_rows_mapper_builds_contract(self):
        problem = _problem_df(
            [
                {
                    "card": "1234567890",
                    "original_partner": "Ostin",
                    "partner": "ostin",
                    "max_consecutive_errors": 3,
                    "threshold": 3,
                    "status": "Ошибка",
                },
                {
                    "card": "9876543210",
                    "original_partner": "Teon",
                    "partner": "teon",
                },
            ]
        )
        rows = map_problem_cards_to_wallet_editor_rows(problem)
        assert rows == [
            {"card": "1234567890", "action": ACTION_REMOVE_PARTNER, "value": "Ostin"},
            {"card": "9876543210", "action": ACTION_REMOVE_PARTNER, "value": "Teon"},
        ]


class TestConversionWEBridgeStats:
    def test_compute_bridge_stats_all_valid(self):
        stats = compute_bridge_stats(25, 25)
        assert stats.found_cards == 25
        assert stats.valid_cards == 25
        assert stats.sent_cards == 25
        assert stats.filtered_invalid == 0

    def test_compute_bridge_stats_with_invalid(self):
        stats = compute_bridge_stats(30, 25)
        assert stats.found_cards == 30
        assert stats.valid_cards == 25
        assert stats.sent_cards == 25
        assert stats.filtered_invalid == 5


class TestConversionWEBridge:
    def test_bridge_sends_all_valid_cards(self, monkeypatch, tmp_path):
        for key, value in _conversion_we_env().items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(
            "integrations.conversion_wallet_editor_bridge.WALLET_EDITOR_INPUT_DIR",
            tmp_path,
        )

        problem = _problem_df(
            [{"card": f"c{i}", "original_partner": f"p{i}"} for i in range(25)]
        )

        with patch("integrations.conversion_wallet_editor_bridge.add_task", return_value=1) as add_task:
            with patch("integrations.conversion_wallet_editor_bridge.send_message_sync"):
                maybe_enqueue_wallet_editor_from_problem_cards(problem)

        add_task.assert_called_once()
        task = add_task.call_args.args[0]
        df = pd.read_excel(task.file_path)
        assert len(df) == 25

    def test_bridge_no_limit_skipped_cards(self, monkeypatch, tmp_path):
        for key, value in _conversion_we_env().items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(
            "integrations.conversion_wallet_editor_bridge.WALLET_EDITOR_INPUT_DIR",
            tmp_path,
        )

        problem = _problem_df(
            [{"card": f"c{i}", "original_partner": f"p{i}"} for i in range(37)]
        )
        captured_message: list[str] = []

        def _capture(text, chat_id=None, **_kwargs):
            captured_message.append(text)

        with patch("integrations.conversion_wallet_editor_bridge.add_task", return_value=1) as add_task:
            with patch(
                "integrations.conversion_wallet_editor_bridge.send_message_sync",
                side_effect=_capture,
            ):
                maybe_enqueue_wallet_editor_from_problem_cards(problem)

        task = add_task.call_args.args[0]
        df = pd.read_excel(task.file_path)
        assert len(df) == 37
        msg = captured_message[0]
        assert "Оставлено" not in msg
        assert "37" in msg
        stats = compute_bridge_stats(37, 37)
        assert stats.filtered_invalid == 0

    def test_bridge_still_filters_invalid_rows(self, monkeypatch, tmp_path):
        for key, value in _conversion_we_env().items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(
            "integrations.conversion_wallet_editor_bridge.WALLET_EDITOR_INPUT_DIR",
            tmp_path,
        )

        rows = [{"card": f"c{i}", "original_partner": f"p{i}"} for i in range(25)]
        rows.extend(
            [
                {"card": "", "original_partner": "X"},
                {"card": "bad", "original_partner": ""},
                {"card": None, "original_partner": "Y"},
                {"card": "ok", "original_partner": "Z"},
                {"card": "last", "original_partner": "P"},
            ]
        )
        problem = _problem_df(rows)

        with patch("integrations.conversion_wallet_editor_bridge.add_task", return_value=1) as add_task:
            with patch("integrations.conversion_wallet_editor_bridge.send_message_sync"):
                maybe_enqueue_wallet_editor_from_problem_cards(problem)

        task = add_task.call_args.args[0]
        df = pd.read_excel(task.file_path)
        assert len(df) == 27

    def test_bridge_info_message_reports_all_sent(self, monkeypatch, tmp_path):
        for key, value in _conversion_we_env().items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(
            "integrations.conversion_wallet_editor_bridge.WALLET_EDITOR_INPUT_DIR",
            tmp_path,
        )

        rows = [{"card": f"c{i}", "original_partner": f"p{i}"} for i in range(25)]
        rows.extend(
            [
                {"card": "", "original_partner": "X"},
                {"card": "x", "original_partner": ""},
                {"card": None, "original_partner": "Y"},
                {"card": "a", "original_partner": ""},
                {"card": "", "original_partner": "Z"},
            ]
        )
        problem = _problem_df(rows)
        captured_message: list[str] = []

        def _capture(text, chat_id=None, **_kwargs):
            captured_message.append(text)

        with patch("integrations.conversion_wallet_editor_bridge.add_task", return_value=1):
            with patch(
                "integrations.conversion_wallet_editor_bridge.send_message_sync",
                side_effect=_capture,
            ):
                maybe_enqueue_wallet_editor_from_problem_cards(problem)

        expected = INFO_MESSAGE_TEMPLATE.format(
            found_cards=30,
            valid_cards=25,
            sent_cards=25,
            filtered_invalid=5,
        )
        assert captured_message[0] == expected
        assert "Оставлено" not in captured_message[0]

    def test_conversion_we_bridge_skips_missing_env(self, monkeypatch):
        monkeypatch.delenv(ENV_CHAT_ID, raising=False)
        monkeypatch.delenv(ENV_LOGIN, raising=False)
        monkeypatch.delenv(ENV_PASSWORD, raising=False)

        with patch("integrations.conversion_wallet_editor_bridge.add_task") as add_task:
            maybe_enqueue_wallet_editor_from_problem_cards(
                _problem_df([{"card": "1", "original_partner": "Ostin"}])
            )

        add_task.assert_not_called()

    def test_conversion_we_bridge_enqueues_task(self, monkeypatch, tmp_path):
        for key, value in _conversion_we_env().items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(
            "integrations.conversion_wallet_editor_bridge.WALLET_EDITOR_INPUT_DIR",
            tmp_path,
        )

        with patch("integrations.conversion_wallet_editor_bridge.add_task", return_value=1) as add_task:
            with patch("integrations.conversion_wallet_editor_bridge.send_message_sync"):
                maybe_enqueue_wallet_editor_from_problem_cards(
                    _problem_df([{"card": "123", "original_partner": "Ostin"}]),
                    conv_file="/tmp/conv_report.xlsx",
                )

        add_task.assert_called_once()
        task = add_task.call_args.args[0]
        assert isinstance(task, WalletEditorTask)
        assert task.chat_id == -100123
        assert task.login == "we-login"
        assert task.password == "we-pass"
        assert task.operator_profile == OPERATOR_PROFILE
        assert task.telegram_user_id == CONVERSION_WE_TELEGRAM_USER_ID
        assert task.source_file_name == "conversion_auto_conv_report.xlsx"
        assert task.file_path.endswith(".xlsx")

    def test_conversion_we_bridge_never_raises_on_wallet_editor_error(self, monkeypatch):
        for key, value in _conversion_we_env().items():
            monkeypatch.setenv(key, value)

        with patch(
            "integrations.conversion_wallet_editor_bridge.add_task",
            side_effect=RuntimeError("queue failed"),
        ):
            with patch("integrations.conversion_wallet_editor_bridge.send_message_sync"):
                maybe_enqueue_wallet_editor_from_problem_cards(
                    _problem_df([{"card": "123", "original_partner": "Ostin"}])
                )

    def test_resolve_conversion_we_config_partial_env(self, monkeypatch):
        monkeypatch.setenv(ENV_CHAT_ID, "-100999")
        monkeypatch.delenv(ENV_LOGIN, raising=False)
        monkeypatch.delenv(ENV_PASSWORD, raising=False)
        config, error = resolve_conversion_we_config()
        assert config is None
        assert "CONVERSION_WALLET_EDITOR_OPERATOR_PROFILE_LOGIN" in error


class TestConversionRunIntegration:
    def test_conversion_run_does_not_fail_when_bridge_raises(self, monkeypatch):
        problem = pd.DataFrame([{"card": "X", "original_partner": "P"}])

        def _boom(_problem, *, conv_file=None):
            raise RuntimeError("bridge exploded")

        monkeypatch.setattr(
            "analyzers.conversion.maybe_enqueue_wallet_editor_from_problem_cards",
            _boom,
        )

        from analyzers.conversion import run

        with patch("analyzers.conversion.get_snapshot_v2", return_value=MagicMock()):
            with patch.object(
                type(run.__globals__["_analyzer"]),
                "load_special_cards_state",
                return_value=MagicMock(special_loaded=False, df_special=None),
            ):
                with patch.object(
                    type(run.__globals__["_analyzer"]),
                    "analyze",
                    return_value=MagicMock(summary={}, problem_cards=problem),
                ):
                    with patch("analyzers.conversion.render_excel", return_value=MagicMock(sheetnames=[])):
                        with patch("analyzers.conversion.send_message_sync"):
                            with patch("analyzers.conversion.send_file_sync"):
                                monkeypatch.setenv("TELEGRAM_CHAT_ID_ANALIZ", "1")
                                result = run(
                                    "/tmp/fake_conv.xlsx",
                                    [],
                                    {},
                                    generate_excel=True,
                                    send_telegram=False,
                                )

        assert "problem_cards" in result
        assert len(result["problem_cards"]) == 1


def test_conversion_we_excel_passes_prepare_df(tmp_path):
    rows = [
        {"card": "1234567890", "action": ACTION_REMOVE_PARTNER, "value": "Ostin"},
        {"card": "9876543210", "action": ACTION_REMOVE_PARTNER, "value": "Teon"},
    ]
    path = str(tmp_path / "conversion_we_input.xlsx")
    build_wallet_editor_excel(rows, path)

    df = _prepare_df(path)
    assert list(df["action"]) == [ACTION_REMOVE_PARTNER, ACTION_REMOVE_PARTNER]
    assert list(df["value"]) == ["Ostin", "Teon"]
