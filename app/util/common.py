from __future__ import annotations

import asyncio
import re
from functools import wraps
from inspect import iscoroutinefunction
from typing import Any, Awaitable, Callable, ParamSpec, TYPE_CHECKING, Type, TypeVar

from discord.ext.commands import Converter

if TYPE_CHECKING:
    from app.core import Context

    T = TypeVar('T')
    KwargT = TypeVar('KwargT')
    ConstantT = TypeVar('ConstantT', bound='SetinelConstant')

    P = ParamSpec('P')
    R = TypeVar('R')

__all__ = (
    'sentinel',
    'converter',
    'cutoff',
    'pluralize',
    'humanize_list',
    'humanize_duration',
    'humanize_small_duration',
)

EMOJI_REGEX: re.Pattern[str] = re.compile(r'<(a)?:([a-zA-Z0-9_]{2,32}):([0-9]{17,25})>')
PLURALIZE_REGEX: re.Pattern[str] = re.compile(r'(?P<quantity>-?[0-9.,]+) (?P<thing>[a-zA-Z]+)\((?P<plural>i?e?s)\)')


# This exists for type checkers
class SentinelConstant:
    pass


def _create_sentinel_callback(v: KwargT) -> Callable[[ConstantT], KwargT]:
    def wrapper(_self: ConstantT) -> KwargT:
        return v

    return wrapper


def sentinel(name: str, **dunders: KwargT) -> ConstantT:  # "sentinel" is a misleading name for this, maybe I should rename it
    """Creates a constant singleton object."""
    attrs = {f'__{k}__': _create_sentinel_callback(v) for k, v in dunders.items()}
    return type(name, (SentinelConstant,), attrs)()


def converter(func: Callable[[Context, str], T]) -> Type[Converter | T]:
    """Creates a :class:`discord.ext.commands.Converter` that calls the decorated function on the input."""
    class Wrapper(Converter[T]):
        async def convert(self, ctx: Context, argument: str) -> T:
            return await func(ctx, argument)

    return Wrapper


def cutoff(string: str, /, max_length: int = 64, *, exact: bool = False) -> str:
    """Cuts-off a string at a certain length, and if it has been cutoff, append "..." to it."""
    if len(string) <= max_length:
        return string

    offset = 0 if not exact else 3
    return string[:max_length - offset] + '...'


def pluralize(text: str, /) -> str:
    """Automatically finds words that need to be pluralized in a string and pluralizes it."""
    def callback(match):
        quantity = abs(float((q := match.group('quantity')).replace(',', '')))
        return f'{q} ' + match.group('thing') + (('', match.group('plural'))[quantity != 1])

    return PLURALIZE_REGEX.sub(callback, text)


def humanize_list(li: list[Any]) -> str:
    """Takes a list and returns it joined."""
    if len(li) <= 2:
        return " and ".join(li)

    return ", ".join(li[:-1]) + f", and {li[-1]}"


def humanize_small_duration(seconds: float) -> str:
    """Turns a very small duration into a human-readable string."""
    units = ('ms', 'μs', 'ns', 'ps')

    for i, unit in enumerate(units, start=1):
        boundary = 10 ** (3 * i)

        if seconds > 1 / boundary:
            m = seconds * boundary
            m = round(m, 2) if m >= 10 else round(m, 3)

            return f"{m} {unit}"

    return "<1 ps"


def humanize_duration(seconds: float, depth: int = 3) -> str:
    """Formats a duration (in seconds) into one that is human-readable."""
    if seconds < 1:
        return '<1 second'

    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    mo, d = divmod(d, 30)
    y, mo = divmod(mo, 12)

    if y > 100:
        return ">100 years"

    y, mo, d, h, m, s = [int(entity) for entity in (y, mo, d, h, m, s)]
    items = (y, 'year'), (mo, 'month'), (d, 'day'), (h, 'hour'), (m, 'minute'), (s, 'second')

    as_list = [f"{quantity} {unit}{'s' if quantity != 1 else ''}" for quantity, unit in items if quantity > 0]
    return humanize_list(as_list[:depth])


def _wrap_exceptions_sync(func: Callable[P, R], exc_type: Type[BaseException]) -> Callable[P, R]:
    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            raise exc_type(exc) from exc

    return wrapper


def _wrap_exceptions_async(func: Callable[P, Awaitable[R]], exc_type: Type[BaseException]) -> Callable[P, Awaitable[R]]:
    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            raise exc_type(exc) from exc

    return wrapper


def wrap_exceptions(exc_type: Type[BaseException]) -> Callable[[Callable[P, R]], Callable[P, R]]:
    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        if iscoroutinefunction(func):
            return _wrap_exceptions_async(func, exc_type)

        return _wrap_exceptions_sync(func, exc_type)

    return decorator


def executor_function(func: Callable[P, R]) -> Callable[P, Awaitable[R]]:
    """Runs the decorated function in an executor"""
    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Awaitable[R]:
        return asyncio.to_thread(func, *args, **kwargs)

    return wrapper
