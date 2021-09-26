from __future__ import annotations

import asyncio
import functools
import inspect
import re
from datetime import timedelta

from typing import Any, AsyncGenerator, AsyncIterator, Callable, Generator, TYPE_CHECKING, TypeGuard, TypeVar

if TYPE_CHECKING:
    from typing_extensions import ParamSpec

    P = ParamSpec('P')
    R = TypeVar('R')
    T = TypeVar('T')

__all__ = (
    'url_from_emoji',
    'human_short_delta',
    'aenumerate',
    'limit_generator',
    'to_thread',
)

EMOJI_REGEX = re.compile(r'<(a)?:([a-zA-Z0-9_]{2,32}):([0-9]{17,25})>')


def url_from_emoji(emoji: str) -> str:
    """Retrieves an image from either a custom emoji or a unicode one.

    Parameters
    ----------
    emoji: str
        The emoji to get the URL of

    Returns
    -------
    str
        The URL of the emoji
    """
    if match := EMOJI_REGEX.match(emoji):
        animated, _, id = match.groups()
        extension = 'gif' if animated else 'png'
        return f'https://cdn.discordapp.com/emojis/{id}.{extension}?v=1'
    else:
        fmt = 'https://emojicdn.elk.sh/{}?style=twitter'
        return fmt.format(quote_plus(emoji))


def human_short_delta(delta: float | timedelta) -> str:
    """Formats something such as 0.0024 into '2.4 ms'.

    Parameters
    ----------
    delta: float | :class:`datetime.timedelta`
        The amount of seconds to format.

    Returns
    -------
    str
        The formatted, human readable string representing the delta.
    """
    if isinstance(delta, timedelta):
        delta = delta.total_seconds()

    if delta < 0.000001:
        unit, factor = 'ns', 1e9
    elif delta < 0.001:
        unit, factor = 'μs', 1000000
    else:
        unit, factor = 'ms', 1000

    delta *= factor
    spec = '.2f' if delta < 10 else '.1f'

    return f'{delta:{spec}} {unit}'


def to_thread(func: Callable[P, R]) -> Callable[P, Awaitable[R]]:
    """Makes a blocking function run in an executor."""
    @functools.wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        return await asyncio.to_thread(func, *args, **kwargs)

    return wrapper


async def aenumerate(sequence: AsyncIterator[T], start: int = 0) -> AsyncIterator[tuple[int, T]]:
    """Asynchronously enumerate an async iterator from a given start value."""
    n = start
    async for item in sequence:
        yield n, item
        n += 1


def _isasyncgenfunction(
    func: Callable[P, Generator[R, Any, Any] | AsyncGenerator[R, Any, Any]]
) -> TypeGuard[Callable[P, AsyncGenerator[R, Any, Any]]]:
    """Shortcut to :func:`inspect.isasyncgenfunction`, but hinted with a TypeGuard"""
    return inspect.isasyncgenfunction(func)


def limit_generator(limit: int) -> Callable[
    [Callable[P, Generator[R, Any, Any]] | Callable[P, AsyncGenerator[R, Any, Any]]],
    Callable[P, Generator[R, Any, Any]] | Callable[P, AsyncGenerator[R, Any, Any]],
]:
    """Limits how many times the given generator function can yield.
    This should be used like a decorator.

    Parameters
    ----------
    limit: int
        The maximum amount of times
    """
    def decorator(
        func: Callable[P, Generator[R, Any, Any]] | Callable[P, AsyncGenerator[R, Any, Any]]
    ) -> Callable[P, Generator[R, Any, Any]] | Callable[P, AsyncGenerator[R, Any, Any]]:
        if _isasyncgenfunction(func):
            async def wrapper(*args: P.args, **kwargs: P.kwargs) -> AsyncGenerator[R, Any, Any]:
                async for i, res in aenumerate(func(*args, **kwargs)):
                    if i >= limit:
                        break
                    yield res

        else:
            def wrapper(*args: P.args, **kwargs: P.kwargs) -> Generator[R, Any, Any]:
                for i, res in enumerate(func(*args, **kwargs)):
                    if i >= limit:
                        break
                    yield res

        return functools.wraps(func)(wrapper)

    return decorator
