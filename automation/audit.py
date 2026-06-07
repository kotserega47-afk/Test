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


ERROR_CARD_NOT_FOUND = "CARD_NOT_FOUND"
ERROR_TECHNICAL = "TECHNICAL"
ERROR_MODAL_CARD_MISMATCH = "MODAL_CARD_MISMATCH"
ERROR_MODAL_DATA_TIMEOUT = "MODAL_DATA_TIMEOUT"
ERROR_MODAL_CONTAINER_TIMEOUT = "MODAL_CONTAINER_TIMEOUT"
ERROR_ROW_MATCH_TIMEOUT = "ROW_MATCH_TIMEOUT"
ERROR_PLAYWRIGHT_TIMEOUT = "PLAYWRIGHT_TIMEOUT"
ERROR_RETRY_LIMIT_REACHED = "RETRY_LIMIT_REACHED"

_OPEN_CARD_STAGE_TO_ERROR_CODE = {
    "card_verify": ERROR_MODAL_CARD_MISMATCH,
    "modal_data": ERROR_MODAL_DATA_TIMEOUT,
    "modal_container": ERROR_MODAL_CONTAINER_TIMEOUT,
    "row_match": ERROR_ROW_MATCH_TIMEOUT,
}

_RETRY_COUNT_RE = re.compile(r"RETRY:(\d+)/(\d+)")


@dataclass(frozen=True, slots=True)
class RetryClassification:
    error_code: str
    retryable: bool
    message: str


def classify_enable_exception(exc: Exception) -> RetryClassification:
    """Map enable-path exceptions to error_code and retryable flag."""
    stage = getattr(exc, "stage", None)
    message = str(exc).strip() or type(exc).__name__

    if stage:
        code = _OPEN_CARD_STAGE_TO_ERROR_CODE.get(stage, ERROR_TECHNICAL)
        return RetryClassification(error_code=code, retryable=True, message=message)

    if "Карта не найдена" in message:
        return RetryClassification(
            error_code=ERROR_CARD_NOT_FOUND,
            retryable=False,
            message=message,
        )

    exc_name = type(exc).__name__
    if exc_name in {"TimeoutError", "PlaywrightTimeoutError"}:
        return RetryClassification(
            error_code=ERROR_PLAYWRIGHT_TIMEOUT,
            retryable=True,
            message=message,
        )
    if "timeout" in message.lower() or "Timeout" in exc_name:
        return RetryClassification(
            error_code=ERROR_PLAYWRIGHT_TIMEOUT,
            retryable=True,
            message=message,
        )

    return RetryClassification(
        error_code=ERROR_TECHNICAL,
        retryable=True,
        message=message,
    )


def parse_retry_count(comment: str) -> int:
    match = _RETRY_COUNT_RE.search(comment or "")
    if not match:
        return 0
    return int(match.group(1))


def parse_retry_max(comment: str) -> int | None:
    match = _RETRY_COUNT_RE.search(comment or "")
    if not match:
        return None
    return int(match.group(2))


def build_retryable_fail_comment(
    error_code: str,
    retry_count: int,
    max_attempts: int,
    message: str,
) -> str:
    cleaned = re.sub(r"\s+", " ", (message or "").strip())
    return (
        f"TECHNICAL:{error_code}: RETRY:{retry_count}/{max_attempts}: "
        f"{cleaned}; можно повторить"
    )


def build_retry_limit_reached_comment(error_code: str) -> str:
    return f"RETRY_LIMIT_REACHED:{error_code}: ручной разбор"


def is_retry_limit_reached_comment(comment: str) -> bool:
    return (comment or "").strip().startswith("RETRY_LIMIT_REACHED:")


def is_retryable_fail_comment_for_eligibility(
    comment: str,
    *,
    max_attempts: int,
) -> bool:
    """Whether a FAIL row with this enable comment may be auto-selected again."""
    if max_attempts <= 0:
        return False

    text = (comment or "").strip()
    if not text:
        return False
    if is_retry_limit_reached_comment(text):
        return False
    if text.startswith(f"{ERROR_CARD_NOT_FOUND}") or "CARD_NOT_FOUND" in text.split(";")[0]:
        return False
    if "UNKNOWN_STATUS" in text or "PARTNER_NOT_AVAILABLE" in text:
        return False

    retry_count = parse_retry_count(text)
    if _RETRY_COUNT_RE.search(text):
        return retry_count < max_attempts

    if "можно повторить" in text:
        return retry_count < max_attempts

    return False


def next_retry_fail_comment(
    *,
    prior_comment: str,
    error_code: str,
    message: str,
    max_attempts: int,
) -> tuple[str, str]:
    """
    Build registry comment for a retryable technical failure.

    Returns (registry_comment, outcome_error_code).
    """
    if max_attempts <= 0:
        cleaned = re.sub(r"\s+", " ", (message or "").strip())
        return f"TECHNICAL: {cleaned}; можно повторить", error_code

    next_count = parse_retry_count(prior_comment) + 1
    if next_count >= max_attempts:
        return build_retry_limit_reached_comment(error_code), ERROR_RETRY_LIMIT_REACHED

    return (
        build_retryable_fail_comment(error_code, next_count, max_attempts, message),
        error_code,
    )


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
