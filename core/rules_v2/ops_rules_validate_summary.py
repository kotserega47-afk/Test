"""Read-only diagnostics for rules_v2 validation + publish (C6 / C6.1).

Machine-readable ``RulesValidatePayload`` is the single source of truth;
Telegram text and JSON exports are derived views (no duplicate field logic).

Does not change provider caches, validation semantics, or contract policy.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from core.config_manager import rules_validate_all
from core.rules_provider import (
    ContractPublishRejected,
    RulesWorkbookSnapshot,
    get_rules_snapshot,
)
from core.rules_v2.contract_publish import (
    SnapshotPublishDecision,
    evaluate_snapshot_publish,
    resolve_contract_validation_mode,
)
from core.rules_v2.models import RulesSnapshotV2
from core.rules_v2.snapshot_fingerprint import rules_snapshot_fingerprint
from core.rules_v2.validation_issues import ValidationIssue, ValidationSeverity, is_blocking

_TELEGRAM_SOFT_LIMIT = 3800
_PAYLOAD_SCHEMA_VERSION = "rules_validate_payload.v1"


def _classify_source_kind(wb: RulesWorkbookSnapshot) -> str:
    """Coarse origin tag: ``local`` / ``dropbox`` / ``cache``."""

    src = (wb.source or "").strip()
    if src.startswith("local:"):
        return "local"
    try:
        resolved = Path(wb.local_path).resolve()
        parts_lower = {p.lower() for p in resolved.parts}
        if "rules_cache" in parts_lower:
            return "cache"
    except OSError:
        pass
    return "dropbox"


def _iso_from_epoch(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _issue_record(issue: ValidationIssue) -> ContractIssueRecord:
    sev = issue.severity
    sev_s = sev.value if isinstance(sev, ValidationSeverity) else str(sev)
    return ContractIssueRecord(
        code=str(issue.code),
        severity=str(sev_s),
        message=str(issue.message),
        sheet=issue.sheet,
        row_index=issue.row_index,
        rule_id=issue.rule_id,
        field=issue.field,
    )


def _top_contract_issue_records(
    decision: SnapshotPublishDecision,
    *,
    predicate,
    limit: int,
) -> tuple[ContractIssueRecord, ...]:
    out: list[ContractIssueRecord] = []
    for issue in decision.contract_issues:
        if predicate(issue):
            out.append(_issue_record(issue))
        if len(out) >= limit:
            break
    return tuple(out)


def _top_identity_shadow_records(
    decision: SnapshotPublishDecision,
    *,
    limit: int = 8,
) -> tuple[ContractIssueRecord, ...]:
    out: list[ContractIssueRecord] = []
    for issue in decision.identity_shadow_issues:
        out.append(_issue_record(issue))
        if len(out) >= limit:
            break
    return tuple(out)


@dataclass(frozen=True, slots=True)
class ContractIssueRecord:
    """One contract validation finding (JSON-safe, immutable)."""

    code: str
    severity: str
    message: str
    sheet: str | None
    row_index: int | None
    rule_id: str | None
    field: str | None


@dataclass(frozen=True, slots=True)
class RulesValidatePayload:
    """Immutable machine-readable rules validation + publish snapshot (C6.1)."""

    schema_version: str
    meta_version: str
    stat_key_mtime: float
    stat_key_size: int
    workbook_path: str
    workbook_loaded_at_iso: str
    source_raw: str
    source_kind: str
    validation_mode: str
    validators_strict: bool
    contract_error_count: int
    contract_warning_count: int
    contract_info_count: int
    contract_issue_total: int
    legacy_error_count: int
    legacy_warning_count: int
    total_issue_count: int
    snapshot_fingerprint: str | None
    evaluated_snapshot_updated_at_iso: str | None
    publish_allowed: bool
    load_error: str | None
    build_error: str | None
    validation_crash: str | None
    has_blocking_contract: bool
    blocking_issue_codes: tuple[str, ...]
    contract_top_blocking: tuple[ContractIssueRecord, ...]
    contract_top_warnings: tuple[ContractIssueRecord, ...]
    identity_shadow_issue_total: int
    identity_shadow_issues: tuple[ContractIssueRecord, ...]
    top_legacy_errors: tuple[str, ...]
    top_legacy_warnings: tuple[str, ...]
    active_runtime_readable: bool
    active_ruleset_version: str | None
    active_snapshot_fingerprint: str | None
    active_meta_updated_at_iso: str | None
    active_failure_kind: str | None
    active_failure_detail: str | None
    fingerprint_matches_evaluated: bool | None


ActiveResolutionMode = Literal["runtime_get", "published", "rejected_audit"]


def _resolve_active_fields(
    *,
    mode: ActiveResolutionMode,
    decision: SnapshotPublishDecision,
    published_snapshot: RulesSnapshotV2 | None,
) -> tuple[
    bool,
    str | None,
    str | None,
    str | None,
    str | None,
    str | None,
    bool | None,
]:
    """Return active-runtime slice for ``RulesValidatePayload`` (no provider recursion in ``rejected_audit``)."""

    if mode == "published":
        if published_snapshot is None:
            raise ValueError("published mode requires published_snapshot")
        active_fp = rules_snapshot_fingerprint(published_snapshot)
        fp_match = (
            active_fp == decision.snapshot_fingerprint
            if decision.snapshot_fingerprint is not None
            else None
        )
        return (
            True,
            str(published_snapshot.meta.ruleset_version),
            active_fp,
            published_snapshot.meta.updated_at.isoformat(),
            None,
            None,
            fp_match,
        )
    if mode == "rejected_audit":
        d = decision
        return (
            False,
            None,
            None,
            None,
            "publish_rejected",
            f"policy={d.policy_mode} publish_allowed={d.publish_allowed} blocking={d.has_blocking_contract}",
            None,
        )
    # runtime_get — lazy import avoids import cycles with optional audit hooks in ``rules_provider``.
    from core.rules_provider import get_snapshot_v2 as _get_snapshot_v2
    from core.rules_provider import suppress_identity_registry_save

    active_runtime_readable = False
    active_ruleset_version: str | None = None
    active_fp: str | None = None
    active_meta_updated_at_iso: str | None = None
    active_failure_kind: str | None = None
    active_failure_detail: str | None = None
    fp_match: bool | None = None
    try:
        with suppress_identity_registry_save():
            snap = _get_snapshot_v2(force_sync=False)
        active_runtime_readable = True
        active_ruleset_version = str(snap.meta.ruleset_version)
        active_fp = rules_snapshot_fingerprint(snap)
        active_meta_updated_at_iso = snap.meta.updated_at.isoformat()
        if decision.snapshot_fingerprint is not None:
            fp_match = active_fp == decision.snapshot_fingerprint
    except ContractPublishRejected as exc:
        d = exc.decision
        active_failure_kind = "publish_rejected"
        active_failure_detail = (
            f"policy={d.policy_mode} publish_allowed={d.publish_allowed} "
            f"blocking={d.has_blocking_contract}"
        )
    except Exception as exc:  # noqa: BLE001
        active_failure_kind = type(exc).__name__
        active_failure_detail = str(exc)[:2000]
    return (
        active_runtime_readable,
        active_ruleset_version,
        active_fp,
        active_meta_updated_at_iso,
        active_failure_kind,
        active_failure_detail,
        fp_match,
    )


def assemble_rules_validate_payload(
    wb: RulesWorkbookSnapshot,
    decision: SnapshotPublishDecision,
    legacy_errors: list[str],
    legacy_warnings: list[str],
    *,
    active_mode: ActiveResolutionMode,
    published_snapshot: RulesSnapshotV2 | None = None,
) -> RulesValidatePayload:
    """Assemble canonical ``RulesValidatePayload`` (shared by Telegram, audit, CI)."""

    ctot = len(decision.contract_issues)
    total_issue_count = ctot + len(legacy_errors) + len(legacy_warnings)

    if decision.snapshot is not None:
        meta_version = str(decision.snapshot.meta.ruleset_version)
        evaluated_snapshot_updated_at_iso = decision.snapshot.meta.updated_at.isoformat()
    else:
        meta_version = str(wb.rules_version)
        evaluated_snapshot_updated_at_iso = None

    top_legacy_errors = tuple(str(x) for x in legacy_errors[:8])
    top_legacy_warnings = tuple(str(x) for x in legacy_warnings[:8])

    (
        active_runtime_readable,
        active_ruleset_version,
        active_fp,
        active_meta_updated_at_iso,
        active_failure_kind,
        active_failure_detail,
        fp_match,
    ) = _resolve_active_fields(
        mode=active_mode,
        decision=decision,
        published_snapshot=published_snapshot,
    )

    st = wb.stat_key
    return RulesValidatePayload(
        schema_version=_PAYLOAD_SCHEMA_VERSION,
        meta_version=meta_version,
        stat_key_mtime=float(st[0]),
        stat_key_size=int(st[1]),
        workbook_path=str(Path(wb.local_path).resolve()),
        workbook_loaded_at_iso=_iso_from_epoch(float(wb.loaded_at_ts)),
        source_raw=str(wb.source),
        source_kind=_classify_source_kind(wb),
        validation_mode=str(decision.policy_mode),
        validators_strict=bool(decision.validators_strict),
        contract_error_count=int(decision.error_count),
        contract_warning_count=int(decision.warning_count),
        contract_info_count=int(decision.info_count),
        contract_issue_total=ctot,
        legacy_error_count=len(legacy_errors),
        legacy_warning_count=len(legacy_warnings),
        total_issue_count=total_issue_count,
        snapshot_fingerprint=decision.snapshot_fingerprint,
        evaluated_snapshot_updated_at_iso=evaluated_snapshot_updated_at_iso,
        publish_allowed=bool(decision.publish_allowed),
        load_error=decision.load_error,
        build_error=decision.build_error,
        validation_crash=decision.validation_crash,
        has_blocking_contract=bool(decision.has_blocking_contract),
        blocking_issue_codes=tuple(str(c) for c in decision.blocking_issue_codes),
        contract_top_blocking=_top_contract_issue_records(
            decision,
            predicate=is_blocking,
            limit=8,
        ),
        contract_top_warnings=_top_contract_issue_records(
            decision,
            predicate=lambda i: i.severity == ValidationSeverity.WARN,
            limit=8,
        ),
        identity_shadow_issue_total=len(decision.identity_shadow_issues),
        identity_shadow_issues=_top_identity_shadow_records(decision, limit=8),
        top_legacy_errors=top_legacy_errors,
        top_legacy_warnings=top_legacy_warnings,
        active_runtime_readable=active_runtime_readable,
        active_ruleset_version=active_ruleset_version,
        active_snapshot_fingerprint=active_fp,
        active_meta_updated_at_iso=active_meta_updated_at_iso,
        active_failure_kind=active_failure_kind,
        active_failure_detail=active_failure_detail,
        fingerprint_matches_evaluated=fp_match,
    )


def build_rules_validate_payload_for_publish_audit(
    wb: RulesWorkbookSnapshot,
    decision: SnapshotPublishDecision,
    legacy_errors: list[str],
    legacy_warnings: list[str],
) -> RulesValidatePayload:
    """Payload for C7b audit from ``rules_provider`` (no ``get_snapshot_v2`` recursion on reject)."""

    if decision.publish_allowed and decision.snapshot is not None:
        return assemble_rules_validate_payload(
            wb,
            decision,
            legacy_errors,
            legacy_warnings,
            active_mode="published",
            published_snapshot=decision.snapshot,
        )
    return assemble_rules_validate_payload(
        wb,
        decision,
        legacy_errors,
        legacy_warnings,
        active_mode="rejected_audit",
    )


def build_rules_validate_payload() -> RulesValidatePayload:
    """Build immutable payload (read-only APIs only; no Excel/pandas in result)."""

    wb = get_rules_snapshot(force_sync=False)
    policy = resolve_contract_validation_mode()
    decision = evaluate_snapshot_publish(Path(wb.local_path), policy_mode=policy)
    legacy_errors, legacy_warnings = rules_validate_all(force_sync=False)
    return assemble_rules_validate_payload(
        wb,
        decision,
        list(legacy_errors),
        list(legacy_warnings),
        active_mode="runtime_get",
    )


def rules_validate_payload_to_jsonable(payload: RulesValidatePayload) -> dict[str, Any]:
    """Return a plain ``dict`` / JSON-serializable snapshot (lists, no tuples; no mutation of *payload*)."""

    def _convert(obj: Any) -> Any:
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj
        if is_dataclass(obj) and not isinstance(obj, type):
            return {k: _convert(v) for k, v in asdict(obj).items()}
        if isinstance(obj, tuple):
            return [_convert(x) for x in obj]
        raise TypeError(f"Unsupported payload value type: {type(obj)!r}")

    out = {f.name: _convert(getattr(payload, f.name)) for f in fields(payload)}
    out["stat_key"] = [payload.stat_key_mtime, payload.stat_key_size]
    return out


def rules_validate_payload_json_dumps(payload: RulesValidatePayload, **json_kwargs: Any) -> str:
    """Serialize *payload* to a JSON string (UTF-8 safe, stable for CI/audit)."""

    return json.dumps(rules_validate_payload_to_jsonable(payload), ensure_ascii=False, **json_kwargs)


def _active_status_line(p: RulesValidatePayload) -> str:
    if p.active_runtime_readable:
        return (
            f"readable=yes ruleset_version={p.active_ruleset_version!r} "
            f"updated_at={p.active_meta_updated_at_iso}"
        )
    if p.active_failure_kind == "publish_rejected":
        return f"readable=no (ContractPublishRejected) {p.active_failure_detail or ''}"
    if p.active_failure_kind:
        return f"readable=no ({p.active_failure_kind}: {p.active_failure_detail or ''})"
    return "readable=no"


def format_rules_validate_telegram(p: RulesValidatePayload) -> str:
    """Human layout derived only from ``RulesValidatePayload`` (no parallel field sources)."""

    lines: list[str] = [
        "📋 /rules_validate — rules_v2 (read-only)",
        "",
        f"meta.version (ruleset): {p.meta_version}",
        f"snapshot_fingerprint: {p.snapshot_fingerprint or '—'}",
        f"stat_key (mtime,size): ({p.stat_key_mtime!r}, {p.stat_key_size!r})",
        f"workbook_loaded_at: {p.workbook_loaded_at_iso}",
        f"source_kind: {p.source_kind}",
        f"source_ref: {p.source_raw}",
        f"workbook_path: {p.workbook_path}",
        "",
        f"validation_mode: {p.validation_mode}",
        f"validators_strict: {p.validators_strict}",
        "",
        "— contract (C2+C3+C4) —",
        f"errors: {p.contract_error_count}  warnings: {p.contract_warning_count}  info: {p.contract_info_count}",
        f"contract_issues_total: {p.contract_issue_total}",
        f"has_blocking_contract: {p.has_blocking_contract}",
        f"blocking_codes: {list(p.blocking_issue_codes) or '—'}",
        "",
        "— legacy sheet validators —",
        f"errors: {p.legacy_error_count}  warnings: {p.legacy_warning_count}",
        "",
        f"total_issues (contract rows + legacy strings): {p.total_issue_count}",
        "",
        f"snapshot_timestamp (evaluated meta.updated_at): {p.evaluated_snapshot_updated_at_iso or '—'}",
        f"publish_allowed: {'yes' if p.publish_allowed else 'no'}",
        "",
        "— active runtime snapshot (get_snapshot_v2 force_sync=False) —",
        f"active_readable: {'yes' if p.active_runtime_readable else 'no'}",
        _active_status_line(p),
    ]
    if p.active_snapshot_fingerprint:
        lines.append(f"active_fingerprint: {p.active_snapshot_fingerprint}")
    if p.fingerprint_matches_evaluated is not None:
        lines.append(f"fingerprint_matches_evaluated: {p.fingerprint_matches_evaluated}")

    if p.load_error or p.build_error or p.validation_crash:
        lines.extend(
            [
                "",
                "— infra —",
                f"load_error: {p.load_error or '—'}",
                f"build_error: {p.build_error or '—'}",
                f"validation_crash: {p.validation_crash or '—'}",
            ]
        )

    lines.extend(["", "— top blocking (contract) —"])
    if p.contract_top_blocking:
        lines.extend(f"• {rec.message}" for rec in p.contract_top_blocking)
    else:
        lines.append("—")

    lines.extend(["", "— top warnings (contract) —"])
    if p.contract_top_warnings:
        lines.extend(f"• {rec.message}" for rec in p.contract_top_warnings)
    else:
        lines.append("—")

    lines.extend(
        [
            "",
            "— Identity shadow findings —",
            f"identity_shadow_issue_total: {p.identity_shadow_issue_total}",
        ]
    )
    if p.identity_shadow_issues:
        lines.extend(f"• {rec.message}" for rec in p.identity_shadow_issues)
    else:
        lines.append("—")

    lines.extend(["", "— top legacy errors —"])
    if p.top_legacy_errors:
        lines.extend(f"• {m}" for m in p.top_legacy_errors)
    else:
        lines.append("—")

    lines.extend(["", "— top legacy warnings —"])
    if p.top_legacy_warnings:
        lines.extend(f"• {m}" for m in p.top_legacy_warnings)
    else:
        lines.append("—")

    return "\n".join(lines)


def chunk_telegram_text(text: str, *, limit: int = _TELEGRAM_SOFT_LIMIT) -> list[str]:
    """Split a long diagnostic into Telegram-sized segments."""

    text = text.strip("\n")
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            chunks.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut == -1 or cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:]
    return chunks


def build_rules_validate_telegram_chunks_with_payload() -> tuple[list[str], RulesValidatePayload]:
    """Single payload build for Telegram + optional C7b manual audit."""

    p = build_rules_validate_payload()
    return chunk_telegram_text(format_rules_validate_telegram(p)), p


def build_rules_validate_telegram_chunks() -> list[str]:
    """Payload + Telegram layout + chunking."""

    chunks, _p = build_rules_validate_telegram_chunks_with_payload()
    return chunks


# Backward compatibility (C6 name); identical to ``build_rules_validate_payload``.
def build_rules_validate_diagnostics() -> RulesValidatePayload:
    return build_rules_validate_payload()


RulesValidateDiagnostics = RulesValidatePayload

__all__ = [
    "ActiveResolutionMode",
    "ContractIssueRecord",
    "RulesValidateDiagnostics",
    "RulesValidatePayload",
    "assemble_rules_validate_payload",
    "build_rules_validate_diagnostics",
    "build_rules_validate_payload",
    "build_rules_validate_payload_for_publish_audit",
    "build_rules_validate_telegram_chunks",
    "build_rules_validate_telegram_chunks_with_payload",
    "chunk_telegram_text",
    "format_rules_validate_telegram",
    "rules_validate_payload_json_dumps",
    "rules_validate_payload_to_jsonable",
]
