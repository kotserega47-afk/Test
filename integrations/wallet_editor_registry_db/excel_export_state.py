"""In-process Excel export health for postgres-first registry (no DB reads in health)."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone

_lock = threading.Lock()

_state: dict[str, object] = {
    "last_error": None,
    "last_success_at": None,
    "last_failure_at": None,
    "failure_count": 0,
    "recent_failures": 0,
    "last_operation": None,
}


@dataclass(frozen=True, slots=True)
class ExcelExportHealthSnapshot:
    last_error: str | None
    last_success_at: str | None
    last_failure_at: str | None
    recent_failures: int
    failure_count: int
    last_operation: str | None


def record_excel_export_success(*, operation: str) -> None:
    with _lock:
        _state["last_error"] = None
        _state["last_success_at"] = datetime.now(timezone.utc).isoformat()
        _state["last_operation"] = operation
        _state["recent_failures"] = 0


def record_excel_export_failure(*, operation: str, error: str) -> None:
    with _lock:
        now = datetime.now(timezone.utc).isoformat()
        _state["last_error"] = error
        _state["last_failure_at"] = now
        _state["recent_failures"] = int(_state["recent_failures"]) + 1
        _state["failure_count"] = int(_state["failure_count"]) + 1
        _state["last_operation"] = operation


def get_excel_export_health() -> ExcelExportHealthSnapshot:
    with _lock:
        return ExcelExportHealthSnapshot(
            last_error=_state["last_error"],  # type: ignore[arg-type]
            last_success_at=_state["last_success_at"],  # type: ignore[arg-type]
            last_failure_at=_state["last_failure_at"],  # type: ignore[arg-type]
            recent_failures=int(_state["recent_failures"]),
            failure_count=int(_state["failure_count"]),
            last_operation=_state["last_operation"],  # type: ignore[arg-type]
        )


def reset_excel_export_health_for_tests() -> None:
    with _lock:
        _state["last_error"] = None
        _state["last_success_at"] = None
        _state["last_failure_at"] = None
        _state["recent_failures"] = 0
        _state["failure_count"] = 0
        _state["last_operation"] = None
