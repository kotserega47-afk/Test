"""Unit tests for manual sync pre-run gate (Phase 2)."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from automation.runtime import WalletEditorAddWalletTask, WalletEditorEditWalletTask, WalletEditorTask
from automation.worker import _run_add_wallet_task, _run_disable_task, _run_edit_wallet_task
from integrations.wallet_editor_registry_db.config import ENV_MANUAL_SYNC_ENABLED
from integrations.wallet_editor_registry_db.manual_sync import (
    ManualSyncDecision,
    ManualSyncPrerunResult,
    ManualSyncResult,
    RunSnapshotBinding,
    format_manual_sync_gate_failure_message,
    run_manual_sync_prerun_gate,
)
from integrations.wallet_editor_registry_db.manual_store import InMemoryManualSyncStore


def _binding() -> RunSnapshotBinding:
    return RunSnapshotBinding(
        manual_sync_run_id=42,
        manual_snapshot_hash="abc123def456789",
        manual_snapshot_synced_at="2026-07-01T12:00:00+00:00",
    )


def _ok_prerun(binding: RunSnapshotBinding | None = None) -> ManualSyncPrerunResult:
    b = binding or _binding()
    return ManualSyncPrerunResult(
        ok=True,
        binding=b,
        operator_message=None,
        sync_result=ManualSyncResult(
            decision=ManualSyncDecision.SYNCED,
            blocked=False,
            snapshot_hash=b.manual_snapshot_hash,
            source_rev="rev-1",
            sync_run_id=b.manual_sync_run_id,
            synced_at=b.manual_snapshot_synced_at,
            run_binding=b,
        ),
    )


def _failed_prerun(error: str = "validation failed") -> ManualSyncPrerunResult:
    return ManualSyncPrerunResult(
        ok=False,
        binding=None,
        operator_message=format_manual_sync_gate_failure_message(error),
        sync_result=ManualSyncResult(
            decision=ManualSyncDecision.FAILED,
            blocked=True,
            snapshot_hash=None,
            source_rev="rev-1",
            sync_run_id=None,
            synced_at=None,
            error=error,
        ),
    )


class TestPrerunGateHelper:
    def test_disabled_bypasses_without_sync(self, monkeypatch):
        monkeypatch.delenv(ENV_MANUAL_SYNC_ENABLED, raising=False)
        with patch(
            "integrations.wallet_editor_registry_db.manual_sync.sync_manual_to_postgres"
        ) as sync_mock:
            result = run_manual_sync_prerun_gate(triggered_by="test")
        assert result.ok is True
        assert result.bypassed is True
        sync_mock.assert_not_called()

    def test_failure_message_is_human_readable(self):
        msg = format_manual_sync_gate_failure_message("missing required sheet: hold")
        assert "WalletEditor не запущен" in msg
        assert "hold" in msg
        assert "Dropbox" in msg

    def test_enabled_sync_ok_returns_binding(self, monkeypatch):
        monkeypatch.setenv(ENV_MANUAL_SYNC_ENABLED, "1")
        store = InMemoryManualSyncStore()
        with patch(
            "integrations.wallet_editor_registry_db.manual_sync.sync_manual_to_postgres",
            return_value=ManualSyncResult(
                decision=ManualSyncDecision.SYNCED,
                blocked=False,
                snapshot_hash="h1",
                source_rev="r1",
                sync_run_id=1,
                synced_at="t1",
                run_binding=_binding(),
            ),
        ):
            result = run_manual_sync_prerun_gate(triggered_by="test", store=store)
        assert result.ok is True
        assert result.binding is not None
        assert result.binding.manual_sync_run_id == 42


class TestWorkerDisableGate:
    def _task(self) -> WalletEditorTask:
        return WalletEditorTask(
            file_path="/tmp/in.xlsx",
            chat_id=1001,
            telegram_user_id=2002,
            operator_profile="DENIS",
            source_file_name="in.xlsx",
            login="u",
            password="p",
            auth_state_path="/tmp/auth.json",
        )

    def test_flag_disabled_starts_engine_without_gate(self, monkeypatch):
        monkeypatch.delenv(ENV_MANUAL_SYNC_ENABLED, raising=False)
        task = self._task()
        with patch("automation.worker.run_manual_sync_prerun_gate") as gate_mock, patch(
            "automation.worker.run", return_value=("out.xlsx", MagicMock(summary=lambda: "ok"))
        ) as run_mock, patch("automation.worker.send_text"), patch(
            "automation.worker.send_document"
        ), patch(
            "automation.worker.prepare_registry_outbox_and_schedule"
        ), patch(
            "automation.worker.threading.Thread"
        ):
            _run_disable_task("DENIS", task)
        gate_mock.assert_not_called()
        run_mock.assert_called_once()

    def test_flag_enabled_sync_ok_starts_engine(self, monkeypatch):
        monkeypatch.setenv(ENV_MANUAL_SYNC_ENABLED, "1")
        task = self._task()
        with patch(
            "automation.worker.run_manual_sync_prerun_gate",
            return_value=_ok_prerun(),
        ), patch(
            "automation.worker.run", return_value=("out.xlsx", MagicMock(summary=lambda: "ok"))
        ) as run_mock, patch("automation.worker.send_text"), patch(
            "automation.worker.send_document"
        ), patch(
            "automation.worker.prepare_registry_outbox_and_schedule"
        ), patch(
            "automation.worker.threading.Thread"
        ):
            _run_disable_task("DENIS", task)
        run_mock.assert_called_once()

    def test_flag_enabled_sync_failed_blocks_engine(self, monkeypatch):
        monkeypatch.setenv(ENV_MANUAL_SYNC_ENABLED, "1")
        task = self._task()
        with patch(
            "automation.worker.run_manual_sync_prerun_gate",
            return_value=_failed_prerun(),
        ), patch("automation.worker.run") as run_mock, patch(
            "automation.worker.send_text"
        ) as send_mock:
            _run_disable_task("DENIS", task)
        run_mock.assert_not_called()
        send_mock.assert_called_once()
        assert "WalletEditor не запущен" in send_mock.call_args.kwargs["text"]

    def test_disable_attaches_binding_to_task(self, monkeypatch):
        monkeypatch.setenv(ENV_MANUAL_SYNC_ENABLED, "1")
        binding = _binding()
        task = self._task()
        captured: dict = {}

        def _capture_outbox(task_arg, *args, **kwargs):
            captured["binding"] = task_arg.manual_snapshot_binding

        with patch(
            "automation.worker.run_manual_sync_prerun_gate",
            return_value=_ok_prerun(binding),
        ), patch(
            "automation.worker.run", return_value=("out.xlsx", MagicMock(summary=lambda: "ok"))
        ), patch("automation.worker.send_text"), patch(
            "automation.worker.send_document"
        ), patch(
            "automation.worker.prepare_registry_outbox_and_schedule",
            side_effect=_capture_outbox,
        ), patch(
            "automation.worker.threading.Thread"
        ):
            _run_disable_task("DENIS", task)
        assert captured["binding"] == binding


class TestWorkerAddWalletGate:
    def _task(self, *, requires_gate: bool = False) -> WalletEditorAddWalletTask:
        return WalletEditorAddWalletTask(
            file_path="/tmp/add.xlsx",
            original_filename="add.xlsx",
            operator_profile="DENIS",
            chat_id=1001,
            user_id=2002,
            login="u",
            password="p",
            auth_state_path="/tmp/auth.json",
            requires_manual_snapshot_gate=requires_gate,
        )

    def test_add_wallet_not_gated_by_default(self, monkeypatch):
        monkeypatch.setenv(ENV_MANUAL_SYNC_ENABLED, "1")
        with patch("automation.worker.run_manual_sync_prerun_gate") as gate_mock, patch(
            "automation.add_wallet_engine.run",
            return_value=("out.xlsx", MagicMock(telegram_summary=lambda: "ok")),
        ), patch("automation.worker.send_text"), patch("automation.worker.send_document"), patch(
            "automation.worker.threading.Thread"
        ):
            _run_add_wallet_task("DENIS", self._task())
        gate_mock.assert_not_called()

    def test_add_wallet_gated_when_hold_relevant(self, monkeypatch):
        monkeypatch.setenv(ENV_MANUAL_SYNC_ENABLED, "1")
        with patch(
            "automation.worker.run_manual_sync_prerun_gate",
            return_value=_failed_prerun(),
        ), patch("automation.add_wallet_engine.run") as run_mock, patch(
            "automation.worker.send_text"
        ):
            _run_add_wallet_task("DENIS", self._task(requires_gate=True))
        run_mock.assert_not_called()


class TestWorkerEditWalletNotGated:
    def test_edit_wallet_never_calls_gate(self, monkeypatch):
        monkeypatch.setenv(ENV_MANUAL_SYNC_ENABLED, "1")
        task = WalletEditorEditWalletTask(
            file_path="/tmp/edit.xlsx",
            original_filename="edit.xlsx",
            operator_profile="DENIS",
            chat_id=1001,
            user_id=2002,
            login="u",
            password="p",
            auth_state_path="/tmp/auth.json",
        )
        with patch("automation.worker.run_manual_sync_prerun_gate") as gate_mock, patch(
            "automation.edit_wallet_engine.run",
            return_value=("out.xlsx", MagicMock(telegram_summary=lambda: "ok")),
        ), patch("automation.worker.send_text"), patch("automation.worker.send_document"), patch(
            "automation.worker.threading.Thread"
        ):
            _run_edit_wallet_task("DENIS", task)
        gate_mock.assert_not_called()


class TestAutoEnableGate:
    def test_plan_blocked_when_gate_fails(self, monkeypatch):
        monkeypatch.setenv(ENV_MANUAL_SYNC_ENABLED, "1")
        from integrations.wallet_editor_auto_enable import run_auto_enable_plan

        settings = MagicMock()
        settings.enabled = True
        settings.telegram_route_report = "wallet_editor_auto_enable_report"
        with patch(
            "integrations.wallet_editor_auto_enable.load_auto_enable_settings",
            return_value=settings,
        ), patch(
            "integrations.wallet_editor_auto_enable.registry_stale_outbox_warning",
            return_value=None,
        ), patch(
            "integrations.wallet_editor_auto_enable._require_manual_sync_gate_for_auto_enable",
            return_value=(False, "WalletEditor не запущен"),
        ), patch(
            "integrations.wallet_editor_auto_enable.load_registry_frames_for_planning"
        ) as load_mock, patch(
            "integrations.wallet_editor_auto_enable._send_to_route",
            return_value=True,
        ):
            result = run_auto_enable_plan(manual=True)
        assert result.skipped_reason == "manual_sync_gate"
        load_mock.assert_not_called()

    def test_run_blocked_when_gate_fails(self, monkeypatch):
        monkeypatch.setenv(ENV_MANUAL_SYNC_ENABLED, "1")
        from integrations.wallet_editor_auto_enable import run_auto_enable

        settings = MagicMock()
        settings.enabled = True
        settings.telegram_route_report = "wallet_editor_auto_enable_report"
        with patch(
            "integrations.wallet_editor_auto_enable.load_auto_enable_settings",
            return_value=settings,
        ), patch(
            "integrations.wallet_editor_auto_enable.registry_stale_outbox_warning",
            return_value=None,
        ), patch(
            "integrations.wallet_editor_auto_enable._require_manual_sync_gate_for_auto_enable",
            return_value=(False, "WalletEditor не запущен"),
        ), patch(
            "integrations.wallet_editor_auto_enable.load_registry_frames_for_planning"
        ) as load_mock, patch(
            "integrations.wallet_editor_auto_enable._send_to_route",
            return_value=True,
        ):
            result = run_auto_enable(manual=True)
        assert result.skipped_reason == "manual_sync_gate"
        load_mock.assert_not_called()


class TestRegistryJobsGate:
    def test_replay_blocked_when_gate_fails(self, monkeypatch):
        monkeypatch.setenv(ENV_MANUAL_SYNC_ENABLED, "1")
        from integrations.wallet_editor_registry import replay_pending_outbox_records

        with patch(
            "integrations.wallet_editor_registry_db.manual_sync.run_manual_sync_prerun_gate",
            return_value=_failed_prerun(),
        ):
            result = replay_pending_outbox_records()
        assert result.attempted == 0
        assert result.errors
        assert "WalletEditor не запущен" in result.errors[0]

    def test_refresh_blocked_when_gate_fails(self, monkeypatch):
        monkeypatch.setenv(ENV_MANUAL_SYNC_ENABLED, "1")
        from integrations.wallet_editor_registry_refresh import refresh_wallet_editor_registry_lifecycle

        with patch(
            "integrations.wallet_editor_registry_refresh.wallet_editor_dropbox_path",
            return_value="/dropbox/wallet_editor.xlsx",
        ), patch(
            "integrations.wallet_editor_registry_db.manual_sync.run_manual_sync_prerun_gate",
            return_value=_failed_prerun(),
        ), patch(
            "integrations.wallet_editor_registry_refresh._send_refresh_report",
            return_value=True,
        ):
            result = refresh_wallet_editor_registry_lifecycle()
        assert result.success is False
        assert "WalletEditor не запущен" in (result.error or "")


class TestRegistryHealthManualBlock:
    def test_health_report_includes_manual_block(self):
        from integrations.wallet_editor_registry import (
            RegistryHealthReport,
            format_registry_health_report,
        )

        report = RegistryHealthReport(
            outbox_pending_count=0,
            outbox_failed_count=0,
            outbox_synced_count=0,
            oldest_pending_age_sec=None,
            last_sync_error=None,
            processed_without_rows_count=0,
            processed_run_ids_corrupted=False,
            processed_run_ids_corruption_error=None,
            overdue_ready_count=0,
            missing_otlezka_count=0,
            missing_durable_result_count=0,
            stale_outbox=False,
            degraded=False,
            manual_snapshot_block="Manual snapshot health\nmanual sync enabled: False",
        )
        text = format_registry_health_report(report)
        assert "Manual snapshot health" in text
        assert "manual sync enabled" in text

    def test_build_health_composes_manual_block(self, monkeypatch):
        monkeypatch.delenv(ENV_MANUAL_SYNC_ENABLED, raising=False)
        from integrations.wallet_editor_registry import build_registry_health_report

        with patch(
            "integrations.wallet_editor_registry_async.load_outbox_records",
            return_value=[],
        ), patch(
            "integrations.wallet_editor_registry._count_processed_without_rows",
            return_value=0,
        ), patch(
            "integrations.wallet_editor_registry.is_processed_run_ids_corrupted",
            return_value=False,
        ), patch(
            "integrations.wallet_editor_registry.processed_run_ids_corruption_error",
            return_value=None,
        ), patch(
            "integrations.wallet_editor_registry.wallet_editor_dropbox_path",
            return_value=None,
        ), patch(
            "integrations.wallet_editor_registry_db.manual_sync.build_manual_snapshot_health_block",
            return_value="Manual snapshot health\nmanual sync enabled: False",
        ):
            report = build_registry_health_report()
        assert report.manual_snapshot_block is not None
        assert "manual sync enabled" in report.manual_snapshot_block


class TestSnapshotIsolation:
    def test_gate_called_once_per_worker_disable_run(self, monkeypatch):
        monkeypatch.setenv(ENV_MANUAL_SYNC_ENABLED, "1")
        task = WalletEditorTask(
            file_path="/tmp/in.xlsx",
            chat_id=1,
            telegram_user_id=2,
            operator_profile="DENIS",
            source_file_name="in.xlsx",
            login="u",
            password="p",
            auth_state_path="/tmp/auth.json",
        )
        gate_mock = MagicMock(return_value=_ok_prerun())
        with patch("automation.worker.run_manual_sync_prerun_gate", gate_mock), patch(
            "automation.worker.run", return_value=("out.xlsx", MagicMock(summary=lambda: "ok"))
        ), patch("automation.worker.send_text"), patch("automation.worker.send_document"), patch(
            "automation.worker.prepare_registry_outbox_and_schedule"
        ), patch("automation.worker.threading.Thread"):
            _run_disable_task("DENIS", task)
        assert gate_mock.call_count == 1
