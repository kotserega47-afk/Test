"""Read-only PTB compatibility / request-graph inspector for sender gates (TASK-46).

Does not import ``integrations.telegram_bot`` (that module starts the global loop).
Caller passes ``bot`` and ``expected_general_request``. Never mutates Bot/HTTP/queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

REASON_SENDER_BOT_UNAVAILABLE: Final = "sender_bot_unavailable"
REASON_BOT_SHUTDOWN_UNAVAILABLE: Final = "bot_shutdown_unavailable"
REASON_REQUEST_GRAPH_UNAVAILABLE: Final = "request_graph_unavailable"
REASON_REQUEST_GRAPH_AMBIGUOUS: Final = "request_graph_ambiguous"
REASON_GENERAL_REQUEST_MISMATCH: Final = "general_request_mismatch"
REASON_REQUEST_SHUTDOWN_UNAVAILABLE: Final = "request_shutdown_unavailable"
REASON_LEFTOVER_DIAGNOSTIC_UNAVAILABLE: Final = "leftover_diagnostic_unavailable"


@dataclass(frozen=True, slots=True)
class SenderRequestRolePlan:
    """One logical role in the Bot request graph."""

    role: str
    request: Any


@dataclass(frozen=True, slots=True)
class SenderPtbCompatibilityResult:
    """Structured capability / graph inspection result (no mutation)."""

    supported: bool
    reason: str | None
    roles: tuple[SenderRequestRolePlan, ...]
    close_targets: tuple[Any, ...]
    telegram_version: str | None = None


def _telegram_version() -> str | None:
    try:
        import telegram

        return getattr(telegram, "__version__", None)
    except Exception:
        return None


def _refuse(
    reason: str,
    *,
    roles: tuple[SenderRequestRolePlan, ...] = (),
) -> SenderPtbCompatibilityResult:
    return SenderPtbCompatibilityResult(
        supported=False,
        reason=reason,
        roles=roles,
        close_targets=(),
        telegram_version=_telegram_version(),
    )


def _discover_owned_request_roles(bot: Any) -> tuple[SenderRequestRolePlan, ...] | None:
    """Return recognized full graph or None if unavailable/unrecognized.

    Recognized shape (PTB Bot): ``bot._request`` is a tuple of length >= 2
    ``(getUpdates request, general API request)``. Public-only ``bot.request``
    without that pair is **not** accepted as full-graph proof (TASK-45/46).
    """

    pair = getattr(bot, "_request", None)
    if not isinstance(pair, tuple) or len(pair) < 2:
        return None
    get_updates, general = pair[0], pair[1]
    if get_updates is None or general is None:
        return None
    return (
        SenderRequestRolePlan(role="get_updates_request", request=get_updates),
        SenderRequestRolePlan(role="request", request=general),
    )


def _has_leftover_diagnostic(req: Any) -> bool:
    # Recognized diagnostic path (same idea as application_lifecycle): _client / is_closed.
    if not hasattr(req, "_client"):
        return False
    client = getattr(req, "_client", None)
    if client is None:
        return True
    return hasattr(client, "is_closed")


def _dedupe_close_targets(roles: tuple[SenderRequestRolePlan, ...]) -> tuple[Any, ...]:
    seen: set[int] = set()
    targets: list[Any] = []
    for role in roles:
        key = id(role.request)
        if key in seen:
            continue
        seen.add(key)
        targets.append(role.request)
    return tuple(targets)


def inspect_sender_ptb_compatibility(
    *,
    bot: Any,
    expected_general_request: Any,
) -> SenderPtbCompatibilityResult:
    """Read-only compatibility inspection for future sender HTTP close.

    Never calls ``shutdown`` / ``initialize`` / queue mutation.
    """

    if bot is None:
        return _refuse(REASON_SENDER_BOT_UNAVAILABLE)

    shutdown = getattr(bot, "shutdown", None)
    if not callable(shutdown):
        return _refuse(REASON_BOT_SHUTDOWN_UNAVAILABLE)

    if expected_general_request is None:
        return _refuse(REASON_GENERAL_REQUEST_MISMATCH)

    try:
        roles = _discover_owned_request_roles(bot)
    except Exception:
        return _refuse(REASON_REQUEST_GRAPH_AMBIGUOUS)

    if roles is None:
        # Missing/unrecognized private graph — fail-closed (do not guess via public request).
        pair = getattr(bot, "_request", None)
        if pair is None and getattr(bot, "request", None) is not None:
            return _refuse(REASON_REQUEST_GRAPH_AMBIGUOUS)
        return _refuse(REASON_REQUEST_GRAPH_UNAVAILABLE)

    general_role = next((r for r in roles if r.role == "request"), None)
    if general_role is None:
        return _refuse(REASON_REQUEST_GRAPH_UNAVAILABLE, roles=roles)
    if general_role.request is not expected_general_request:
        return _refuse(REASON_GENERAL_REQUEST_MISMATCH, roles=roles)

    for role in roles:
        if not callable(getattr(role.request, "shutdown", None)):
            return _refuse(REASON_REQUEST_SHUTDOWN_UNAVAILABLE, roles=roles)
        if not _has_leftover_diagnostic(role.request):
            return _refuse(REASON_LEFTOVER_DIAGNOSTIC_UNAVAILABLE, roles=roles)

    return SenderPtbCompatibilityResult(
        supported=True,
        reason=None,
        roles=roles,
        close_targets=_dedupe_close_targets(roles),
        telegram_version=_telegram_version(),
    )
