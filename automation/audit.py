from __future__ import annotations

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
