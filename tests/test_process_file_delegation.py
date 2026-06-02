"""Tests for main.process_file conversion delegation to run_conversion_pipeline (Phase B3)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from analyzers import conversion as conversion_module
from main import process_file


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DROPBOX_INPUT_PATH", "/dropbox/in")
    monkeypatch.setenv("DROPBOX_PROCESSED_PATH", "/dropbox/out")
    monkeypatch.setattr("main.LOCAL_TMP_PATH", str(tmp_path))
    return {
        "tmp_root": str(tmp_path),
        "conv_name": "conversion_b3_test.xlsx",
        "card_name": "card_b3_test.xlsx",
        "payout_name": "payout_b3_test.xlsx",
        "cd_name": "cd_b3_test.xlsx",
    }


class TestProcessFileConversionDelegation:
    def test_process_file_conversion_delegates_to_pipeline(self, env):
        conv_name = env["conv_name"]
        card_name = env["card_name"]

        with patch(
            "integrations.conversion_pipeline.run_conversion_pipeline", return_value=True
        ) as pipeline_mock:
            ok = process_file(conv_name, aux_filename=card_name)

        assert ok is True
        pipeline_mock.assert_called_once_with(
            conv_name,
            card_filename=card_name,
            card_local_path=None,
        )

    def test_process_file_conversion_delegates_pipeline_false(self, env):
        with patch(
            "integrations.conversion_pipeline.run_conversion_pipeline", return_value=False
        ) as pipeline_mock:
            ok = process_file(env["conv_name"], aux_filename=env["card_name"])

        assert ok is False
        pipeline_mock.assert_called_once()

    def test_process_file_payout_does_not_delegate(self, env):
        payout_name = env["payout_name"]
        cd_name = env["cd_name"]
        tmp_root = env["tmp_root"]

        with patch(
            "integrations.conversion_pipeline.run_conversion_pipeline"
        ) as pipeline_mock, patch("main.download_file", return_value=True), patch(
            "main.move_file", return_value=True
        ), patch(
            "analyzers.payout.run",
            return_value={"summary": {}},
        ) as payout_run_mock:
            ok = process_file(payout_name, aux_filename=cd_name)

        assert ok is True
        pipeline_mock.assert_not_called()
        payout_run_mock.assert_called_once()
        call_kwargs = payout_run_mock.call_args.kwargs
        assert call_kwargs["payout_file"] == os.path.join(tmp_root, payout_name)
        assert call_kwargs["card_files"] == [os.path.join(tmp_root, cd_name)]

    def test_process_file_conversion_passes_aux_filename(self, env):
        conv_name = env["conv_name"]
        card_name = env["card_name"]

        with patch("integrations.conversion_pipeline.run_conversion_pipeline") as pipeline_mock:
            pipeline_mock.return_value = True
            process_file(conv_name, aux_filename=card_name)

        _, kwargs = pipeline_mock.call_args
        assert kwargs["card_filename"] == card_name
        assert kwargs["card_local_path"] is None

    def test_process_file_conversion_passes_last_card_path(self, env, monkeypatch):
        conv_name = env["conv_name"]
        saved_card = "/tmp/previous_card.xlsx"
        monkeypatch.setattr("main.last_card_path", saved_card)

        with patch("integrations.conversion_pipeline.run_conversion_pipeline") as pipeline_mock:
            pipeline_mock.return_value = True
            process_file(conv_name)

        _, kwargs = pipeline_mock.call_args
        assert kwargs["card_filename"] is None
        assert kwargs["card_local_path"] == saved_card

    def test_process_file_conversion_no_duplicate_download_or_move(self, env):
        with patch("integrations.conversion_pipeline.run_conversion_pipeline", return_value=True), patch(
            "main.download_file"
        ) as main_download, patch("main.move_file") as main_move:
            process_file(env["conv_name"], aux_filename=env["card_name"])

        main_download.assert_not_called()
        main_move.assert_not_called()

    def test_process_file_conversion_no_duplicate_analyzer_call(self, env):
        with patch(
            "integrations.conversion_pipeline.run_conversion_pipeline", return_value=True
        ) as pipeline_mock:
            process_file(env["conv_name"], aux_filename=env["card_name"])

        pipeline_mock.assert_called_once()
