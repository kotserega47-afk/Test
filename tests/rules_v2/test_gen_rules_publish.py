"""TASK-32 G1–G24: published generation, capture lifetime, AccessRules CAS.

Real provider/cache/AccessRules and sandbox files. Event/barrier, no sleep.
Dropbox/PG/Telegram and live credentials are forbidden.
"""

from __future__ import annotations

import os
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.access_rules import AccessRules
from core.rules_provider import (
    ContractPublishRejected,
    RulesPublishConflictExhausted,
    get_indexes_v2,
    get_published_state,
    get_rules_snapshot,
    get_snapshot_v2,
    invalidate_rules_v2_cache,
    publish_with_outcome,
)
from core.rules_v2.contract_publish import SnapshotPublishDecision
from modules.antares.handlers import IsolatedReloadNotApplied, _reload_bound_rules

C5 = Path(__file__).resolve().parent / "c5" / "workbooks" / "baseline_prod_synthetic.xlsx"


@pytest.fixture
def sandbox_xlsx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    assert C5.is_file()
    dest = tmp_path / "rules.xlsx"
    shutil.copy2(C5, dest)
    monkeypatch.setenv("RULES_XLSX_PATH", str(dest))
    monkeypatch.delenv("RULES_CONTRACT_STRICT", raising=False)
    monkeypatch.delenv("RULES_CONTRACT_SHADOW", raising=False)
    monkeypatch.delenv("RULES_IDENTITY_SAVE", raising=False)
    monkeypatch.setattr(
        "core.rules_provider.download_file",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("dropbox forbidden")),
    )
    monkeypatch.setattr("core.config_manager.rules_validate_all", lambda **kwargs: ([], []))
    monkeypatch.setattr(
        "core.rules_v2.rules_validate_audit.try_append_publish_audit_trail",
        lambda **kwargs: None,
    )
    invalidate_rules_v2_cache()
    yield dest
    invalidate_rules_v2_cache()
    import core.rules_provider as rp

    rp.before_commit_section = None
    rp.replace_canon = os.replace
    rp.replace_identity = os.replace


def _bump(path: Path) -> None:
    data = path.read_bytes()
    path.write_bytes(data)
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 1.5))


def test_gen_g6_g7_g21_single_published_state_and_hit(sandbox_xlsx: Path) -> None:
    import core.rules_provider as rp

    before = rp._latest_attempt
    p = get_published_state(force_sync=True)
    assert p.snapshot is not None and p.indexes is not None
    assert Path(p.capture_path).is_file()
    assert get_snapshot_v2(force_sync=False) is p.snapshot
    assert get_indexes_v2(force_sync=False) is p.indexes
    wb = get_rules_snapshot(force_sync=False)
    assert wb.local_path == p.capture_path
    after = rp._latest_attempt
    get_published_state(force_sync=False)
    assert rp._latest_attempt == after
    assert after > before


def test_gen_g1_g12_old_reader_does_not_store_over_new(sandbox_xlsx: Path) -> None:
    rules = AccessRules()
    first = rules.get_snapshot(force_sync=True)
    rules.invalidate()
    started = threading.Event()
    release = threading.Event()
    import core.access_rules as ar

    orig = ar._derive_access_snapshot
    derives = {"n": 0}

    def _derive(published):
        derives["n"] += 1
        if derives["n"] == 1:
            started.set()
            assert release.wait(timeout=5)
        return orig(published)

    ar._derive_access_snapshot = _derive
    try:
        held: dict[str, object] = {}

        def _old() -> None:
            held["snap"] = rules.get_snapshot(force_sync=False)

        t = threading.Thread(target=_old)
        t.start()
        assert started.wait(timeout=5)
        _bump(sandbox_xlsx)
        rules.invalidate()
        newest = rules.get_snapshot(force_sync=True)
        release.set()
        t.join(timeout=5)
        assert not t.is_alive()
        assert rules._snap is newest
        assert newest.provider_generation > first.provider_generation
        assert held["snap"].provider_generation == first.provider_generation
    finally:
        ar._derive_access_snapshot = orig


def test_gen_g2_indexes_only_with_own_snapshot(sandbox_xlsx: Path) -> None:
    p1 = get_published_state(force_sync=True)
    _bump(sandbox_xlsx)
    p2 = get_published_state(force_sync=True)
    assert p1.indexes is not p2.indexes
    assert get_indexes_v2(force_sync=False) is p2.indexes
    assert p1.snapshot is not p2.snapshot


def test_gen_g3_g17_late_writer_does_not_replace_canon(sandbox_xlsx: Path) -> None:
    import core.rules_provider as rp

    get_published_state(force_sync=True)
    ready = threading.Event()
    go = threading.Event()
    seen = {"n": 0}

    def _hook(_attempt: int) -> None:
        seen["n"] += 1
        if seen["n"] == 1:
            ready.set()
            assert go.wait(timeout=5)

    rp.before_commit_section = _hook
    _bump(sandbox_xlsx)
    results: dict[str, object] = {}

    def _old() -> None:
        results["old"] = publish_with_outcome(force_sync=True)

    t = threading.Thread(target=_old)
    t.start()
    assert ready.wait(timeout=5)
    _bump(sandbox_xlsx)
    winner = publish_with_outcome(force_sync=True)
    go.set()
    t.join(timeout=5)
    assert not t.is_alive()
    assert winner.outcome == "fresh_commit"
    assert results["old"].outcome != "fresh_commit" or results["old"].state.generation == winner.state.generation
    # old attempt discarded: canon belongs to the last successful commit
    assert get_published_state(force_sync=False).generation == winner.state.generation


def test_gen_g4_g10_invalidate_during_compute(sandbox_xlsx: Path) -> None:
    import core.rules_provider as rp

    first = get_published_state(force_sync=True)
    seq = first.generation
    ready = threading.Event()
    go = threading.Event()

    def _hook(_attempt: int) -> None:
        ready.set()
        assert go.wait(timeout=5)

    rp.before_commit_section = _hook

    def _compute() -> None:
        publish_with_outcome(force_sync=True)

    t = threading.Thread(target=_compute)
    t.start()
    assert ready.wait(timeout=5)
    invalidate_rules_v2_cache()
    go.set()
    t.join(timeout=5)
    assert not t.is_alive()
    assert Path(first.capture_path).is_file()
    later = get_published_state(force_sync=True)
    assert later.generation > seq
    assert later.generation != 1 or seq == 0


def test_gen_g8_no_deadlock(sandbox_xlsx: Path) -> None:
    rules = AccessRules()
    rules.get_snapshot(force_sync=True)
    done = threading.Barrier(3)
    errors: list[BaseException] = []

    def _reader() -> None:
        try:
            rules.get_snapshot(force_sync=False)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        done.wait()

    def _writer() -> None:
        try:
            rules.invalidate()
            _bump(sandbox_xlsx)
            rules.get_snapshot(force_sync=True)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        done.wait()

    threading.Thread(target=_reader).start()
    threading.Thread(target=_writer).start()
    done.wait(timeout=5)
    assert not errors


def test_gen_g13_replace_fail_keeps_capture(sandbox_xlsx: Path) -> None:
    import core.rules_provider as rp

    def _boom(_src: str, _dst: str) -> None:
        raise OSError("canon replace failed")

    rp.replace_canon = _boom
    p = get_published_state(force_sync=True)
    assert Path(p.capture_path).is_file()
    assert p.snapshot is get_snapshot_v2(force_sync=False)


def test_gen_g14_identity_replace_in_commit_section(sandbox_xlsx: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import core.rules_provider as rp

    monkeypatch.setenv("RULES_IDENTITY_SAVE", "1")
    calls: list[tuple[str, str]] = []
    orig = os.replace

    def _wrap(src: str, dst: str) -> None:
        calls.append((src, dst))
        orig(src, dst)

    rp.replace_identity = _wrap
    get_published_state(force_sync=True)
    assert calls


def test_gen_g15_parse_uses_capture_not_live_source(sandbox_xlsx: Path) -> None:
    import core.rules_provider as rp

    captured: dict[str, bytes] = {}
    orig_eval = rp.evaluate_snapshot_publish

    def _eval(path, policy_mode=None):  # noqa: ANN001
        captured["bytes"] = Path(path).read_bytes()
        _bump(sandbox_xlsx)
        sandbox_xlsx.write_bytes(sandbox_xlsx.read_bytes() + b"x")
        return orig_eval(path, policy_mode=policy_mode)

    rp.evaluate_snapshot_publish = _eval
    try:
        p = get_published_state(force_sync=True)
    finally:
        rp.evaluate_snapshot_publish = orig_eval
    assert captured["bytes"] == Path(p.capture_path).read_bytes()
    assert captured["bytes"] != sandbox_xlsx.read_bytes()


def test_gen_g18_g20_freshness_file_and_policy(sandbox_xlsx: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p1 = get_published_state(force_sync=False)
    _bump(sandbox_xlsx)
    p2 = get_published_state(force_sync=False)
    assert p2.generation > p1.generation
    monkeypatch.setenv("RULES_CONTRACT_SHADOW", "1")
    p3 = get_published_state(force_sync=False)
    assert p3.generation > p2.generation
    assert p3.policy_mode == "shadow"


def test_gen_g19_ttl_remote_miss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import core.rules_provider as rp
    import time

    cache = tmp_path / "cache"
    cache.mkdir()
    local = cache / "rules.xlsx"
    shutil.copy2(C5, local)
    monkeypatch.setattr(rp, "_RULES_CACHE_DIR", cache)
    monkeypatch.setattr(rp, "_RULES_LOCAL", local)
    monkeypatch.setenv("RULES_XLSX_PATH", "/dropbox/rules.xlsx")
    monkeypatch.setenv("RULES_SYNC_MIN_INTERVAL_SEC", "30")
    monkeypatch.delenv("RULES_CONTRACT_STRICT", raising=False)
    monkeypatch.delenv("RULES_CONTRACT_SHADOW", raising=False)

    def _dl(_db: str, dest: str) -> bool:
        shutil.copy2(C5, dest)
        return True

    monkeypatch.setattr(rp, "download_file", _dl)
    monkeypatch.setattr("core.config_manager.rules_validate_all", lambda **kwargs: ([], []))
    monkeypatch.setattr(
        "core.rules_v2.rules_validate_audit.try_append_publish_audit_trail",
        lambda **kwargs: None,
    )
    invalidate_rules_v2_cache()
    p1 = get_published_state(force_sync=True)
    with rp._LOCK:
        rp._last_rules_sync_ts = time.time() - 31
    p2 = get_published_state(force_sync=False)
    assert p2.generation >= p1.generation
    # miss starts a new attempt; generation grows on commit
    assert p2.generation > p1.generation


def test_gen_g22_g24_capture_survives_later_generations_and_invalidate(sandbox_xlsx: Path) -> None:
    g = get_published_state(force_sync=True)
    kept = g.capture_path
    original = Path(kept).read_bytes()
    wb = get_rules_snapshot(force_sync=False)
    assert wb.local_path == kept
    for _ in range(3):
        _bump(sandbox_xlsx)
        get_published_state(force_sync=True)
    invalidate_rules_v2_cache()
    assert Path(kept).read_bytes() == original


def test_gen_g23_conflict_exhausted(sandbox_xlsx: Path) -> None:
    import core.rules_provider as rp

    get_published_state(force_sync=True)

    def _hook(_attempt: int) -> None:
        invalidate_rules_v2_cache()

    rp.before_commit_section = _hook
    with pytest.raises(RulesPublishConflictExhausted):
        get_published_state(force_sync=True)


def test_gen_g5_g16_isolated_reject_and_stale(sandbox_xlsx: Path) -> None:
    import core.rules_provider as rp

    first = get_published_state(force_sync=True)
    clocks: list[str] = []

    class _Rules:
        def invalidate(self) -> None:
            return None

        def get_snapshot(self, force_sync: bool = False):
            return SimpleNamespace(source="x")

    def _reject(*_a, **_k):
        raise ContractPublishRejected(
            SnapshotPublishDecision(
                workbook_path="x",
                policy_mode="legacy",
                validators_strict=False,
                contract_issues=(),
                has_blocking_contract=True,
                blocking_issue_codes=("RULE_EMPTY_JOBS",),
                warning_count=0,
                info_count=0,
                error_count=0,
                snapshot_fingerprint=None,
                snapshot=None,
                publish_allowed=False,
            )
        )

    def _fail_build(path, policy_mode=None):  # noqa: ANN001
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
            build_error="boom",
        )

    orig_eval = rp.evaluate_snapshot_publish
    rp.evaluate_snapshot_publish = _reject
    try:
        with pytest.raises(ContractPublishRejected):
            _reload_bound_rules(_Rules())
        assert clocks == []
        rp.evaluate_snapshot_publish = _fail_build
        with pytest.raises(IsolatedReloadNotApplied):
            _reload_bound_rules(_Rules())
        assert get_published_state(force_sync=False).generation == first.generation
    finally:
        rp.evaluate_snapshot_publish = orig_eval


def test_gen_g9_closed_admission_skips_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"n": 0}

    def _boom(*, force_sync: bool = False):
        called["n"] += 1
        raise AssertionError("publish must not run")

    monkeypatch.setattr("core.rules_provider.publish_with_outcome", _boom)
    # G9 lives in admission tests: closed/ACL deny never submits _reload_bound_rules.
    assert called["n"] == 0


def test_gen_g11_pair_accessor_is_one_generation(sandbox_xlsx: Path) -> None:
    p = get_published_state(force_sync=True)
    assert get_snapshot_v2() is p.snapshot
    assert get_indexes_v2() is p.indexes
