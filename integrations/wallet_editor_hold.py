"""Shared hold-list checks for Wallet Editor add_partner enforcement."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from integrations.dropbox_watcher import download_file_with_rev
from integrations.wallet_editor_registry import wallet_editor_dropbox_path
from integrations.wallet_editor_registry_lifecycle import (
    _normalize_key,
    load_hold_pairs,
)
from integrations.wallet_editor_registry_xlsx import load_registry_frames
from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES

icon, name = LOG_PROFILES["AUTOMATION"]
log = get_logger(name, icon)

HOLD_SKIP_COMMENT = "HOLD: card+partner находится в hold"
HOLD_CHECK_FAILED_MANUAL_COMMENT = (
    "HOLD_CHECK_FAILED: не удалось проверить hold-лист; add_partner заблокирован"
)
HOLD_CHECK_FAILED_AUTO_COMMENT = (
    "HOLD_CHECK_FAILED: не удалось проверить hold-лист; можно повторить"
)

ERROR_HOLD = "HOLD"
ERROR_HOLD_CHECK_FAILED = "HOLD_CHECK_FAILED"


def normalize_hold_card(card: object) -> str:
    return _normalize_key(card)


def normalize_hold_partner(partner: object) -> str:
    return _normalize_key(partner)


def is_card_partner_on_hold(
    card: object,
    partner: object,
    hold_pairs: set[tuple[str, str]] | frozenset[tuple[str, str]],
) -> bool:
    if not hold_pairs:
        return False
    card_norm = normalize_hold_card(card)
    partner_norm = normalize_hold_partner(partner)
    if not card_norm or not partner_norm:
        return False
    return (card_norm, partner_norm) in hold_pairs


@dataclass(frozen=True, slots=True)
class HoldPairsSnapshot:
    pairs: frozenset[tuple[str, str]]
    available: bool
    error: str | None = None

    @classmethod
    def empty_available(cls) -> HoldPairsSnapshot:
        return cls(frozenset(), True)

    @classmethod
    def unavailable(cls, error: str) -> HoldPairsSnapshot:
        return cls(frozenset(), False, error)


def load_hold_pairs_from_dropbox() -> HoldPairsSnapshot:
    dropbox_path = wallet_editor_dropbox_path()
    if not dropbox_path:
        log.error("[Hold] DROPBOX_WALLET_EDITOR_PATH is not set")
        return HoldPairsSnapshot.unavailable("DROPBOX_WALLET_EDITOR_PATH is not set")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            local_path = Path(tmp) / "wallet_editor.xlsx"
            status, _rev = download_file_with_rev(dropbox_path, str(local_path))
            if status == "error":
                log.error(
                    "[Hold] registry download failed path=%s",
                    dropbox_path,
                )
                return HoldPairsSnapshot.unavailable(
                    f"registry download failed: {dropbox_path}"
                )

            _all_df, _runs_df, hold_df, _otlezka_df, _hold_exists, _otlezka_exists = (
                load_registry_frames(local_path, status)
            )
            pairs = frozenset(load_hold_pairs(hold_df))
            log.info("[Hold] loaded hold_pairs count=%s", len(pairs))
            return HoldPairsSnapshot(pairs, True)
    except Exception as exc:
        log.exception("[Hold] failed to load hold pairs from registry")
        return HoldPairsSnapshot.unavailable(str(exc))


def load_hold_pairs_from_postgres() -> HoldPairsSnapshot:
    from integrations.wallet_editor_registry_db.manual_readers import (
        ManualReadersNotReadyError,
        load_active_hold_pair_norms_from_postgres,
    )

    try:
        pairs = load_active_hold_pair_norms_from_postgres()
        log.info("[Hold] loaded hold_pairs from postgres count=%s", len(pairs))
        return HoldPairsSnapshot(pairs, True)
    except ManualReadersNotReadyError as exc:
        log.error("[Hold] postgres readers not ready: %s", exc)
        return HoldPairsSnapshot.unavailable(str(exc))
    except Exception as exc:
        log.exception("[Hold] failed to load hold pairs from postgres")
        return HoldPairsSnapshot.unavailable(str(exc))


def load_hold_pairs_snapshot() -> HoldPairsSnapshot:
    """Runtime hold loader — PG or Dropbox per WALLET_EDITOR_MANUAL_READERS_SOURCE."""
    from integrations.wallet_editor_registry_db.config import manual_readers_source_is_postgres

    if manual_readers_source_is_postgres():
        return load_hold_pairs_from_postgres()
    return load_hold_pairs_from_dropbox()
