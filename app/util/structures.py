from __future__ import annotations

import datetime
from time import perf_counter
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
        self.start_time: float | None = None
        self.end_time: float | None = None

    def __enter__(self: TimerT) -> TimerT:
        self.start_time = perf_counter()
        return self

    def __exit__(self, _type, _val, _tb) -> None:
        self.end_time = perf_counter()

    @property
    def elapsed(self) -> float:
        if not (self.start_time and self.end_time):
            raise ValueError('timer has not been stopped')

        return self.end_time - self.start_time

    def __repr__(self) -> str:
        return f'<Timer elapsed={self.elapsed}>'

    def __int__(self) -> int:
        return int(self.elapsed)

    def __float__(self) -> float:
        return self.elapsed


class TemporaryAttribute(Generic[T, V]):
    __slots__ = ('obj', 'attr', 'value')

    def __init__(self, obj: T, attr: str, value: V) -> None:
        self.obj: T = obj
        self.attr: str = attr
        self.value: V = value

    def __enter__(self) -> T:
        setattr(self.obj, self.attr, self.value)
        return self.obj

    def __exit__(self, _type, _val, _tb) -> None:
        delattr(self.obj, self.attr)


class TimestampFormatter:
    __slots__ = ('dt',)

    def __init__(self, dt: datetime.datetime) -> None:
        self.dt: datetime.datetime = dt

    def __format__(self, format_spec: utils.TimestampStyle | Literal['']) -> str:
        return utils.format_dt(self.dt, format_spec or None)
