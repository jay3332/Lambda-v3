from __future__ import annotations as _  # PyCharm thinking "annotations" is shadowing

import inspect
import sys
from argparse import ArgumentParser
from dataclasses import dataclass
from typing import Annotated, Any, Collection, Generic, Iterator, Type, TypeVar, TYPE_CHECKING

from discord.ext.commands import Converter, run_converters
from discord.ext.commands.view import StringView
from discord.utils import MISSING, resolve_annotation

if TYPE_CHECKING:
    from app.core.models import Command, Context

    FlagMetaT = TypeVar("FlagMetaT", bound="FlagMeta")

T = TypeVar('T')


@dataclass
class Flag(Generic[T]):
    """Represents a flag."""
    name: str = MISSING
    dest: str = MISSING
    aliases: Collection[str] = ()
    store_true: bool = False
    converter: Converter[T] | Type[Any] | None = None
    short: str | None = None
    description: str | None = None
    required: bool = False
    default: T = None

    def add_to(self, parser: ArgumentParser, /) -> None:
        """Adds the flag to the parser."""
        if self.name is MISSING or self.dest is MISSING:
            raise ValueError("name and dest must be set.")

        args = ['--' + self.name] if self.short is None else [self.name, '-' + self.short]
        args.extend('--' + alias for alias in self.aliases)

        if not self.store_true:
            parser.add_argument(
                *args,
                nargs='+',
                dest=self.dest,
                required=self.required,
                default=self.default,
            )
            return

        parser.add_argument(*args, dest=self.dest, action='store_true')


def _resolve_aliases(alias: str, aliases: Collection[str]) -> Collection[str]:
    if alias and aliases:
        raise ValueError("alias and aliases are mutually exclusive.")

    if alias is not MISSING:
        aliases = (alias,)

    return [alias.casefold() for alias in aliases]


def flag(
    *,
    name: str = MISSING,
    short: str = None,
    alias: str = MISSING,
    aliases: Collection[str] = (),
    converter: Converter[T] | Type[T] = None,
    description: str = None,
    required: bool = False,
    default: Any = None,
) -> Annotated[T, Flag[T]]:
    """Creates a flag dataclass."""
    return Flag(
        name=name.casefold(),
        short=short,
        aliases=_resolve_aliases(alias, aliases),
        converter=converter,
        description=description,
        required=required,
        default=default,
    )


def store_true(
    *,
    name: str = MISSING,
    short: str = None,
    alias: str = MISSING,
    aliases: Collection[str] = (),
    description: str = None,
) -> Annotated[bool, Flag[bool]]:
    """Creates a store true flag."""
    aliases = _resolve_aliases(alias, aliases)
    return Flag(name=name, short=short, aliases=aliases, store_true=True, description=description)  # type: ignore


class FlagOrConvert(Converter[T]):
    """If this argument starts with a valid flag then stop converting."""

    def __init__(self) -> None:
        self._err: BaseException | None = None

    async def convert(self, ctx: Context, argument: str) -> T:
        if self._err is not None:
            raise StopIteration

        if not isinstance(ctx.command, Command) or ctx.command.custom_flags is None:
            raise TypeError

        if ctx.command.custom_flags.is_flag_starter(argument):
            raise StopIteration

        annotation = ctx.current_parameter.annotation
        if annotation is None:
            return argument

        try:
            return await run_converters(ctx, annotation, argument, ctx.current_parameter)
        except Exception as exc:
            self._err = exc
            raise


def _get_namespaces(attrs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        global_ns = sys.modules[attrs['__module__']].__dict__
    except KeyError:
        global_ns = {}

    frame = inspect.currentframe()
    try:
        if frame is None:
            local_ns = {}
        else:
            parent = frame if frame.f_back is None else frame.f_back
            local_ns = parent.f_locals
    finally:
        del frame

    return local_ns, global_ns


# noinspection PyShadowingNames
def _resolve_flag_annotation(flag: Flag[Any], annotation: Any, *args: Any) -> None:
    annotation = resolve_annotation(annotation, *args)
    if annotation is type(None) or not annotation:
        annotation = str

    flag.converter = annotation


def _resolve_flags(attrs: dict[str, T]) -> dict[str, Flag[T]]:
    local_ns, global_ns = _get_namespaces(attrs)
    annotations = attrs.get('__annotations__', {})

    flags = {}
    args = global_ns, local_ns, {}

    for name, value in attrs.items():
        if name.startswith('__') or not isinstance(value, Flag):
            continue

        if value.converter is None and not value.store_true:
            _resolve_flag_annotation(value, annotations[name], *args)

        value.dest = name = name.casefold()
        flags[name] = value

    for name, annotation in annotations.items():
        if name in flags:
            continue

        flags[name] = res = flag(name=name.casefold())
        res.dest = name

        _resolve_flag_annotation(res, annotation, *args)

    return flags


class FlagMeta(type, Generic[T]):
    if TYPE_CHECKING:
        _flags: dict[str, Flag[T]]
        _parser: ArgumentParser

    def __new__(mcs: Type[FlagMetaT], name: str, bases: tuple[Type[Any], ...], attrs: dict[str, Any]) -> FlagMetaT:
        cls = super().__new__(mcs, name, bases, attrs)
        cls.__doc__ = inspect.cleandoc(inspect.getdoc(cls))

        cls._flags = flags = _resolve_flags(attrs)
        cls._parser = parser = ArgumentParser(description=cls.__doc__)

        # noinspection PyShadowingNames
        for flag in flags.values():
            flag.add_to(parser)

        return cls

    @property
    def flags(cls) -> dict[str, Flag[T]]:
        return cls._flags

    @property
    def parser(cls) -> ArgumentParser:
        return cls._parser

    def is_flag_starter(cls, sample: str) -> bool:
        """Return whether the sample starts with a valid flag."""
        sample = sample.lstrip()
        if not sample.startswith('-'):
            return False

        # noinspection PyShadowingNames
        for flag in cls.walk_flags():
            if flag.short and sample.startswith('-' + flag.short):
                return True

            if flag.name and sample.casefold().startswith('--' + flag.name):
                return True

            if any(sample.casefold().startswith('--' + alias) for alias in flag.aliases):
                return True

        return False

    def walk_flags(cls) -> Iterator[Flag[T]]:
        yield from cls._flags.values()

    def inject(cls, command: Command) -> None:
        command.custom_flags = cls._flags


class Flags(Converter[T], metaclass=FlagMeta[T]):
    """Base class for all flag groups."""

    async def convert(self, ctx: Context, argument: str) -> T:
        try:
            flags: FlagMeta[T] = ctx.command.custom_flags
        except Exception as exc:
            raise TypeError(f'bad flag annotation: {exc}')

        # we should split the args MAKE SURE TO PUT FLAG STARTS AT THE BEGINNING OF EACH PART
        # impl below is bad maybe rewrite (possible with re.split + lookbehind magic)
        view = StringView(argument)
        buffer = ''
        args = []

        while not view.eof:
            view.skip_ws()
            if view.eof:
                break

            word = view.get_quoted_word()
            if word is None:
                break

            args.append(word)
