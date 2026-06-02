"""DTO containers for conversion business analysis (no I/O side effects)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd


@dataclass
class SpecialCardsState:
    """Parsed special_cards.xlsx state used by analysis and facade notifications."""

    special_rules: dict[tuple[str, str], date] = field(default_factory=dict)
    latest_special_date: date | None = None
    special_loaded: bool = False
    df_special: pd.DataFrame | None = None

    @classmethod
    def empty(cls) -> SpecialCardsState:
        return cls()


@dataclass
class ConversionAnalysisResult:
    """Business analysis output consumed by conversion.run() facade.

    ``problem_cards`` includes normalized ``partner`` for rules/Telegram and
    raw ``original_partner`` for Excel output on sheet «Отключить».
    """

    summary: dict[str, Any]
    problem_cards: pd.DataFrame
    cards_in_work_by_partner: pd.Series
    cards_in_work_by_pool: pd.Series
    special_cards_state: SpecialCardsState
