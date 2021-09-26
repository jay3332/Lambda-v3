from __future__ import annotations

from time import perf_counter
from typing import Callable, Literal, overload, Protocol, TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    TimerT = TypeVar('TimerT', bound='Timer')

    class _RoundedTimer(Protocol):
        round: Literal[True]

    class _UnroundedTimer(Protocol):
        round: Literal[False]

    RoundedTimer = TypeVar('RoundedTimer', bound=_RoundedTimer, covariant=True)
    UnroundedTimer = TypeVar('UnroundedTimer', bound=_UnroundedTimer, covariant=True)


class Timer:
    """Context manager that outputs the time from __enter__ to __exit__.

    Parameters
    ----------
    ms: bool
        Whether or not outputs should be in milliseconds. Defaults to ``False``
    round: bool
        Whether or not to round outputs. Defaults to ``False``
    clock: Callable[..., :class:`float`]
        The function to use to retrieve the current time. Should return in seconds.
        Defaults to :func:`time.perf_counter`.
    """

    # noinspection PyShadowingBuiltins
    def __init__(self, *, ms: bool = False, round: bool = False, clock: Callable[..., float] = perf_counter) -> None:
        self.ms: bool = ms
        self.round: bool = round
        self.clock: Callable[..., float] = clock

        self.__enter: float | None = None
        self.__exit: float | None = None

    def __enter__(self: TimerT) -> TimerT:
        self.__enter = self.clock()
        return self

    def __exit__(self, *_) -> None:
        self.__exit = self.clock()

    @overload
    def elapsed(self: RoundedTimer) -> int:
        ...

    @overload
    def elapsed(self: UnroundedTimer) -> float:
        ...

    @property
    def elapsed(self) -> float | int:
        if self.__enter is None:
            raise ValueError('timers must be used in a context manager')

        if self.__exit is None:
            self.__exit = self.clock()

        result = self.__exit - self.__enter

        if self.ms:
            result *= 1000

        if self.round:
            result = round(result)

        return result
