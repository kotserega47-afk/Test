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
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

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


_RULES_CACHE_DIR = Path("/tmp/rules_cache")
_RULES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_RULES_LOCAL = _RULES_CACHE_DIR / "rules.xlsx"

log = logging.getLogger(__name__)

_last_rules_sync_ts: float = 0.0
_last_rules_wb: Optional[RulesWorkbookSnapshot] = None

_last_v2_stat: Optional[tuple[float, int]] = None
_last_v2_snapshot: Optional[RulesSnapshotV2] = None
_last_v2_decision: Optional[SnapshotPublishDecision] = None

_last_indexes: Optional[RulesIndexes] = None
_last_indexes_stat: Optional[tuple[float, int]] = None
_last_indexes_policy: Optional[str] = None
_last_indexes_snapshot_id: Optional[int] = None


def _stat_key(path: Path) -> tuple[float, int]:
    st = path.stat()
    return (st.st_mtime, st.st_size)


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
    """Prefer published snapshot meta when this workbook is already loaded under C4."""

    global _last_rules_wb, _last_v2_stat, _last_v2_snapshot

    try:
        if (
            _last_rules_wb is not None
            and _last_v2_snapshot is not None
            and _last_v2_stat == stat_key
            and Path(_last_rules_wb.local_path).resolve() == path.resolve()
        ):
            return _last_v2_snapshot.meta.ruleset_version
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


_identity_registry_save_suppressed: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "identity_registry_save_suppressed",
    default=False,
)


@contextmanager
def suppress_identity_registry_save() -> Iterator[None]:
    """Read-only ``get_snapshot_v2`` (diagnostics) must not persist identity registry."""

    token = _identity_registry_save_suppressed.set(True)
    try:
        yield
    finally:
        _identity_registry_save_suppressed.reset(token)


def _try_save_identity_registry_after_publish(
    workbook_path: Path,
    decision: SnapshotPublishDecision,
) -> None:
    """Best-effort registry baseline after ``publish_allowed`` (§17.7); never raises."""

    if _identity_registry_save_suppressed.get() or not _identity_save_enabled():
        return
    snap = decision.snapshot
    if snap is None:
        return
    try:
        from core.rules_v2.identity_drift import extract_identity_manifest
        from core.rules_v2.identity_registry_io import (
            build_registry_from_manifest,
            save_identity_registry,
        )

        manifest = extract_identity_manifest(workbook_path)
        registry = build_registry_from_manifest(
            manifest,
            workbook_path=workbook_path,
            meta_version=str(snap.meta.ruleset_version),
        )
        save_identity_registry(registry)
    except Exception:  # noqa: BLE001
        log.exception(
            "identity registry: save after publish failed (ignored)",
            extra={"workbook_path": str(workbook_path.resolve())},
        )


def invalidate_rules_v2_cache() -> None:
    """Drop cached workbook metadata, published snapshot, and ``RulesIndexes`` (C4)."""

    global _last_rules_sync_ts, _last_rules_wb
    global _last_v2_stat, _last_v2_snapshot, _last_v2_decision
    global _last_indexes, _last_indexes_stat, _last_indexes_policy, _last_indexes_snapshot_id

    _last_rules_sync_ts = 0.0
    _last_rules_wb = None
    _last_v2_stat = None
    _last_v2_snapshot = None
    _last_v2_decision = None
    _last_indexes = None
    _last_indexes_stat = None
    _last_indexes_policy = None
    _last_indexes_snapshot_id = None


def get_rules_snapshot(*, force_sync: bool = False) -> RulesWorkbookSnapshot:
    """Ensure ``rules.xlsx`` exists locally and return its path + ``stat_key``."""

    global _last_rules_sync_ts, _last_rules_wb

    ttl = float(os.getenv("RULES_SYNC_MIN_INTERVAL_SEC", "30"))
    now = time.time()

    local_direct = _try_local_workbook_path()
    if local_direct is not None:
        st = _stat_key(local_direct)
        snap = _make_workbook_snapshot(
            local_path=str(local_direct),
            stat_key=st,
            loaded_at_ts=now,
            source=f"local:{local_direct}",
        )
        _last_rules_wb = snap
        return snap

    if (
        (not force_sync)
        and _last_rules_wb is not None
        and Path(_last_rules_wb.local_path).exists()
        and (now - _last_rules_sync_ts) < ttl
    ):
        try:
            if _stat_key(Path(_last_rules_wb.local_path)) == _last_rules_wb.stat_key:
                return _last_rules_wb
        except OSError:
            pass

    db_path = _rules_dropbox_path()
    ok = download_file(db_path, str(_RULES_LOCAL))
    policy = resolve_contract_validation_mode()

    if ok and _RULES_LOCAL.exists():
        _last_rules_sync_ts = now
        key = _stat_key(_RULES_LOCAL)
        snap = _make_workbook_snapshot(
            local_path=str(_RULES_LOCAL),
            stat_key=key,
            loaded_at_ts=now,
            source=db_path,
        )
        _last_rules_wb = snap
        return snap

    # fail-soft: reuse last local workbook if present (legacy-style availability).
    if _last_rules_wb is not None and Path(_last_rules_wb.local_path).exists():
        if policy == ContractValidationMode.STRICT:
            raise RuntimeError(f"rules.xlsx download failed (strict; no stale reuse): {db_path}")
        return _last_rules_wb

    if _RULES_LOCAL.exists():
        _last_rules_sync_ts = now
        key = _stat_key(_RULES_LOCAL)
        snap = _make_workbook_snapshot(
            local_path=str(_RULES_LOCAL),
            stat_key=key,
            loaded_at_ts=now,
            source=db_path,
        )
        _last_rules_wb = snap
        return snap

    raise RuntimeError(f"rules.xlsx not available: download failed and no cache at {db_path}")


def get_snapshot_v2(*, force_sync: bool = False) -> RulesSnapshotV2:
    """Load ``RulesSnapshotV2`` through C4 publish policy."""

    global _last_v2_stat, _last_v2_snapshot, _last_v2_decision

    wb = get_rules_snapshot(force_sync=force_sync)
    path = Path(wb.local_path)
    st = _stat_key(path)

    policy = resolve_contract_validation_mode()

    if (
        not force_sync
        and _last_v2_snapshot is not None
        and _last_v2_stat == st
        and _last_v2_decision is not None
        and _last_v2_decision.publish_allowed
        and _last_v2_decision.policy_mode == policy.value
    ):
        return _last_v2_snapshot

    decision = evaluate_snapshot_publish(path, policy_mode=policy)

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

    if not decision.publish_allowed:
        raise ContractPublishRejected(decision)

    assert decision.snapshot is not None
    _try_save_identity_registry_after_publish(path, decision)
    _last_v2_stat = st
    _last_v2_snapshot = decision.snapshot
    _last_v2_decision = decision
    return decision.snapshot


def get_indexes_v2(*, force_sync: bool = False) -> RulesIndexes:
    """Return ``RulesIndexes`` for the published snapshot (cached across calls).

    Reuses the same ``RulesIndexes`` instance when the workbook ``stat_key``,
    contract policy mode, and cached snapshot match — without calling
    ``build_indexes`` again. ``force_sync=True`` forces a fresh snapshot then
    rebuilds indexes.
    """

    global _last_indexes, _last_indexes_stat, _last_indexes_policy, _last_indexes_snapshot_id

    policy = resolve_contract_validation_mode()
    policy_s = policy.value

    snapshot = get_snapshot_v2(force_sync=force_sync)
    snap_id = id(snapshot)

    if (
        not force_sync
        and _last_indexes is not None
        and _last_indexes_stat is not None
        and _last_indexes_snapshot_id is not None
        and _last_indexes_policy == policy_s
        and _last_v2_stat is not None
        and _last_indexes_stat == _last_v2_stat
        and _last_indexes_snapshot_id == snap_id
    ):
        return _last_indexes

    indexes = build_indexes(snapshot)
    _last_indexes = indexes
    _last_indexes_stat = _last_v2_stat
    _last_indexes_policy = policy_s
    _last_indexes_snapshot_id = snap_id
    return indexes


__all__ = [
    "RulesWorkbookSnapshot",
    "get_rules_snapshot",
    "get_snapshot_v2",
    "get_indexes_v2",
    "_rules_dropbox_path",
    "invalidate_rules_v2_cache",
    "suppress_identity_registry_save",
    "ContractPublishRejected",
]
