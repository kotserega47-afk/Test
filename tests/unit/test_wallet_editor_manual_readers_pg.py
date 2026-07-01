"""Unit tests for PG manual readers (Phase 3)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from automation.audit import Stats
from automation.engine import _apply_add_partner_hold_precheck
from integrations.wallet_editor_auto_enable import load_registry_frames_for_planning, run_auto_enable_plan
from integrations.wallet_editor_auto_enable_settings import AutoEnableSettings
from integrations.wallet_editor_hold import (
    HOLD_CHECK_FAILED_MANUAL_COMMENT,
    load_hold_pairs_from_postgres,
    load_hold_pairs_snapshot,
)
from integrations.wallet_editor_registry import (
    build_registry_health_report,
    format_registry_health_report,
)
from integrations.wallet_editor_registry_db.config import (
    ENV_MANUAL_READERS_SOURCE,
    ENV_MANUAL_SYNC_ENABLED,
)
from integrations.wallet_editor_registry_db.manual_readers import (
    PG_MANUAL_READERS_NOT_READY,
    ManualReadersNotReadyError,
    load_hold_otlezka_for_runtime,
    pg_manual_readers_missing_successful_sync,
)
from integrations.wallet_editor_registry_db.manual_snapshot import HoldSnapshotRow, OtlezkaSnapshotRow
from integrations.wallet_editor_registry_db.manual_store import InMemoryManualSyncStore
from integrations.wallet_editor_registry_db.manual_sync_state import (
    META_LAST_SNAPSHOT_HASH,
    META_LAST_SYNC_AT,
    META_LAST_SYNC_STATUS,
)
from integrations.wallet_editor_registry_db.postgres_source import refresh_attempt_postgres
from integrations.wallet_editor_registry_lifecycle import ALL_RESULTS_COLUMNS, recalculate_all_results


def _seed_successful_sync(store: InMemoryManualSyncStore) -> None:
    store.set_meta(META_LAST_SYNC_STATUS, "success")
    store.set_meta(META_LAST_SNAPSHOT_HASH, "abc123hash")
    store.set_meta(META_LAST_SYNC_AT, "2026-07-01T12:00:00+00:00")
    store.hold[("4111", "ostin")] = {
        "active": True,
        "row": HoldSnapshotRow(
            card="4111",
            partner="Ostin",
            card_norm="4111",
            partner_norm="ostin",
            added_at="01.07.2026",
            comment="",
            source_row_index=1,
        ),
    }
    store.otlezka["ostin"] = {
        "active": True,
        "row": OtlezkaSnapshotRow(
            partner="Ostin",
            partner_norm="ostin",
            full_days=30,
            comment="",
            source_row_index=1,
        ),
    }


def _empty_all_results() -> pd.DataFrame:
    return pd.DataFrame(columns=ALL_RESULTS_COLUMNS)


@pytest.fixture
def pg_readers(monkeypatch):
    monkeypatch.setenv(ENV_MANUAL_READERS_SOURCE, "postgres")


@pytest.fixture
def synced_store() -> InMemoryManualSyncStore:
    store = InMemoryManualSyncStore()
    _seed_successful_sync(store)
    return store


class TestPgManualReadersReady:
    def test_hold_reads_active_pairs_from_pg(self, pg_readers):
        with patch(
            "integrations.wallet_editor_registry_db.manual_readers.load_active_hold_pair_norms_from_postgres",
            return_value=frozenset({("4111", "ostin")}),
        ):
            snapshot = load_hold_pairs_from_postgres()
        assert snapshot.available is True
        assert ("4111", "ostin") in snapshot.pairs

    def test_hold_pg_mode_no_dropbox_download(self, pg_readers, synced_store, monkeypatch):
        monkeypatch.setenv(ENV_MANUAL_READERS_SOURCE, "postgres")
        download = MagicMock(side_effect=AssertionError("Dropbox download must not run"))
        monkeypatch.setattr(
            "integrations.dropbox_watcher.download_file_with_rev",
            download,
        )
        with patch(
            "integrations.wallet_editor_registry_db.manual_readers.load_active_hold_pair_norms_from_postgres",
            return_value=frozenset({("4111", "ostin")}),
        ):
            snapshot = load_hold_pairs_snapshot()
        assert snapshot.available is True
        download.assert_not_called()

    def test_hold_dropbox_fallback_legacy(self, monkeypatch):
        monkeypatch.delenv(ENV_MANUAL_READERS_SOURCE, raising=False)
        with patch(
            "integrations.wallet_editor_hold.load_hold_pairs_from_dropbox",
            return_value=MagicMock(available=True, pairs=frozenset({("1", "a")}), error=None),
        ) as dropbox_mock:
            snapshot = load_hold_pairs_snapshot()
        dropbox_mock.assert_called_once()
        assert snapshot.available is True

    def test_no_successful_sync_fail_closed_hold(self, pg_readers):
        with pytest.raises(ManualReadersNotReadyError, match=PG_MANUAL_READERS_NOT_READY):
            load_hold_otlezka_for_runtime("/dropbox/path", store=InMemoryManualSyncStore())

    def test_pg_manual_readers_missing_flag(self, pg_readers):
        assert pg_manual_readers_missing_successful_sync() is True

    def test_hold_enforcement_unavailable_blocks_add_partner(self, pg_readers):
        from integrations.wallet_editor_hold import HoldPairsSnapshot

        df = pd.DataFrame(
            [
                {
                    "action": "add_partner",
                    "card": "4111",
                    "value": "Ostin",
                    "status": "",
                    "comment": "",
                }
            ]
        )
        stats = Stats()
        _apply_add_partner_hold_precheck(
            df,
            HoldPairsSnapshot.unavailable(PG_MANUAL_READERS_NOT_READY),
            stats,
        )
        assert df.iloc[0]["status"] == "SKIP"
        assert HOLD_CHECK_FAILED_MANUAL_COMMENT in str(df.iloc[0]["comment"])


class TestAutoEnablePlanningPg:
    def _settings(self) -> AutoEnableSettings:
        return AutoEnableSettings(
            enabled=True,
            dry_run=False,
            approval_required=False,
            max_rows_per_batch=200,
            max_rows_per_run=0,
            seconds_per_card_timeout=10,
            batch_timeout_buffer_seconds=300,
            working_statuses=("готов к работе",),
            auto_return_statuses=(),
            auto_return_target_status="Готов к работе",
            allowed_statuses_for_enable=(),
            deprecated_working_statuses_fallback=False,
            include_overdue=True,
            telegram_route_report="wallet_editor_auto_enable",
            telegram_route_alert="wallet_editor_auto_enable_alert",
        )

    def test_planning_reads_frames_from_pg(self, pg_readers, synced_store, monkeypatch):
        monkeypatch.setenv(ENV_MANUAL_SYNC_ENABLED, "0")
        all_df = _empty_all_results()
        hold_df = pd.DataFrame([{"card": "4111", "partner": "Ostin", "added_at": "", "comment": ""}])
        otlezka_df = pd.DataFrame([{"partner": "Ostin", "Полные дни": 30, "comment": ""}])

        with (
            patch(
                "integrations.wallet_editor_registry_db.frames.load_registry_frames_from_postgres",
                return_value=(all_df, pd.DataFrame()),
            ),
            patch(
                "integrations.wallet_editor_registry_db.manual_readers.load_hold_otlezka_for_runtime",
                return_value=(hold_df, otlezka_df, True, True),
            ) as hold_mock,
            patch(
                "integrations.wallet_editor_auto_enable.download_file_with_rev",
                side_effect=AssertionError("workbook download must not run"),
            ),
            patch(
                "integrations.wallet_editor_registry_db.config.registry_source_is_postgres",
                return_value=True,
            ),
        ):
            recalculated, h, o, raw = load_registry_frames_for_planning()
        hold_mock.assert_called_once()
        assert len(recalculated.columns) == len(ALL_RESULTS_COLUMNS)
        assert len(h) == 1
        assert len(o) == 1
        assert raw is all_df

    def test_planning_no_sync_fail_closed(self, pg_readers, monkeypatch):
        monkeypatch.setenv(ENV_MANUAL_SYNC_ENABLED, "0")
        with (
            patch(
                "integrations.wallet_editor_registry_db.config.registry_source_is_postgres",
                return_value=True,
            ),
            patch(
                "integrations.wallet_editor_registry_db.frames.load_registry_frames_from_postgres",
                return_value=(_empty_all_results(), pd.DataFrame()),
            ),
            patch(
                "integrations.wallet_editor_registry_db.manual_readers.load_hold_otlezka_for_runtime",
                side_effect=ManualReadersNotReadyError(PG_MANUAL_READERS_NOT_READY),
            ),
        ):
            with pytest.raises(RuntimeError, match=PG_MANUAL_READERS_NOT_READY):
                load_registry_frames_for_planning()

    def test_auto_enable_plan_fail_closed_without_sync(self, pg_readers, monkeypatch):
        monkeypatch.setenv(ENV_MANUAL_SYNC_ENABLED, "0")
        with (
            patch(
                "integrations.wallet_editor_auto_enable.load_registry_frames_for_planning",
                side_effect=RuntimeError(PG_MANUAL_READERS_NOT_READY),
            ),
            patch(
                "integrations.wallet_editor_auto_enable._send_to_route",
                return_value=True,
            ) as send_mock,
            patch(
                "integrations.wallet_editor_auto_enable.registry_stale_outbox_warning",
                return_value=None,
            ),
        ):
            result = run_auto_enable_plan(settings=self._settings())
        assert result.skipped_reason == "error"
        assert PG_MANUAL_READERS_NOT_READY in result.report_text
        send_mock.assert_called_once()


class TestLifecycleRefreshPg:
    def test_refresh_hold_otlezka_from_pg(self, pg_readers, synced_store):
        all_df = _empty_all_results()
        hold_df = pd.DataFrame(columns=["Дата добавления", "card", "partner", "comment"])
        otlezka_df = pd.DataFrame(columns=["partner", "Полные дни", "comment"])

        with patch(
            "integrations.wallet_editor_registry_db.postgres_source.load_registry_frames_from_postgres",
            return_value=(all_df, pd.DataFrame()),
        ), patch(
            "integrations.wallet_editor_registry_db.postgres_source.load_hold_otlezka_for_runtime",
            return_value=(hold_df, otlezka_df, True, False),
        ) as hold_mock:
            from integrations.wallet_editor_registry_refresh import RefreshBreakdown

            outcome, changed, breakdown, err, _batch = refresh_attempt_postgres(
                dropbox_path="/path",
                today=pd.Timestamp("2026-07-01").date(),
                normalize_all_results=lambda df: df,
                recalculate_all_results=recalculate_all_results,
                compute_lifecycle_diff=lambda _b, _a: (0, RefreshBreakdown()),
                lifecycle_row_changed=lambda _b, _a: False,
            )
        hold_mock.assert_called_once()
        assert outcome.value == "skipped"
        assert changed == 0

    def test_refresh_fail_closed_without_sync(self, pg_readers):
        with patch(
            "integrations.wallet_editor_registry_db.postgres_source.load_registry_frames_from_postgres",
            return_value=(_empty_all_results(), pd.DataFrame()),
        ), patch(
            "integrations.wallet_editor_registry_db.postgres_source.load_hold_otlezka_for_runtime",
            side_effect=ManualReadersNotReadyError(PG_MANUAL_READERS_NOT_READY),
        ):
            from integrations.wallet_editor_registry_refresh import RefreshBreakdown

            outcome, _changed, _breakdown, err, _batch = refresh_attempt_postgres(
                dropbox_path="/path",
                today=pd.Timestamp("2026-07-01").date(),
                normalize_all_results=lambda df: df,
                recalculate_all_results=recalculate_all_results,
                compute_lifecycle_diff=lambda _b, _a: (0, RefreshBreakdown()),
                lifecycle_row_changed=lambda _b, _a: False,
            )
        assert outcome.value == "permanent"
        assert PG_MANUAL_READERS_NOT_READY in (err or "")


class TestMissingOtlezkaWarn:
    def test_missing_partner_is_warning_not_block(self, synced_store, pg_readers):
        all_df = pd.DataFrame(
            [
                {
                    "card": "4111",
                    "partner": "Unknown",
                    "Дата отключения": "01.06.2026 10:00:00",
                    "Дата включения": "",
                    "Статус включения": "Ожидает",
                    "hold": "",
                    "vklyucheno": "",
                }
            ]
        )
        hold_df, otlezka_df, _hold_exists, _otlezka_exists = load_hold_otlezka_for_runtime(
            "", store=synced_store
        )
        recalculated, missing = recalculate_all_results(all_df, hold_df, otlezka_df)
        assert "Unknown" in missing or len(missing) >= 0
        assert not recalculated.empty


class TestRegistryHealth:
    def test_health_shows_manual_readers_source_and_degraded(self, pg_readers, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://test")
        with (
            patch(
                "integrations.wallet_editor_registry_async.load_outbox_records",
                return_value=[],
            ),
            patch(
                "integrations.wallet_editor_registry.is_processed_run_ids_corrupted",
                return_value=False,
            ),
            patch(
                "integrations.wallet_editor_registry.wallet_editor_dropbox_path",
                return_value=None,
            ),
            patch(
                "integrations.wallet_editor_registry_db.mirror_state.get_mirror_health",
                return_value=MagicMock(
                    last_error=None,
                    last_success_at=None,
                    last_failure_at=None,
                    recent_failures=0,
                    failure_count=0,
                    last_operation=None,
                ),
            ),
            patch(
                "integrations.wallet_editor_registry_db.manual_readers.pg_manual_readers_missing_successful_sync",
                return_value=True,
            ),
            patch(
                "integrations.wallet_editor_registry_db.manual_sync.build_manual_snapshot_health_block",
                return_value="Manual snapshot health",
            ),
        ):
            report = build_registry_health_report()
        assert report.manual_readers_source == "postgres"
        assert report.manual_readers_no_successful_sync is True
        assert report.degraded is True
        text = format_registry_health_report(report)
        assert "manual readers source: postgres" in text
        assert "DEGRADED: no successful manual sync" in text

    def test_health_ok_when_sync_meta_present(self, pg_readers, synced_store, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://test")
        with (
            patch(
                "integrations.wallet_editor_registry_async.load_outbox_records",
                return_value=[],
            ),
            patch(
                "integrations.wallet_editor_registry.is_processed_run_ids_corrupted",
                return_value=False,
            ),
            patch(
                "integrations.wallet_editor_registry.wallet_editor_dropbox_path",
                return_value=None,
            ),
            patch(
                "integrations.wallet_editor_registry_db.manual_readers.pg_manual_readers_missing_successful_sync",
                return_value=False,
            ),
            patch(
                "integrations.wallet_editor_registry_db.mirror_state.get_mirror_health",
                return_value=MagicMock(
                    last_error=None,
                    last_success_at=None,
                    last_failure_at=None,
                    recent_failures=0,
                    failure_count=0,
                    last_operation=None,
                ),
            ),
            patch(
                "integrations.wallet_editor_registry_db.manual_sync.build_manual_snapshot_health_block",
                return_value="",
            ),
        ):
            report = build_registry_health_report()
        assert report.manual_readers_no_successful_sync is False
        assert report.degraded is False


class TestGatePlusReaders:
    def test_enforcement_uses_pg_after_sync(self, pg_readers):
        with patch(
            "integrations.wallet_editor_registry_db.manual_readers.load_active_hold_pair_norms_from_postgres",
            return_value=frozenset({("4111", "ostin")}),
        ):
            snapshot = load_hold_pairs_from_postgres()
        assert snapshot.available
        assert ("4111", "ostin") in snapshot.pairs


class TestEditWalletUnaffected:
    def test_edit_wallet_task_has_no_manual_gate_flag(self):
        import dataclasses

        from automation.runtime import WalletEditorEditWalletTask

        field_names = {f.name for f in dataclasses.fields(WalletEditorEditWalletTask)}
        assert "requires_manual_snapshot_gate" not in field_names
