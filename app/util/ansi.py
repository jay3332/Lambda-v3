from __future__ import annotations

from enum import IntEnum

from app.util.common import setinel

CLEAR = setinel('CLEAR', str='\x1b[0m', repr='<clear>', bool=False)


class AnsiColor(IntEnum):
    default = 0
    black = 30
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
    green = 42
    yellow = 43
    blue = 44
    magenta = 45
    cyan = 46
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

    def __init__(self, string: str = '') -> None:
        self.buffer: str = string
        self._raw: str = string

        self._persisted_kwargs: dict[str, AnsiColor | AnsiBackgroundColor | bool] = {}

    def append(self, string: str, *, clear: bool = True, **kwargs: AnsiColor | AnsiBackgroundColor | bool) -> AnsiStringBuilder:
        """Adds text to this string."""
        kwargs = self._persisted_kwargs | kwargs
        if kwargs:
            self.buffer += ansi_identifier(**kwargs)

        self.buffer += string + (str(CLEAR) if clear else '')
        self._raw += string
        return self

    def bold(self, string: str = '', **kwargs: AnsiColor | AnsiBackgroundColor | bool) -> AnsiStringBuilder:
        """Adds and persists bold text."""
        self.append(string, bold=True, clear=False, **kwargs)

        self._persisted_kwargs['bold'] = True
        return self

    def no_bold(self, string: str = '', **kwargs: AnsiColor | AnsiBackgroundColor | bool) -> AnsiStringBuilder:
        """Adds and persists no-bold text."""
        self.append(string, bold=False, clear=False, **kwargs)

        del self._persisted_kwargs['bold']
        return self

    def underline(self, string: str = '', **kwargs: AnsiColor | AnsiBackgroundColor | bool) -> AnsiStringBuilder:
        """Adds and persists underlined text."""
        self.append(string, underline=True, clear=False, **kwargs)

        self._persisted_kwargs['underline'] = True
        return self

    def no_underline(self, string: str = '', **kwargs: AnsiColor | AnsiBackgroundColor | bool) -> AnsiStringBuilder:
        """Adds and persists no-underlined text."""
        self.append(string, underline=False, clear=False, **kwargs)

        del self._persisted_kwargs['underline']
        return self

    def color(self, color: AnsiColor = CLEAR, string: str = '', **kwargs: AnsiBackgroundColor | bool) -> AnsiStringBuilder:
        """Adds and persists colored text."""
        self.append(string, color=color, clear=False, **kwargs)

        self._persisted_kwargs['color'] = color
        return self

    def no_color(self, string: str = '', **kwargs: AnsiColor | AnsiBackgroundColor | bool) -> AnsiStringBuilder:
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

    def build(self) -> str:
        """Returns the built string."""
        return self.buffer

    @property
    def raw(self) -> str:
        """The raw, no-ansi version of this string."""
        return self._raw

    @property
    def raw_length(self) -> int:
        return len(self._raw)

    def __str__(self) -> str:
        return self.build()

    def __len__(self) -> int:
        return len(self.buffer)

    def __repr__(self) -> str:
        return f'<AnsiStringBuilder: {self.buffer!r}>'

    def __iadd__(self, other: str) -> AnsiStringBuilder:
        self.buffer += other
        return self
