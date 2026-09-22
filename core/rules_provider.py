# core/rules_provider.py
"""Rules workbook sync + ``RulesSnapshotV2`` publish path (C4).

Downloads or resolves ``rules.xlsx``, evaluates CONTRACT_V2 publish policy,
and returns a snapshot for runtime readers (access rules, schedules, …).

``RULES_CONTRACT_STRICT`` / ``RULES_CONTRACT_SHADOW`` are interpreted only
here — bridge and snapshot layout stay unchanged.
"""

from __future__ import annotations

import contextvars
import logging
import os
import shutil
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Optional

from integrations.dropbox_watcher import download_file

from core.rules_v2.contract_publish import (
    ContractPublishRejected,
    ContractValidationMode,
    SnapshotPublishDecision,
    evaluate_snapshot_publish,
    resolve_contract_validation_mode,
)
from core.rules_v2.indexes import RulesIndexes, build_indexes
from core.rules_v2.models import RulesSnapshotV2


@dataclass(frozen=True)
class RulesWorkbookSnapshot:
    """Local materialization of ``rules.xlsx`` (path + freshness key).

    ``rules_version`` mirrors ``RulesSnapshotV2.meta.ruleset_version`` (meta sheet
    ``version`` key, default ``legacy``) for callers such as ``job_runner`` that
    only use ``get_rules_snapshot()`` without loading the full snapshot.
    """

    local_path: str
    stat_key: tuple[float, int]
    loaded_at_ts: float
    source: str
    rules_version: str


@dataclass(frozen=True)
class PublishedState:
    generation: int
    attempt_id: int
    snapshot: RulesSnapshotV2
    decision: SnapshotPublishDecision
    source_path: str
    capture_path: str
    canon_path: str
    stat_key: tuple[float, int]
    indexes: RulesIndexes
    policy_mode: str


class RulesPublishConflictExhausted(Exception):
    """Three force attempts lost the commit race without a successful read."""


OUTCOME_FRESH_COMMIT = "fresh_commit"
OUTCOME_EXISTING = "existing"
OUTCOME_STALE_REUSE = "stale_reuse"

_RULES_CACHE_DIR = Path("/tmp/rules_cache")
_RULES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_RULES_LOCAL = _RULES_CACHE_DIR / "rules.xlsx"
_CAPTURE_TOKEN = f"{os.getpid()}_{time.time_ns()}"

log = logging.getLogger(__name__)

_LOCK = threading.Lock()
_publish_seq = 0
_latest_attempt = 0
_invalidate_epoch = 0
_published: Optional[PublishedState] = None
_last_rules_sync_ts: float = 0.0
_last_rules_wb: Optional[RulesWorkbookSnapshot] = None
_source_stat_at_publish: Optional[tuple[float, int]] = None

# Test barriers: callable(attempt_id) between prepare and the commit section.
before_commit_section: Optional[Callable[[int], None]] = None
after_attempt_evaluate: Optional[Callable[[int], None]] = None
before_compat_cache_write: Optional[Callable[[], None]] = None
replace_canon: Callable[[str, str], None] = os.replace
replace_identity: Callable[[str, str], None] = os.replace


@dataclass
class _AttemptLocal:
    attempt_id: int
    capture_path: Path
    source_path: str
    stat_key: tuple[float, int]
    wb: RulesWorkbookSnapshot
    snapshot: Optional[RulesSnapshotV2] = None
    indexes: Optional[RulesIndexes] = None
    decision: Optional[SnapshotPublishDecision] = None


_attempt_local: contextvars.ContextVar[Optional[_AttemptLocal]] = contextvars.ContextVar(
    "rules_publish_attempt_local",
    default=None,
)

_identity_registry_save_suppressed: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "identity_registry_save_suppressed",
    default=False,
)


class _PublishCallResult:
    __slots__ = ("outcome", "state")

    def __init__(self, outcome: str, state: Optional[PublishedState]):
        self.outcome = outcome
        self.state = state


def _stat_key(path: Path) -> tuple[float, int]:
    st = path.stat()
    return (st.st_mtime, st.st_size)


def _is_valid_xlsx_zip(path: Path) -> bool:
    """Reject truncated/corrupt downloads before promoting a capture."""

    try:
        if not path.is_file() or path.stat().st_size < 22:
            return False
        if not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path, "r") as zf:
            if zf.testzip() is not None:
                return False
    except (zipfile.BadZipFile, OSError, EOFError):
        return False
    return True


def _capture_dir() -> Path:
    d = _RULES_CACHE_DIR / f"captures_{_CAPTURE_TOKEN}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _identity_canon_path() -> Path:
    from core.rules_v2.identity_registry_io import resolve_identity_registry_path

    return resolve_identity_registry_path()


def _read_meta_ruleset_version(path: Path) -> str:
    """Read ``meta.version`` from workbook; align defaults with ``bridge_legacy._build_meta``."""

    try:
        import pandas as pd

        df = pd.read_excel(path, sheet_name="meta", engine="openpyxl")
        df.columns = [str(c).strip() for c in df.columns]
        if "key" not in df.columns or "value" not in df.columns:
            return "legacy"
        for _, row in df.iterrows():
            k = row.get("key")
            if k is None or (isinstance(k, float) and pd.isna(k)):
                continue
            if str(k).strip() != "version":
                continue
            v = row.get("value")
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return "legacy"
            s = str(v).strip()
            return s if s else "legacy"
        return "legacy"
    except Exception:
        return "legacy"


def _rules_version_for_workbook(path: Path, stat_key: tuple[float, int]) -> str:
    ctx = _attempt_local.get()
    if ctx is not None and ctx.snapshot is not None:
        return ctx.snapshot.meta.ruleset_version
    published = _published
    try:
        if (
            published is not None
            and published.stat_key == stat_key
            and Path(published.capture_path).resolve() == path.resolve()
        ):
            return published.snapshot.meta.ruleset_version
    except OSError:
        pass
    return _read_meta_ruleset_version(path)


def _make_workbook_snapshot(
    *,
    local_path: str,
    stat_key: tuple[float, int],
    loaded_at_ts: float,
    source: str,
) -> RulesWorkbookSnapshot:
    p = Path(local_path)
    return RulesWorkbookSnapshot(
        local_path=local_path,
        stat_key=stat_key,
        loaded_at_ts=loaded_at_ts,
        source=source,
        rules_version=_rules_version_for_workbook(p, stat_key),
    )


def _rules_dropbox_path() -> str:
    """
    ``RULES_XLSX_PATH``:
      - can be '/folder' or '/folder/rules.xlsx'
    Returns Dropbox path to ``rules.xlsx``.
    """
    p = (os.getenv("RULES_XLSX_PATH") or "").strip()
    if not p:
        raise RuntimeError("RULES_XLSX_PATH пуст — ожидаю dropbox папку или путь к rules.xlsx")
    return p if p.lower().endswith(".xlsx") else p.rstrip("/") + "/rules.xlsx"


def _try_local_workbook_path() -> Path | None:
    raw = (os.getenv("RULES_XLSX_PATH") or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser().resolve()
    if p.is_file() and p.suffix.lower() == ".xlsx":
        return p
    return None


def _env_truthy(name: str) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _identity_save_enabled() -> bool:
    """Whether C3.5 registry baseline is written after publish (default off)."""

    return _env_truthy("RULES_IDENTITY_SAVE")


@contextmanager
def suppress_identity_registry_save() -> Iterator[None]:
    """Read-only ``get_snapshot_v2`` (diagnostics) must not persist identity registry."""

    token = _identity_registry_save_suppressed.set(True)
    try:
        yield
    finally:
        _identity_registry_save_suppressed.reset(token)


def _ttl_sec() -> float:
    return float(os.getenv("RULES_SYNC_MIN_INTERVAL_SEC", "30"))


def _wb_from_published(published: PublishedState, *, now: float) -> RulesWorkbookSnapshot:
    return RulesWorkbookSnapshot(
        local_path=published.capture_path,
        stat_key=published.stat_key,
        loaded_at_ts=now,
        source=published.source_path,
        rules_version=published.snapshot.meta.ruleset_version,
    )


def _freshness_hit(*, force_sync: bool, policy: ContractValidationMode, now: float) -> Optional[PublishedState]:
    """Return current ``_published`` if TTL/source/policy match. Caller holds ``_LOCK``."""

    if force_sync:
        return None
    published = _published
    if published is None:
        return None
    if published.policy_mode != policy.value:
        return None
    local_direct = _try_local_workbook_path()
    if local_direct is not None:
        try:
            if _stat_key(local_direct) != published.stat_key:
                return None
        except OSError:
            return None
        if not Path(published.capture_path).exists():
            return None
        return published
    if not Path(published.capture_path).exists():
        return None
    if (now - _last_rules_sync_ts) >= _ttl_sec():
        return None
    return published


def _existing_if_fresh_newer(*, observed: Optional[int]) -> Optional[PublishedState]:
    """EXISTING after a lost attempt. Caller holds ``_LOCK``.

    Allowed only when published generation is newer than ``observed``, current
    policy matches, and source/TTL freshness hits. Generation difference does
    not replace a freshness miss.
    """

    current = _published
    if current is None:
        return None
    if observed is not None and current.generation <= observed:
        return None
    policy = resolve_contract_validation_mode()
    hit = _freshness_hit(force_sync=False, policy=policy, now=time.time())
    if hit is None:
        return None
    return hit


def _legacy_build_fail_outcome(
    *,
    start_epoch: int,
    my_attempt: int,
    observed_generation: Optional[int],
) -> _PublishCallResult:
    """Legacy build/load fail. Caller holds ``_LOCK``. One captured ``_published``.

    Newer published generation is checked (with freshness) before stale_reuse.
    """

    if _invalidate_epoch != start_epoch:
        return _PublishCallResult("discard", None)
    current = _published
    if current is None:
        return _PublishCallResult("reject", None)
    newer_published = (
        observed_generation is None or current.generation > observed_generation
    )
    if newer_published:
        hit = _existing_if_fresh_newer(observed=observed_generation)
        if hit is not None:
            return _PublishCallResult(OUTCOME_EXISTING, hit)
        return _PublishCallResult("discard", None)
    if my_attempt == _latest_attempt:
        return _PublishCallResult(OUTCOME_STALE_REUSE, current)
    return _PublishCallResult("discard", None)


def _copy_to_capture(src: Path, attempt_id: int) -> Path:
    dest = _capture_dir() / f"attempt_{attempt_id}_{uuid.uuid4().hex}.xlsx"
    shutil.copy2(src, dest)
    return dest


def _try_save_identity_registry_after_publish(*_a: object, **_k: object) -> None:
    """Removed hook; identity temp is written after evaluate (§5). Kept for test patches."""

    return None


def _download_to_capture(db_path: str, attempt_id: int) -> Path | None:
    part = _capture_dir() / f"attempt_{attempt_id}_{uuid.uuid4().hex}.part.xlsx"
    try:
        if part.exists():
            part.unlink()
    except OSError:
        pass
    if not download_file(db_path, str(part)):
        return None
    if not _is_valid_xlsx_zip(part):
        log.warning(
            "rules.xlsx download rejected (corrupt/truncated); cache unchanged",
            extra={"dropbox_path": db_path, "part_path": str(part)},
        )
        try:
            part.unlink()
        except OSError:
            pass
        return None
    dest = _capture_dir() / f"attempt_{attempt_id}_{uuid.uuid4().hex}.xlsx"
    os.replace(str(part), str(dest))
    return dest


def _delete_unpublished_capture(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _materialize_capture(attempt_id: int) -> tuple[Path, str, tuple[float, int], str]:
    """Copy/download into this process capture dir. Never replaces canon."""

    now = time.time()
    local_direct = _try_local_workbook_path()
    if local_direct is not None:
        dest = _copy_to_capture(local_direct, attempt_id)
        return dest, f"local:{local_direct}", _stat_key(dest), str(local_direct)

    db_path = _rules_dropbox_path()
    dest = _download_to_capture(db_path, attempt_id)
    policy = resolve_contract_validation_mode()
    if dest is not None:
        return dest, db_path, _stat_key(dest), db_path

    with _LOCK:
        published = _published
    if published is not None and Path(published.capture_path).exists():
        if policy == ContractValidationMode.STRICT:
            raise RuntimeError(f"rules.xlsx download failed (strict; no stale reuse): {db_path}")
        cap = Path(published.capture_path)
        return cap, published.source_path, published.stat_key, published.source_path

    if _RULES_LOCAL.exists():
        dest = _copy_to_capture(_RULES_LOCAL, attempt_id)
        return dest, db_path, _stat_key(dest), db_path

    raise RuntimeError(f"rules.xlsx not available: download failed and no cache at {db_path}")


def _write_identity_temp(capture: Path, decision: SnapshotPublishDecision) -> Path | None:
    if _identity_registry_save_suppressed.get() or not _identity_save_enabled():
        return None
    snap = decision.snapshot
    if snap is None:
        return None
    try:
        from core.rules_v2.identity_drift import extract_identity_manifest
        from core.rules_v2.identity_registry_io import (
            IdentityRegistry,
            build_registry_from_manifest,
            canonical_registry_json,
        )

        manifest = extract_identity_manifest(capture)
        registry = build_registry_from_manifest(
            manifest,
            workbook_path=capture,
            meta_version=str(snap.meta.ruleset_version),
        )
        to_save = IdentityRegistry(
            rows=registry.rows,
            schema_version=registry.schema_version,
            workbook_path=registry.workbook_path,
            workbook_sha256=registry.workbook_sha256,
            stat_key=registry.stat_key,
            meta_version=registry.meta_version,
            published_at_utc=registry.published_at_utc,
        )
        dest = _capture_dir() / f"identity_{uuid.uuid4().hex}.json"
        dest.write_text(canonical_registry_json(to_save), encoding="utf-8")
        return dest
    except Exception:  # noqa: BLE001
        log.exception(
            "identity registry: temp after evaluate failed (ignored)",
            extra={"workbook_path": str(capture.resolve())},
        )
        return None


def _audit(kind: str, **payload: object) -> None:
    log.info("rules_publish %s", kind, extra={"rules_publish": {"kind": kind, **payload}})


def _run_publish_audit(wb: RulesWorkbookSnapshot, decision: SnapshotPublishDecision) -> None:
    try:
        from core.config_manager import rules_validate_all
        from core.rules_v2.rules_validate_audit import try_append_publish_audit_trail

        _leg_e, _leg_w = rules_validate_all(force_sync=False)
        try_append_publish_audit_trail(
            wb=wb,
            decision=decision,
            legacy_errors=list(_leg_e),
            legacy_warnings=list(_leg_w),
        )
    except Exception:  # noqa: BLE001
        log.exception("rules_validate_audit: publish hook failed (ignored)")


def invalidate_rules_v2_cache() -> None:
    """Drop published state. ``publish_seq`` is not reset. Published captures stay."""

    global _last_rules_sync_ts, _last_rules_wb, _published, _source_stat_at_publish
    global _latest_attempt, _invalidate_epoch

    with _LOCK:
        _invalidate_epoch += 1
        _latest_attempt += 1
        _published = None
        _last_rules_sync_ts = 0.0
        _last_rules_wb = None
        _source_stat_at_publish = None


def _register_attempt() -> tuple[int, int, Optional[int]]:
    global _latest_attempt
    with _LOCK:
        _latest_attempt += 1
        observed = _published.generation if _published is not None else None
        return _latest_attempt, _invalidate_epoch, observed


def _commit_attempt(
    *,
    my_attempt: int,
    start_epoch: int,
    capture_path: Path,
    source_path: str,
    stat_key: tuple[float, int],
    snapshot: RulesSnapshotV2,
    decision: SnapshotPublishDecision,
    indexes: RulesIndexes,
    policy: ContractValidationMode,
    canon_tmp: Path,
    identity_tmp: Optional[Path],
) -> Optional[PublishedState]:
    global _publish_seq, _published, _last_rules_wb, _last_rules_sync_ts, _source_stat_at_publish

    hook = before_commit_section
    if hook is not None:
        hook(my_attempt)

    with _LOCK:
        if _invalidate_epoch != start_epoch or my_attempt != _latest_attempt:
            return None
        _publish_seq += 1
        state = PublishedState(
            generation=_publish_seq,
            attempt_id=my_attempt,
            snapshot=snapshot,
            decision=decision,
            source_path=source_path,
            capture_path=str(capture_path),
            canon_path=str(_RULES_LOCAL),
            stat_key=stat_key,
            indexes=indexes,
            policy_mode=policy.value,
        )
        _published = state
        try:
            _RULES_LOCAL.parent.mkdir(parents=True, exist_ok=True)
            replace_canon(str(canon_tmp), str(_RULES_LOCAL))
        except OSError:
            log.exception(
                "rules canon replace failed (memory kept; capture kept)",
                extra={"capture_path": str(capture_path), "canon_path": str(_RULES_LOCAL)},
            )
        if identity_tmp is not None:
            try:
                canon_id = _identity_canon_path()
                canon_id.parent.mkdir(parents=True, exist_ok=True)
                replace_identity(str(identity_tmp), str(canon_id))
            except OSError:
                log.exception(
                    "identity canon replace failed (ignored for in-memory)",
                    extra={"identity_tmp": str(identity_tmp)},
                )
        now = time.time()
        _last_rules_wb = _wb_from_published(state, now=now)
        _last_rules_sync_ts = now
        local_direct = _try_local_workbook_path()
        if local_direct is not None:
            try:
                _source_stat_at_publish = _stat_key(local_direct)
            except OSError:
                _source_stat_at_publish = state.stat_key
        else:
            _source_stat_at_publish = state.stat_key
        return state


def _compute_attempt(
    my_attempt: int,
    start_epoch: int,
    observed_generation: Optional[int],
) -> _PublishCallResult:
    capture_path, source_label, stat_key, source_path = _materialize_capture(my_attempt)
    now = time.time()
    wb = _make_workbook_snapshot(
        local_path=str(capture_path),
        stat_key=stat_key,
        loaded_at_ts=now,
        source=source_label,
    )
    ctx = _AttemptLocal(
        attempt_id=my_attempt,
        capture_path=capture_path,
        source_path=source_path,
        stat_key=stat_key,
        wb=wb,
    )
    token = _attempt_local.set(ctx)
    canon_tmp: Optional[Path] = None
    identity_tmp: Optional[Path] = None
    unpublished = True
    try:
        policy = resolve_contract_validation_mode()
        decision = evaluate_snapshot_publish(capture_path, policy_mode=policy)
        ctx.decision = decision
        ctx.snapshot = decision.snapshot
        if decision.snapshot is not None and (
            decision.publish_allowed
            or (
                policy == ContractValidationMode.LEGACY
                and not decision.has_blocking_contract
                and decision.snapshot is not None
            )
        ):
            ctx.indexes = build_indexes(decision.snapshot)
        _run_publish_audit(wb, decision)
        _audit("attempt", attempt=my_attempt, observed_generation=observed_generation)
        eval_hook = after_attempt_evaluate
        if eval_hook is not None:
            eval_hook(my_attempt)

        if policy == ContractValidationMode.LEGACY:
            if decision.has_blocking_contract:
                _audit("reject", attempt=my_attempt)
                raise ContractPublishRejected(decision)
            if decision.snapshot is None or ctx.indexes is None:
                if decision.build_error or decision.load_error or decision.validation_crash:
                    with _LOCK:
                        fail_out = _legacy_build_fail_outcome(
                            start_epoch=start_epoch,
                            my_attempt=my_attempt,
                            observed_generation=observed_generation,
                        )
                    if fail_out.outcome == "reject":
                        _audit("reject", attempt=my_attempt)
                        raise ContractPublishRejected(decision)
                    if fail_out.outcome == OUTCOME_EXISTING:
                        gen = fail_out.state.generation if fail_out.state is not None else None
                        _audit("existing", attempt=my_attempt, generation=gen)
                        return fail_out
                    if fail_out.outcome == OUTCOME_STALE_REUSE:
                        log.warning(
                            "rules contract legacy: workbook build/load failed (non-blocking), "
                            "reusing last valid in-memory snapshot",
                            extra={"rules_contract": decision.to_log_dict()},
                        )
                        _audit("discard", attempt=my_attempt, reason="legacy_stale")
                        return fail_out
                    _audit("discard", attempt=my_attempt, reason="legacy_build_fail")
                    return fail_out
                _audit("reject", attempt=my_attempt)
                raise ContractPublishRejected(decision)
        elif not decision.publish_allowed:
            _audit("reject", attempt=my_attempt)
            raise ContractPublishRejected(decision)
        else:
            assert decision.snapshot is not None
            if ctx.indexes is None:
                ctx.indexes = build_indexes(decision.snapshot)

        identity_tmp = _write_identity_temp(capture_path, decision)
        canon_tmp = _capture_dir() / f"canon_{my_attempt}_{uuid.uuid4().hex}.xlsx"
        shutil.copy2(capture_path, canon_tmp)

        committed = _commit_attempt(
            my_attempt=my_attempt,
            start_epoch=start_epoch,
            capture_path=capture_path,
            source_path=source_label,
            stat_key=stat_key,
            snapshot=decision.snapshot,
            decision=decision,
            indexes=ctx.indexes,
            policy=policy,
            canon_tmp=canon_tmp,
            identity_tmp=identity_tmp,
        )
        if committed is None:
            _audit("discard", attempt=my_attempt)
            with _LOCK:
                captured = _published
            return _PublishCallResult("discard", captured)
        unpublished = False
        _audit("commit", attempt=my_attempt, generation=committed.generation)
        return _PublishCallResult(OUTCOME_FRESH_COMMIT, committed)
    finally:
        _attempt_local.reset(token)
        if canon_tmp is not None:
            try:
                if canon_tmp.exists():
                    canon_tmp.unlink()
            except OSError:
                pass
        if unpublished:
            _delete_unpublished_capture(capture_path)
            if identity_tmp is not None:
                try:
                    identity_tmp.unlink()
                except OSError:
                    pass


def _attempt_local_published(ctx: _AttemptLocal) -> PublishedState:
    """In-memory view of this attempt. Does not publish, bump ``publish_seq``, or replace canons."""

    assert ctx.snapshot is not None and ctx.indexes is not None and ctx.decision is not None
    return PublishedState(
        generation=0,
        attempt_id=ctx.attempt_id,
        snapshot=ctx.snapshot,
        decision=ctx.decision,
        source_path=ctx.source_path,
        capture_path=str(ctx.capture_path),
        canon_path=str(_RULES_LOCAL),
        stat_key=ctx.stat_key,
        indexes=ctx.indexes,
        policy_mode=resolve_contract_validation_mode().value,
    )


def _publish(*, force_sync: bool) -> _PublishCallResult:
    ctx = _attempt_local.get()
    if ctx is not None:
        if ctx.snapshot is not None and ctx.indexes is not None and ctx.decision is not None:
            return _PublishCallResult(OUTCOME_EXISTING, _attempt_local_published(ctx))
        raise RuntimeError(
            "nested rules accessor requires attempt-local snapshot, indexes, and decision "
            "(get_rules_snapshot may still return this attempt's workbook before that)"
        )

    policy = resolve_contract_validation_mode()
    now = time.time()
    with _LOCK:
        hit = _freshness_hit(force_sync=force_sync, policy=policy, now=now)
        published = _published
    if hit is not None:
        return _PublishCallResult(OUTCOME_EXISTING, hit)

    last_error: Optional[BaseException] = None
    for _ in range(3):
        my_attempt, start_epoch, observed = _register_attempt()
        try:
            result = _compute_attempt(my_attempt, start_epoch, observed)
        except ContractPublishRejected:
            raise
        if result.outcome == OUTCOME_FRESH_COMMIT:
            return result
        if result.outcome in (OUTCOME_STALE_REUSE, OUTCOME_EXISTING):
            return result
        with _LOCK:
            existing = _existing_if_fresh_newer(observed=observed)
        if existing is not None:
            return _PublishCallResult(OUTCOME_EXISTING, existing)
        last_error = None
    raise RulesPublishConflictExhausted("rules publish: 3 attempts lost without commit") from last_error


def get_published_state(*, force_sync: bool = False) -> PublishedState:
    result = _publish(force_sync=force_sync)
    if result.state is None:
        raise RuntimeError("rules publish produced no PublishedState")
    return result.state


def publish_with_outcome(*, force_sync: bool = False) -> _PublishCallResult:
    return _publish(force_sync=force_sync)


def with_provider_lock(fn):  # noqa: ANN001
    """Run ``fn(published)`` while holding the provider lock (AccessRules CAS)."""

    with _LOCK:
        return fn(_published)


def get_rules_snapshot(*, force_sync: bool = False) -> RulesWorkbookSnapshot:
    """Return workbook path + ``stat_key``. On published hit, ``local_path`` is capture."""

    global _last_rules_wb, _last_rules_sync_ts

    ctx = _attempt_local.get()
    if ctx is not None:
        return ctx.wb

    now = time.time()
    policy = resolve_contract_validation_mode()
    with _LOCK:
        start_epoch = _invalidate_epoch
        hit = _freshness_hit(force_sync=force_sync, policy=policy, now=now)
        published = _published
        cached = _last_rules_wb
        sync_ts = _last_rules_sync_ts

    local_wb: Optional[RulesWorkbookSnapshot] = None
    if hit is not None:
        local_wb = _wb_from_published(hit, now=now)
    elif not force_sync and published is None and cached is not None:
        local_direct = _try_local_workbook_path()
        try:
            if local_direct is not None:
                if Path(cached.local_path).exists() and _stat_key(local_direct) == cached.stat_key:
                    local_wb = cached
            elif Path(cached.local_path).exists() and (now - sync_ts) < _ttl_sec():
                if _stat_key(Path(cached.local_path)) == cached.stat_key:
                    local_wb = cached
        except OSError:
            local_wb = None

    if local_wb is None:
        with _LOCK:
            dummy_attempt = _latest_attempt
        capture_path, source_label, stat_key, _source = _materialize_capture(dummy_attempt)
        local_wb = _make_workbook_snapshot(
            local_path=str(capture_path),
            stat_key=stat_key,
            loaded_at_ts=now,
            source=source_label,
        )

    hook = before_compat_cache_write
    if hook is not None:
        hook()

    with _LOCK:
        if _invalidate_epoch == start_epoch:
            _last_rules_wb = local_wb
            if _try_local_workbook_path() is None:
                _last_rules_sync_ts = now
    return local_wb


def get_snapshot_v2(*, force_sync: bool = False) -> RulesSnapshotV2:
    """Load ``RulesSnapshotV2`` through C4 publish policy."""

    return get_published_state(force_sync=force_sync).snapshot


def get_indexes_v2(*, force_sync: bool = False) -> RulesIndexes:
    """Return ``RulesIndexes`` for the published snapshot of this accessor call."""

    return get_published_state(force_sync=force_sync).indexes


__all__ = [
    "PublishedState",
    "RulesPublishConflictExhausted",
    "RulesWorkbookSnapshot",
    "get_published_state",
    "get_rules_snapshot",
    "get_snapshot_v2",
    "get_indexes_v2",
    "publish_with_outcome",
    "with_provider_lock",
    "_rules_dropbox_path",
    "invalidate_rules_v2_cache",
    "suppress_identity_registry_save",
    "ContractPublishRejected",
]
