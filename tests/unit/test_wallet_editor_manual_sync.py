"""Unit tests for manual sync orchestration (rev/hash skip, PG apply)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from integrations.wallet_editor_registry_db.config import ENV_MANUAL_SYNC_ENABLED
from integrations.wallet_editor_registry_db.manual_snapshot import (
    ManualSyncValidationError,
    compute_snapshot_hash,
    parse_manual_workbook,
)
from integrations.wallet_editor_registry_db.manual_store import InMemoryManualSyncStore
from integrations.wallet_editor_registry_db.manual_sync import (
    ManualSyncDecision,
    RunSnapshotBinding,
    ensure_manual_snapshot_current,
)
from integrations.wallet_editor_registry_db.manual_sync_state import (
    build_manual_snapshot_health_report,
    save_manual_sync_meta,
)
from integrations.wallet_editor_registry_lifecycle import HOLD_COLUMNS, OTLEZKA_COLUMNS, SHEET_HOLD, SHEET_OTLEZKA
from tests.unit.test_wallet_editor_manual_snapshot import _write_manual_workbook

DROPBOX_PATH = "/test/wallet_editor.xlsx"


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv(ENV_MANUAL_SYNC_ENABLED, "1")
    monkeypatch.setenv("DROPBOX_WALLET_EDITOR_PATH", DROPBOX_PATH)


@pytest.fixture
def store():
    return InMemoryManualSyncStore()


def _seed_meta(store: InMemoryManualSyncStore, *, rev: str, hash_value: str, status: str = "success") -> None:
    save_manual_sync_meta(
        store,
        source_rev=rev,
        snapshot_hash=hash_value,
        sync_status=status,
        sync_run_id=1,
        active_hold=0,
        active_otlezka=0,
    )


def _make_workbook(tmp_path: Path, hold_comment: str = "note") -> Path:
    path = tmp_path / "manual.xlsx"
    _write_manual_workbook(
        path,
        hold_rows=[
            {
                "Дата добавления": "01.01.2026",
                "card": "4111111111111111",
                "partner": "Ostin",
                "comment": hold_comment,
            }
        ],
        include_legacy=True,
    )
    return path


def _hash_for_workbook(path: Path) -> str:
    return compute_snapshot_hash(parse_manual_workbook(path, "ok"))


class TestManualSyncDisabled:
    def test_skipped_when_flag_off(self, monkeypatch, store):
        monkeypatch.delenv(ENV_MANUAL_SYNC_ENABLED, raising=False)
        result = ensure_manual_snapshot_current(store)
        assert result.decision == ManualSyncDecision.SKIPPED_DISABLED


class TestManualSyncRevSkip:
    def test_same_rev_skips_download(self, enabled, store, tmp_path):
        wb = _make_workbook(tmp_path)
        content_hash = _hash_for_workbook(wb)
        _seed_meta(store, rev="rev-1", hash_value=content_hash)

        download = MagicMock()
        rev_fn = MagicMock(return_value="rev-1")

        result = ensure_manual_snapshot_current(
            store,
            dropbox_path=DROPBOX_PATH,
            download_fn=download,
            rev_fn=rev_fn,
        )

        assert result.decision == ManualSyncDecision.SKIPPED_REV
        download.assert_not_called()
        assert result.snapshot_hash == content_hash
        assert isinstance(result.run_binding, RunSnapshotBinding)


class TestManualSyncHashSkip:
    def test_rev_changed_same_hash_skips_pg_mutation(self, enabled, store, tmp_path):
        wb = _make_workbook(tmp_path)
        content_hash = _hash_for_workbook(wb)
        _seed_meta(store, rev="rev-old", hash_value=content_hash)

        store.apply_snapshot(parse_manual_workbook(wb, "ok"), sync_run_id=99)
        before_hold = dict(store.hold)

        def _download(_path: str, local_path: str) -> tuple[str, str | None]:
            Path(local_path).write_bytes(wb.read_bytes())
            return "ok", "rev-new"

        result = ensure_manual_snapshot_current(
            store,
            dropbox_path=DROPBOX_PATH,
            download_fn=_download,
            rev_fn=lambda _p: "rev-new",
        )

        assert result.decision == ManualSyncDecision.SKIPPED_HASH
        assert store.hold == before_hold
        assert store.get_meta("last_manual_source_rev") == "rev-new"
        assert store.get_meta("last_manual_snapshot_hash") == content_hash
        assert store.get_meta("last_manual_sync_status") == "skipped_hash"


class TestManualSyncApply:
    def test_changed_snapshot_syncs_and_deactivates(self, enabled, store, tmp_path):
        wb_v1 = _make_workbook(tmp_path, hold_comment="v1")
        hash_v1 = _hash_for_workbook(wb_v1)
        _seed_meta(store, rev="rev-1", hash_value=hash_v1)
        store.apply_snapshot(parse_manual_workbook(wb_v1, "ok"), sync_run_id=1)

        wb_v2_dir = tmp_path / "v2"
        wb_v2_dir.mkdir()
        wb_v2 = _make_workbook(wb_v2_dir, hold_comment="v2")
        hash_v2 = _hash_for_workbook(wb_v2)
        assert hash_v1 != hash_v2

        def _download(_path: str, local_path: str) -> tuple[str, str | None]:
            Path(local_path).write_bytes(wb_v2.read_bytes())
            return "ok", "rev-2"

        result = ensure_manual_snapshot_current(
            store,
            dropbox_path=DROPBOX_PATH,
            download_fn=_download,
            rev_fn=lambda _p: "rev-2",
        )

        assert result.decision == ManualSyncDecision.SYNCED
        assert store.count_active_hold() == 1
        active = next(v for v in store.hold.values() if v["active"])
        assert active["row"].comment == "v2"
        assert result.run_binding is not None
        assert result.run_binding.manual_snapshot_hash == hash_v2


class TestManualSyncValidation:
    def test_invalid_workbook_leaves_pg_unchanged(self, enabled, store, tmp_path):
        good = _make_workbook(tmp_path)
        good_hash = _hash_for_workbook(good)
        _seed_meta(store, rev="rev-good", hash_value=good_hash)
        store.apply_snapshot(parse_manual_workbook(good, "ok"), sync_run_id=5)
        before = dict(store.hold)

        bad = tmp_path / "bad.xlsx"
        with pd.ExcelWriter(bad, engine="openpyxl") as writer:
            pd.DataFrame(
                columns=["card"],
            ).to_excel(writer, sheet_name=SHEET_HOLD, index=False)
            pd.DataFrame(columns=OTLEZKA_COLUMNS).to_excel(
                writer, sheet_name=SHEET_OTLEZKA, index=False
            )

        def _download(_path: str, local_path: str) -> tuple[str, str | None]:
            Path(local_path).write_bytes(bad.read_bytes())
            return "ok", "rev-bad"

        result = ensure_manual_snapshot_current(
            store,
            dropbox_path=DROPBOX_PATH,
            download_fn=_download,
            rev_fn=lambda _p: "rev-bad",
        )

        assert result.decision == ManualSyncDecision.FAILED
        assert store.hold == before
        assert store.get_meta("last_manual_sync_status") == "failed"
        with pytest.raises(ManualSyncValidationError):
            parse_manual_workbook(bad, "ok")


class TestManualSnapshotHealth:
    def test_health_report_fields(self, store):
        save_manual_sync_meta(
            store,
            source_rev="rev-a",
            snapshot_hash="abc123def456",
            sync_status="success",
            sync_run_id=7,
            active_hold=2,
            active_otlezka=1,
        )
        store.hold[("4111", "ostin")] = {"row": None, "active": True}
        store.otlezka["ostin"] = {"row": None, "active": True}

        report = build_manual_snapshot_health_report(
            store,
            current_dropbox_rev="rev-b",
            current_snapshot_hash="abc123def456789",
        )
        assert report.current_dropbox_rev == "rev-b"
        assert report.last_synced_dropbox_rev == "rev-a"
        assert report.current_snapshot_hash_short == "abc123def456"
        assert report.last_successful_sync_at is not None
        assert report.active_hold_rows == 1
        assert report.active_otlezka_rows == 1
        assert report.last_sync_status == "success"
        assert report.stale is True
        assert report.failed is False

    def test_health_failed_status(self, store):
        save_manual_sync_meta(
            store,
            source_rev="rev-a",
            snapshot_hash="deadbeef",
            sync_status="failed",
            sync_run_id=1,
            active_hold=0,
            active_otlezka=0,
        )
        report = build_manual_snapshot_health_report(
            store,
            current_dropbox_rev="rev-a",
        )
        assert report.failed is True
