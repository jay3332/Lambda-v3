from __future__ import annotations

from collections import defaultdict
from typing import Iterator, TYPE_CHECKING

import pytz
from discord.utils import cached_property

if TYPE_CHECKING:
    from app.core import Context

_CACHED_ABBREVIATION_MAPPING: dict[str, set[str]] | None = None


def populate_abbreviation_mapping() -> dict[str, set[str]]:
    global _CACHED_ABBREVIATION_MAPPING

    if _CACHED_ABBREVIATION_MAPPING is not None:
        return _CACHED_ABBREVIATION_MAPPING

    _CACHED_ABBREVIATION_MAPPING = mapping = defaultdict(set)

    for name in pytz.all_timezones:
        tz = pytz.timezone(name)

        try:
            info = tz._transition_info  # type: ignore
        except AttributeError:
            continue

        for _, _, abbrev in info:
            mapping[abbrev].add(name)

    for source, dest in (
        ('EST', 'ET'),
        ('PST', 'PT'),
        ('MST', 'MT'),
        ('CST', 'CT'),
    ):
        mapping[dest] = mapping[source]

    return mapping


def transform_timezone(tz_name: str) -> str:
    """If the given timezone is an abbreviation still allow it."""
    try:
        pytz.timezone(tz_name)
        return tz_name
    except pytz.UnknownTimeZoneError:
        mapping = populate_abbreviation_mapping()

        for name in mapping[tz_name]:
            return name  # returns the first item in the set


def get_timezone_offset(tz_name: str) -> int:
    """Return the offset in seconds for the given timezone."""
    tz = pytz.timezone(transform_timezone(tz_name))
    delta = tz.utcoffset(None)

    if delta is None:
        try:
            *_, (delta, *_) = tz._transition_info  # type: ignore
        except AttributeError:
            return 0

    return int(delta.total_seconds())


def walk_timezones_by_offset(offset: int, common_only: bool = True) -> Iterator[str]:
    """Walks through all timezones with the given offset IN SECONDS."""
    timezones = pytz.common_timezones_set if common_only else pytz.all_timezones_set
    yield from filter(lambda tz: get_timezone_offset(tz) == offset, timezones)


def parse_utc_offset_string(string: str, *, check: int = False) -> int:
    """Get the offset in seconds of the given UTC offset string, e.g. 'UTC+5' or 'UTC-06:30'."""
    offset = string.upper().removeprefix('UTC').removeprefix('GMT').replace(' ', '')

    if not offset:
        raise ValueError(f'invalid UTC offset string {string!r}')

    hours, _, minutes = offset.partition(':')
    try:
        hours, minutes = float(hours), int(minutes or 0)
    except ValueError:
        raise ValueError(f'invalid UTC offset string {string!r}')

    offset = int(hours * 3600 + minutes * 60)
    if not -43200 <= offset <= 50400:
        raise ValueError('offset must be between -12:00 and +14:00')

    if check and not sum(1 for _ in walk_timezones_by_offset(offset)):
        raise ValueError('no timezones found with the given offset')

    return offset


class TimeZone:
    """A high-level wrapper around a PyTZ timezone object."""

    def __init__(self, tz_name: str) -> None:
        self.name: str = self.parse(tz_name)
        self.tz: pytz.BaseTzInfo = pytz.timezone(self.name)

    @cached_property
    def offset(self) -> int:
        return get_timezone_offset(self.name)

    @staticmethod
    def parse(tz_name: str) -> str:
        """Converts a timezone name to a valid one able to be passed into :meth:`pytz.timezone()`."""
        if tz_name.upper().replace(' ', '').startswith(('UTC+', 'UTC-', 'GMT+', 'GMT-')):
            return next(walk_timezones_by_offset(parse_utc_offset_string(tz_name, check=True)))

        return transform_timezone(tz_name)

    @classmethod
    def from_str(cls, tz_name: str) -> TimeZone:  # backwards compatibility purposes
        return cls(tz_name)

    @classmethod
    def from_offset(cls, offset: int) -> TimeZone:
        self = cls.__new__(cls)  # avoid __init__
        self.name = next(walk_timezones_by_offset(offset))
        self.tz = pytz.timezone(self.name)

        return self

    @classmethod
    async def convert(cls, _ctx: Context, argument: str) -> TimeZone:
        return cls.from_str(argument)

    @classmethod
    def utc(cls) -> TimeZone:
        return cls('UTC')

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f'<TimeZone name={self.name!r} offset={self.offset:+}>'

    def __eq__(self, other: TimeZone) -> bool:
        return self.offset == other.offset
