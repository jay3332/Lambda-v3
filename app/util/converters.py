from __future__ import annotations

import re
from datetime import timedelta
from typing import ClassVar, Final, TYPE_CHECKING, Type

from discord import app_commands
from discord.ext.commands import BadArgument, Converter

if TYPE_CHECKING:
    from app.core import Context

    IntervalConverter: Type[timedelta]

RELATIVE_TIME_REGEX: Final[re.Pattern[str]] = re.compile(
    r"(?:(?P<years>[0-9]{1,2})\s*(?:years?|yrs?|y)[, ]*)?"
    r"(?:(?P<months>[0-9]{1,2})\s*(?:months?|mo)[, ]*)?"
    r"(?:(?P<weeks>[0-9]{1,4})\s*(?:weeks?|wks?|w)[, ]*)?"
    r"(?:(?P<days>[0-9]{1,5})\s*(?:days?|d)[, ]*)?"
    r"(?:(?P<hours>[0-9]{1,5})\s*(?:hours?|hrs?|h)[, ]*)?"
    r"(?:(?P<minutes>[0-9]{1,5})\s*(?:minutes?|mins?|m)[, ]*)?"
    r"(?:(?P<seconds>[0-9]{1,5})\s*(?:seconds?|secs?|s))?",
)


class IntervalConverter(Converter[timedelta], app_commands.Transformer):
    """Return a timedelta representing the interval of time specified.

    This can also be used as a way to parse relative time.
    """

    INTERVALS: Final[ClassVar[dict[str, int]]] = {
        'seconds': 1,
        'minutes': 60,
        'hours': 3600,
        'days': 86400,
        'weeks': 604800,
        'months': 86400 * 30,  # TODO: make month calculation more accurate. Right now it's constant to 30 days.
        'years': 86400 * 365,  # TODO: year calculation could also be adjusted to account for leap years.
    }

    @classmethod
    def _convert(cls, argument: str) -> tuple[timedelta, re.Match[str]]:
        """Convert the argument to a timedelta."""
        match = RELATIVE_TIME_REGEX.search(argument)
        if match is None:
            raise BadArgument(
                'Invalid time or time interval given. Try something such as "5 days" next time.',
            )

        groups = match.groupdict(default=0)
        seconds = sum(
            cls.INTERVALS[key] * float(value)
            for key, value in groups.items()
        )

        if seconds <= 0:
            raise BadArgument('Invalid time or time interval given.')

        return timedelta(seconds=seconds), match

    async def convert(self, ctx: Context, argument: str) -> timedelta:
        return self._convert(argument)[0]

    async def transform(self, itx, argument: str) -> timedelta:
        return self._convert(argument)[0]
