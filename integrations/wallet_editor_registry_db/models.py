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


@dataclass(frozen=True, slots=True)
class RegistryHoldRow:
    card: str
    partner: str
    card_norm: str
    partner_norm: str
    added_at: str | None = None
    comment: str | None = None
    active: bool = True
    source_sync_run_id: int | None = None
    source_row_index: int | None = None


@dataclass(frozen=True, slots=True)
class RegistryOtlezkaRow:
    partner: str
    partner_norm: str
    full_days: int
    comment: str | None = None
    active: bool = True
    source_sync_run_id: int | None = None
    source_row_index: int | None = None


@dataclass(frozen=True, slots=True)
class ManualSyncRunRow:
    sync_id: str
    status: str
    triggered_by: str
    source_path: str
    started_at: str
    triggered_by_actor: str | None = None
    source_rev: str | None = None
    content_hash: str | None = None
    hold_rows_seen: int = 0
    hold_rows_activated: int = 0
    hold_rows_deactivated: int = 0
    otlezka_rows_seen: int = 0
    otlezka_rows_activated: int = 0
    otlezka_rows_deactivated: int = 0
    error_code: str | None = None
    error_message: str | None = None
    finished_at: str | None = None
