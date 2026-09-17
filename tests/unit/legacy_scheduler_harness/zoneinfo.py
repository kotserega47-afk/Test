"""zoneinfo stub so Europe/Moscow works without the tzdata package."""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo


class ZoneInfo(tzinfo):
    def __init__(self, key: str) -> None:
        self.key = key
        if key in {"Europe/Moscow", "MSK"}:
            self._offset = timedelta(hours=3)
        else:
            self._offset = timedelta(0)

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return self._offset

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        return self.key

    def fromutc(self, dt: datetime) -> datetime:
        return dt.replace(tzinfo=self) + self._offset


def available_timezones() -> set[str]:
    return {"Europe/Moscow", "UTC"}
