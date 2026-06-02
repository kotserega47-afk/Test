"""Downloader runtime wiring tests (Phase B2 — conversion → run_conversion_pipeline)."""

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


class TestDownloaderConversionWiring:
    def test_downloader_calls_run_conversion_pipeline_then_process_file_for_payout(
        self, downloader_module, monkeypatch, tmp_path
    ):
        timestamp = "12.00"
        conv_name = f"conversion_{timestamp}.xlsx"
        card_name = f"card_{timestamp}.xlsx"
        cd_name = f"cd_{timestamp}.xlsx"
        payout_name = f"payout_{timestamp}.xlsx"

        call_log: list[tuple[str, ...]] = []
        lock_held = {"value": False}

        def track_acquire_lock(timeout=600):
            assert lock_held["value"] is False
            lock_held["value"] = True
            call_log.append(("acquire_lock", timeout))
            return True

        def track_release_lock():
            assert lock_held["value"] is True
            lock_held["value"] = False
            call_log.append(("release_lock",))

        def track_run_conversion_pipeline(conv_filename, card_filename=None, **kwargs):
            assert lock_held["value"] is True
            call_log.append(("run_conversion_pipeline", conv_filename, card_filename))
            return True

        def track_process_file(filename, aux_filename=None):
            assert lock_held["value"] is True
            call_log.append(("process_file", filename, aux_filename))
            return True

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
        monkeypatch.setattr(downloader_module, "send_message_sync", lambda *args, **kwargs: None)
        monkeypatch.setattr(downloader_module, "acquire_lock", track_acquire_lock)
        monkeypatch.setattr(downloader_module, "release_lock", track_release_lock)
        monkeypatch.setattr(downloader_module, "run_conversion_pipeline", track_run_conversion_pipeline)
        monkeypatch.setattr(downloader_module, "process_file", track_process_file)

        for name in (card_name, conv_name, cd_name, payout_name):
            (tmp_path / name).write_bytes(b"stub")

        downloader_module.run_download()

        assert call_log[0] == ("acquire_lock", 600)
        assert call_log[1] == ("run_conversion_pipeline", conv_name, card_name)
        assert call_log[2] == ("process_file", payout_name, cd_name)
        assert call_log[3] == ("release_lock",)
        assert lock_held["value"] is False

    def test_downloader_source_still_uses_process_file_for_payout_only(self):
        src = open(
            os.path.join(os.path.dirname(__file__), "..", "integrations", "downloader.py"),
            encoding="utf-8",
        ).read()
        assert "run_conversion_pipeline(" in src
        assert "card_filename=card_name" in src
        assert 'source="downloader"' in src
        assert src.count("process_file(") == 1
        assert f"process_file(payout_name, aux_filename=cd_name)" in src
