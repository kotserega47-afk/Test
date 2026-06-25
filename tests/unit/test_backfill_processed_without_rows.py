"""Unit tests for processed_run_ids Postgres backfill CLI."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from automation.audit import Stats
from automation.runtime import WalletEditorTask
from integrations.wallet_editor_registry_async import (
    OUTBOX_STATUS_SYNCED,
    create_outbox_record,
    persist_durable_result_copy,
    update_outbox_status,
)
from integrations.wallet_editor_registry_db.backfill_processed import backfill_processed_runs
from integrations.wallet_editor_registry_db.mapping import map_all_results_row
from integrations.wallet_editor_registry_db.store import InMemoryRegistryStore
from integrations.wallet_editor_registry_lifecycle import (
    OPERATION_DATE_COLUMN,
    DISABLE_DATE_COLUMN,
    mark_run_processed,
    outbox_index_path,
    processed_run_ids_path,
    result_row_dates,
    rows_from_result_excel,
)
from integrations.wallet_editor_registry_xlsx import card_as_text
from tools.backfill_processed_without_rows import main as backfill_cli_main

MSK = ZoneInfo("Europe/Moscow")
RUN_STARTED = datetime(2026, 6, 22, 9, 0, 0, tzinfo=MSK)
RUN_FINISHED = datetime(2026, 6, 22, 9, 5, 0, tzinfo=MSK)


def _make_task(*, run_id: str) -> WalletEditorTask:
    return WalletEditorTask(
        file_path="/tmp/wallet_editor/in.xlsx",
        chat_id=-1001,
        telegram_user_id=111,
        operator_profile="DENIS",
        source_file_name="batch.xlsx",
        login="login",
        password="pass",
        auth_state_path="/tmp/auth.json",
        run_id=run_id,
    )


def _write_result(path: Path, *, card: str = "4111111111111111") -> None:
    processed = datetime(2026, 6, 22, 9, 0, 0, tzinfo=MSK)
    operation_date, disable_date = result_row_dates("remove_partner", "OK", processed)
    pd.DataFrame(
        {
            OPERATION_DATE_COLUMN: [operation_date],
            DISABLE_DATE_COLUMN: [disable_date],
            "card": [card],
            "action": ["remove_partner"],
            "value": ["Ostin"],
            "status": ["OK"],
            "comment": [""],
        }
    ).to_excel(path, index=False)


def _setup_synced_processed_run(
    tmp_path: Path,
    *,
    run_id: str,
    card: str = "4111111111111111",
) -> Path:
    result_path = tmp_path / f"{run_id}.xlsx"
    _write_result(result_path, card=card)
    task = _make_task(run_id=run_id)
    stats = Stats()
    stats.ok = 1
    durable = persist_durable_result_copy(task.run_id, str(result_path))
    create_outbox_record(
        task,
        durable_path=durable,
        stats=stats,
        run_started_at=RUN_STARTED,
        run_finished_at=RUN_FINISHED,
        output_file="result.xlsx",
    )
    update_outbox_status(task.run_id, status=OUTBOX_STATUS_SYNCED)
    mark_run_processed(task.run_id)
    return Path(durable)


def _mapped_result_row(durable: Path, *, run_id: str):
    result_df = pd.read_excel(
        durable,
        engine="openpyxl",
        converters={"card": card_as_text},
    )
    row = rows_from_result_excel(result_df).iloc[0]
    return map_all_results_row(row, run_id=run_id, source_row_index=0)


def _snapshot_state_files() -> dict[str, bytes]:
    snapshots: dict[str, bytes] = {}
    processed = processed_run_ids_path()
    if processed.is_file():
        snapshots[str(processed)] = processed.read_bytes()
    outbox = outbox_index_path()
    if outbox.is_file():
        snapshots[str(outbox)] = outbox.read_bytes()
    return snapshots


@pytest.fixture
def backfill_env(monkeypatch, tmp_path):
    monkeypatch.setenv("STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WALLET_EDITOR_REGISTRY_SOURCE", "postgres")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")


class TestBackfillProcessedRuns:
    def test_dry_run_does_not_insert(self, backfill_env, tmp_path):
        _setup_synced_processed_run(tmp_path, run_id="bf-dry")
        store = InMemoryRegistryStore()

        with patch(
            "integrations.wallet_editor_registry_db.backfill_processed._load_postgres_fingerprint_set",
            return_value=frozenset(),
        ):
            summary = backfill_processed_runs(apply=False)

        assert summary.dry_run is True
        assert summary.scanned == 1
        assert summary.eligible == 1
        assert summary.inserted == 1
        assert summary.skipped_existing == 0
        assert len(store.results) == 0

    def test_apply_inserts_missing_rows(self, backfill_env, tmp_path):
        durable = _setup_synced_processed_run(tmp_path, run_id="bf-apply")
        store = InMemoryRegistryStore()

        with patch(
            "integrations.wallet_editor_registry_db.backfill_processed._load_postgres_fingerprint_set",
            return_value=frozenset(),
        ):
            summary = backfill_processed_runs(apply=True, store=store)

        assert summary.dry_run is False
        assert summary.eligible == 1
        assert summary.inserted == 1
        assert summary.skipped_existing == 0
        assert len(store.results) == 1
        row = next(iter(store.results.values()))
        assert row.run_id == "bf-apply"
        assert Path(durable).is_file()

    def test_rerun_is_idempotent(self, backfill_env, tmp_path):
        _setup_synced_processed_run(tmp_path, run_id="bf-idem")
        store = InMemoryRegistryStore()
        patch_target = (
            "integrations.wallet_editor_registry_db.backfill_processed."
            "_load_postgres_fingerprint_set"
        )

        with patch(patch_target, return_value=frozenset()):
            first = backfill_processed_runs(apply=True, store=store)
        assert first.inserted == 1

        with patch(patch_target, return_value=frozenset(store.results.keys())):
            second = backfill_processed_runs(apply=True, store=store)

        assert second.eligible == 0
        assert second.inserted == 0
        assert second.skipped_existing == 0
        assert len(store.results) == 1

    def test_existing_fingerprints_skipped_on_apply(self, backfill_env, tmp_path):
        durable = _setup_synced_processed_run(tmp_path, run_id="bf-skip")
        store = InMemoryRegistryStore()
        existing = _mapped_result_row(durable, run_id="bf-skip")
        store.results[existing.row_fingerprint] = existing

        with patch(
            "integrations.wallet_editor_registry_db.backfill_processed._load_postgres_fingerprint_set",
            return_value=frozenset(),
        ):
            summary = backfill_processed_runs(apply=True, store=store)

        assert summary.eligible == 1
        assert summary.inserted == 0
        assert summary.skipped_existing == 1
        assert len(store.results) == 1

    def test_missing_durable_result_skipped(self, backfill_env, tmp_path):
        durable = _setup_synced_processed_run(tmp_path, run_id="bf-missing")
        Path(durable).unlink()

        with patch(
            "integrations.wallet_editor_registry_db.backfill_processed._load_postgres_fingerprint_set",
            return_value=frozenset(),
        ):
            summary = backfill_processed_runs(apply=False)

        assert summary.scanned == 1
        assert summary.eligible == 0
        assert summary.missing_result_file == 1

    def test_explicit_run_id_filter(self, backfill_env, tmp_path):
        _setup_synced_processed_run(tmp_path, run_id="bf-a")
        _setup_synced_processed_run(tmp_path, run_id="bf-b", card="4222222222222222")

        with patch(
            "integrations.wallet_editor_registry_db.backfill_processed._load_postgres_fingerprint_set",
            return_value=frozenset(),
        ):
            summary = backfill_processed_runs(apply=False, run_ids=["bf-b"])

        assert summary.scanned == 1
        assert summary.eligible == 1
        assert summary.inserted == 1

    def test_no_writes_to_excel_outbox_or_processed_ids(self, backfill_env, tmp_path):
        durable = _setup_synced_processed_run(tmp_path, run_id="bf-state")
        durable_bytes = Path(durable).read_bytes()
        state_before = _snapshot_state_files()
        store = InMemoryRegistryStore()

        with patch(
            "integrations.wallet_editor_registry_db.backfill_processed._load_postgres_fingerprint_set",
            return_value=frozenset(),
        ):
            backfill_processed_runs(apply=True, store=store)

        state_after = _snapshot_state_files()
        assert state_before == state_after
        assert Path(durable).read_bytes() == durable_bytes
        assert json.loads(processed_run_ids_path().read_text(encoding="utf-8"))["run_ids"] == [
            "bf-state"
        ]


class TestBackfillCli:
    def test_cli_dry_run_exit_code(self, backfill_env, tmp_path, capsys):
        _setup_synced_processed_run(tmp_path, run_id="bf-cli")

        with patch(
            "integrations.wallet_editor_registry_db.backfill_processed._load_postgres_fingerprint_set",
            return_value=frozenset(),
        ):
            code = backfill_cli_main(["--run-id", "bf-cli"])

        output = capsys.readouterr().out
        assert code == 0
        assert "mode: dry-run" in output
        assert "eligible: 1" in output
