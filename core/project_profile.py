"""Parse a project profile name. No I/O, env, logging, or job registration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

ProjectProfileName = Literal["antares", "raccoon", "wr"]

KNOWN_PROJECT_PROFILES: Final[frozenset[str]] = frozenset({"antares", "raccoon", "wr"})


class InvalidProjectProfileError(ValueError):
    """Raised when the input is not a known exact profile token."""


@dataclass(frozen=True, slots=True)
class ProjectProfileSelection:
    name: ProjectProfileName
    implicit_default: bool


def parse_project_profile(value: str | None) -> ProjectProfileSelection:
    """Return a profile selection from an explicit string or implicit default.

    Does not read the environment. ``implicit_default`` describes the parse
    only: it does not mean the running process is isolated as Antares.
    Accepting ``wr`` does not mean WR is ready to run.
    """
    if value is None:
        return ProjectProfileSelection(name="antares", implicit_default=True)

    stripped = value.strip()
    if stripped == "":
        return ProjectProfileSelection(name="antares", implicit_default=True)

    if stripped in KNOWN_PROJECT_PROFILES:
        return ProjectProfileSelection(
            name=stripped,  # type: ignore[arg-type]
            implicit_default=False,
        )

    allowed = ", ".join(sorted(KNOWN_PROJECT_PROFILES))
    raise InvalidProjectProfileError(
        f"Invalid project profile {value!r}. Allowed exact values: {allowed}."
    )
