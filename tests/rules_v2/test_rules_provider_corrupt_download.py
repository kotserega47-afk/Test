"""Fail-soft rules cache when Dropbox delivers a truncated/corrupt rules.xlsx."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

import pytest

from core import rules_provider as rp
from core.rules_provider import (
    ContractPublishRejected,
    get_published_state,
    get_snapshot_v2,
    invalidate_rules_v2_cache,
    publish_with_outcome,
)
from core.rules_v2.contract_publish import SnapshotPublishDecision
from core.rules_v2.models import MetaInfo, RulesSnapshotV2

C5_BASELINE = Path(__file__).resolve().parent / "c5" / "workbooks" / "baseline_prod_synthetic.xlsx"


@pytest.fixture(autouse=True)
def _reset_caches() -> None:
    orig_eval = rp.evaluate_snapshot_publish
    invalidate_rules_v2_cache()
    yield
    rp.evaluate_snapshot_publish = orig_eval
    invalidate_rules_v2_cache()


@pytest.fixture
def rules_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "rules_cache"
    cache.mkdir()
    local = cache / "rules.xlsx"
    shutil.copy(C5_BASELINE, local)
    monkeypatch.setattr(rp, "_RULES_CACHE_DIR", cache)
    monkeypatch.setattr(rp, "_RULES_LOCAL", local)
    monkeypatch.setenv("RULES_XLSX_PATH", "/dropbox/rules.xlsx")
    monkeypatch.delenv("RULES_CONTRACT_STRICT", raising=False)
    monkeypatch.delenv("RULES_CONTRACT_SHADOW", raising=False)
    monkeypatch.setattr("core.config_manager.rules_validate_all", lambda **kwargs: ([], []))
    monkeypatch.setattr(
        "core.rules_v2.rules_validate_audit.try_append_publish_audit_trail",
        lambda **kwargs: None,
    )

    def _dl(_db: str, dest: str) -> bool:
        shutil.copy(C5_BASELINE, dest)
        return True

    monkeypatch.setattr(rp, "download_file", _dl)
    return local


def _minimal_snapshot() -> RulesSnapshotV2:
    return RulesSnapshotV2(
        meta=MetaInfo(
            ruleset_version="cached-good",
            updated_at=datetime(2026, 6, 3, 12, 0, 0),
            updated_by="test",
        ),
    )


def _build_error(path, *, message: str = "BadZipFile: Truncated file header") -> SnapshotPublishDecision:
    return SnapshotPublishDecision(
        workbook_path=str(path),
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
        build_error=message,
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

    got = rp._download_to_capture("/dropbox/rules.xlsx", attempt_id=1)
    assert got is None
    assert rules_cache.stat().st_size == good_size
    assert rules_cache.stat().st_mtime == good_mtime


def test_build_error_reuses_last_valid_snapshot_new_stat(
    rules_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = get_snapshot_v2(force_sync=True)
    kept = get_published_state(force_sync=False)
    original = Path(kept.capture_path).read_bytes()

    def _fail(_db: str, dest: str) -> bool:
        return False

    monkeypatch.setattr(rp, "download_file", _fail)
    orig_eval = rp.evaluate_snapshot_publish
    rp.evaluate_snapshot_publish = lambda path, policy_mode=None: _build_error(path)
    try:
        result = publish_with_outcome(force_sync=True)
    finally:
        rp.evaluate_snapshot_publish = orig_eval

    assert result.outcome == "stale_reuse"
    assert result.state is kept
    second = get_snapshot_v2(force_sync=False)
    assert second is first
    assert Path(kept.capture_path).read_bytes() == original


def test_force_sync_build_error_does_not_raise_contract_rejected(
    rules_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_snapshot_v2(force_sync=True)

    def _fail(_db: str, dest: str) -> bool:
        return False

    monkeypatch.setattr(rp, "download_file", _fail)
    orig_eval = rp.evaluate_snapshot_publish
    rp.evaluate_snapshot_publish = lambda path, policy_mode=None: _build_error(path, message="EOFError")
    try:
        get_snapshot_v2(force_sync=True)
    finally:
        rp.evaluate_snapshot_publish = orig_eval


def test_no_prior_snapshot_build_error_still_rejects(
    rules_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orig_eval = rp.evaluate_snapshot_publish
    rp.evaluate_snapshot_publish = lambda path, policy_mode=None: _build_error(path)
    try:
        with pytest.raises(ContractPublishRejected) as ei:
            get_snapshot_v2(force_sync=True)
    finally:
        rp.evaluate_snapshot_publish = orig_eval

    assert ei.value.decision.build_error


def test_valid_workbook_after_corrupt_attempt_replaces_snapshot(
    rules_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = get_snapshot_v2(force_sync=True)
    first_version = first.meta.ruleset_version
    kept = get_published_state(force_sync=False)
    original = Path(kept.capture_path).read_bytes()

    def _write_truncated(_db: str, dest: str) -> bool:
        Path(dest).write_bytes(b"PK\x03\x04")
        return True

    monkeypatch.setattr(rp, "download_file", _write_truncated)
    assert rp._download_to_capture("/dropbox/rules.xlsx", attempt_id=2) is None

    second = get_snapshot_v2(force_sync=True)
    assert second.meta.ruleset_version == first_version
    assert Path(kept.capture_path).read_bytes() == original


def test_legacy_blocking_contract_still_rejects_with_cached_snapshot(
    rules_cache: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = get_published_state(force_sync=True)
    original = Path(published.capture_path).read_bytes()
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

    orig_eval = rp.evaluate_snapshot_publish
    rp.evaluate_snapshot_publish = lambda path, policy_mode=None: decision
    try:
        with pytest.raises(ContractPublishRejected) as ei:
            get_snapshot_v2(force_sync=True)
    finally:
        rp.evaluate_snapshot_publish = orig_eval

    assert ei.value.decision.has_blocking_contract is True
    assert Path(published.capture_path).read_bytes() == original
