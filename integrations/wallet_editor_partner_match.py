"""Alias-aware partner chip matching for Wallet Editor.

Display names and Rules ``source_partners`` are the same partner.
Matching is exact or alias-identity only — never substring.
Ambiguous or missing aliases fail closed (unresolved).
"""

from __future__ import annotations

from collections import defaultdict

from integrations.wallet_editor_partner_resolve import PartnerAliasMap, runtime_partner_aliases

CHIP_PRESENT = "present"
CHIP_ABSENT = "absent"
CHIP_UNRESOLVED = "unresolved"


def _norm(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip().casefold()


def _cell(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _unique_displays_by_source(aliases: PartnerAliasMap) -> dict[str, tuple[str, ...]]:
    buckets: dict[str, list[str]] = defaultdict(list)
    for display_key, sources in aliases.display_to_sources.items():
        if len(sources) != 1:
            continue
        source_key = _norm(sources[0])
        if not source_key:
            continue
        if display_key not in buckets[source_key]:
            buckets[source_key].append(display_key)
    return {key: tuple(values) for key, values in buckets.items()}


def identity_norms(name: str, aliases: PartnerAliasMap) -> frozenset[str] | None:
    """Normalized identity set for a partner/chip label.

    ``None`` means the name cannot be resolved (ambiguous alias).
    """

    text = _cell(name)
    if not text:
        return frozenset()

    key = _norm(text)
    sources = aliases.sources_for(text)
    if len(sources) > 1:
        return None

    keys = {key}
    reverse = _unique_displays_by_source(aliases)
    if len(sources) == 1:
        source_key = _norm(sources[0])
        keys.add(source_key)
        keys.update(reverse.get(source_key, ()))
        return frozenset(keys)

    keys.update(reverse.get(key, ()))
    return frozenset(keys)


def chip_matches_partner(
    chip_text: str,
    partner: str,
    aliases: PartnerAliasMap | None = None,
) -> str:
    """Return ``present``, ``absent``, or ``unresolved`` for one chip vs partner."""

    partner_norm = _norm(partner)
    if not partner_norm:
        return CHIP_ABSENT

    chip_norm = _norm(chip_text)
    if not chip_norm:
        return CHIP_ABSENT

    if chip_norm == partner_norm:
        return CHIP_PRESENT

    aliases = aliases if aliases is not None else runtime_partner_aliases()
    if not aliases.available:
        return CHIP_UNRESOLVED

    partner_keys = identity_norms(partner, aliases)
    chip_keys = identity_norms(chip_text, aliases)
    if partner_keys is None or chip_keys is None:
        return CHIP_UNRESOLVED
    if partner_keys & chip_keys:
        return CHIP_PRESENT
    return CHIP_ABSENT


def partner_presence_on_chips(
    chips: list[str],
    partner: str,
    aliases: PartnerAliasMap | None = None,
) -> str:
    """Presence of ``partner`` among selected chips. Fail closed on unresolved."""

    if not _norm(partner):
        return CHIP_ABSENT

    aliases = aliases if aliases is not None else runtime_partner_aliases()
    saw_unresolved = False
    for chip in chips:
        state = chip_matches_partner(chip, partner, aliases)
        if state == CHIP_PRESENT:
            return CHIP_PRESENT
        if state == CHIP_UNRESOLVED:
            saw_unresolved = True
    if saw_unresolved:
        return CHIP_UNRESOLVED
    return CHIP_ABSENT
