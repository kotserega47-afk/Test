"""Passive conversion input fingerprint (Phase 1A — compute/compare only, no dedup skip)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from typing import Callable

from core.rules_provider import get_snapshot_v2
from core.rules_v2.snapshot_fingerprint import rules_snapshot_fingerprint, workbook_sha256
from utils.logger import logger

SPECIAL_CARDS_MISSING = "missing"


@dataclass(frozen=True)
class ConversionFingerprint:
    combined: str
    conv_hash: str
    card_hash: str
    rules_hash: str
    special_cards_hash: str


def _hash_file(path: str) -> str:
    return workbook_sha256(path)


def _special_cards_hash(download_fn: Callable) -> str:
    special_folder = os.getenv("DROPBOX_SPECIAL_PATH", "/Ostin/platform/special")
    dropbox_special_file = os.path.join(special_folder, "special_cards.xlsx")
    local_special_path = os.path.join(tempfile.gettempdir(), "special_cards.xlsx")
    try:
        if download_fn(dropbox_special_file, local_special_path) and os.path.isfile(local_special_path):
            return _hash_file(local_special_path)
    except Exception as e:
        logger.warning(f"conversion fingerprint: special_cards unavailable: {e}")
    return SPECIAL_CARDS_MISSING


def build_conversion_fingerprint(
    conv_local_path: str,
    card_local_path: str,
    *,
    rules_force_sync: bool = False,
    download_fn: Callable | None = None,
) -> ConversionFingerprint:
    if download_fn is None:
        from integrations.dropbox_watcher import download_file

        download_fn = download_file

    conv_hash = _hash_file(conv_local_path)
    card_hash = _hash_file(card_local_path)
    snapshot = get_snapshot_v2(force_sync=rules_force_sync)
    rules_hash = rules_snapshot_fingerprint(snapshot)
    special_cards_hash = _special_cards_hash(download_fn)

    payload = {
        "conv": conv_hash,
        "card": card_hash,
        "rules": rules_hash,
        "special_cards": special_cards_hash,
    }
    combined = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()

    return ConversionFingerprint(
        combined=combined,
        conv_hash=conv_hash,
        card_hash=card_hash,
        rules_hash=rules_hash,
        special_cards_hash=special_cards_hash,
    )
