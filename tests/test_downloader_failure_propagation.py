"""Downloader failure propagation tests (Phase 2 observability)."""

from __future__ import annotations

import importlib
import os
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def downloader_module(monkeypatch):
    monkeypatch.setenv("TELEGRAM_CHAT_ID_ANALIZ", "test-chat-id")
    monkeypatch.setenv("ANTARES_LOGIN", "test-login")
    monkeypatch.setenv("ANTARES_PASSWORD", "test-password")
    monkeypatch.setenv("DROPBOX_INPUT_PATH", "/dropbox/in")
    import integrations.downloader as downloader

    importlib.reload(downloader)
    return downloader


def _run_download_with_mocks(
    downloader_module,
    monkeypatch,
    tmp_path,
    *,
    conv_ok: bool,
    payout_ok: bool,
):
    timestamp = "12.00"
    conv_name = f"conversion_{timestamp}.xlsx"
    card_name = f"card_{timestamp}.xlsx"
    cd_name = f"cd_{timestamp}.xlsx"
    payout_name = f"payout_{timestamp}.xlsx"

    messages: list[str] = []
    call_log: list[tuple[str, ...]] = []
    lock_held = {"value": False}

    def track_send(msg, chat_id=None, **kwargs):
        messages.append(msg)

    def track_acquire_lock(timeout=600):
        lock_held["value"] = True
        call_log.append(("acquire_lock",))
        return True

    def track_release_lock():
        assert lock_held["value"] is True
        lock_held["value"] = False
        call_log.append(("release_lock",))

    def track_run_conversion_pipeline(conv_filename, card_filename=None, **kwargs):
        call_log.append(("run_conversion_pipeline", conv_filename, card_filename))
        return conv_ok

    def track_process_file(filename, aux_filename=None):
        call_log.append(("process_file", filename, aux_filename))
        return payout_ok

    class FakePlaywright:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        @property
        def chromium(self):
            browser = MagicMock()
            context = MagicMock()
            page = MagicMock()
            browser.launch.return_value = browser
            browser.new_context.return_value = context
            context.new_page.return_value = page
            return browser

    monkeypatch.setattr(downloader_module, "sync_playwright", FakePlaywright)
    monkeypatch.setattr(downloader_module, "_ts", lambda: timestamp)
    monkeypatch.setattr(downloader_module, "_ensure_logged_in", lambda page, context: None)
    monkeypatch.setattr(
        downloader_module,
        "_download_wallet_export",
        lambda page, ts: str(tmp_path / f"card_{ts}.xlsx"),
    )
    monkeypatch.setattr(
        downloader_module,
        "_download_payin_export",
        lambda page, ts: str(tmp_path / f"conversion_{ts}.xlsx"),
    )
    monkeypatch.setattr(
        downloader_module,
        "_download_extra_files",
        lambda page, ts: [
            str(tmp_path / f"cd_{ts}.xlsx"),
            str(tmp_path / f"payout_{ts}.xlsx"),
        ],
    )
    monkeypatch.setattr(downloader_module, "_upload_local_to_dropbox", lambda local, name: name)
    monkeypatch.setattr(downloader_module, "send_message_sync", track_send)
    monkeypatch.setattr(downloader_module, "acquire_lock", track_acquire_lock)
    monkeypatch.setattr(downloader_module, "release_lock", track_release_lock)
    monkeypatch.setattr(downloader_module, "run_conversion_pipeline", track_run_conversion_pipeline)
    monkeypatch.setattr(downloader_module, "process_file", track_process_file)

    for name in (card_name, conv_name, cd_name, payout_name):
        (tmp_path / name).write_bytes(b"stub")

    downloader_module.run_download()

    final_messages = [m for m in messages if m.startswith(("✅ Downloader", "⚠️ Downloader", "❌ Downloader"))]
    assert len(final_messages) == 1
    return final_messages[0], call_log, messages


class TestDownloaderFinalMessagePolicy:
    def test_downloader_final_success_when_conversion_and_payout_ok(
        self, downloader_module, monkeypatch, tmp_path
    ):
        final_msg, _, _ = _run_download_with_mocks(
            downloader_module, monkeypatch, tmp_path, conv_ok=True, payout_ok=True
        )
        assert final_msg.startswith("✅ Downloader завершил цикл успешно")
        assert "Conversion: OK" in final_msg
        assert "Payout: OK" in final_msg

    def test_downloader_partial_when_conversion_false_payout_ok(
        self, downloader_module, monkeypatch, tmp_path
    ):
        final_msg, call_log, messages = _run_download_with_mocks(
            downloader_module, monkeypatch, tmp_path, conv_ok=False, payout_ok=True
        )
        assert final_msg.startswith("⚠️ Downloader завершил цикл с частичной ошибкой")
        assert "Conversion: FAILED" in final_msg
        assert "Payout: OK" in final_msg
        assert "✅ Downloader завершил цикл успешно" not in messages
        assert call_log[1][0] == "run_conversion_pipeline"
        assert any(c[0] == "process_file" for c in call_log)

    def test_downloader_partial_when_conversion_ok_payout_false(
        self, downloader_module, monkeypatch, tmp_path
    ):
        final_msg, _, _ = _run_download_with_mocks(
            downloader_module, monkeypatch, tmp_path, conv_ok=True, payout_ok=False
        )
        assert final_msg.startswith("⚠️ Downloader завершил цикл с частичной ошибкой")
        assert "Conversion: OK" in final_msg
        assert "Payout: FAILED" in final_msg

    def test_downloader_error_when_both_false(self, downloader_module, monkeypatch, tmp_path):
        final_msg, _, _ = _run_download_with_mocks(
            downloader_module, monkeypatch, tmp_path, conv_ok=False, payout_ok=False
        )
        assert final_msg.startswith("❌ Downloader завершил цикл с ошибками")
        assert "Conversion: FAILED" in final_msg
        assert "Payout: FAILED" in final_msg


class TestDownloaderFailurePropagation:
    def test_downloader_payout_still_runs_when_conversion_false(
        self, downloader_module, monkeypatch, tmp_path
    ):
        _, call_log, _ = _run_download_with_mocks(
            downloader_module, monkeypatch, tmp_path, conv_ok=False, payout_ok=True
        )
        conv_idx = next(i for i, c in enumerate(call_log) if c[0] == "run_conversion_pipeline")
        payout_idx = next(i for i, c in enumerate(call_log) if c[0] == "process_file")
        assert conv_idx < payout_idx

    def test_downloader_lock_released_when_conversion_false(
        self, downloader_module, monkeypatch, tmp_path
    ):
        _, call_log, _ = _run_download_with_mocks(
            downloader_module, monkeypatch, tmp_path, conv_ok=False, payout_ok=True
        )
        assert call_log[-1] == ("release_lock",)

    def test_downloader_does_not_raise_on_conversion_false(
        self, downloader_module, monkeypatch, tmp_path
    ):
        _run_download_with_mocks(
            downloader_module, monkeypatch, tmp_path, conv_ok=False, payout_ok=True
        )

    def test_downloader_passes_source_downloader_to_pipeline(
        self, downloader_module, monkeypatch, tmp_path
    ):
        captured: dict = {}

        def track_pipeline(conv_filename, card_filename=None, **kwargs):
            captured.update(kwargs)
            return True

        timestamp = "12.00"

        class FakePlaywright:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            @property
            def chromium(self):
                browser = MagicMock()
                browser.launch.return_value = browser
                browser.new_context.return_value = MagicMock(new_page=MagicMock())
                return browser

        monkeypatch.setattr(downloader_module, "sync_playwright", FakePlaywright)
        monkeypatch.setattr(downloader_module, "_ts", lambda: timestamp)
        monkeypatch.setattr(downloader_module, "_ensure_logged_in", lambda p, c: None)
        monkeypatch.setattr(
            downloader_module, "_download_wallet_export", lambda p, ts: str(tmp_path / "c.xlsx")
        )
        monkeypatch.setattr(
            downloader_module, "_download_payin_export", lambda p, ts: str(tmp_path / "v.xlsx")
        )
        monkeypatch.setattr(downloader_module, "_download_extra_files", lambda p, ts: [])
        monkeypatch.setattr(downloader_module, "_upload_local_to_dropbox", lambda l, n: n)
        monkeypatch.setattr(downloader_module, "send_message_sync", lambda *a, **k: None)
        monkeypatch.setattr(downloader_module, "acquire_lock", lambda timeout=600: True)
        monkeypatch.setattr(downloader_module, "release_lock", lambda: None)
        monkeypatch.setattr(downloader_module, "run_conversion_pipeline", track_pipeline)

        downloader_module.run_download()
        assert captured.get("source") == "downloader"


class TestBuildDownloaderFinalMessage:
    def test_full_success_payout_not_attempted(self, downloader_module):
        msg = downloader_module._build_downloader_final_message(
            conv_ok=True, payout_ok=True, payout_attempted=False
        )
        assert msg.startswith("✅")
        assert "Payout: not attempted" in msg

    def test_build_message_both_failed(self, downloader_module):
        msg = downloader_module._build_downloader_final_message(
            conv_ok=False, payout_ok=False, payout_attempted=True
        )
        assert msg.startswith("❌")
