"""Eligibility selection and batch planning for Wallet Editor auto-enable."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import pandas as pd

from integrations.wallet_editor_registry_lifecycle import (
    ACTION_REMOVE_PARTNER,
    HOLD_MARK,
    MISSING_OTLEZKA_STATUS,
    STATUS_K_VKLUCHENIYU,
    STATUS_OSHIBKA,
    STATUS_OZHIDAET,
    STATUS_PROSROCHENO,
    parse_disable_datetime,
)

_VKLYUCHENO_OK = "OK"
_VKLYUCHENO_SKIP = "SKIP"
_VKLYUCHENO_FAIL = "FAIL"


def _cell_str(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _normalize_key(value: str) -> str:
    return (value or "").strip().casefold()


@dataclass(frozen=True, slots=True)
class CandidateRow:
    card: str
    partner: str
    disable_at: str
    enable_status: str
    vklyucheno: str
    source_row_index: int


@dataclass(frozen=True, slots=True)
class EligibilityBreakdown:
    k_vklyucheniyu: int
    prosrocheno: int
    fail_retry: int
    empty_vklyucheno: int


@dataclass(frozen=True, slots=True)
class EligibilityResult:
    eligible_before_dedup: int
    selected: tuple[CandidateRow, ...]
    duplicates_skipped: int
    breakdown: EligibilityBreakdown


def _row_is_eligible(
    row: pd.Series,
    *,
    include_overdue: bool,
) -> bool:
    action = _cell_str(row.get("action", "")).lower()
    if action != ACTION_REMOVE_PARTNER:
        return False

    if _cell_str(row.get("status", "")).upper() != "OK":
        return False

    partner = _cell_str(row.get("partner", ""))
    if not partner:
        return False

    hold = _cell_str(row.get("hold", "")).upper()
    if hold == HOLD_MARK:
        return False

    vklyucheno = _cell_str(row.get("Включено", "")).upper()

    enable_status = _cell_str(row.get("Статус включения", ""))
    if enable_status in {MISSING_OTLEZKA_STATUS, STATUS_OZHIDAET, HOLD_MARK}:
        return False

    allowed_statuses = {STATUS_K_VKLUCHENIYU}
    if include_overdue:
        allowed_statuses.add(STATUS_PROSROCHENO)
    if enable_status == STATUS_OSHIBKA:
        if vklyucheno != _VKLYUCHENO_FAIL:
            return False
    elif enable_status not in allowed_statuses:
        return False
    if vklyucheno in {_VKLYUCHENO_OK, _VKLYUCHENO_SKIP}:
        return False
    if vklyucheno not in {"", _VKLYUCHENO_FAIL}:
        return False

    return True


def _breakdown_for_rows(rows: Iterable[CandidateRow]) -> EligibilityBreakdown:
    k_count = 0
    prosrocheno_count = 0
    fail_count = 0
    empty_count = 0
    for row in rows:
        if row.enable_status == STATUS_K_VKLUCHENIYU:
            k_count += 1
        elif row.enable_status == STATUS_PROSROCHENO:
            prosrocheno_count += 1
        vk = (row.vklyucheno or "").strip().upper()
        if vk == _VKLYUCHENO_FAIL:
            fail_count += 1
        elif not vk:
            empty_count += 1
    return EligibilityBreakdown(
        k_vklyucheniyu=k_count,
        prosrocheno=prosrocheno_count,
        fail_retry=fail_count,
        empty_vklyucheno=empty_count,
    )


def select_auto_enable_candidates(
    all_results: pd.DataFrame,
    *,
    include_overdue: bool = True,
) -> EligibilityResult:
    """
    Filter lifecycle-recalculated all_results and deduplicate by (card, partner).

    Keeps the row with the latest parseable ``Дата отключения`` per pair.
    Older duplicate rows are not modified — only counted in ``duplicates_skipped``.
    """
    if all_results is None or all_results.empty:
        empty_breakdown = EligibilityBreakdown(0, 0, 0, 0)
        return EligibilityResult(0, (), 0, empty_breakdown)

    eligible_rows: list[tuple[int, pd.Series]] = []
    for idx in all_results.index:
        row = all_results.loc[idx]
        if _row_is_eligible(row, include_overdue=include_overdue):
            eligible_rows.append((int(idx), row))

    eligible_before_dedup = len(eligible_rows)

    best_by_pair: dict[tuple[str, str], tuple[int, pd.Series, datetime | None]] = {}
    for idx, row in eligible_rows:
        card = _cell_str(row.get("card", ""))
        partner = _cell_str(row.get("partner", ""))
        pair = (_normalize_key(card), _normalize_key(partner))
        disable_dt = parse_disable_datetime(row.get("Дата отключения", ""))

        current = best_by_pair.get(pair)
        if current is None:
            best_by_pair[pair] = (idx, row, disable_dt)
            continue

        _, _, current_dt = current
        if disable_dt is None and current_dt is None:
            if idx > current[0]:
                best_by_pair[pair] = (idx, row, disable_dt)
            continue
        if current_dt is None:
            best_by_pair[pair] = (idx, row, disable_dt)
            continue
        if disable_dt is None:
            continue
        if disable_dt >= current_dt:
            best_by_pair[pair] = (idx, row, disable_dt)

    duplicates_skipped = max(0, eligible_before_dedup - len(best_by_pair))

    selected: list[CandidateRow] = []
    for idx, row, _ in best_by_pair.values():
        selected.append(
            CandidateRow(
                card=_cell_str(row.get("card", "")),
                partner=_cell_str(row.get("partner", "")),
                disable_at=_cell_str(row.get("Дата отключения", "")),
                enable_status=_cell_str(row.get("Статус включения", "")),
                vklyucheno=_cell_str(row.get("Включено", "")),
                source_row_index=idx,
            )
        )

    selected.sort(key=lambda c: (c.disable_at, c.card, c.partner))
    breakdown = _breakdown_for_rows(selected)

    return EligibilityResult(
        eligible_before_dedup=eligible_before_dedup,
        selected=tuple(selected),
        duplicates_skipped=duplicates_skipped,
        breakdown=breakdown,
    )


def apply_run_limit(
    candidates: Sequence[CandidateRow] | tuple[CandidateRow, ...],
    *,
    max_rows_per_run: int,
) -> tuple[CandidateRow, ...]:
    """Cap candidates for a single run. ``max_rows_per_run <= 0`` means no limit."""
    items = tuple(candidates)
    if max_rows_per_run <= 0:
        return items
    return items[:max_rows_per_run]


def is_run_limited(
    *,
    selected_after_dedup: int,
    selected_for_run: int,
    max_rows_per_run: int,
) -> bool:
    return max_rows_per_run > 0 and selected_for_run < selected_after_dedup


def split_batches(
    candidates: Sequence[CandidateRow] | tuple[CandidateRow, ...],
    *,
    max_rows_per_batch: int,
) -> tuple[tuple[CandidateRow, ...], ...]:
    if max_rows_per_batch <= 0:
        raise ValueError("max_rows_per_batch must be positive")
    items = tuple(candidates)
    if not items:
        return ()
    batches: list[tuple[CandidateRow, ...]] = []
    for offset in range(0, len(items), max_rows_per_batch):
        batches.append(items[offset : offset + max_rows_per_batch])
    return tuple(batches)


def calculate_batch_timeout(
    batch_size: int,
    *,
    seconds_per_card_timeout: int,
    batch_timeout_buffer_seconds: int,
) -> int:
    if batch_size < 0:
        raise ValueError("batch_size must be non-negative")
    return batch_size * seconds_per_card_timeout + batch_timeout_buffer_seconds
