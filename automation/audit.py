from __future__ import annotations

import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

from utils.loggers import get_logger
from utils.log_profiles import LOG_PROFILES


def _mk(profile_key: str):
    icon, name = LOG_PROFILES[profile_key]
    return get_logger(name, icon)


log = _mk("AUTOMATION")


@dataclass
class Stats:
    ok: int = 0
    fail: int = 0
    skip: int = 0

    def inc(self, result: str) -> None:
        result = (result or "").strip().lower()

        if result.startswith("skip"):
            self.skip += 1
        elif (
                result.startswith("set")
                or "set_status" in result
                or "removed" in result
                or "added" in result
                or result == "saved"
        ):
            self.ok += 1
        else:
            self.fail += 1

    def summary(self) -> str:
        return f"OK={self.ok} | FAIL={self.fail} | SKIP={self.skip}"


def mask_card(card: str | None) -> str:
    digits = "".join(c for c in (card or "") if c.isdigit())
    if len(digits) <= 4:
        return "***"
    return f"***{digits[-4:]}"


def normalize_card_digits(value: object) -> str:
    """Extract comparable card digits from Excel/table text (.0, spaces, NBSP)."""
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    text = text.replace("\u00a0", " ").replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", "", text)
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return "".join(c for c in text if c.isdigit())


def row_matches_card(row_text: str, card_digits: str) -> bool:
    if not card_digits:
        return False
    row_digits = normalize_card_digits(row_text)
    if not row_digits:
        return False
    return (
        row_digits == card_digits
        or card_digits in row_digits
        or row_digits in card_digits
    )


def shorten_for_log(text: str, *, max_len: int = 80) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").replace("\u00a0", " ")).strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 3] + "..."


def log_timing(
    *,
    profile: str,
    scope: str,
    step: str,
    duration_ms: int,
    outcome: str = "ok",
    card: str | None = None,
) -> None:
    card_suffix = f" card={mask_card(card)}" if card else ""
    log.info(
        "[WE/timing] profile=%s scope=%s step=%s%s duration_ms=%s outcome=%s",
        profile or "unknown",
        scope,
        step,
        card_suffix,
        duration_ms,
        outcome,
    )


@contextmanager
def log_step_duration(
    *,
    profile: str,
    scope: str,
    step: str,
    card: str | None = None,
) -> Iterator[None]:
    started = time.perf_counter()
    outcome = "ok"
    try:
        yield
    except Exception:
        outcome = "fail"
        raise
    finally:
        duration_ms = round((time.perf_counter() - started) * 1000)
        log_timing(
            profile=profile,
            scope=scope,
            step=step,
            duration_ms=duration_ms,
            outcome=outcome,
            card=card,
        )
