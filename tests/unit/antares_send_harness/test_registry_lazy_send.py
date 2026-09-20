from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from automation.audit import Stats
from automation.runtime import WalletEditorTask
from integrations.wallet_editor_registry import (
    SLOW_APPEND_MESSAGE,
    TIMEOUT_MESSAGE,
    _AppendOutcome,
    _process_missing_otlezka_warnings,
    _send_chat_warning,
    append_run_to_dropbox_registry,
)
from integrations.wallet_editor_registry_lifecycle import (
    ALL_RESULTS_COLUMNS,
    load_warned_partners,
    sync_warned_partners_after_otlezka,
)
from integrations.wallet_editor_registry_settings import RegistrySettings
from integrations.wallet_editor_registry_db.postgres_source import append_attempt_postgres

MSK = ZoneInfo("Europe/Moscow")
RUN_STARTED = datetime(2026, 6, 3, 9, 0, 0, tzinfo=MSK)
RUN_FINISHED = datetime(2026, 6, 3, 9, 5, 0, tzinfo=MSK)


def _task(**kwargs) -> WalletEditorTask:
    values = dict(
        file_path="/tmp/wallet_editor/in.xlsx",
        chat_id=-555,
        telegram_user_id=1,
        operator_profile="DENIS",
        source_file_name="batch.xlsx",
        login="l",
        password="p",
        auth_state_path="/tmp/auth.json",
        run_id="send-run",
    )
    values.update(kwargs)
    return WalletEditorTask(**values)


def _write_result(path: Path, *, partner: str = "NoOtlezka") -> None:
    pd.DataFrame(
        {
            "Дата отключения": ["03.06.2026 09:00:00"],
            "card": ["4111111111111111"],
            "action": ["remove_partner"],
            "value": [partner],
            "status": ["OK"],
            "comment": [""],
        }
    ).to_excel(path, index=False)


def _fast(*, warning: int, timeout: int, retry: int) -> RegistrySettings:
    return RegistrySettings(
        registry_warning_seconds=warning,
        registry_timeout_seconds=timeout,
        registry_retry_interval_seconds=retry,
    )


def _isolate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DROPBOX_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", "/Ostin/platform/Tests/wallet_editor.xlsx")
    monkeypatch.setenv("WALLET_EDITOR_MANUAL_READERS_SOURCE", "dropbox")
    monkeypatch.setenv("DATABASE_URL", "postgresql://registry-send-test.invalid:1/unused")
    monkeypatch.setattr(
        "integrations.wallet_editor_registry_db.connection.connect",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("postgres blocked")),
    )


def test_missing_otlezka_warning_sent_once_per_partner(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    messages: list[str] = []
    empty = pd.DataFrame(columns=["partner", "Полные дни", "comment"])
    with patch(
        "integrations.telegram_bot.send_message_sync",
        side_effect=lambda text, chat_id=None, **_kw: messages.append(text),
    ):
        _process_missing_otlezka_warnings(_task(run_id="warn-1"), {"NoOtlezka"}, empty)
        _process_missing_otlezka_warnings(_task(run_id="warn-2"), {"NoOtlezka"}, empty)
    warn_msgs = [m for m in messages if "Не настроена отлёжка" in m]
    assert len(warn_msgs) == 1
    assert "NoOtlezka" in warn_msgs[0]


def test_missing_otlezka_warning_state_cleared_after_fix(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    empty = pd.DataFrame(columns=["partner", "Полные дни", "comment"])
    with patch("integrations.telegram_bot.send_message_sync"):
        _process_missing_otlezka_warnings(_task(run_id="teon-1"), {"Teon"}, empty)
    assert "teon" in load_warned_partners()
    otlezka = pd.DataFrame([{"partner": "Teon", "Полные дни": 2, "comment": ""}])
    warned = sync_warned_partners_after_otlezka(otlezka, load_warned_partners())
    assert "teon" not in warned


def test_timeout_warning_uses_real_send(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    result_path = tmp_path / "result.xlsx"
    _write_result(result_path)
    messages: list[str] = []

    def slow_then_transient(*_a, **_k):
        time_mod = __import__("time")
        time_mod.sleep(1.5)
        return _AppendOutcome.TRANSIENT, None, None

    with patch(
        "integrations.wallet_editor_registry._append_attempt",
        side_effect=slow_then_transient,
    ):
        with patch(
            "integrations.telegram_bot.send_message_sync",
            side_effect=lambda text, **kw: messages.append(text),
        ):
            append_run_to_dropbox_registry(
                _task(run_id="warn-before-timeout"),
                str(result_path),
                Stats(ok=1, fail=0, skip=0),
                run_started_at=RUN_STARTED,
                run_finished_at=RUN_FINISHED,
                settings=_fast(warning=1, timeout=4, retry=1),
            )
    assert any(SLOW_APPEND_MESSAGE in m for m in messages)
    assert any(TIMEOUT_MESSAGE in m for m in messages)


def test_success_fast_sends_no_timeout_warning(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    result_path = tmp_path / "result.xlsx"
    _write_result(result_path)
    messages: list[str] = []
    with patch(
        "integrations.wallet_editor_registry._append_attempt",
        return_value=(_AppendOutcome.SUCCESS, "rev", None),
    ):
        with patch(
            "integrations.telegram_bot.send_message_sync",
            side_effect=lambda text, **kw: messages.append(text),
        ):
            append_run_to_dropbox_registry(
                _task(run_id="fast-ok"),
                str(result_path),
                Stats(ok=1, fail=0, skip=0),
                run_started_at=RUN_STARTED,
                run_finished_at=RUN_FINISHED,
                settings=_fast(warning=60, timeout=180, retry=10),
            )
    assert not any(SLOW_APPEND_MESSAGE in m for m in messages)
    assert not any(TIMEOUT_MESSAGE in m for m in messages)


def test_chat_warning_swallows_sender_error() -> None:
    with patch(
        "integrations.telegram_bot.send_message_sync",
        side_effect=RuntimeError("send failed"),
    ) as send:
        _send_chat_warning(-42, "warn")
    send.assert_called_once_with("warn", chat_id="-42")


def test_append_attempt_postgres_uses_mocked_db_boundary(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    result_path = tmp_path / "result.xlsx"
    _write_result(result_path, partner="Ostin")
    persisted = {"n": 0}

    def persist(*_a, **_k):
        persisted["n"] += 1

    empty_all = pd.DataFrame(columns=ALL_RESULTS_COLUMNS)
    empty_runs = pd.DataFrame(
        columns=[
            "started_at",
            "finished_at",
            "input_rows",
            "success_rows",
            "failed_rows",
            "skipped_rows",
            "output_file",
        ]
    )
    otlezka = pd.DataFrame([{"partner": "Ostin", "Полные дни": 3, "comment": ""}])
    with patch(
        "integrations.wallet_editor_registry_db.postgres_source.load_registry_frames_from_postgres",
        return_value=(empty_all, empty_runs),
    ):
        with patch(
            "integrations.wallet_editor_registry_db.postgres_source.load_hold_otlezka_for_runtime",
            return_value=(pd.DataFrame(), otlezka, False, True),
        ):
            with patch(
                "integrations.wallet_editor_registry_db.postgres_source.postgres_run_exists",
                return_value=False,
            ):
                with patch(
                    "integrations.wallet_editor_registry_db.postgres_source._persist_full_registry",
                    side_effect=persist,
                ):
                    outcome, _rev, _batch = append_attempt_postgres(
                        _task(run_id="pg-append"),
                        str(result_path),
                        Stats(ok=1, fail=0, skip=0),
                        dropbox_path="/Ostin/platform/Tests/wallet_editor.xlsx",
                        run_started_at=RUN_STARTED,
                        run_finished_at=RUN_FINISHED,
                        output_file="result.xlsx",
                        resolve_source=lambda profile: "telegram_manual",
                        process_missing_otlezka_warnings=lambda *a, **k: None,
                    )
    assert outcome is _AppendOutcome.SUCCESS
    assert persisted["n"] == 1
    assert os.getenv("DATABASE_URL", "").endswith("unused")
