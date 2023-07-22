from __future__ import annotations

import inspect
import re
from functools import wraps
from typing import Any, AsyncIterable, Awaitable, Callable, Iterable, ParamSpec, TYPE_CHECKING, TypeAlias, TypeVar

import discord
from discord.ext import commands

from app.core.flags import FlagMeta
from app.core.models import Command, GroupCommand, HybridCommand, HybridGroupCommand
from app.util.ansi import AnsiStringBuilder
from app.util.common import sentinel
from app.util.pagination import Paginator

if TYPE_CHECKING:
    from app.core.models import Context, Cog
    from app.util.types import AsyncCallable

    P = ParamSpec('P')
    R = TypeVar('R')
    AsyncCallableDecorator: TypeAlias = Callable[[AsyncCallable[P, R]], AsyncCallable[P, R]]

__all__ = (
    'REPLY',
    'EDIT',
    'BAD_ARGUMENT',
    'MISSING',
    'ERROR',
    'Param',
    'easy_command_callback',
    'command',
    'group',
    'cooldown',
)

EDIT  = sentinel('EDIT', repr='EDIT')
REPLY = sentinel('REPLY', repr='REPLY')
BAD_ARGUMENT = sentinel('BAD_ARGUMENT', repr='BAD_ARGUMENT')
ERROR = sentinel('ERROR', repr='ERROR')

MISSING: Any = sentinel('MISSING', bool=False, repr='MISSING')  # type: ignore

HYPERLINK_REGEX: re.Pattern[str] = re.compile(r'(?<!\\)\[([^]]+)]\([^)]+\)')


class Param:
    """Used to change the current_parameter value in a BadArgument call."""

    def __init__(self, name: str, *, flag: bool = False) -> None:
        self.name: str = name
        self.flag: bool = flag


class GenericCommandError(commands.BadArgument):
    pass


async def process_message(ctx: Context, payload: Any) -> discord.Message | None:
    # sourcery no-metrics
    if payload is None:
        return

    kwargs: dict[str, Any] = {}
    kwargs.setdefault('embeds', [])
    kwargs.setdefault('files', [])

    if not isinstance(payload, (tuple, list, set)):
        payload = [payload]

    paginator = None
    bad_argument = False

    for part in payload:
        if part is REPLY:
            kwargs['reference'] = ctx.message

        elif part is EDIT:
            kwargs['edit'] = True

        elif part is BAD_ARGUMENT:
            bad_argument = True

        elif part is ERROR:
            raise GenericCommandError(kwargs['content'])

        elif isinstance(part, Param):
            ctx.current_parameter = (
                discord.utils.find(
                    lambda v: isinstance(v.annotation, FlagMeta),
                    ctx.command.params.values(),
                )
                if part.flag
                else ctx.command.params[part.name]
            )

        elif isinstance(part, discord.Embed):
            kwargs['embeds'].append(part)

        elif isinstance(part, discord.File):
            kwargs['files'].append(part)

        elif isinstance(part, discord.ui.View):
            kwargs['view'] = part

        elif isinstance(part, Paginator):
            paginator = part

        elif isinstance(part, AnsiStringBuilder):
            result = part.ensure_codeblock().dynamic(ctx)
            if not kwargs.get('content'):
                kwargs['content'] = result
            else:
                kwargs['content'] += '\n' + result

        elif isinstance(part, dict):
            kwargs.update(part)

        elif part is None:
            continue

        else:
            kwargs['content'] = str(part)

    if kwargs.get('content') and not ctx.interaction:
        kwargs['content'] = HYPERLINK_REGEX.sub(r'\1', kwargs['content'])

    if bad_argument:
        raise commands.BadArgument(kwargs['content'])

    if paginator:
        return await paginator.start(**kwargs)

    return await ctx.send(**kwargs)


async def _walk_callback(ctx: Context, coro: Awaitable[Any] | AsyncIterable[Any]) -> None:
    if inspect.isasyncgen(coro):
        async for payload in coro:
            await process_message(ctx, payload)
    else:
        await process_message(ctx, await coro)


def easy_command_callback(func: AsyncCallable[..., Any]) -> AsyncCallable[..., Any]:
    @wraps(func)
    async def wrapper(cog: Cog, ctx: Context, /, *args, **kwargs) -> None:
        await _walk_callback(ctx, func(cog, ctx, *args, **kwargs))

    return wrapper


def _no_cog_easy_command_callback(func: AsyncCallable[..., Any]) -> AsyncCallable[..., Any]:
    @wraps(func)
    async def wrapper(ctx: Context, /, *args, **kwargs) -> None:
        await _walk_callback(ctx, func(ctx, *args, **kwargs))

    return wrapper


# noinspection PyShadowingBuiltins
def _resolve_command_kwargs(
    cls: type,
    *,
    name: str = MISSING,
    alias: str = MISSING,
    aliases: Iterable[str] = MISSING,
    usage: str = MISSING,
    brief: str = MISSING,
    help: str = MISSING,
) -> dict[str, Any]:  # sourcery skip: compare-via-equals
    kwargs = {'cls': cls}

    if name is not MISSING:
        kwargs['name'] = name

    if alias is not MISSING and aliases is not MISSING:
        raise TypeError('cannot have alias and aliases kwarg filled')

    if alias is not MISSING:
        kwargs['aliases'] = (alias,)

    if aliases is not MISSING:
        kwargs['aliases'] = tuple(aliases)

    if usage is not MISSING:
        kwargs['usage'] = usage

    if brief is not MISSING:
        kwargs['brief'] = brief

    if help is not MISSING:
        kwargs['help'] = help

    return kwargs


# noinspection PyShadowingBuiltins
def command(
    name: str = MISSING,
    *,
    alias: str = MISSING,
    aliases: Iterable[str] = MISSING,
    usage: str = MISSING,
    brief: str = MISSING,
    help: str = MISSING,
    easy_callback: bool | AsyncCallableDecorator = True,
    hybrid: bool = False,
    **other_kwargs: Any,
) -> Callable[..., Command]:
    kwargs = _resolve_command_kwargs(
        HybridCommand if hybrid else Command,
        name=name, alias=alias, aliases=aliases, brief=brief, help=help, usage=usage,
    )
    result = commands.command(**kwargs, **other_kwargs)

    if easy_callback:
        if easy_callback is True:
            easy_callback = easy_command_callback

        return lambda func: result(easy_callback(func))

    return result


def dynamic_command(**kwargs: Any) -> Callable[..., Command]:
    return command(**kwargs, easy_callback=_no_cog_easy_command_callback)


# noinspection PyShadowingBuiltins
def group(
    name: str = MISSING,
    *,
    alias: str = MISSING,
    aliases: Iterable[str] = MISSING,
    usage: str = MISSING,
    brief: str = MISSING,
    help: str = MISSING,
    easy_callback: bool = True,
    hybrid: bool = False,
    iwc: bool = True,
    **other_kwargs: Any,
) -> Callable[..., GroupCommand]:
    kwargs = _resolve_command_kwargs(
        HybridGroupCommand if hybrid else GroupCommand,
        name=name, alias=alias, aliases=aliases, brief=brief, help=help, usage=usage,
    )
    kwargs['invoke_without_command'] = iwc
    result = commands.group(**kwargs, **other_kwargs)

    if easy_callback:
        return lambda func: result(easy_command_callback(func))

    return result


def cooldown(rate: int, per: float, bucket: commands.BucketType = commands.BucketType.user) -> AsyncCallableDecorator:
    return commands.cooldown(rate, per, bucket)


def user_max_concurrency(count: int, *, wait: bool = False) -> AsyncCallableDecorator:
    return commands.max_concurrency(count, commands.BucketType.user, wait=wait)


def guild_max_concurrency(count: int, *, wait: bool = False) -> AsyncCallableDecorator:
    return commands.max_concurrency(count, commands.BucketType.guild, wait=wait)
