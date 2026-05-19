"""C4 — contract publish policy (strict / legacy / shadow) around validation.

This module does **not** change bridge, snapshot shape, or validator logic.
It composes existing ``validate_workbook_schema`` / ``validate_snapshot``
results into an explicit ``SnapshotPublishDecision`` and applies env-driven
policy for whether a snapshot may be published to runtime consumers.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from core.rules_v2.bridge_legacy import build_snapshot_v2_from_legacy
from core.rules_v2.snapshot_fingerprint import rules_snapshot_fingerprint
from core.rules_v2.models import RulesSnapshotV2
from core.rules_v2.validation_issues import (
    ValidationIssue,
    ValidationSeverity,
    count_by_severity,
    has_blocking_errors,
    is_blocking,
)
from core.rules_v2.validation_snapshot import validate_snapshot
from core.rules_v2.validation_commands import validate_duplicate_command_policies
from core.rules_v2.validation_workbook import (
    read_workbook_headers,
    validate_meta_version,
    validate_workbook_schema,
)

log = logging.getLogger(__name__)

PolicyMode = Literal["legacy", "strict", "shadow"]


def _env_truthy(name: str) -> bool:
    v = (os.getenv(name) or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


class ContractValidationMode(str, Enum):
    """Resolved policy mode. Precedence: strict > shadow > legacy."""

    LEGACY = "legacy"
    STRICT = "strict"
    SHADOW = "shadow"


def resolve_contract_validation_mode() -> ContractValidationMode:
    if _env_truthy("RULES_CONTRACT_STRICT"):
        return ContractValidationMode.STRICT
    if _env_truthy("RULES_CONTRACT_SHADOW"):
        return ContractValidationMode.SHADOW
    return ContractValidationMode.LEGACY


def validators_use_strict(policy: ContractValidationMode) -> bool:
    """Whether CONTRACT_V2 validators run with ``strict=True``.

    Legacy: ``False`` (unchanged default semantics).
    Strict: ``True`` (catalog severities for duplicates/orphans/overlaps).
    Shadow: ``True`` (same diagnostics as strict, publish policy differs).
    """

    return policy != ContractValidationMode.LEGACY


@dataclass(frozen=True, slots=True)
class SnapshotPublishDecision:
    """Explicit outcome of workbook → snapshot → contract validation."""

    workbook_path: str
    policy_mode: PolicyMode
    validators_strict: bool
    contract_issues: tuple[ValidationIssue, ...]
    has_blocking_contract: bool
    blocking_issue_codes: tuple[str, ...]
    warning_count: int
    info_count: int
    error_count: int
    snapshot_fingerprint: str | None
    snapshot: RulesSnapshotV2 | None
    publish_allowed: bool
    load_error: str | None = None
    build_error: str | None = None
    validation_crash: str | None = None

    def to_log_dict(self) -> dict[str, Any]:
        """Structured payload for application logging."""

        return {
            "event": "rules_contract_publish",
            "workbook_path": self.workbook_path,
            "policy_mode": self.policy_mode,
            "validators_strict": self.validators_strict,
            "publish_allowed": self.publish_allowed,
            "has_blocking_contract": self.has_blocking_contract,
            "blocking_issue_codes": list(self.blocking_issue_codes),
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "snapshot_fingerprint": self.snapshot_fingerprint,
            "load_error": self.load_error,
            "build_error": self.build_error,
            "validation_crash": self.validation_crash,
            "contract_issue_total": len(self.contract_issues),
        }


class ContractPublishRejected(RuntimeError):
    """Strict mode blocked publication; inspect ``decision`` for details."""

    def __init__(self, decision: SnapshotPublishDecision):
        self.decision = decision
        msg = (
            f"rules snapshot publish blocked ({decision.policy_mode}): "
            f"blocking={decision.has_blocking_contract} "
            f"codes={list(decision.blocking_issue_codes)}"
        )
        super().__init__(msg)


def _blocking_codes(issues: Iterable[ValidationIssue]) -> tuple[str, ...]:
    return tuple(sorted({i.code for i in issues if is_blocking(i)}))


def _identity_compare_enabled() -> bool:
    """Whether C3.5 identity drift issues are merged (default off)."""

    if _env_truthy("RULES_IDENTITY_DISABLED"):
        return False
    mode = (os.getenv("RULES_IDENTITY_COMPARE") or "").strip().lower()
    return mode in {"on", "enforce"}


def _identity_drift_issues(workbook_path: Path, *, strict: bool) -> list[ValidationIssue]:
    from core.rules_v2.identity_drift import compare_identity_registry, extract_identity_manifest
    from core.rules_v2.identity_registry_io import load_identity_registry

    registry = load_identity_registry()
    manifest = extract_identity_manifest(workbook_path)
    return list(compare_identity_registry(manifest, registry, strict=strict))


def evaluate_snapshot_publish(
    workbook_path: str | Path,
    *,
    policy_mode: ContractValidationMode | None = None,
) -> SnapshotPublishDecision:
    """Run C2 + C3 validation and compute publish policy (read-only)."""

    path = Path(workbook_path)
    path_s = str(path.resolve())
    policy = policy_mode or resolve_contract_validation_mode()
    policy_s: PolicyMode = policy.value
    v_strict = validators_use_strict(policy)

    issues: list[ValidationIssue] = []
    load_error: str | None = None
    build_error: str | None = None
    validation_crash: str | None = None
    snapshot: RulesSnapshotV2 | None = None
    fingerprint: str | None = None

    try:
        headers = read_workbook_headers(path)
        issues.extend(validate_workbook_schema(headers, strict=v_strict))
        issues.extend(validate_meta_version(path, strict=v_strict))
        issues.extend(validate_duplicate_command_policies(path, strict=v_strict))
    except Exception as exc:
        load_error = f"{type(exc).__name__}: {exc}"
        log.exception(
            "rules contract: workbook header read / schema stage failed",
            extra={"rules_contract": {"path": path_s, "policy": policy_s, "load_error": load_error}},
        )

    if load_error is None:
        try:
            snapshot = build_snapshot_v2_from_legacy(path)
            fingerprint = rules_snapshot_fingerprint(snapshot)
        except Exception as exc:
            build_error = f"{type(exc).__name__}: {exc}"
            log.exception(
                "rules contract: snapshot build failed",
                extra={"rules_contract": {"path": path_s, "policy": policy_s, "build_error": build_error}},
            )

    if snapshot is not None and validation_crash is None:
        try:
            issues.extend(validate_snapshot(snapshot, strict=v_strict))
        except Exception as exc:
            validation_crash = f"{type(exc).__name__}: {exc}"
            log.exception(
                "rules contract: snapshot validation crashed",
                extra={
                    "rules_contract": {
                        "path": path_s,
                        "policy": policy_s,
                        "validation_crash": validation_crash,
                    }
                },
            )

    if _identity_compare_enabled() and load_error is None and build_error is None:
        issues.extend(_identity_drift_issues(path, strict=v_strict))

    contract_tuple = tuple(issues)
    has_blocking = has_blocking_errors(contract_tuple)
    counts = count_by_severity(contract_tuple)

    infra_blocked = load_error is not None or build_error is not None or validation_crash is not None

    if policy == ContractValidationMode.STRICT:
        publish_allowed = (
            snapshot is not None
            and not infra_blocked
            and not has_blocking
        )
    elif policy == ContractValidationMode.SHADOW:
        publish_allowed = snapshot is not None and load_error is None and build_error is None
    else:
        publish_allowed = snapshot is not None and load_error is None and build_error is None

    blocking_codes = _blocking_codes(contract_tuple)

    decision = SnapshotPublishDecision(
        workbook_path=path_s,
        policy_mode=policy_s,
        validators_strict=v_strict,
        contract_issues=contract_tuple,
        has_blocking_contract=bool(has_blocking),
        blocking_issue_codes=blocking_codes,
        warning_count=int(counts.get(ValidationSeverity.WARN, 0)),
        info_count=int(counts.get(ValidationSeverity.INFO, 0)),
        error_count=int(counts.get(ValidationSeverity.ERROR, 0)),
        snapshot_fingerprint=fingerprint,
        snapshot=snapshot,
        publish_allowed=publish_allowed,
        load_error=load_error,
        build_error=build_error,
        validation_crash=validation_crash,
    )

    payload = decision.to_log_dict()
    if policy == ContractValidationMode.SHADOW and (has_blocking or infra_blocked):
        log.warning("rules contract shadow diagnostics", extra={"rules_contract": payload})
    elif not publish_allowed and policy == ContractValidationMode.STRICT:
        log.error("rules contract strict publish blocked", extra={"rules_contract": payload})
    elif has_blocking and policy == ContractValidationMode.LEGACY:
        log.warning(
            "rules contract legacy publish allowed with blocking-level findings",
            extra={"rules_contract": payload},
        )
    else:
        log.info("rules contract publish evaluated", extra={"rules_contract": payload})

    return decision