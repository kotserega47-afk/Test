"""Unit tests for Postgres lifecycle field refresh CLI."""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pandas as pd
import pytest

from integrations.wallet_editor_registry_db.mapping import map_all_results_row
from integrations.wallet_editor_registry_db.refresh_lifecycle_fields import (
    _apply_patches,
    plan_lifecycle_patches,
    refresh_lifecycle_fields_from_postgres,
)
from integrations.wallet_editor_registry_db.store import InMemoryRegistryStore
from integrations.wallet_editor_registry_lifecycle import (
    ALL_RESULTS_COLUMNS,
    HOLD_COLUMNS,
    MISSING_OTLEZKA_DATE_TEXT,
    MISSING_OTLEZKA_STATUS,
    OTLEZKA_COLUMNS,
    STATUS_OZHIDAET,
    normalize_all_results,
    result_row_fingerprint,
)
from tools.refresh_registry_lifecycle_fields import main as refresh_cli_main

TODAY = date(2026, 6, 23)


def _row(**overrides) -> dict:
    base = {
        "Дата операции": "22.06.2026",
        "Дата отключения": "22.06.2026 09:00:00",
        "Дата включения": MISSING_OTLEZKA_DATE_TEXT,
        "Статус включения": MISSING_OTLEZKA_STATUS,
        "Включено": "",
        "Комментарий включения": "",
        "card": "4111111111111111",
        "partner": "Ostin",
        "action": "remove_partner",
        "status": "OK",
        "comment": "orig comment",
        "hold": "",
    }
    base.update(overrides)
    return base


def _all_results_df(*rows: dict) -> pd.DataFrame:
    return pd.DataFrame(list(rows), columns=ALL_RESULTS_COLUMNS)


def _otlezka_df(*, partner: str = "Ostin", days: int = 5) -> pd.DataFrame:
    return pd.DataFrame(
        [{"partner": partner, "Полные дни": days, "comment": ""}],
        columns=OTLEZKA_COLUMNS,
    )


def _empty_hold() -> pd.DataFrame:
    return pd.DataFrame(columns=HOLD_COLUMNS)


def _seed_store(df: pd.DataFrame, store: InMemoryRegistryStore) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    normalized = normalize_all_results(df)
    for idx in normalized.index:
        mapped = map_all_results_row(normalized.loc[idx], source_row_index=int(idx))
        store.results[mapped.row_fingerprint] = mapped
        fingerprints[str(idx)] = mapped.row_fingerprint
    return fingerprints


class TestPlanLifecyclePatches:
    def test_stale_missing_otlezka_recalculated_when_partner_configured(self):
        df = _all_results_df(_row())
        patches, stale_rows, missing = plan_lifecycle_patches(
            df,
            _empty_hold(),
            _otlezka_df(days=5),
            today=TODAY,
        )

        assert stale_rows == 1
        assert missing == set()
        assert len(patches) == 1
        assert patches[0].sample.before_reenable == MISSING_OTLEZKA_DATE_TEXT
        assert patches[0].sample.after_reenable == "27.06.2026"
        assert patches[0].sample.after_status == STATUS_OZHIDAET

    def test_missing_otlezka_remains_when_partner_not_configured(self):
        df = _all_results_df(_row(partner="UnknownPartner"))
        patches, stale_rows, missing = plan_lifecycle_patches(
            df,
            _empty_hold(),
            _otlezka_df(),
            today=TODAY,
        )

        assert stale_rows == 1
        assert missing == {"UnknownPartner"}
        assert patches == []

    def test_fingerprint_uses_before_operation_fields(self):
        df = _all_results_df(_row(partner="Ostin"))
        before_fp = result_row_fingerprint(normalize_all_results(df).iloc[0])
        patches, _stale, _missing = plan_lifecycle_patches(
            df,
            _empty_hold(),
            _otlezka_df(),
            today=TODAY,
        )
        assert patches[0].row_fingerprint == before_fp


class TestRefreshLifecycleApply:
    def test_dry_run_does_not_update_db(self):
        df = _all_results_df(_row())
        store = InMemoryRegistryStore()
        _seed_store(df, store)

        with (
            patch(
                "integrations.wallet_editor_registry_db.refresh_lifecycle_fields.load_registry_frames_from_postgres",
                return_value=(df, pd.DataFrame()),
            ),
            patch(
                "integrations.wallet_editor_registry_db.refresh_lifecycle_fields.load_hold_otlezka_for_runtime",
                return_value=(_empty_hold(), _otlezka_df(), True, True),
            ),
            patch(
                "integrations.wallet_editor_registry_db.refresh_lifecycle_fields.wallet_editor_dropbox_path",
                return_value="/dropbox/wallet_editor.xlsx",
            ),
        ):
            summary = refresh_lifecycle_fields_from_postgres(apply=False, store=store)

        assert summary.dry_run is True
        assert summary.rows_changed == 1
        row = next(iter(store.results.values()))
        assert row.reenable_date == MISSING_OTLEZKA_DATE_TEXT
        assert row.enable_status == MISSING_OTLEZKA_STATUS

    def test_apply_updates_stale_rows(self):
        df = _all_results_df(_row())
        store = InMemoryRegistryStore()
        fingerprints = _seed_store(df, store)
        patches, _stale, _missing = plan_lifecycle_patches(
            df,
            _empty_hold(),
            _otlezka_df(days=5),
            today=TODAY,
        )

        updated = _apply_patches(patches, store)

        assert updated == 1
        row = store.results[fingerprints["0"]]
        assert row.reenable_date == "27.06.2026"
        assert row.enable_status == STATUS_OZHIDAET

    def test_apply_preserves_operation_fields_and_fingerprint(self):
        df = _all_results_df(_row())
        store = InMemoryRegistryStore()
        fingerprints = _seed_store(df, store)
        fingerprint = fingerprints["0"]
        before = store.results[fingerprint]
        patches, _stale, _missing = plan_lifecycle_patches(
            df,
            _empty_hold(),
            _otlezka_df(days=5),
            today=TODAY,
        )

        _apply_patches(patches, store)
        after = store.results[fingerprint]

        assert fingerprint in store.results
        assert after.row_fingerprint == before.row_fingerprint
        assert after.card == before.card
        assert after.partner == before.partner
        assert after.action == before.action
        assert after.status == before.status
        assert after.comment == before.comment
        assert after.disable_at == before.disable_at
        assert after.operation_date == before.operation_date
        assert after.enable_comment == before.enable_comment
        assert after.vklyucheno == before.vklyucheno

    def test_rerun_after_apply_is_noop(self):
        df = _all_results_df(_row())
        patches_first, _stale, _missing = plan_lifecycle_patches(
            df,
            _empty_hold(),
            _otlezka_df(days=5),
            today=TODAY,
        )
        store = InMemoryRegistryStore()
        _seed_store(df, store)
        _apply_patches(patches_first, store)

        from integrations.wallet_editor_registry_db.frames import result_row_to_dict

        stored_row = next(iter(store.results.values()))
        refreshed_df = pd.DataFrame(
            [result_row_to_dict(stored_row)],
            columns=ALL_RESULTS_COLUMNS,
        )
        patches_second, stale_second, _missing2 = plan_lifecycle_patches(
            refreshed_df,
            _empty_hold(),
            _otlezka_df(days=5),
            today=TODAY,
        )

        assert stale_second == 0
        assert patches_second == []

    def test_apply_updates_postgres_when_rows_changed(self):
        df = _all_results_df(_row())
        store = InMemoryRegistryStore()
        _seed_store(df, store)

        with (
            patch(
                "integrations.wallet_editor_registry_db.refresh_lifecycle_fields.load_registry_frames_from_postgres",
                return_value=(df, pd.DataFrame()),
            ),
            patch(
                "integrations.wallet_editor_registry_db.refresh_lifecycle_fields.load_hold_otlezka_for_runtime",
                return_value=(_empty_hold(), _otlezka_df(), True, True),
            ),
            patch(
                "integrations.wallet_editor_registry_db.refresh_lifecycle_fields.wallet_editor_dropbox_path",
                return_value="/dropbox/wallet_editor.xlsx",
            ),
        ):
            summary = refresh_lifecycle_fields_from_postgres(apply=True, store=store)

        assert summary.rows_changed >= 1
        assert summary.error_count == 0


class TestRefreshLifecycleCli:
    def test_cli_dry_run_reports_stale_rows(self, capsys):
        df = _all_results_df(_row())

        with (
            patch(
                "integrations.wallet_editor_registry_db.refresh_lifecycle_fields.load_registry_frames_from_postgres",
                return_value=(df, pd.DataFrame()),
            ),
            patch(
                "integrations.wallet_editor_registry_db.refresh_lifecycle_fields.load_hold_otlezka_for_runtime",
                return_value=(_empty_hold(), _otlezka_df(), True, True),
            ),
            patch(
                "integrations.wallet_editor_registry_db.refresh_lifecycle_fields.wallet_editor_dropbox_path",
                return_value="/dropbox/wallet_editor.xlsx",
            ),
        ):
            code = refresh_cli_main([])

        output = capsys.readouterr().out
        assert code == 0
        assert "mode: dry-run" in output
        assert "rows with old Нет даты отлёжки: 1" in output
        assert "rows changed: 1" in output
