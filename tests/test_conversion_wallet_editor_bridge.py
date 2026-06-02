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
    MAX_CONVERSION_WALLET_EDITOR_CARDS_PER_RUN,
    OPERATOR_PROFILE,
    apply_card_limit,
    build_wallet_editor_excel,
    compute_rollout_stats,
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


class TestConversionWELimit:
    def test_conversion_we_limit_under_10(self):
        rows = [{"card": f"c{i}", "action": ACTION_REMOVE_PARTNER, "value": f"p{i}"} for i in range(6)]
        limited, stats = apply_card_limit(rows)
        assert stats.total_found_cards == 6
        assert stats.processed_cards == 6
        assert stats.skipped_cards == 0
        assert len(limited) == 6

    def test_conversion_we_limit_exact_10(self):
        rows = [{"card": f"c{i}", "action": ACTION_REMOVE_PARTNER, "value": f"p{i}"} for i in range(10)]
        limited, stats = apply_card_limit(rows)
        assert stats.total_found_cards == 10
        assert stats.processed_cards == 10
        assert stats.skipped_cards == 0
        assert len(limited) == 10

    def test_conversion_we_limit_over_10(self):
        rows = [{"card": f"c{i}", "action": ACTION_REMOVE_PARTNER, "value": f"p{i}"} for i in range(37)]
        limited, stats = apply_card_limit(rows)
        assert stats.total_found_cards == 37
        assert stats.processed_cards == 10
        assert stats.skipped_cards == 27
        assert len(limited) == 10
        assert limited[0]["card"] == "c0"
        assert limited[-1]["card"] == "c9"

    def test_compute_rollout_stats_constant(self):
        assert MAX_CONVERSION_WALLET_EDITOR_CARDS_PER_RUN == 10
        stats = compute_rollout_stats(37)
        assert stats.processed_cards == min(37, 10)


class TestConversionWEBridge:
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
