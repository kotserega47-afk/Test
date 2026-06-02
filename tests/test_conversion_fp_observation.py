"""Tests for conversion fingerprint observation layer (Phase 1B diagnostic)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from analyzers import conversion as conversion_module
from integrations import conversion_pipeline
from integrations.conversion_fingerprint import ConversionFingerprint, SPECIAL_CARDS_MISSING
from observability import conversion_fp_observation as obs


def _fp(
    *,
    combined: str = "aa" * 32,
    conv: str = "bb" * 32,
    card: str = "cc" * 32,
    rules: str = "dd" * 32,
    special: str = SPECIAL_CARDS_MISSING,
) -> ConversionFingerprint:
    return ConversionFingerprint(
        combined=combined,
        conv_hash=conv,
        card_hash=card,
        rules_hash=rules,
        special_cards_hash=special,
    )


class TestChangedComponents:
    def test_all_components_on_first_baseline(self):
        current = _fp()
        assert obs.compute_changed_components(current, None) == [
            "conv",
            "card",
            "rules",
            "special_cards",
        ]

    def test_detects_single_component_change(self):
        baseline = _fp(conv="11" * 32)
        current = _fp(conv="22" * 32)
        assert obs.compute_changed_components(current, baseline) == ["conv"]

    def test_no_changes_when_hashes_match(self):
        baseline = _fp()
        current = _fp()
        assert obs.compute_changed_components(current, baseline) == []


class TestStorage:
    def test_file_created_and_append_works(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_DIR", str(tmp_path))
        record = obs.build_observation_record(
            fingerprint=_fp(),
            previous_fingerprint=None,
            matched_previous=False,
            source="test",
            outcome="success",
            runtime_sec=1.5,
            baseline=None,
            ts=1_700_000_000.0,
        )
        obs.append_observation_record(record, obs_dir=tmp_path / "observability")

        files = list((tmp_path / "observability").glob("conversion_fp_observation_*.jsonl"))
        assert len(files) == 1
        lines = files[0].read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        stored = json.loads(lines[0])
        assert stored["fingerprint"] == "aa" * 32
        assert stored["conv_hash"] == "bb" * 32

    def test_daily_rotation_uses_utc_day(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STATE_DIR", str(tmp_path))
        obs_dir = tmp_path / "observability"

        day_one = datetime(2026, 6, 1, 23, 0, 0, tzinfo=timezone.utc).timestamp()
        day_two = datetime(2026, 6, 2, 1, 0, 0, tzinfo=timezone.utc).timestamp()

        for ts in (day_one, day_two):
            obs.append_observation_record(
                obs.build_observation_record(
                    fingerprint=_fp(combined=f"{int(ts)}" + "0" * 54),
                    previous_fingerprint=None,
                    matched_previous=False,
                    source="test",
                    outcome="success",
                    runtime_sec=1.0,
                    baseline=None,
                    ts=ts,
                ),
                obs_dir=obs_dir,
            )

        files = sorted(p.name for p in obs_dir.glob("conversion_fp_observation_*.jsonl"))
        assert files == [
            "conversion_fp_observation_2026-06-01.jsonl",
            "conversion_fp_observation_2026-06-02.jsonl",
        ]


class TestRetention:
    def test_removes_files_older_than_14_days(self, tmp_path):
        obs_dir = tmp_path / "observability"
        obs_dir.mkdir()

        old_day = (datetime.now(timezone.utc) - timedelta(days=20)).strftime("%Y-%m-%d")
        fresh_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        old_path = obs_dir / f"conversion_fp_observation_{old_day}.jsonl"
        fresh_path = obs_dir / f"conversion_fp_observation_{fresh_day}.jsonl"
        old_path.write_text('{"schema_version":1}\n', encoding="utf-8")
        fresh_path.write_text('{"schema_version":1}\n', encoding="utf-8")

        obs.apply_retention(obs_dir=obs_dir, retention_days=14)

        assert not old_path.exists()
        assert fresh_path.exists()

    def test_retention_does_not_touch_other_dirs(self, tmp_path):
        events_dir = tmp_path / "events"
        events_dir.mkdir()
        stale_event = events_dir / "events_2020-01-01.jsonl"
        stale_event.write_text("{}\n", encoding="utf-8")

        obs_dir = tmp_path / "observability"
        obs_dir.mkdir()
        old_obs = obs_dir / "conversion_fp_observation_2020-01-01.jsonl"
        old_obs.write_text("{}\n", encoding="utf-8")

        obs.apply_retention(obs_dir=obs_dir, retention_days=14)

        assert stale_event.exists()
        assert not old_obs.exists()


class TestPayload:
    def test_full_hashes_and_previous_fingerprint(self):
        fp = _fp()
        record = obs.build_observation_record(
            fingerprint=fp,
            previous_fingerprint="prev" + "0" * 60,
            matched_previous=False,
            source="downloader",
            outcome="success",
            runtime_sec=2.25,
            baseline=_fp(conv="11" * 32),
        )
        assert len(record["fingerprint"]) == 64
        assert record["previous_fingerprint"] == "prev" + "0" * 60
        assert record["conv_hash"] == "bb" * 32
        assert record["changed_components"] == ["conv"]
        assert record["would_skip"] is False

    def test_would_skip_only_on_success_and_match(self):
        fp = _fp()
        record = obs.build_observation_record(
            fingerprint=fp,
            previous_fingerprint=fp.combined,
            matched_previous=True,
            source="downloader",
            outcome="success",
            runtime_sec=1.0,
            baseline=fp,
        )
        assert record["would_skip"] is True

        failed = obs.build_observation_record(
            fingerprint=fp,
            previous_fingerprint=fp.combined,
            matched_previous=True,
            source="downloader",
            outcome="failed",
            runtime_sec=1.0,
            baseline=fp,
        )
        assert failed["would_skip"] is False

    def test_find_last_successful_baseline(self, tmp_path):
        obs_dir = tmp_path / "observability"
        obs_dir.mkdir()
        baseline_fp = _fp(conv="11" * 32, combined="11" * 32)
        obs.append_observation_record(
            obs.build_observation_record(
                fingerprint=baseline_fp,
                previous_fingerprint=None,
                matched_previous=False,
                source="test",
                outcome="success",
                runtime_sec=1.0,
                baseline=None,
            ),
            obs_dir=obs_dir,
        )
        obs.append_observation_record(
            obs.build_observation_record(
                fingerprint=_fp(combined="ff" * 32),
                previous_fingerprint=None,
                matched_previous=False,
                source="test",
                outcome="failed",
                runtime_sec=1.0,
                baseline=None,
            ),
            obs_dir=obs_dir,
        )

        loaded = obs.find_last_successful_baseline(obs_dir=obs_dir)
        assert loaded is not None
        assert loaded.conv_hash == "11" * 32


class TestRuntimeSafety:
    def test_write_failure_does_not_fail_pipeline(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CONVERSION_FINGERPRINT_ENABLED", "1")
        monkeypatch.setenv("CONVERSION_FP_OBSERVATION_ENABLED", "1")
        monkeypatch.setenv("DROPBOX_INPUT_PATH", "/dropbox/in")
        monkeypatch.setenv("DROPBOX_PROCESSED_PATH", "/dropbox/out")

        conv_name = "conversion_obs_safe.xlsx"
        card_name = "card_obs_safe.xlsx"
        conv_local = tmp_path / conv_name
        card_local = tmp_path / card_name
        conv_local.write_bytes(b"conv")
        card_local.write_bytes(b"card")

        fp_obj = _fp()

        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(conversion_module, "run", return_value={"summary": {}}), patch.object(
            conversion_pipeline, "build_conversion_fingerprint", return_value=fp_obj
        ), patch.object(conversion_pipeline, "state_get", return_value=None), patch.object(
            conversion_pipeline, "append_event"
        ), patch.object(conversion_pipeline, "state_update"), patch.object(
            obs, "append_observation_record", side_effect=OSError("disk full")
        ):
            ok = conversion_pipeline.run_conversion_pipeline(
                conv_name,
                card_filename=card_name,
                conv_local_path=str(conv_local),
                card_local_path=str(card_local),
                local_tmp_path=str(tmp_path),
            )

        assert ok is True

    def test_retention_failure_does_not_fail_pipeline(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CONVERSION_FINGERPRINT_ENABLED", "1")
        monkeypatch.setenv("CONVERSION_FP_OBSERVATION_ENABLED", "1")
        monkeypatch.setenv("DROPBOX_INPUT_PATH", "/dropbox/in")
        monkeypatch.setenv("DROPBOX_PROCESSED_PATH", "/dropbox/out")

        conv_name = "conversion_obs_ret.xlsx"
        card_name = "card_obs_ret.xlsx"
        conv_local = tmp_path / conv_name
        card_local = tmp_path / card_name
        conv_local.write_bytes(b"conv")
        card_local.write_bytes(b"card")

        fp_obj = _fp()

        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(conversion_module, "run", return_value={"summary": {}}), patch.object(
            conversion_pipeline, "build_conversion_fingerprint", return_value=fp_obj
        ), patch.object(conversion_pipeline, "state_get", return_value=None), patch.object(
            conversion_pipeline, "append_event"
        ), patch.object(conversion_pipeline, "state_update"), patch.object(
            obs, "apply_retention", side_effect=OSError("retention boom")
        ):
            ok = conversion_pipeline.run_conversion_pipeline(
                conv_name,
                card_filename=card_name,
                conv_local_path=str(conv_local),
                card_local_path=str(card_local),
                local_tmp_path=str(tmp_path),
            )

        assert ok is True


class TestPipelineInvariants:
    @pytest.fixture
    def pipeline_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DROPBOX_INPUT_PATH", "/dropbox/in")
        monkeypatch.setenv("DROPBOX_PROCESSED_PATH", "/dropbox/out")
        monkeypatch.setenv("CONVERSION_FINGERPRINT_ENABLED", "1")
        monkeypatch.setenv("CONVERSION_FP_OBSERVATION_ENABLED", "1")
        monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
        conv_name = "conversion_obs_inv.xlsx"
        card_name = "card_obs_inv.xlsx"
        conv_local = tmp_path / conv_name
        card_local = tmp_path / card_name
        conv_local.write_bytes(b"conv")
        card_local.write_bytes(b"card")
        return {
            "conv_name": conv_name,
            "card_name": card_name,
            "conv_local": str(conv_local),
            "card_local": str(card_local),
            "tmp_root": str(tmp_path),
            "obs_dir": tmp_path / "state" / "observability",
        }

    def test_observation_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("CONVERSION_FP_OBSERVATION_ENABLED", raising=False)
        assert obs.observation_enabled() is False

    def test_conversion_run_still_executes_with_observation(self, pipeline_env):
        matched_fp = "deadbeef" * 8
        run_calls: list[tuple] = []

        def fake_run(*args, **kwargs):
            run_calls.append((args, kwargs))
            return {"summary": {}}

        fp_obj = _fp(combined=matched_fp)

        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(conversion_module, "run", side_effect=fake_run), patch.object(
            conversion_pipeline, "build_conversion_fingerprint", return_value=fp_obj
        ), patch.object(conversion_pipeline, "state_get", return_value=matched_fp), patch.object(
            conversion_pipeline, "append_event"
        ), patch.object(conversion_pipeline, "state_update"):
            ok = conversion_pipeline.run_conversion_pipeline(
                pipeline_env["conv_name"],
                card_filename=pipeline_env["card_name"],
                conv_local_path=pipeline_env["conv_local"],
                card_local_path=pipeline_env["card_local"],
                local_tmp_path=pipeline_env["tmp_root"],
            )

        assert ok is True
        assert len(run_calls) == 1

        files = list(pipeline_env["obs_dir"].glob("conversion_fp_observation_*.jsonl"))
        assert len(files) == 1
        record = json.loads(files[0].read_text(encoding="utf-8").strip())
        assert record["matched_previous"] is True
        assert record["would_skip"] is True
        assert record["outcome"] == "success"
        assert len(record["fingerprint"]) == 64

    def test_no_skip_when_matched(self, pipeline_env):
        events: list[dict] = []

        def track_event(*, type, job_type=None, payload=None, **kwargs):
            events.append({"type": type, "payload": payload or {}})

        fp_obj = _fp(combined="abc" * 21 + "a")

        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(conversion_module, "run", return_value={"summary": {}}), patch.object(
            conversion_pipeline, "build_conversion_fingerprint", return_value=fp_obj
        ), patch.object(conversion_pipeline, "state_get", return_value=fp_obj.combined), patch.object(
            conversion_pipeline, "append_event", side_effect=track_event
        ), patch.object(conversion_pipeline, "state_update"):
            ok = conversion_pipeline.run_conversion_pipeline(
                pipeline_env["conv_name"],
                card_filename=pipeline_env["card_name"],
                conv_local_path=pipeline_env["conv_local"],
                card_local_path=pipeline_env["card_local"],
                local_tmp_path=pipeline_env["tmp_root"],
            )

        assert ok is True
        skipped = [e for e in events if e["type"] == "conversion_skipped"]
        assert skipped == []

    def test_fingerprint_computed_event_unchanged(self, pipeline_env):
        fp_obj = _fp()
        events: list[dict] = []

        def track_event(*, type, job_type=None, payload=None, **kwargs):
            events.append({"type": type, "payload": payload or {}})

        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(conversion_module, "run", return_value={"summary": {}}), patch.object(
            conversion_pipeline, "build_conversion_fingerprint", return_value=fp_obj
        ), patch.object(conversion_pipeline, "state_get", return_value=None), patch.object(
            conversion_pipeline, "append_event", side_effect=track_event
        ), patch.object(conversion_pipeline, "state_update"):
            conversion_pipeline.run_conversion_pipeline(
                pipeline_env["conv_name"],
                card_filename=pipeline_env["card_name"],
                conv_local_path=pipeline_env["conv_local"],
                card_local_path=pipeline_env["card_local"],
                local_tmp_path=pipeline_env["tmp_root"],
            )

        computed = [e for e in events if e["type"] == "conversion_fingerprint_computed"]
        assert len(computed) == 1
        payload = computed[0]["payload"]
        assert set(payload.keys()) == {
            "fingerprint_prefix",
            "matched_previous",
            "conv_hash_prefix",
            "card_hash_prefix",
            "rules_hash_prefix",
            "special_cards_hash_prefix",
        }
