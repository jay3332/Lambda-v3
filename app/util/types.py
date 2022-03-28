from __future__ import annotations

from typing import (
    Any,
    AsyncGenerator,
    AsyncIterable,
    Awaitable,
    Callable,
    ParamSpec,
    TypeAlias,
    TypedDict,
    TypeVar,
    TYPE_CHECKING,
)

from discord import Embed, File, Interaction
from discord.ext import commands
from discord.ui import View

from app.util.ansi import AnsiStringBuilder
from app.util.common import SentinelConstant
from app.util.pagination import Paginator

if TYPE_CHECKING:
    from inspect import Parameter

    from discord import ClientUser, Guild, Member, Message, User, VoiceProtocol
    from discord.abc import Messageable
    from discord.interactions import InteractionChannel

    from app.core.bot import Bot
    from app.core.helpers import Param
    from app.core.models import Command, Cog

P = ParamSpec('P')
R = TypeVar('R')
ConstantT = TypeVar('ConstantT', bound=SentinelConstant)


__all__ = (
    'TypedContext',
    'TypedInteraction',
    'AsyncCallable',
    'CommandResponse',
    'OptionalCommandResponse',
    'LevelingData',
    'LevelingConfig',
    'RankCard',
    'RGBColor',
    'RGBAColor',
    'AnyColor',
    'JsonObject',
    'JsonValue',
)

AsyncCallable: TypeAlias = Callable[P, Awaitable[R] | AsyncIterable[R]]

CommandResponseFragment: TypeAlias = (
    str | Embed | File | Paginator | View | dict[str, Any] | ConstantT | AnsiStringBuilder | 'Param'
)
SingleCommandResponse: TypeAlias = CommandResponseFragment | tuple[CommandResponseFragment, ...]
CommandResponse: TypeAlias = SingleCommandResponse | AsyncGenerator[SingleCommandResponse, Any]
OptionalCommandResponse: TypeAlias = CommandResponse | None

JsonValue: TypeAlias = str | int | float | bool | dict[str, 'JsonValue'] | list['JsonValue'] | None
JsonObject: TypeAlias = dict[str, JsonValue] | list[JsonValue]

Snowflake: TypeAlias = int
StrSnowflake: TypeAlias = str

RGBColor: TypeAlias = tuple[int, int, int]
RGBAColor: TypeAlias = tuple[int, int, int, int]
AnyColor: TypeAlias = RGBColor | RGBAColor | str


class _LevelingDataOptional(TypedDict, total=False):
    rank: int


class LevelingData(_LevelingDataOptional):
    guild_id: Snowflake
    user_id: Snowflake
    level: int  # default: 0
    xp: int  # default: 0


class LevelingConfig(TypedDict):
    guild_id: Snowflake
    module_enabled: bool  # default: False
    role_stack: bool  # default: True
    base: int  # default: 100
    factor: float  # default: 1.3
    min_gain: int  # default: 8
    max_gain: int  # default: 15
    cooldown_rate: int  # default: 1
    cooldown_per: int  # default: 40
    level_up_message: str  # has default
    special_level_up_messages: dict[str, str]  # default: {} - keys are integers cast to str
    level_up_channel: Snowflake | int  # default: 1
    blacklisted_roles: list[int]  # default: []
    blacklisted_channels: list[int]  # default: []
    blacklisted_users: list[int]  # default: []
    level_roles: dict[StrSnowflake, int]  # default: {}
    multiplier_roles: dict[StrSnowflake, int]  # default: {}
    multiplier_channels: dict[StrSnowflake, int]  # default: {}
    reset_on_leave: bool  # default: False


class RankCard(TypedDict):
    user_id: Snowflake
    background_color: int  # default: 1644825
    background_url: str | None
    background_blur: int  # default: 0
    background_alpha: float  # default: 1.0
    font: int  # default: 0
    primary_color: int  # default: 12434877
    secondary_color: int  # default: 9671571
    tertiary_color: int  # default: 7064552
    overlay_color: int  # default: 15988735
    overlay_alpha: float  # default: 0.15
    overlay_border_radius: int  # default: 52
    avatar_border_color: int  # default: 16777215
    avatar_border_alpha: float  # default: 0.09
    avatar_border_radius: int  # default: 103
    progress_bar_color: int  # default: 16777215
    progress_bar_alpha: float  # default: 0.16


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
    class TypedContext(commands.Context['Bot']):
        interaction: TypedInteraction | None
