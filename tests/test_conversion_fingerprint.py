"""Passive conversion fingerprint tests (Phase 1A)."""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import patch

import pytest

from analyzers import conversion as conversion_module
from core.datetime_utils import MSK_TZ
from core.rules_v2.models import MetaInfo, RulesSnapshotV2
from core.rules_v2.snapshot_fingerprint import rules_snapshot_fingerprint
from integrations import conversion_pipeline
from integrations.conversion_fingerprint import (
    SPECIAL_CARDS_MISSING,
    build_conversion_fingerprint,
)


def _minimal_snapshot(*, ruleset_version: str = "fp-test") -> RulesSnapshotV2:
    return RulesSnapshotV2(
        meta=MetaInfo(
            ruleset_version=ruleset_version,
            updated_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=MSK_TZ),
            updated_by="pytest",
        )
    )


@pytest.fixture
def fingerprint_env(tmp_path, monkeypatch):
    monkeypatch.setenv("CONVERSION_FINGERPRINT_ENABLED", "1")
    conv_path = tmp_path / "conv.xlsx"
    card_path = tmp_path / "card.xlsx"
    conv_path.write_bytes(b"conv-bytes-v1")
    card_path.write_bytes(b"card-bytes-v1")
    snapshot = _minimal_snapshot()
    return {
        "conv_path": str(conv_path),
        "card_path": str(card_path),
        "snapshot": snapshot,
        "rules_hash": rules_snapshot_fingerprint(snapshot),
    }


def _special_download_writer(special_bytes: bytes):
    def _download(src, dst):
        if src.endswith("special_cards.xlsx"):
            os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
            with open(dst, "wb") as fh:
                fh.write(special_bytes)
            return True
        return False

    return _download


class TestBuildConversionFingerprint:
    def test_conversion_fingerprint_stable_for_same_inputs(self, fingerprint_env):
        with patch(
            "integrations.conversion_fingerprint.get_snapshot_v2",
            return_value=fingerprint_env["snapshot"],
        ):
            first = build_conversion_fingerprint(
                fingerprint_env["conv_path"],
                fingerprint_env["card_path"],
                download_fn=_special_download_writer(b"special-v1"),
            )
            second = build_conversion_fingerprint(
                fingerprint_env["conv_path"],
                fingerprint_env["card_path"],
                download_fn=_special_download_writer(b"special-v1"),
            )

        assert first.combined == second.combined

    def test_conversion_fingerprint_changes_when_conversion_file_changes(
        self, fingerprint_env, tmp_path
    ):
        other_conv = tmp_path / "conv-other.xlsx"
        other_conv.write_bytes(b"conv-bytes-v2")

        with patch(
            "integrations.conversion_fingerprint.get_snapshot_v2",
            return_value=fingerprint_env["snapshot"],
        ):
            baseline = build_conversion_fingerprint(
                fingerprint_env["conv_path"],
                fingerprint_env["card_path"],
                download_fn=_special_download_writer(b"special-v1"),
            )
            changed = build_conversion_fingerprint(
                str(other_conv),
                fingerprint_env["card_path"],
                download_fn=_special_download_writer(b"special-v1"),
            )

        assert baseline.combined != changed.combined
        assert baseline.conv_hash != changed.conv_hash

    def test_conversion_fingerprint_changes_when_card_file_changes(self, fingerprint_env, tmp_path):
        other_card = tmp_path / "card-other.xlsx"
        other_card.write_bytes(b"card-bytes-v2")

        with patch(
            "integrations.conversion_fingerprint.get_snapshot_v2",
            return_value=fingerprint_env["snapshot"],
        ):
            baseline = build_conversion_fingerprint(
                fingerprint_env["conv_path"],
                fingerprint_env["card_path"],
                download_fn=_special_download_writer(b"special-v1"),
            )
            changed = build_conversion_fingerprint(
                fingerprint_env["conv_path"],
                str(other_card),
                download_fn=_special_download_writer(b"special-v1"),
            )

        assert baseline.combined != changed.combined
        assert baseline.card_hash != changed.card_hash

    def test_conversion_fingerprint_changes_when_rules_fingerprint_changes(self, fingerprint_env):
        snapshot_v2 = _minimal_snapshot(ruleset_version="fp-test-v2")

        with patch(
            "integrations.conversion_fingerprint.get_snapshot_v2",
            return_value=fingerprint_env["snapshot"],
        ):
            baseline = build_conversion_fingerprint(
                fingerprint_env["conv_path"],
                fingerprint_env["card_path"],
                download_fn=_special_download_writer(b"special-v1"),
            )

        with patch(
            "integrations.conversion_fingerprint.get_snapshot_v2",
            return_value=snapshot_v2,
        ):
            changed = build_conversion_fingerprint(
                fingerprint_env["conv_path"],
                fingerprint_env["card_path"],
                download_fn=_special_download_writer(b"special-v1"),
            )

        assert baseline.combined != changed.combined
        assert baseline.rules_hash != changed.rules_hash

    def test_conversion_fingerprint_changes_when_special_cards_changes(self, fingerprint_env):
        with patch(
            "integrations.conversion_fingerprint.get_snapshot_v2",
            return_value=fingerprint_env["snapshot"],
        ):
            baseline = build_conversion_fingerprint(
                fingerprint_env["conv_path"],
                fingerprint_env["card_path"],
                download_fn=_special_download_writer(b"special-v1"),
            )
            changed = build_conversion_fingerprint(
                fingerprint_env["conv_path"],
                fingerprint_env["card_path"],
                download_fn=_special_download_writer(b"special-v2"),
            )

        assert baseline.combined != changed.combined
        assert baseline.special_cards_hash != changed.special_cards_hash

    def test_special_cards_missing_is_stable(self, fingerprint_env):
        with patch(
            "integrations.conversion_fingerprint.get_snapshot_v2",
            return_value=fingerprint_env["snapshot"],
        ):
            first = build_conversion_fingerprint(
                fingerprint_env["conv_path"],
                fingerprint_env["card_path"],
                download_fn=lambda _src, _dst: False,
            )
            second = build_conversion_fingerprint(
                fingerprint_env["conv_path"],
                fingerprint_env["card_path"],
                download_fn=lambda _src, _dst: False,
            )

        assert first.special_cards_hash == SPECIAL_CARDS_MISSING
        assert first.combined == second.combined


class TestPassiveFingerprintPipeline:
    @pytest.fixture
    def pipeline_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DROPBOX_INPUT_PATH", "/dropbox/in")
        monkeypatch.setenv("DROPBOX_PROCESSED_PATH", "/dropbox/out")
        monkeypatch.setenv("CONVERSION_FINGERPRINT_ENABLED", "1")
        conv_name = "conversion_fp.xlsx"
        card_name = "card_fp.xlsx"
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
        }

    def test_passive_fingerprint_does_not_skip_conversion_run(self, pipeline_env):
        matched_fp = "deadbeef" * 8
        run_calls: list[tuple] = []
        events: list[dict] = []

        def fake_run(*args, **kwargs):
            run_calls.append((args, kwargs))
            return {"summary": {}}

        def track_event(*, type, job_type=None, payload=None, **kwargs):
            events.append({"type": type, "payload": payload or {}})

        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(conversion_module, "run", side_effect=fake_run), patch.object(
            conversion_pipeline,
            "build_conversion_fingerprint",
            return_value=type(
                "FP",
                (),
                {
                    "combined": matched_fp,
                    "conv_hash": "a" * 64,
                    "card_hash": "b" * 64,
                    "rules_hash": "c" * 64,
                    "special_cards_hash": "d" * 64,
                },
            )(),
        ), patch.object(
            conversion_pipeline, "state_get", return_value=matched_fp
        ), patch.object(conversion_pipeline, "append_event", side_effect=track_event), patch.object(
            conversion_pipeline, "state_update"
        ):
            ok = conversion_pipeline.run_conversion_pipeline(
                pipeline_env["conv_name"],
                card_filename=pipeline_env["card_name"],
                conv_local_path=pipeline_env["conv_local"],
                card_local_path=pipeline_env["card_local"],
                local_tmp_path=pipeline_env["tmp_root"],
                dropbox_input_path="/dropbox/in",
                dropbox_processed_path="/dropbox/out",
            )

        assert ok is True
        assert len(run_calls) == 1
        skipped = [e for e in events if e["type"] == "conversion_skipped"]
        assert skipped == []
        assert not any(e.get("payload", {}).get("reason") == "no_changes" for e in events)

    def test_fingerprint_committed_only_on_success(self, pipeline_env):
        fp_value = "cafebabe" * 8
        state_patches: list[dict] = []

        def track_state(job_type, patch):
            state_patches.append({"job_type": job_type, "patch": patch})

        fp_obj = type(
            "FP",
            (),
            {
                "combined": fp_value,
                "conv_hash": "a" * 64,
                "card_hash": "b" * 64,
                "rules_hash": "c" * 64,
                "special_cards_hash": SPECIAL_CARDS_MISSING,
            },
        )()

        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(conversion_pipeline, "state_update", side_effect=track_state), patch.object(
            conversion_pipeline, "append_event"
        ), patch.object(
            conversion_pipeline, "build_conversion_fingerprint", return_value=fp_obj
        ), patch.object(conversion_pipeline, "state_get", return_value=None), patch.object(
            conversion_module, "run", return_value={"summary": {}}
        ):
            ok_success = conversion_pipeline.run_conversion_pipeline(
                pipeline_env["conv_name"],
                card_filename=pipeline_env["card_name"],
                conv_local_path=pipeline_env["conv_local"],
                card_local_path=pipeline_env["card_local"],
                local_tmp_path=pipeline_env["tmp_root"],
                dropbox_input_path="/dropbox/in",
                dropbox_processed_path="/dropbox/out",
            )

        assert ok_success is True
        success_patches = [
            p["patch"] for p in state_patches if p["patch"].get("last_status") == "success"
        ]
        assert success_patches
        assert success_patches[-1]["last_fingerprint"] == fp_value

        state_patches.clear()
        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(conversion_pipeline, "state_update", side_effect=track_state), patch.object(
            conversion_pipeline, "append_event"
        ), patch.object(
            conversion_pipeline, "build_conversion_fingerprint", return_value=fp_obj
        ), patch.object(conversion_pipeline, "state_get", return_value=None), patch.object(
            conversion_module, "run", side_effect=RuntimeError("boom")
        ), patch.object(conversion_pipeline, "_safe_send"):
            ok_failed = conversion_pipeline.run_conversion_pipeline(
                pipeline_env["conv_name"],
                card_filename=pipeline_env["card_name"],
                conv_local_path=pipeline_env["conv_local"],
                card_local_path=pipeline_env["card_local"],
                local_tmp_path=pipeline_env["tmp_root"],
                dropbox_input_path="/dropbox/in",
                dropbox_processed_path="/dropbox/out",
            )

        assert ok_failed is False
        failed_patches = [
            p["patch"] for p in state_patches if p["patch"].get("last_status") == "failed"
        ]
        assert failed_patches
        assert "last_fingerprint" not in failed_patches[-1]

    def test_passive_fingerprint_emits_computed_event(self, pipeline_env):
        events: list[dict] = []

        def track_event(*, type, job_type=None, payload=None, **kwargs):
            events.append({"type": type, "job_type": job_type, "payload": payload or {}})

        fp_obj = type(
            "FP",
            (),
            {
                "combined": "ab" * 32,
                "conv_hash": "a" * 64,
                "card_hash": "b" * 64,
                "rules_hash": "c" * 64,
                "special_cards_hash": SPECIAL_CARDS_MISSING,
            },
        )()

        with patch.object(conversion_pipeline, "download_file", return_value=True), patch.object(
            conversion_pipeline, "move_file", return_value=True
        ), patch.object(conversion_module, "run", return_value={"summary": {}}), patch.object(
            conversion_pipeline, "append_event", side_effect=track_event
        ), patch.object(conversion_pipeline, "state_update"), patch.object(
            conversion_pipeline, "build_conversion_fingerprint", return_value=fp_obj
        ), patch.object(conversion_pipeline, "state_get", return_value=None):
            conversion_pipeline.run_conversion_pipeline(
                pipeline_env["conv_name"],
                card_filename=pipeline_env["card_name"],
                conv_local_path=pipeline_env["conv_local"],
                card_local_path=pipeline_env["card_local"],
                local_tmp_path=pipeline_env["tmp_root"],
                dropbox_input_path="/dropbox/in",
                dropbox_processed_path="/dropbox/out",
            )

        computed = [e for e in events if e["type"] == "conversion_fingerprint_computed"]
        assert len(computed) == 1
        payload = computed[0]["payload"]
        assert payload["matched_previous"] is False
        assert "fingerprint_prefix" in payload
        assert "conv_hash_prefix" in payload
        assert "card_hash_prefix" in payload
        assert "rules_hash_prefix" in payload
        assert "special_cards_hash_prefix" in payload
