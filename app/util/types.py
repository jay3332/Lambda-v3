from __future__ import annotations

from typing import Any, AsyncIterable, Awaitable, Callable, ParamSpec, TypeAlias, TypeVar, TYPE_CHECKING

from discord import Interaction
from discord.ext import commands

if TYPE_CHECKING:
    from inspect import Parameter

    from discord import ClientUser, Guild, Member, Message, User, VoiceProtocol
    from discord.abc import Messageable
    from discord.interactions import InteractionChannel

    from app.core.bot import Bot
    from app.core.models import Command, Cog

P = ParamSpec('P')
R = TypeVar('R')


__all__ = (
    'TypedContext',
    'TypedInteraction',
    'AsyncCallable',
)

AsyncCallable: TypeAlias = Callable[P, Awaitable[R] | AsyncIterable[R]]


class TypedInteraction(Interaction):
    client: Bot
    channel: InteractionChannel


# noinspection PyPropertyDefinition
class _TypedContext:
    message: Message
    bot: Bot
    args: list[Any]
    kwargs: dict[str, Any]
    current_parameter: Parameter | None
    prefix: str
    command: Command
    invoked_with: str
    invoked_parents: list[str]
    invoked_subcommand: Command | None
    subcommand_passed: str
    command_failed: bool

    @property
    def valid(self) -> bool:
        ...

    @property
    def clean_prefix(self) -> str:
        ...

    @property
    def cog(self) -> Cog:
        ...

    @property
    def guild(self) -> Guild:
        ...

    @property
    def channel(self) -> Messageable:
        ...

    @property
    def me(self) -> Member | ClientUser:
        ...

    @property
    def author(self) -> Member | User:
        ...

    @property
    def voice_client(self) -> VoiceProtocol | None:
        ...


if TYPE_CHECKING:
    class TypedContext(_TypedContext):
        ...
else:
    class TypedContext(commands.Context[Bot]):
        interaction: TypedInteraction | None
