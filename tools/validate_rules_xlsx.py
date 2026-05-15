"""CLI for CONTRACT_V2 rules workbook validation (C2 + C3).

Thin wrapper over the same read-only pipeline as runtime publish
(``evaluate_snapshot_publish``). Does not mutate the workbook.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from core.datetime_utils import now_msk
from core.rules_v2.contract_publish import (
    ContractValidationMode,
    SnapshotPublishDecision,
    evaluate_snapshot_publish,
)
from core.rules_v2.validation_issues import (
    ValidationIssue,
    ValidationSeverity,
    count_by_severity,
    is_blocking,
)


@dataclass
class CheckMessage:
    """Legacy message shape kept for ``rules_writer`` imports."""

    level: str  # "ERROR" | "WARN" | "INFO"
    where: str
    message: str


def _policy_from_strict(strict: bool) -> ContractValidationMode:
    return ContractValidationMode.STRICT if strict else ContractValidationMode.LEGACY


def validate_rules_xlsx(path: Path, *, strict: bool = False) -> SnapshotPublishDecision:
    """Run normative contract validation (C2 workbook schema + C3 snapshot)."""

    return evaluate_snapshot_publish(path, policy_mode=_policy_from_strict(strict))


def _issue_to_check_message(issue: ValidationIssue) -> CheckMessage:
    level = issue.severity.value.upper()
    where = issue.sheet or "workbook"
    parts = [issue.code, issue.message]
    if issue.rule_id:
        parts.append(f"id={issue.rule_id}")
    if issue.field:
        parts.append(f"field={issue.field}")
    if issue.row_index is not None:
        parts.append(f"row={issue.row_index}")
    return CheckMessage(level, where, " | ".join(parts))


def check_rules_xlsx(path: Path) -> list[CheckMessage]:
    """Legacy adapter: contract pipeline in legacy policy mode."""

    decision = validate_rules_xlsx(path, strict=False)
    msgs = [_issue_to_check_message(issue) for issue in decision.contract_issues]
    for label, err in (
        ("load", decision.load_error),
        ("build", decision.build_error),
        ("validation", decision.validation_crash),
    ):
        if err:
            msgs.append(CheckMessage("ERROR", "workbook", f"{label}: {err}"))
    return msgs


def _format_issue_line(issue: ValidationIssue) -> str:
    row_id = (
        issue.rule_id
        if issue.rule_id is not None
        else (str(issue.row_index) if issue.row_index is not None else "-")
    )
    sheet = issue.sheet or "-"
    field = issue.field or "-"
    sev = issue.severity.value.upper()
    return (
        f"[{sev}] {issue.code} sheet={sheet} id={row_id} field={field} | {issue.message}"
    )


def _infra_messages(decision: SnapshotPublishDecision) -> list[str]:
    lines: list[str] = []
    if decision.load_error:
        lines.append(f"[ERROR] WORKBOOK_LOAD sheet=- id=- field=- | {decision.load_error}")
    if decision.build_error:
        lines.append(f"[ERROR] SNAPSHOT_BUILD sheet=- id=- field=- | {decision.build_error}")
    if decision.validation_crash:
        lines.append(
            f"[ERROR] VALIDATION_CRASH sheet=- id=- field=- | {decision.validation_crash}"
        )
    return lines


def _print_report(decision: SnapshotPublishDecision) -> None:
    counts = count_by_severity(decision.contract_issues)
    blocking = [i for i in decision.contract_issues if is_blocking(i)]

    print(f"Checked at: {now_msk().isoformat(timespec='seconds')}")
    print(f"Workbook: {decision.workbook_path}")
    print(f"Policy: {decision.policy_mode} (validators_strict={decision.validators_strict})")
    if decision.snapshot_fingerprint:
        print(f"Snapshot fingerprint: {decision.snapshot_fingerprint}")

    for line in _infra_messages(decision):
        print(line)

    for severity in (ValidationSeverity.INFO, ValidationSeverity.WARN, ValidationSeverity.ERROR):
        for issue in decision.contract_issues:
            if issue.severity != severity:
                continue
            print(_format_issue_line(issue))

    print()
    print(
        "Summary: "
        f"errors={counts.get(ValidationSeverity.ERROR, 0)}, "
        f"warnings={counts.get(ValidationSeverity.WARN, 0)}, "
        f"info={counts.get(ValidationSeverity.INFO, 0)}, "
        f"blocking={len(blocking)}, "
        f"publish_allowed={decision.publish_allowed}"
    )
    if decision.blocking_issue_codes:
        print(f"Blocking codes: {', '.join(decision.blocking_issue_codes)}")


def _exit_code(decision: SnapshotPublishDecision) -> int:
    """Legacy mode always exits 0; strict exits 1 when publish would be blocked."""

    if decision.policy_mode == "strict":
        return 0 if decision.publish_allowed else 1
    return 0


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate rules.xlsx via CONTRACT_V2 contract pipeline (C2+C3).",
    )
    parser.add_argument(
        "workbook",
        nargs="?",
        type=Path,
        help="Path to rules.xlsx (default: ./rules.xlsx if present)",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Strict policy: catalog severities; exit 1 on blocking issues.",
    )
    return parser.parse_args(argv[1:])


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv)
    xlsx_path = args.workbook if args.workbook is not None else Path("rules.xlsx")

    if not xlsx_path.exists():
        print(f"[ERROR] WORKBOOK_NOT_FOUND sheet=- id=- field=- | File not found: {xlsx_path}")
        return 2

    decision = validate_rules_xlsx(xlsx_path, strict=args.strict)
    _print_report(decision)
    return _exit_code(decision)


if __name__ == "__main__":
    raise SystemExit(main())
