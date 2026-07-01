"""Unit tests for auto-enable registry completeness warnings."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from integrations.wallet_editor_registry import (
    RegistryHealthReport,
    registry_stale_outbox_warning,
)


def _health_report(**overrides) -> RegistryHealthReport:
    defaults = {
        "outbox_pending_count": 0,
        "outbox_failed_count": 0,
        "outbox_synced_count": 0,
        "oldest_pending_age_sec": None,
        "last_sync_error": None,
        "processed_without_rows_count": 0,
        "processed_run_ids_corrupted": False,
        "processed_run_ids_corruption_error": None,
        "overdue_ready_count": 0,
        "missing_otlezka_count": 0,
        "missing_durable_result_count": 0,
        "stale_outbox": False,
        "degraded": False,
        "registry_source": "excel",
    }
    defaults.update(overrides)
    return RegistryHealthReport(**defaults)


class TestRegistryStaleOutboxWarning:
    @pytest.mark.parametrize(
        ("failed", "processed_without_rows", "expected_kind"),
        [
            (33, 0, "historical"),
            (33, 2, "incomplete"),
            (0, 2, "incomplete"),
            (0, 0, "none"),
        ],
        ids=[
            "failed_only",
            "failed_and_missing_rows",
            "missing_rows",
            "all_clear",
        ],
    )
    def test_decision_table_failed_vs_missing_rows(
        self,
        failed: int,
        processed_without_rows: int,
        expected_kind: str,
    ):
        report = _health_report(
            registry_source="postgres",
            outbox_failed_count=failed,
            processed_without_rows_count=processed_without_rows,
        )
        with patch(
            "integrations.wallet_editor_registry.build_registry_health_report",
            return_value=report,
        ):
            message = registry_stale_outbox_warning()

        if expected_kind == "none":
            assert message is None
        elif expected_kind == "incomplete":
            assert message is not None
            assert "Registry may be incomplete" in message
            if failed:
                assert f"failed={failed}" in message or f"processed_without_rows={processed_without_rows}" in message
            if processed_without_rows:
                assert f"processed_without_rows={processed_without_rows}" in message
        elif expected_kind == "historical":
            assert message is not None
            assert "Historical failed outbox records: 33" in message
            assert "Registry integrity verified." in message
            assert "Registry may be incomplete" not in message

    def test_failed_only_uses_historical_warning(self):
        report = _health_report(registry_source="postgres", outbox_failed_count=5)
        with patch(
            "integrations.wallet_editor_registry.build_registry_health_report",
            return_value=report,
        ):
            message = registry_stale_outbox_warning()

        assert message == (
            "ℹ️ Historical failed outbox records: 5\nRegistry integrity verified."
        )

    def test_postgres_pending_still_warns_without_failed_signal(self):
        report = _health_report(
            registry_source="postgres",
            outbox_pending_count=2,
            outbox_failed_count=33,
        )
        with patch(
            "integrations.wallet_editor_registry.build_registry_health_report",
            return_value=report,
        ):
            message = registry_stale_outbox_warning()

        assert message == "⚠️ Registry may be incomplete: pending=2"

    def test_postgres_stale_outbox_still_warns_even_with_historical_failed(self):
        report = _health_report(
            registry_source="postgres",
            outbox_failed_count=33,
            stale_outbox=True,
            oldest_pending_age_sec=7200.0,
        )
        with patch(
            "integrations.wallet_editor_registry.build_registry_health_report",
            return_value=report,
        ):
            message = registry_stale_outbox_warning()

        assert message is not None
        assert "Registry may be incomplete" in message
        assert "registry outbox stale" in message
        assert "failed=33" not in message
