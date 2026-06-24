"""Row models for Wallet Editor registry PostgreSQL mirror."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RegistryResultRow:
    row_fingerprint: str
    card: str
    partner: str
    action: str
    status: str
    run_id: str | None = None
    operation_date: str | None = None
    disable_at: str | None = None
    reenable_date: str | None = None
    enable_status: str | None = None
    vklyucheno: str | None = None
    enable_comment: str | None = None
    comment: str | None = None
    hold_mark: str | None = None
    source_row_index: int | None = None


@dataclass(frozen=True, slots=True)
class RegistryRunRow:
    run_id: str
    started_at: str
    finished_at: str
    input_rows: int
    success_rows: int
    failed_rows: int
    skipped_rows: int
    output_file: str | None = None
    operator_profile: str | None = None
    source: str | None = None
