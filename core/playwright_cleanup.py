"""Best-effort Playwright page/context/browser cleanup (never raises)."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


def close_playwright_stack(
    *,
    page: Any | None = None,
    context: Any | None = None,
    browser: Any | None = None,
) -> None:
    """Close page → context → browser; safe on partial init or double-close."""
    for label, obj in (("page", page), ("context", context), ("browser", browser)):
        if obj is None:
            continue
        try:
            if label == "page":
                is_closed = getattr(obj, "is_closed", None)
                if callable(is_closed) and is_closed():
                    continue
            obj.close()
        except Exception:
            log.debug("[PlaywrightCleanup] %s close failed (ignored)", label, exc_info=True)
