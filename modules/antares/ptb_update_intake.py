"""Antares update-queue intake seal for Q-PTB1 producer wait (TASK-49.A).

PTB ``Application.update_queue`` is the entry for fetcher-driven producers.
Sealing this queue is the Antares-owned stop of **new** producers without
calling ``Application.stop`` / HTTP shutdown.

Justification for this connection point: without a seal on ``put``, empty-queue
observations race with a late ``put`` (Orc11). Mixed/unbound paths that keep a
plain ``asyncio.Queue`` are unsupported by the producer-wait primitive and are
left unchanged.

``Application.stop`` still enqueues PTB's private ``_STOP_SIGNAL``; that control
object is allowed after seal so later P10 cleanup can stop the fetcher without
re-opening update intake.
"""

from __future__ import annotations

import asyncio
from typing import Any

from telegram.ext._application import _STOP_SIGNAL


class AntaresUpdateIntakeError(RuntimeError):
    """Refused put after intake seal (or invalid intake use)."""


class AntaresUpdateIntakeQueue(asyncio.Queue):
    """``asyncio.Queue`` that can refuse new updates after :meth:`seal`.

    Generation increments on each seal so attestations bind to a specific seal.
    """

    def __init__(self, maxsize: int = 0) -> None:
        super().__init__(maxsize=maxsize)
        self._sealed = False
        self._seal_generation = 0

    @property
    def sealed(self) -> bool:
        return self._sealed

    @property
    def seal_generation(self) -> int:
        return self._seal_generation

    def seal(self) -> int:
        """Close intake for new updates. Idempotent; returns current generation."""

        if not self._sealed:
            self._sealed = True
            self._seal_generation += 1
        return self._seal_generation

    def _refuse_if_sealed(self, item: Any) -> None:
        if not self._sealed:
            return
        if item is _STOP_SIGNAL:
            return
        raise AntaresUpdateIntakeError(
            "Antares update intake is sealed; new PTB producers are refused"
        )

    async def put(self, item: Any) -> None:
        self._refuse_if_sealed(item)
        await super().put(item)

    def put_nowait(self, item: Any) -> None:
        self._refuse_if_sealed(item)
        super().put_nowait(item)
