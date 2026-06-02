"""Unit tests for integrations/conversion_pipeline.py (Phase B1/B2 orchestrator)."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from analyzers import conversion as conversion_module
from integrations import conversion_pipeline
from main import CONVERSION_COLUMNS, process_file


@pytest.fixture
def pipeline_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DROPBOX_INPUT_PATH", "/dropbox/in")
    monkeypatch.setenv("DROPBOX_PROCESSED_PATH", "/dropbox/out")
    monkeypatch.setenv("CONVERSION_FINGERPRINT_ENABLED", "0")
    return {
        "tmp_root": str(tmp_path),
        "conv_name": "conversion_12_00.xlsx",
        "card_name": "card_12_00.xlsx",
    }


class TestConversionPipelineOrchestrator:
    def test_orchestrator_calls_conversion_run(self, pipeline_env):
        conv_name = pipeline_env["conv_name"]
        card_name = pipeline_env["card_name"]
        tmp_root = pipeline_env["tmp_root"]
        conv_local = f"{tmp_root}/{conv_name}"
        card_local = f"{tmp_root}/{card_name}"

        captured: dict = {}

        def fake_run(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return {"summary": {"Карты на отключение": 0}}

        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(conversion_module, "run", side_effect=fake_run):
            ok = conversion_pipeline.run_conversion_pipeline(
                conv_name,
                card_filename=card_name,
                conv_local_path=conv_local,
                card_local_path=card_local,
                local_tmp_path=tmp_root,
                dropbox_input_path="/dropbox/in",
                dropbox_processed_path="/dropbox/out",
            )

        assert ok is True
        assert captured["args"] == (conv_local, [card_local], CONVERSION_COLUMNS)
        assert captured["kwargs"] == {
            "generate_excel": True,
            "send_telegram": True,
            "rules_force_sync": False,
        }

    def test_orchestrator_downloads_conv_and_card(self, pipeline_env):
        conv_name = pipeline_env["conv_name"]
        card_name = pipeline_env["card_name"]
        tmp_root = pipeline_env["tmp_root"]

        with patch.object(conversion_pipeline, "download_file", return_value=True) as download_mock, patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(
            conversion_module,
            "run",
            return_value={"summary": {}},
        ):
            conversion_pipeline.run_conversion_pipeline(
                conv_name,
                card_filename=card_name,
                local_tmp_path=tmp_root,
                dropbox_input_path="/dropbox/in",
                dropbox_processed_path="/dropbox/out",
            )

        assert download_mock.call_count == 2
        download_mock.assert_any_call(f"/dropbox/in/{card_name}", os.path.join(tmp_root, card_name))
        download_mock.assert_any_call(f"/dropbox/in/{conv_name}", os.path.join(tmp_root, conv_name))

    def test_orchestrator_moves_processed_on_success(self, pipeline_env, monkeypatch):
        conv_name = pipeline_env["conv_name"]
        card_name = pipeline_env["card_name"]
        tmp_root = pipeline_env["tmp_root"]
        conv_local = f"{tmp_root}/{conv_name}"
        card_local = f"{tmp_root}/{card_name}"

        monkeypatch.setattr(
            conversion_pipeline,
            "now_msk",
            lambda: __import__("datetime").datetime(2026, 1, 15, 12, 0, 0),
        )

        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ) as move_mock, patch.object(conversion_module, "run", return_value={"summary": {}}):
            ok = conversion_pipeline.run_conversion_pipeline(
                conv_name,
                card_filename=card_name,
                conv_local_path=conv_local,
                card_local_path=card_local,
                local_tmp_path=tmp_root,
                dropbox_input_path="/dropbox/in",
                dropbox_processed_path="/dropbox/out",
            )

        assert ok is True
        move_mock.assert_any_call(
            f"/dropbox/in/{conv_name}",
            f"/dropbox/out/conversion_12_00_(15.01.2026).xlsx",
        )

    def test_orchestrator_aux_move_before_analysis(self, pipeline_env):
        conv_name = pipeline_env["conv_name"]
        card_name = pipeline_env["card_name"]
        tmp_root = pipeline_env["tmp_root"]
        order: list[str] = []

        def track_download(src, dst):
            order.append(f"download:{src}")
            return True

        def track_move(src, dst):
            order.append(f"move:{src}")
            return True

        def track_run(*_args, **_kwargs):
            order.append("run")
            return {"summary": {}}

        with patch.object(conversion_pipeline, "download_file", side_effect=track_download), patch.object(
            conversion_pipeline, "move_file", side_effect=track_move
        ), patch.object(conversion_module, "run", side_effect=track_run):
            conversion_pipeline.run_conversion_pipeline(
                conv_name,
                card_filename=card_name,
                local_tmp_path=tmp_root,
                dropbox_input_path="/dropbox/in",
                dropbox_processed_path="/dropbox/out",
            )

        run_idx = order.index("run")
        aux_move_idx = order.index(f"move:/dropbox/in/{card_name}")
        assert aux_move_idx < run_idx

    def test_orchestrator_no_move_on_failure(self, pipeline_env):
        conv_name = pipeline_env["conv_name"]
        card_name = pipeline_env["card_name"]
        tmp_root = pipeline_env["tmp_root"]
        conv_local = f"{tmp_root}/{conv_name}"
        card_local = f"{tmp_root}/{card_name}"

        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ) as move_mock, patch.object(conversion_module, "run", side_effect=RuntimeError("boom")):
            ok = conversion_pipeline.run_conversion_pipeline(
                conv_name,
                card_filename=card_name,
                conv_local_path=conv_local,
                card_local_path=card_local,
                local_tmp_path=tmp_root,
                dropbox_input_path="/dropbox/in",
                dropbox_processed_path="/dropbox/out",
            )

        assert ok is False
        conv_moves = [c for c in move_mock.call_args_list if c.args[0].endswith(conv_name)]
        assert conv_moves == []

    def test_orchestrator_returns_bool_contract(self, pipeline_env):
        conv_name = pipeline_env["conv_name"]
        card_name = pipeline_env["card_name"]
        tmp_root = pipeline_env["tmp_root"]
        conv_local = f"{tmp_root}/{conv_name}"
        card_local = f"{tmp_root}/{card_name}"

        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(conversion_module, "run", return_value={"summary": {}}):
            assert (
                conversion_pipeline.run_conversion_pipeline(
                    conv_name,
                    card_filename=card_name,
                    conv_local_path=conv_local,
                    card_local_path=card_local,
                    local_tmp_path=tmp_root,
                    dropbox_input_path="/dropbox/in",
                    dropbox_processed_path="/dropbox/out",
                )
                is True
            )

        with patch.object(conversion_pipeline, "download_file", return_value=False):
            assert (
                conversion_pipeline.run_conversion_pipeline(
                    conv_name,
                    card_filename=card_name,
                    local_tmp_path=tmp_root,
                    dropbox_input_path="/dropbox/in",
                    dropbox_processed_path="/dropbox/out",
                )
                is False
            )


class TestLegacyProcessFilePath:
    def test_main_process_file_conversion_delegates_to_pipeline(self, tmp_path, monkeypatch):
        conv_name = "conversion_legacy_check.xlsx"
        card_name = "card_legacy_check.xlsx"
        (tmp_path / conv_name).write_bytes(b"stub")
        (tmp_path / card_name).write_bytes(b"stub")

        monkeypatch.setenv("DROPBOX_INPUT_PATH", "/dropbox/in")
        monkeypatch.setenv("DROPBOX_PROCESSED_PATH", "/dropbox/out")
        monkeypatch.setenv("CONVERSION_FINGERPRINT_ENABLED", "0")
        monkeypatch.setattr("main.LOCAL_TMP_PATH", str(tmp_path))

        mock_run = MagicMock(return_value={"summary": {"Карты на отключение": 0}})
        mock_run.__module__ = "analyzers.conversion"

        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(conversion_module, "run", mock_run):
            ok = process_file(conv_name, aux_filename=card_name)

        assert ok is True
        mock_run.assert_called_once()
