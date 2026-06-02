"""Observability tests for conversion pipeline (event_log + state_store + status)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from analyzers import conversion as conversion_module
from integrations import conversion_pipeline
from integrations.tg_commands import _format_observation_status


@pytest.fixture
def pipeline_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DROPBOX_INPUT_PATH", "/dropbox/in")
    monkeypatch.setenv("DROPBOX_PROCESSED_PATH", "/dropbox/out")
    monkeypatch.setenv("CONVERSION_FINGERPRINT_ENABLED", "0")
    monkeypatch.setattr("main.LOCAL_TMP_PATH", str(tmp_path))
    return {
        "tmp_root": str(tmp_path),
        "conv_name": "conversion_obs_test.xlsx",
        "card_name": "card_obs_test.xlsx",
    }


@pytest.fixture
def capture_observability():
    events: list[dict] = []
    state_patches: list[dict] = []

    def track_event(*, type, job_type=None, payload=None, **kwargs):
        events.append({"type": type, "job_type": job_type, "payload": payload or {}})

    def track_state(job_type, patch):
        state_patches.append({"job_type": job_type, "patch": patch})

    with patch.object(conversion_pipeline, "append_event", side_effect=track_event), patch.object(
        conversion_pipeline, "state_update", side_effect=track_state
    ):
        yield {"events": events, "state_patches": state_patches}


def _terminal_events(events: list[dict]) -> list[dict]:
    terminal = {"conversion_success", "conversion_failed", "conversion_skipped"}
    return [e for e in events if e["type"] in terminal]


class TestConversionPipelineEvents:
    def test_conversion_pipeline_emits_started_event(self, pipeline_env, capture_observability):
        conv_name = pipeline_env["conv_name"]
        card_name = pipeline_env["card_name"]

        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(conversion_module, "run", return_value={"summary": {}, "report_path": "/tmp/r.xlsx"}):
            conversion_pipeline.run_conversion_pipeline(
                conv_name, card_filename=card_name, source="test"
            )

        started = [e for e in capture_observability["events"] if e["type"] == "conversion_started"]
        assert len(started) == 1
        assert started[0]["job_type"] == "conversion"
        assert started[0]["payload"]["conv_filename"] == conv_name
        assert started[0]["payload"]["card_filename"] == card_name
        assert started[0]["payload"]["source"] == "test"

    def test_conversion_pipeline_emits_success_event(self, pipeline_env, capture_observability):
        conv_name = pipeline_env["conv_name"]
        card_name = pipeline_env["card_name"]

        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(
            conversion_module,
            "run",
            return_value={
                "summary": {"Карты на отключение": 2},
                "report_path": "/tmp/report_obs.xlsx",
            },
        ):
            ok = conversion_pipeline.run_conversion_pipeline(conv_name, card_filename=card_name)

        assert ok is True
        success = [e for e in capture_observability["events"] if e["type"] == "conversion_success"]
        assert len(success) == 1
        payload = success[0]["payload"]
        assert payload["conv_filename"] == conv_name
        assert "runtime_sec" in payload
        assert payload["summary"] == {"Карты на отключение": 2}
        assert payload["report_path"] == "/tmp/report_obs.xlsx"
        assert payload["processed_name"] is not None

    def test_conversion_pipeline_emits_failed_event_on_download_failure(
        self, pipeline_env, capture_observability
    ):
        conv_name = pipeline_env["conv_name"]
        card_name = pipeline_env["card_name"]

        def fake_download(src, dst):
            return "card" in src

        with patch.object(conversion_pipeline, "download_file", side_effect=fake_download), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(conversion_pipeline, "_safe_send"):
            ok = conversion_pipeline.run_conversion_pipeline(conv_name, card_filename=card_name)

        assert ok is False
        failed = [e for e in capture_observability["events"] if e["type"] == "conversion_failed"]
        assert len(failed) == 1
        assert failed[0]["payload"]["stage"] == "conv_download"

    def test_conversion_pipeline_emits_terminal_event_once(self, pipeline_env, capture_observability):
        with patch.object(conversion_pipeline, "download_file", return_value=False), patch.object(
            conversion_pipeline, "_safe_send"
        ):
            conversion_pipeline.run_conversion_pipeline(
                pipeline_env["conv_name"], card_filename=pipeline_env["card_name"]
            )

        assert len(_terminal_events(capture_observability["events"])) == 1

    def test_conversion_pipeline_skipped_on_missing_card(self, pipeline_env, capture_observability):
        conv_name = pipeline_env["conv_name"]

        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "_safe_send"
        ):
            ok = conversion_pipeline.run_conversion_pipeline(conv_name)

        assert ok is False
        skipped = [e for e in capture_observability["events"] if e["type"] == "conversion_skipped"]
        assert len(skipped) == 1
        assert skipped[0]["payload"]["reason"] == "card_missing"
        assert len(_terminal_events(capture_observability["events"])) == 1


class TestConversionPipelineState:
    def test_conversion_pipeline_updates_state_on_success(self, pipeline_env, capture_observability):
        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(
            conversion_module, "run", return_value={"summary": {}, "report_path": "/tmp/r.xlsx"}
        ):
            conversion_pipeline.run_conversion_pipeline(
                pipeline_env["conv_name"], card_filename=pipeline_env["card_name"]
            )

        conversion_patches = [
            p for p in capture_observability["state_patches"] if p["job_type"] == "conversion"
        ]
        assert conversion_patches[0]["patch"]["last_status"] == "running"
        assert conversion_patches[-1]["patch"]["last_status"] == "success"
        assert "last_success_ts" in conversion_patches[-1]["patch"]

    def test_conversion_pipeline_updates_state_on_failure(self, pipeline_env, capture_observability):
        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(conversion_module, "run", side_effect=RuntimeError("boom")), patch.object(
            conversion_pipeline, "_safe_send"
        ):
            conversion_pipeline.run_conversion_pipeline(
                pipeline_env["conv_name"], card_filename=pipeline_env["card_name"]
            )

        conversion_patches = [
            p for p in capture_observability["state_patches"] if p["job_type"] == "conversion"
        ]
        assert conversion_patches[-1]["patch"]["last_status"] == "failed"
        assert conversion_patches[-1]["patch"]["last_error"] == "boom"
        assert "last_failure_ts" in conversion_patches[-1]["patch"]


class TestConversionObservationStatus:
    def test_status_renders_conversion_section(self, monkeypatch):
        def fake_state_get(job_type, key):
            data = {
                ("conversion", "last_status"): "success",
                ("conversion", "last_run_ts"): 1_700_000_000,
                ("conversion", "last_success_ts"): 1_700_000_010,
                ("conversion", "last_failure_ts"): None,
                ("conversion", "last_conv_filename"): "conversion_12.00.xlsx",
                ("conversion", "last_error"): None,
                ("conversion", "last_runtime_sec"): 3.5,
            }
            return data.get((job_type, key))

        monkeypatch.setattr("integrations.tg_commands.state_get", fake_state_get)

        with patch("integrations.tg_commands.get_scheduler_health_snapshot", return_value={}), patch(
            "integrations.tg_commands.get_telegram_sender_health_snapshot",
            return_value={
                "status": "ok",
                "total_sent": 0,
                "total_failed": 0,
                "queue_depth": 0,
                "consecutive_failures": 0,
            },
        ), patch(
            "integrations.tg_commands.get_lock_status_for_job_types",
            return_value={},
        ), patch("integrations.tg_commands.get_status", return_value={}):
            text = _format_observation_status()

        assert "Conversion:" in text
        assert "last_status=success" in text
        assert "last_file=conversion_12.00.xlsx" in text
        assert "last_runtime_sec=3.5" in text

    def test_status_renders_conversion_no_data(self, monkeypatch):
        monkeypatch.setattr("integrations.tg_commands.state_get", lambda job_type, key: None)
        with patch("integrations.tg_commands.get_scheduler_health_snapshot", return_value={}), patch(
            "integrations.tg_commands.get_telegram_sender_health_snapshot",
            return_value={
                "status": "ok",
                "total_sent": 0,
                "total_failed": 0,
                "queue_depth": 0,
                "consecutive_failures": 0,
            },
        ), patch(
            "integrations.tg_commands.get_lock_status_for_job_types",
            return_value={},
        ), patch("integrations.tg_commands.get_status", return_value={}):
            text = _format_observation_status()

        assert "Conversion:" in text
        assert "no data" in text


class TestConversionObservabilityNoDuplicates:
    def test_success_does_not_emit_failed(self, pipeline_env, capture_observability):
        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(conversion_module, "run", return_value={"summary": {}, "report_path": None}):
            conversion_pipeline.run_conversion_pipeline(
                pipeline_env["conv_name"], card_filename=pipeline_env["card_name"]
            )

        types = [e["type"] for e in capture_observability["events"]]
        assert "conversion_success" in types
        assert "conversion_failed" not in types
        assert "conversion_skipped" not in types

    def test_skipped_does_not_emit_failed(self, pipeline_env, capture_observability):
        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "_safe_send"
        ):
            conversion_pipeline.run_conversion_pipeline(pipeline_env["conv_name"])

        types = [e["type"] for e in capture_observability["events"]]
        assert types.count("conversion_skipped") == 1
        assert "conversion_failed" not in types
