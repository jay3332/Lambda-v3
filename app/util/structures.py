from __future__ import annotations

import datetime
from time import perf_counter_ns
from typing import Generic, Literal, TypeVar, TYPE_CHECKING

from discord import utils

if TYPE_CHECKING:
    TimerT = TypeVar('TimerT', bound='Timer')

T = TypeVar('T')
V = TypeVar('V')

__all__ = (
    'Timer',
)


class Timer:
    __slots__ = ('start_time', 'end_time')

    def __init__(self) -> None:
        self.start_time: int | None = None
        self.end_time: int | None = None

    def __enter__(self: TimerT) -> TimerT:
        self.start_time = perf_counter_ns()
        return self

    def __exit__(self, _type, _val, _tb) -> None:
        self.end_time = perf_counter_ns()

    @property
    def elapsed_ns(self) -> int:
        if not (self.start_time and self.end_time):
            raise ValueError('timer has not been stopped')

        return self.end_time - self.start_time

    @property
    def elapsed_ms(self) -> float:
        return self.elapsed_ns / 1e6

    @property
    def elapsed(self) -> float:
        return self.elapsed_ns / 1e9

    def __repr__(self) -> str:
        return f'<Timer elapsed={self.elapsed}>'

    def __int__(self) -> int:
        return int(self.elapsed)

    def __float__(self) -> float:
        return self.elapsed


_ATTR_MISSING = object()


class TemporaryAttribute(Generic[T, V]):
    __slots__ = ('obj', 'attr', 'value', 'original')

    def __init__(self, obj: T, attr: str, value: V) -> None:
        self.obj: T = obj
        self.attr: str = attr
        self.value: V = value
        self.original: V = getattr(obj, attr, _ATTR_MISSING)

    def __enter__(self) -> T:
        setattr(self.obj, self.attr, self.value)
        return self.obj

    def __exit__(self, _type, _val, _tb) -> None:
        if self.original is _ATTR_MISSING:
            return delattr(self.obj, self.attr)

        setattr(self.obj, self.attr, self.original)


class TimestampFormatter:
    __slots__ = ('dt',)

    def __init__(self, dt: datetime.datetime) -> None:
        self.dt: datetime.datetime = dt

    def __format__(self, format_spec: utils.TimestampStyle | Literal['']) -> str:
        return utils.format_dt(self.dt, format_spec or None)
