"""Telegram outbound sender health (Option B)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import integrations.telegram_bot as tg


@pytest.fixture(autouse=True)
def reset_health():
    tg._reset_telegram_sender_health_for_tests()
    yield
    tg._reset_telegram_sender_health_for_tests()


@pytest.fixture
def with_token(monkeypatch):
    monkeypatch.setattr(tg, "TELEGRAM_TOKEN", "123456:ABC-DEF")
    yield


def test_enqueue_increments_enqueued_not_sent(with_token, monkeypatch):
    monkeypatch.setattr(tg, "loop", MagicMock())

    tg.send_message_sync("hello", chat_id="-100")

    snap = tg.get_telegram_sender_health_snapshot()
    assert snap["total_enqueued"] == 1
    assert snap["total_sent"] == 0
    assert snap["queue_depth"] == 1


def test_success_increments_sent_and_resets_consecutive(with_token):
    tg._record_enqueue("text")
    tg._record_delivery_failure("-100", "text", RuntimeError("fail"))
    tg._record_delivery_failure("-100", "text", RuntimeError("fail"))

    tg._record_delivery_success("-100", "text")

    snap = tg.get_telegram_sender_health_snapshot()
    assert snap["total_sent"] == 1
    assert snap["consecutive_failures"] == 0


def test_failure_increments_failed_and_consecutive(with_token):
    tg._record_delivery_failure("-100", "text", RuntimeError("network down"))

    snap = tg.get_telegram_sender_health_snapshot()
    assert snap["total_failed"] == 1
    assert snap["consecutive_failures"] == 1
    assert snap["last_error_class"] == "RuntimeError"
    assert snap["last_target_chat_id"] == "-100"
    assert snap["last_message_kind"] == "text"


def test_threshold_emits_degraded_log(with_token, caplog):
    with patch.dict("os.environ", {"TELEGRAM_HEALTH_DEGRADED_THRESHOLD": "2"}, clear=False):
        tg._record_delivery_failure("-100", "text", RuntimeError("boom"))
        with caplog.at_level("ERROR"):
            tg._record_delivery_failure("-100", "text", RuntimeError("boom"))

    assert any("[TelegramSender/health] DEGRADED" in r.message for r in caplog.records)


def test_recovery_log_after_degraded(with_token, caplog):
    with patch.dict("os.environ", {"TELEGRAM_HEALTH_DEGRADED_THRESHOLD": "2"}, clear=False):
        tg._record_delivery_failure("-100", "text", RuntimeError("boom"))
        tg._record_delivery_failure("-100", "text", RuntimeError("boom"))

        with caplog.at_level("INFO"):
            tg._record_delivery_success("-100", "text")

    assert any("[TelegramSender/health] RECOVERED after 2 failures" in r.message for r in caplog.records)


def test_no_token_status(monkeypatch):
    monkeypatch.setattr(tg, "TELEGRAM_TOKEN", None)

    snap = tg.get_telegram_sender_health_snapshot()
    assert snap["status"] == "NO_TOKEN"


def test_periodic_health_log_respects_interval(with_token, caplog):
    with patch.dict(
        "os.environ",
        {"TELEGRAM_HEALTH_LOG_INTERVAL_SECONDS": "600"},
        clear=False,
    ):
        tg._record_delivery_success("-100", "text")
        with caplog.at_level("INFO"):
            tg.log_telegram_health_if_due(now=1000.0)
            tg.log_telegram_health_if_due(now=1200.0)

    health_logs = [r for r in caplog.records if "[TelegramSender/health] HEALTHY" in r.message]
    assert len(health_logs) == 1


def test_snapshot_does_not_contain_token(with_token):
    secret = "123456:ABC-DEF"
    tg._record_delivery_failure(
        "-100",
        "text",
        RuntimeError(f"failed for bot{secret}"),
    )

    snap = tg.get_telegram_sender_health_snapshot()
    dumped = repr(snap)
    assert secret not in dumped
    assert "ABC-DEF" not in dumped


def test_sanitize_error_message_redacts_token(with_token):
    msg = tg._sanitize_error_message("error bot123456:ABC-DEF timeout")
    assert "ABC-DEF" not in msg
    assert "bot<redacted>" in msg


def test_worker_failure_does_not_stop_processing(with_token):
    """Failure path records state without raising from health helpers."""
    tg._record_delivery_failure("-100", "text", RuntimeError("first"))
    tg._record_delivery_success("-200", "document")
    tg._record_delivery_failure("-200", "document", RuntimeError("second"))

    snap = tg.get_telegram_sender_health_snapshot()
    assert snap["total_sent"] == 1
    assert snap["total_failed"] == 2


def test_degraded_status_when_consecutive_above_threshold(with_token):
    with patch.dict("os.environ", {"TELEGRAM_HEALTH_DEGRADED_THRESHOLD": "2"}, clear=False):
        tg._record_delivery_failure("-100", "text", RuntimeError("a"))
        tg._record_delivery_failure("-100", "text", RuntimeError("b"))
        assert tg.get_telegram_sender_health_snapshot()["status"] == "DEGRADED"


def test_healthy_status_after_success(with_token):
    tg._record_delivery_success("-100", "text")
    assert tg.get_telegram_sender_health_snapshot()["status"] == "HEALTHY"
