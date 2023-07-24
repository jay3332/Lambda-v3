from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from functools import wraps
from inspect import iscoroutinefunction
from typing import Any, Awaitable, Callable, Iterable, ParamSpec, Protocol, TYPE_CHECKING, Type, TypeVar

from discord.ext.commands import Converter

from config import Emojis

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
    'executor_function',
    'wrap_exceptions',
    'progress_bar',
    'preinstantiate',
    'ordinal',
    'proportionally_scale',
    'image_url_from_emoji',
)

EMOJI_REGEX: re.Pattern[str] = re.compile(r'<(a)?:(\w{2,32}):(\d{17,25})>')
PLURALIZE_REGEX: re.Pattern[str] = re.compile(r'(?P<quantity>-?[\d.,]+) (?P<thing>[a-zA-Z ]+?)\((?P<plural>i?e?s)\)')


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
    class Wrapper(Converter['T']):
        async def convert(self, ctx: Context, argument: str) -> T:
            return await func(ctx, argument)

    return Wrapper


def ordinal(number: int) -> str:
    """Convert a number to its ordinal representation."""
    if number % 100 // 10 != 1:
        if number % 10 == 1:
            return f"{number}st"

        if number % 10 == 2:
            return f"{number}nd"

        if number % 10 == 3:
            return f"{number}rd"

    return f"{number}th"


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


def humanize_duration(seconds: float | timedelta, depth: int = 3) -> str:
    """Formats a duration (in seconds) into one that is human-readable."""
    if isinstance(seconds, timedelta):
        seconds = seconds.total_seconds()
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


def humanize_size(size: int, *, precision: int = 2) -> str:
    """Humanizes a filesize-type size in bytes into one that is human-readable.

    E.g. 2048 becomes 2 KB.
    """
    if size < 1024:
        return f"{size} bytes"

    for unit in ('KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB'):
        size /= 1024
        if size < 1024:
            return f"{size:.{precision}f} {unit}"

    return ">1 YB"


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


class ExecutorFunction(Protocol['P', 'R']):
    __sync__: Callable[P, R]

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> Awaitable[R]:
        ...


def executor_function(func: Callable[P, R]) -> ExecutorFunction[P, R]:
    """Runs the decorated function in an executor"""
    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> Awaitable[R]:
        return asyncio.to_thread(func, *args, **kwargs)

    wrapper.__sync__ = func
    return wrapper  # type: ignore


def preinstantiate(*args: Any, **kwargs: Any) -> Callable[[Type[T]], T]:
    """Preinstantiates a class with the given arguments."""
    def decorator(cls: Type[T]) -> T:
        return cls(*args, **kwargs)

    return decorator


def expansion_list(entries: Iterable[str]) -> str:
    """Formats a list into expansion format."""
    entries = list(entries)
    emojis = Emojis.ExpansionEmojis

    if len(entries) == 1:
        first, *lines = entries[0].splitlines()
        result = f'{emojis.single} {first}'

        if lines:
            result += '\n' + '\n'.join(f'{Emojis.space} {line}' for line in lines)

        return result

    result = []

    for i, entry in enumerate(entries):
        first, *lines = entry.splitlines()

        if i + 1 == len(entries):
            result.append(f'{emojis.last} {first}')
            result.extend(f'{Emojis.space} {line}' for line in lines)
            continue

        emoji = emojis.first if i == 0 else emojis.mid

        result.append(f'{emoji} {first}')
        result.extend(f'{emojis.ext} {line}' for line in lines)

    return '\n'.join(result)


def progress_bar(ratio: float, *, length: int = 8, u200b: bool = True) -> str:
    """Generates an emoji-based progress bar."""
    ratio = min(1., max(0., ratio))

    result = ''
    span = 1 / length

    # Pre-calculate spans
    quarter_span = span / 4
    half_span = span / 2
    high_span = 3 * quarter_span

    for i in range(length):
        lower = i / length

        if ratio <= lower:
            key = 'empty'
        elif ratio <= lower + quarter_span:
            key = 'low'
        elif ratio <= lower + half_span:
            key = 'mid'
        elif ratio <= lower + high_span:
            key = 'high'
        else:
            key = 'full'

        if i == 0:
            start = 'left'
        elif i == length - 1:
            start = 'right'
        else:
            start = 'mid'

        result += getattr(Emojis.ProgressBar, f'{start}_{key}')

    if u200b:
        return result + "\u200b"

    return result


def proportionally_scale(
    old: tuple[int, int],
    *,
    min_dimension: int = None,
    max_dimension: int = 600,
) -> tuple[int, int]:
    """Scales the given dimensions proportionally to meet the specified
    minimum and maximum dimensions.

    Instead of a fixed point, this will ensure aspect ratio is kept.
    """
    width, height = old

    if min_dimension is not None:
        if width < min_dimension:
            width, height = min_dimension, int(height / (width / min_dimension))
        elif height < min_dimension:
            width, height = int(width / (height / min_dimension)), min_dimension

    if width > max_dimension:
        return max_dimension, int(height / (width / max_dimension))
    elif height > max_dimension:
        return int(width / (height / max_dimension)), max_dimension

    return width, height


def image_url_from_emoji(emoji: str) -> str:
    """"""
    if match := EMOJI_REGEX.match(emoji):
        animated, _, id = match.groups()
        extension = 'gif' if animated else 'png'
        return f'https://cdn.discordapp.com/emojis/{id}.{extension}?v=1'

    code = format(ord(emoji[0]), 'x')
    return f'https://twemoji.maxcdn.com/v/latest/72x72/{code}.png'
