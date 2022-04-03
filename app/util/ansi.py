from __future__ import annotations

import re
from enum import IntEnum
from typing import TYPE_CHECKING, Type, TypeAlias

from discord import User

from app.util.common import sentinel

if TYPE_CHECKING:
    from app.core import Context

    AnsiIdentifierKwargs: TypeAlias = 'AnsiColor | AnsiBackgroundColor | bool'

CLEAR = sentinel('CLEAR', str='\x1b[0m', repr='<clear>', bool=False)


class AnsiColor(IntEnum):
    default = 0
    gray = 30
    red = 31
    green = 32
    yellow = 33
    blue = 34
    magenta = 35
    cyan = 36
    white = 37


class AnsiStyle(IntEnum):
    default = 0
    bold = 1
    underline = 4


class AnsiBackgroundColor(IntEnum):
    default = 0
    black = 40
    red = 41
    gray = 42
    light_gray = 43
    lighter_gray = 44
    blurple = 45
    lightest_gray = 46
    white = 47


def ansi_identifier(
    *,
    color: AnsiColor = None,
    background_color: AnsiBackgroundColor = None,
    bold: bool = False,
    underline: bool = False,
    clear: bool = False,
    clear_color: bool = False,
) -> str:
    if clear:
        return str(CLEAR)

    if clear_color:
        return '\x1b[00m'

    parts = []
    if bold is CLEAR or underline is CLEAR:
        parts.append('0')

    if bold:
        parts.append('1')

    if underline:
        parts.append('4')

    if color:
        parts.append(format(color.value, ' >2'))

    if background_color:
        parts.append(format(background_color.value, ' >2'))

    if not parts:
        return ''

    return f'\x1b[{";".join(parts)}m'


class AnsiStringBuilder:
    """Aids in building an ANSI string."""

    __slots__ = ('buffer', '_raw', '_persisted_kwargs', '_length_offset')

    ANSI_SPLIT_REGEX: re.Pattern[str] = re.compile(r'((?:\x1b\[(?:\d{1,2};)*\d{1,2}m)+)')

    def __init__(self, string: str = '') -> None:
        self.buffer: str = string
        self._raw: str = string

        self._length_offset: int = 0
        self._persisted_kwargs: dict[str, AnsiIdentifierKwargs] = {}

    def append(self, string: str, *, clear: bool = True, **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Adds text to this string."""
        kwargs = self._persisted_kwargs | kwargs
        if kwargs:
            self.buffer += ansi_identifier(**kwargs)

        self.buffer += string + (str(CLEAR) if clear else '')
        self._raw += string
        return self

    def extend(self, other: AnsiStringBuilder) -> AnsiStringBuilder:
        """Extends this string with another."""
        self.buffer += other.buffer
        self._raw += other._raw
        return self

    def strip(self) -> AnsiStringBuilder:
        """Strips trailing whitespace from the string."""
        self.buffer = self.buffer.strip()
        self._raw = self._raw.strip()
        return self

    def newline(self, lines: int = 1) -> AnsiStringBuilder:
        """Adds a newline to this string."""
        self.buffer += '\n' * lines
        self._raw += '\n' * lines

        return self

    def bold(self, string: str = '', **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Adds and persists bold text."""
        self.append(string, bold=True, clear=False, **kwargs)

        self._persisted_kwargs['bold'] = True
        return self

    def no_bold(self, string: str = '', **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Adds and persists no-bold text."""
        self.append(string, bold=False, clear=False, **kwargs)

        del self._persisted_kwargs['bold']
        return self

    def underline(self, string: str = '', **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Adds and persists underlined text."""
        self.append(string, underline=True, clear=False, **kwargs)

        self._persisted_kwargs['underline'] = True
        return self

    def no_underline(self, string: str = '', **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Adds and persists no-underlined text."""
        self.append(string, underline=False, clear=False, **kwargs)

        del self._persisted_kwargs['underline']
        return self

    def color(self, color: AnsiColor = CLEAR, string: str = '', **kwargs: AnsiBackgroundColor | bool) -> AnsiStringBuilder:
        """Adds and persists colored text."""
        self.append(string, color=color, clear=False, **kwargs)

        self._persisted_kwargs['color'] = color
        return self

    def no_color(self, string: str = '', **kwargs: AnsiIdentifierKwargs) -> AnsiStringBuilder:
        """Adds and persists no-color text."""
        self.append(string, color=AnsiColor.default, clear=False, **kwargs)

        del self._persisted_kwargs['color']
        return self

    def background_color(self, background_color: AnsiBackgroundColor = CLEAR, string: str = '', **kwargs: AnsiColor | bool) -> AnsiStringBuilder:
        """Adds and persists colored background text."""
        self.append(string, background_color=background_color, clear=False, **kwargs)

        self._persisted_kwargs['background_color'] = background_color
        return self

    def no_background_color(self, string: str = '', **kwargs: AnsiColor | bool) -> AnsiStringBuilder:
        """Adds and persists no-color background text."""
        self.append(string, background_color=AnsiBackgroundColor.default, clear=False, **kwargs)

        del self._persisted_kwargs['background_color']
        return self

    def clear_formatting(self) -> AnsiStringBuilder:
        """Clears all formatting."""
        self.buffer += str(CLEAR)
        self._persisted_kwargs = {}
        return self

    def build(self, *, optimize: bool = True) -> str:
        """Returns the built string."""
        if optimize:
            self.optimize()

        if len(self) > 1000:
            # Currently, Discord does not render ANSI if it is above 1,000 characters.
            return self._raw

        return self.buffer

    @property
    def raw(self) -> str:
        """The raw, no-ansi version of this string."""
        return self._raw

    @property
    def raw_length(self) -> int:
        return len(self._raw)

    def ensure_codeblock(self, *, fallback: str = '') -> AnsiStringBuilder:
        """Surrounds the string with a codeblock if it's not already surrounded."""
        if not self.buffer.startswith('```'):
            self.buffer = '```ansi\n' + self.buffer + '```'
            self._raw = f'```{fallback}\n' + self.raw + '```'
            self._length_offset += 11

        return self

    def dynamic(self, ctx: Context) -> str:
        """Returns the built string only if the user of the given context is not on mobile."""
        if isinstance(ctx.author, User):
            if ctx.bot.user_on_mobile(ctx.author):
                return self.raw

        elif ctx.author.is_on_mobile():
            return self.raw

        return self.build()

    @staticmethod
    def _is_valid_enum(enum: Type[IntEnum], value: int) -> bool:
        try:
            enum(value)
        except ValueError:
            return False
        else:
            return True

    def optimize(self) -> AnsiStringBuilder:
        """Optimize the ANSI content for optimal length. This may be slow."""
        previous_color = 0
        previous_background_color = 0
        previous_format = 0
        result = []

        for i, part in enumerate(self.ANSI_SPLIT_REGEX.split(self.buffer)):
            if not i % 2:
                result += part
                continue

            parts = part.split('\x1b')[1:]
            parts = [p for part in parts for p in part[1:-1].split(';')]
            if not parts:
                continue

            color = background_color = fmt = 0
            for spec in parts:
                if spec == '0':
                    color = background_color = fmt = 0
                    continue

                spec = int(spec)
                if self._is_valid_enum(AnsiColor, spec):
                    color = spec

                elif self._is_valid_enum(AnsiBackgroundColor, spec):
                    background_color = spec

                elif self._is_valid_enum(AnsiStyle, spec):
                    fmt = spec

            specs = []
            if color != previous_color:
                specs.append(color)
                previous_color = color

            if background_color != previous_background_color:
                specs.append(background_color)
                previous_background_color = background_color

            if fmt != previous_format:
                specs.append(fmt)
                previous_format = fmt

            if not any(specs):
                specs = []

            if specs:
                specs.sort()
                result.append(f'\x1b[{";".join(map(str, specs))}m')

        result = ''.join(result)
        self.buffer = result
        return self

    def __str__(self) -> str:
        return self.build()

    def __len__(self) -> int:
        return len(self.buffer) - self._length_offset

    def __repr__(self) -> str:
        return f'<AnsiStringBuilder: {self.buffer!r}>'

    def __iadd__(self, other: str) -> AnsiStringBuilder:
        self.buffer += other
        return self
