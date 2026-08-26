"""Resolve Wallet Editor partner names for Отлёжка lookup.

Canonical name is Rules ``source_partners``. Display names (including
renames that dropped the ``HH`` prefix) map through explicit
``display_name → source_partners`` pairs, then a unique trailing
terminal-id fallback. Zero or multiple id matches fail closed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd

from core.rules_v2.normalizers import extract_partner_code
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, _log_name = LOG_PROFILES["DROPBOX"]
log = get_logger(_log_name, icon)

HOURLY_PAYINS_SHEET = "hourly_payins"

VIA_EXACT = "exact"
VIA_ALIAS = "alias"
VIA_TERMINAL_ID = "terminal_id"
FAIL_MISSING = "missing"
FAIL_AMBIGUOUS_ID = "ambiguous_terminal_id"
FAIL_AMBIGUOUS_ALIAS = "ambiguous_alias"
FAIL_ID_MISMATCH = "terminal_id_mismatch"

_DETAIL_AMBIGUOUS_ID = (
    "Неоднозначный ID терминала ({terminal_id}): {count} совпадения в отлёжке "
    "— настройка не выбрана."
)
_DETAIL_AMBIGUOUS_ALIAS = (
    "Неоднозначное соответствие display_name → source_partners в Rules "
    "для «{partner}»."
)
_DETAIL_ID_MISMATCH = (
    "ID терминала в названии ({query_id}) не совпадает с каноническим "
    "source_partners ({canonical_id})."
)
_DETAIL_MISSING = ""


def _norm(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip().casefold()


def _cell(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _csv_tokens(value: object) -> tuple[str, ...]:
    text = _cell(value)
    if not text:
        return ()
    return tuple(part.strip() for part in text.split(",") if part.strip())


@dataclass(frozen=True, slots=True)
class PartnerAliasMap:
    """Explicit Rules mapping: normalized display_name → source_partners names."""

    display_to_sources: Mapping[str, tuple[str, ...]]

    @classmethod
    def empty(cls) -> "PartnerAliasMap":
        return cls({})

    @classmethod
    def from_pairs(cls, pairs: Iterable[tuple[str, str]]) -> "PartnerAliasMap":
        collected: dict[str, list[str]] = {}
        for display, source in pairs:
            display_key = _norm(display)
            source_name = _cell(source)
            if not display_key or not source_name:
                continue
            bucket = collected.setdefault(display_key, [])
            if source_name not in bucket:
                bucket.append(source_name)
        return cls({key: tuple(values) for key, values in collected.items()})

    def sources_for(self, partner: str) -> tuple[str, ...]:
        return tuple(self.display_to_sources.get(_norm(partner), ()))


@dataclass(frozen=True, slots=True)
class OtlezkaIndex:
    days_by_norm: Mapping[str, int]
    original_by_norm: Mapping[str, str]

    def days_for_norm(self, key: str) -> int | None:
        if key not in self.days_by_norm:
            return None
        return int(self.days_by_norm[key])

    def originals_with_terminal_id(self, terminal_id: str) -> tuple[str, ...]:
        if not terminal_id:
            return ()
        found: list[str] = []
        for norm, original in self.original_by_norm.items():
            if extract_partner_code(original) == terminal_id:
                found.append(norm)
        return tuple(found)


@dataclass(frozen=True, slots=True)
class OtlezkaResolveResult:
    days: int | None
    matched_norm: str | None
    via: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.days is not None


def build_partner_alias_map_from_hourly_payins(df: pd.DataFrame | None) -> PartnerAliasMap:
    if df is None or df.empty or "display_name" not in df.columns:
        return PartnerAliasMap.empty()
    if "source_partners" not in df.columns:
        return PartnerAliasMap.empty()

    pairs: list[tuple[str, str]] = []
    for _, row in df.iterrows():
        display = _cell(row.get("display_name"))
        for source in _csv_tokens(row.get("source_partners")):
            pairs.append((display, source))
    return PartnerAliasMap.from_pairs(pairs)


_aliases_cache: tuple[str, PartnerAliasMap] | None = None


def load_partner_alias_map_from_workbook(path: str | Path) -> PartnerAliasMap:
    frame = pd.read_excel(path, sheet_name=HOURLY_PAYINS_SHEET, engine="openpyxl")
    frame.columns = [str(col).strip() for col in frame.columns]
    return build_partner_alias_map_from_hourly_payins(frame)


def runtime_partner_aliases() -> PartnerAliasMap:
    """Best-effort Rules aliases. Never raises. Skips Dropbox during pytest."""

    global _aliases_cache
    candidates = _alias_workbook_candidates()
    cache_key = _alias_cache_key(candidates)
    if _aliases_cache is not None and _aliases_cache[0] == cache_key:
        return _aliases_cache[1]

    aliases = PartnerAliasMap.empty()
    for path in candidates:
        try:
            aliases = load_partner_alias_map_from_workbook(path)
        except Exception:
            log.warning(
                "[WalletEditorRegistry] partner alias workbook unread path=%s",
                path,
            )
            continue
        log.info(
            "[WalletEditorRegistry] partner aliases loaded path=%s display_rows=%s",
            path,
            len(aliases.display_to_sources),
        )
        _aliases_cache = (cache_key, aliases)
        return aliases

    if os.getenv("PYTEST_CURRENT_TEST"):
        _aliases_cache = (cache_key, aliases)
        return aliases

    try:
        from core.rules_provider import get_rules_snapshot

        workbook = get_rules_snapshot(force_sync=False)
        aliases = load_partner_alias_map_from_workbook(workbook.local_path)
        log.info(
            "[WalletEditorRegistry] partner aliases loaded from rules snapshot display_rows=%s",
            len(aliases.display_to_sources),
        )
    except Exception:
        log.warning(
            "[WalletEditorRegistry] partner aliases unavailable; exact+id lookup only"
        )
        aliases = PartnerAliasMap.empty()

    _aliases_cache = (cache_key, aliases)
    return aliases


def _alias_cache_key(paths: list[Path]) -> str:
    parts: list[str] = []
    for path in paths:
        try:
            resolved = path.resolve()
            stat = resolved.stat()
            parts.append(f"{resolved}:{stat.st_mtime_ns}:{stat.st_size}")
        except OSError:
            parts.append(str(path))
    return "|".join(parts)


def _alias_workbook_candidates() -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        try:
            resolved = path.expanduser().resolve()
        except OSError:
            return
        key = str(resolved).casefold()
        if key in seen or not resolved.is_file():
            return
        seen.add(key)
        found.append(resolved)

    raw = (os.getenv("RULES_XLSX_PATH") or "").strip()
    if raw:
        _add(Path(raw))
    _add(Path("/tmp/rules_cache/rules.xlsx"))
    try:
        from core.rules_provider import _last_rules_wb

        if _last_rules_wb is not None:
            _add(Path(_last_rules_wb.local_path))
    except Exception:
        pass
    return found


def _ids_consistent(query: str, canonical: str) -> bool:
    query_id = extract_partner_code(query)
    canonical_id = extract_partner_code(canonical)
    if query_id and canonical_id and query_id != canonical_id:
        return False
    return True


def _id_mismatch_detail(query: str, canonical: str) -> str:
    return _DETAIL_ID_MISMATCH.format(
        query_id=extract_partner_code(query) or "—",
        canonical_id=extract_partner_code(canonical) or "—",
    )


def _unique_source_in_index(
    sources: tuple[str, ...],
    index: OtlezkaIndex,
    *,
    query: str,
) -> OtlezkaResolveResult | None:
    """Resolve alias sources against active otlezka. None = continue to id fallback."""

    if not sources:
        return None

    if len(sources) > 1:
        return OtlezkaResolveResult(
            days=None,
            matched_norm=None,
            via=FAIL_AMBIGUOUS_ALIAS,
            detail=_DETAIL_AMBIGUOUS_ALIAS.format(partner=query),
        )

    source = sources[0]
    if not _ids_consistent(query, source):
        return OtlezkaResolveResult(
            days=None,
            matched_norm=None,
            via=FAIL_ID_MISMATCH,
            detail=_id_mismatch_detail(query, source),
        )
    if _norm(source) not in index.days_by_norm:
        return None

    matched_norm = _norm(source)
    return OtlezkaResolveResult(
        days=index.days_for_norm(matched_norm),
        matched_norm=matched_norm,
        via=VIA_ALIAS,
    )


def _terminal_id_fallback(query: str, index: OtlezkaIndex) -> OtlezkaResolveResult:
    terminal_id = extract_partner_code(query)
    if not terminal_id:
        return OtlezkaResolveResult(days=None, matched_norm=None, via=FAIL_MISSING, detail=_DETAIL_MISSING)

    matches = index.originals_with_terminal_id(terminal_id)
    if len(matches) == 1:
        matched_norm = matches[0]
        return OtlezkaResolveResult(
            days=index.days_for_norm(matched_norm),
            matched_norm=matched_norm,
            via=VIA_TERMINAL_ID,
        )
    if len(matches) > 1:
        return OtlezkaResolveResult(
            days=None,
            matched_norm=None,
            via=FAIL_AMBIGUOUS_ID,
            detail=_DETAIL_AMBIGUOUS_ID.format(terminal_id=terminal_id, count=len(matches)),
        )
    return OtlezkaResolveResult(days=None, matched_norm=None, via=FAIL_MISSING, detail=_DETAIL_MISSING)


def resolve_otlezka_days(
    partner: str,
    index: OtlezkaIndex,
    aliases: PartnerAliasMap | None = None,
) -> OtlezkaResolveResult:
    """Lookup Отлёжка days for a registry partner name.

    Order: exact normalized name, Rules display→source alias (with id check),
    then unique trailing-id fallback. Ambiguous matches fail closed.
    """

    aliases = aliases or PartnerAliasMap.empty()
    partner_name = _cell(partner)
    if not partner_name:
        return OtlezkaResolveResult(days=None, matched_norm=None, via=FAIL_MISSING, detail=_DETAIL_MISSING)

    partner_key = _norm(partner_name)
    exact_days = index.days_for_norm(partner_key)
    if exact_days is not None:
        return OtlezkaResolveResult(days=exact_days, matched_norm=partner_key, via=VIA_EXACT)

    alias_result = _unique_source_in_index(
        aliases.sources_for(partner_name),
        index,
        query=partner_name,
    )
    if alias_result is not None:
        return alias_result

    return _terminal_id_fallback(partner_name, index)
