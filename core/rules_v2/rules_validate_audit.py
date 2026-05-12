"""C7b — append-only JSONL audit trail for rules validation / publish diagnostics.

Best-effort only: persistence failures are logged and never affect C4 publish.
"""

from __future__ import annotations

import json
import logging
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from core.rules_provider import RulesWorkbookSnapshot
from core.rules_v2.contract_publish import SnapshotPublishDecision
from core.rules_v2.ops_rules_validate_summary import (
    RulesValidatePayload,
    build_rules_validate_payload,
    build_rules_validate_payload_for_publish_audit,
    rules_validate_payload_to_jsonable,
)

log = logging.getLogger(__name__)

_AUDIT_SCHEMA = "rules_validate_audit.v1"

AuditEventType = Literal["publish_allowed", "publish_rejected", "manual_validate"]


def _audit_jsonl_path() -> Path | None:
    raw = (os.getenv("RULES_VALIDATE_AUDIT_JSONL") or "").strip()
    if raw.lower() in ("0", "false", "no", "off"):
        return None
    if not raw:
        return Path("/tmp/rules_validate_audit.jsonl")
    return Path(raw).expanduser().resolve()


def _runtime_instance_id() -> str:
    return (os.getenv("RUNTIME_INSTANCE_ID") or "").strip() or socket.gethostname()


def _event_timestamp_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rules_validate_audit_envelope(
    event_type: AuditEventType,
    payload: RulesValidatePayload,
) -> dict[str, Any]:
    """Immutable audit record (plain dict) wrapping canonical C6.1 JSON payload."""

    return {
        "schema_version": _AUDIT_SCHEMA,
        "event_type": event_type,
        "event_timestamp_utc": _event_timestamp_utc_iso(),
        "runtime_instance_id": _runtime_instance_id(),
        "process_pid": os.getpid(),
        "payload": rules_validate_payload_to_jsonable(payload),
    }


def append_rules_validate_audit_record(envelope: dict[str, Any]) -> None:
    """Append one JSON line to the audit JSONL file (O_APPEND, no background workers)."""

    path = _audit_jsonl_path()
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(line)
        fh.write("\n")


def try_append_publish_audit_trail(
    *,
    wb: RulesWorkbookSnapshot,
    decision: SnapshotPublishDecision,
    legacy_errors: list[str],
    legacy_warnings: list[str],
) -> None:
    """Called from ``get_snapshot_v2`` after ``evaluate_snapshot_publish`` (best-effort)."""

    try:
        payload = build_rules_validate_payload_for_publish_audit(
            wb,
            decision,
            legacy_errors,
            legacy_warnings,
        )
        event: AuditEventType = "publish_allowed" if decision.publish_allowed else "publish_rejected"
        append_rules_validate_audit_record(rules_validate_audit_envelope(event, payload))
    except Exception:  # noqa: BLE001
        log.exception("rules_validate_audit: publish trail append failed (ignored)")


def try_append_manual_validate_audit_from_payload(payload: RulesValidatePayload) -> None:
    """Append ``manual_validate`` audit using an already-built payload (no second evaluation)."""

    try:
        append_rules_validate_audit_record(
            rules_validate_audit_envelope("manual_validate", payload),
        )
    except Exception:  # noqa: BLE001
        log.exception("rules_validate_audit: manual_validate append failed (ignored)")


def try_append_manual_validate_audit() -> None:
    """Optional audit for Telegram ``/rules_validate`` (builds payload if caller has none)."""

    try:
        try_append_manual_validate_audit_from_payload(build_rules_validate_payload())
    except Exception:  # noqa: BLE001
        log.exception("rules_validate_audit: manual_validate append failed (ignored)")


__all__ = [
    "AuditEventType",
    "append_rules_validate_audit_record",
    "rules_validate_audit_envelope",
    "try_append_manual_validate_audit",
    "try_append_manual_validate_audit_from_payload",
    "try_append_publish_audit_trail",
]
