"""Fail-soft rules cache when Dropbox delivers a truncated/corrupt rules.xlsx."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from core import rules_provider as rp
from core.rules_provider import (
    ContractPublishRejected,
    RulesWorkbookSnapshot,
    get_rules_snapshot,
    get_snapshot_v2,
    invalidate_rules_v2_cache,
)
from core.rules_v2.contract_publish import SnapshotPublishDecision
from core.rules_v2.models import MetaInfo, RulesSnapshotV2

C5_BASELINE = Path(__file__).resolve().parent / "c5" / "workbooks" / "baseline_prod_synthetic.xlsx"


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    invalidate_rules_v2_cache()
    yield
    invalidate_rules_v2_cache()


@pytest.fixture
def rules_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "rules_cache"
    cache.mkdir()
    local = cache / "rules.xlsx"
    part = cache / "rules.xlsx.part"
    shutil.copy(C5_BASELINE, local)
    monkeypatch.setattr(rp, "_RULES_CACHE_DIR", cache)
    monkeypatch.setattr(rp, "_RULES_LOCAL", local)
    monkeypatch.setattr(rp, "_RULES_DOWNLOAD_PART", part)
    monkeypatch.setenv("RULES_XLSX_PATH", "/dropbox/rules.xlsx")
    monkeypatch.delenv("RULES_CONTRACT_STRICT", raising=False)
    monkeypatch.delenv("RULES_CONTRACT_SHADOW", raising=False)
    return local


def _minimal_snapshot() -> RulesSnapshotV2:
    return RulesSnapshotV2(
        meta=MetaInfo(
            ruleset_version="cached-good",
            updated_at=datetime(2026, 6, 3, 12, 0, 0),
            updated_by="test",
        ),
    )


def test_corrupt_download_does_not_replace_valid_cache(
    rules_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good_size = rules_cache.stat().st_size
    good_mtime = rules_cache.stat().st_mtime

    def _write_truncated(_db: str, dest: str) -> bool:
        Path(dest).write_bytes(b"PK\x03\x04truncated")
        return True

    monkeypatch.setattr(rp, "download_file", _write_truncated)

    ok = rp._download_rules_workbook_atomic("/dropbox/rules.xlsx")
    assert ok is False
    assert rules_cache.stat().st_size == good_size
    assert rules_cache.stat().st_mtime == good_mtime


def test_build_error_reuses_last_valid_snapshot_new_stat(
    rules_cache: Path,
    tmp_path: Path,
) -> None:
    first = get_snapshot_v2(force_sync=True)
    assert first.meta.ruleset_version

    corrupt = tmp_path / "corrupt.xlsx"
    corrupt.write_bytes(b"not-a-valid-xlsx")
    corrupt_stat = rp._stat_key(corrupt)

    wb = RulesWorkbookSnapshot(
        local_path=str(corrupt),
        stat_key=corrupt_stat,
        loaded_at_ts=1.0,
        source="test-corrupt",
        rules_version="legacy",
    )

    decision = SnapshotPublishDecision(
        workbook_path=str(corrupt),
        policy_mode="legacy",
        validators_strict=False,
        contract_issues=(),
        has_blocking_contract=False,
        blocking_issue_codes=(),
        warning_count=0,
        info_count=0,
        error_count=0,
        snapshot_fingerprint=None,
        snapshot=None,
        publish_allowed=False,
        build_error="BadZipFile: Truncated file header",
    )

    with patch("core.rules_provider.get_rules_snapshot", return_value=wb):
        with patch(
            "core.rules_provider.evaluate_snapshot_publish",
            return_value=decision,
        ):
            second = get_snapshot_v2(force_sync=True)

    assert second is first


def test_force_sync_build_error_does_not_raise_contract_rejected(
    rules_cache: Path,
    tmp_path: Path,
) -> None:
    get_snapshot_v2(force_sync=True)

    corrupt = tmp_path / "corrupt.xlsx"
    corrupt.write_bytes(b"bad")
    wb = RulesWorkbookSnapshot(
        local_path=str(corrupt),
        stat_key=rp._stat_key(corrupt),
        loaded_at_ts=1.0,
        source="test",
        rules_version="legacy",
    )
    decision = SnapshotPublishDecision(
        workbook_path=str(corrupt),
        policy_mode="legacy",
        validators_strict=False,
        contract_issues=(),
        has_blocking_contract=False,
        blocking_issue_codes=(),
        warning_count=0,
        info_count=0,
        error_count=0,
        snapshot_fingerprint=None,
        snapshot=None,
        publish_allowed=False,
        build_error="EOFError",
    )

    with patch("core.rules_provider.get_rules_snapshot", return_value=wb):
        with patch(
            "core.rules_provider.evaluate_snapshot_publish",
            return_value=decision,
        ):
            get_snapshot_v2(force_sync=True)


def test_no_prior_snapshot_build_error_still_rejects(
    rules_cache: Path,
    tmp_path: Path,
) -> None:
    corrupt = tmp_path / "only-corrupt.xlsx"
    corrupt.write_bytes(b"bad")
    wb = RulesWorkbookSnapshot(
        local_path=str(corrupt),
        stat_key=rp._stat_key(corrupt),
        loaded_at_ts=1.0,
        source="test",
        rules_version="legacy",
    )
    decision = SnapshotPublishDecision(
        workbook_path=str(corrupt),
        policy_mode="legacy",
        validators_strict=False,
        contract_issues=(),
        has_blocking_contract=False,
        blocking_issue_codes=(),
        warning_count=0,
        info_count=0,
        error_count=0,
        snapshot_fingerprint=None,
        snapshot=None,
        publish_allowed=False,
        build_error="BadZipFile: Truncated file header",
    )

    with patch("core.rules_provider.get_rules_snapshot", return_value=wb):
        with patch(
            "core.rules_provider.evaluate_snapshot_publish",
            return_value=decision,
        ):
            with pytest.raises(ContractPublishRejected) as ei:
                get_snapshot_v2(force_sync=True)

    assert ei.value.decision.build_error


def test_valid_workbook_after_corrupt_attempt_replaces_snapshot(
    rules_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = get_snapshot_v2(force_sync=True)
    first_version = first.meta.ruleset_version

    def _write_truncated(_db: str, dest: str) -> bool:
        Path(dest).write_bytes(b"PK\x03\x04")
        return True

    monkeypatch.setattr(rp, "download_file", _write_truncated)
    assert rp._download_rules_workbook_atomic("/dropbox/rules.xlsx") is False

    second = get_snapshot_v2(force_sync=True)
    assert second.meta.ruleset_version == first_version


def test_legacy_blocking_contract_still_rejects_with_cached_snapshot(
    rules_cache: Path,
) -> None:
    get_snapshot_v2(force_sync=True)
    snap = _minimal_snapshot()
    decision = SnapshotPublishDecision(
        workbook_path=str(rules_cache),
        policy_mode="legacy",
        validators_strict=False,
        contract_issues=(),
        has_blocking_contract=True,
        blocking_issue_codes=("RULE_EMPTY_JOBS",),
        warning_count=0,
        info_count=0,
        error_count=0,
        snapshot_fingerprint="fp",
        snapshot=snap,
        publish_allowed=False,
    )

    with patch(
        "core.rules_provider.evaluate_snapshot_publish",
        return_value=decision,
    ):
        with pytest.raises(ContractPublishRejected) as ei:
            get_snapshot_v2(force_sync=True)

    assert ei.value.decision.has_blocking_contract is True
