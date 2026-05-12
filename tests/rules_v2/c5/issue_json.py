"""C5 golden helpers: JSON-shaped issues with an explicit normalization contract.

Allowed normalizations before comparison (C5 policy):

* issue list ordering;
* recursive lexicographic key ordering inside ``details`` dicts only;
* optional top-level issue fields absent in JSON -> ``None`` when decoding.

Never altered: ``severity``, ``code``, ``sheet``, ``rule_id``, ``field``,
or semantic values inside ``details``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from core.rules_v2.validation_issues import ValidationIssue

_ISSUE_FIELD_ORDER: tuple[str, ...] = (
    "code",
    "severity",
    "message",
    "sheet",
    "row_index",
    "rule_id",
    "field",
    "details",
)


def _sort_details_keys(value: Any) -> Any:
    """Recursively sort mapping keys only (dicts); leaves values untouched."""

    if isinstance(value, Mapping):
        return {
            k: _sort_details_keys(value[k])
            for k in sorted(value.keys(), key=lambda x: str(x))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sort_details_keys(v) for v in value]
    return value


def issue_to_ordered_dict(issue: ValidationIssue) -> dict[str, Any]:
    """Convert a ``ValidationIssue`` to a JSON-ready dict (canonical top-level key order)."""

    details_out: Any
    if issue.details is None:
        details_out = None
    else:
        details_out = _sort_details_keys(dict(issue.details))

    return {
        "code": issue.code,
        "severity": str(issue.severity),
        "message": issue.message,
        "sheet": issue.sheet,
        "row_index": issue.row_index,
        "rule_id": issue.rule_id,
        "field": issue.field,
        "details": details_out,
    }


def issues_json_payload(issues: list[ValidationIssue]) -> list[dict[str, Any]]:
    """Return one dict per issue (unsorted list — sort in the harness for compare)."""

    return [issue_to_ordered_dict(i) for i in issues]


def _issue_sort_key(d: Mapping[str, Any]) -> tuple:
    sheet = d.get("sheet")
    return (
        d.get("code") or "",
        str(sheet) if sheet is not None else "",
        d.get("row_index") if d.get("row_index") is not None else -1,
        d.get("rule_id") or "",
        d.get("field") or "",
        d.get("message") or "",
    )


def normalize_issues_payload(
    payload: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply allowed normalizations: fill missing optional keys with ``None``, sort details keys, sort issues."""

    filled: list[dict[str, Any]] = []
    for raw in payload:
        entry: dict[str, Any] = {}
        for key in _ISSUE_FIELD_ORDER:
            if key == "details":
                det = raw.get("details", None)
                if det is None:
                    entry["details"] = None
                else:
                    entry["details"] = _sort_details_keys(det)
            else:
                entry[key] = raw[key] if key in raw else None
        filled.append(entry)
    return sorted(filled, key=_issue_sort_key)


def dumps_issues_normalized(issues: list[ValidationIssue]) -> str:
    """Stable JSON text for golden files (sorted issues + sorted detail keys)."""

    payload = normalize_issues_payload(issues_json_payload(issues))
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def loads_issues_payloads(text: str) -> list[dict[str, Any]]:
    """Parse JSON array from a golden file."""

    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("golden JSON must be a top-level array")
    return normalize_issues_payload([dict(x) for x in data])
