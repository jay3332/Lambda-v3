from __future__ import annotations

import json
import math
import random
from collections import defaultdict, OrderedDict
from typing import Any, NamedTuple, overload, TypeVar, TYPE_CHECKING

import discord
from discord.abc import Snowflake as HasId
from discord.ext.commands import BucketType, CooldownMapping

from .rank_card import RankCard
from app.util.types import (
    LevelingData as LevelingDataPayload,
    LevelingConfig as LevelingConfigPayload,
    Snowflake,
    StrSnowflake,
)

if TYPE_CHECKING:
    from discord import Guild, Member
    from app.core import Bot

    T = TypeVar('T', bound=int)

__all__ = (
    'LevelingManager',
    'LevelingConfig',
    'LevelingSpec',
    'LevelingStats',
)


class GainRange(NamedTuple):
    minimum: int
    maximum: int


class LevelingSpec(NamedTuple):
    """Represents the information of a leveling specification.

    These specifications are used to calculate levels and EXP requirements.
    """
    guild_id: Snowflake
    base: int
    factor: float
    gain: GainRange

    def level_requirement_for(self, level: int, /) -> int:
        return math.ceil(self.base * (level ** self.factor) / 10) * 10

    def get_xp_gain(self, multiplier: float = 1.0) -> int:
        return round(random.randint(*self.gain) * multiplier)


class CooldownManager:
    """Limits users from gaining XP by just spamming."""

    def __init__(self, guild_id: Snowflake, *, rate: int, per: float) -> None:
        self.guild_id: Snowflake = guild_id
        self.mapping: CooldownMapping = CooldownMapping.from_cooldown(rate, per, BucketType.user)

        self.rate: int = rate
        self.per: float = per

    def _is_ratelimited(self, message: discord.Message) -> bool:
        assert message.guild is not None
        current = message.created_at.timestamp()
        bucket = self.mapping.get_bucket(message, current=current)
        return bool(bucket.update_rate_limit(current))  # A cast to bool may not be necessary here

    def can_gain(self, message: discord.Message) -> bool:
        return not self._is_ratelimited(message)


class LevelingConfig:
    """Represents a leveling configuration for a guild."""

    def __init__(self, *, data: LevelingConfigPayload, bot: Bot) -> None:
        self._bot: Bot = bot
        self._from_data(data)

    def __repr__(self) -> str:
        return f'<LevelingConfig guild_id={self.guild_id} enabled={self.module_enabled}>'

    def _from_data(self, data: LevelingConfigPayload) -> None:
        self.guild_id: Snowflake = data['guild_id']
        self.module_enabled: bool = data['module_enabled']
        self.role_stack: bool = data['role_stack']

        gain = GainRange(data['min_gain'], data['max_gain'])
        self.spec: LevelingSpec = LevelingSpec(
            guild_id=self.guild_id,
            base=data['base'],
            factor=data['factor'],
            gain=gain,
        )

        self.cooldown_manager: CooldownManager = CooldownManager(
            self.guild_id,
            rate=data['cooldown_rate'],
            per=data['cooldown_per'],
        )

        self.level_up_message: str = data['level_up_message']
        self.level_up_channel: Snowflake | int = data['level_up_channel']

        self.blacklisted_roles: list[int] = data['bl_roles']
        self.blacklisted_channels: list[int] = data['bl_channels']
        self.blacklisted_users: list[int] = data['bl_users']

        def _(d: str) -> dict[Snowflake, int]:
            return self._sanitize_snowflakes(json.loads(d))

        self.level_roles: dict[Snowflake, int] = _(data['level_roles'])  # type: ignore
        self.multiplier_roles: dict[Snowflake, int] = _(data['multiplier_roles'])  # type: ignore
        self.multiplier_channels: dict[Snowflake, int] = _(data['multiplier_channels'])  # type: ignore
        self.reset_on_leave: bool = data['reset_on_leave']

    @staticmethod
    def _sanitize_snowflakes(mapping: dict[StrSnowflake, T]) -> dict[Snowflake, T]:
        return {int(k): v for k, v in mapping.items()}

    async def update(self, **kwargs: Any) -> None:
        if not len(kwargs):
            return

        kwargs = OrderedDict(kwargs)
        chunk = ', '.join(f'{key} = ${i}' for i, key in enumerate(kwargs, start=2))

        data = await self._bot.db.fetchrow(
            f'UPDATE level_config SET {chunk} WHERE guild_id = $1 RETURNING level_config.*;',
            self.guild_id, *kwargs.values()
        )
        self._from_data(data)  # type: ignore


class LevelingStats:
    """Represents statistics member's level and/or rank card."""

    def __init__(
        self,
        *,
        user: Member,
        bot: Bot,
        config: LevelingConfig,
        data: LevelingDataPayload | None = None,
        rank: int | None = None,
    ) -> None:
        self.user: Member = user
        self.guild: Guild = user.guild
        self.level_config: LevelingConfig = config
        self._bot: Bot = bot

        self.rank: int | None = None
        self.level: int | None = None
        self.xp: int | None = None

        if data is not None:
            self._from_data(data, rank=rank)

    def _from_data(self, data: LevelingDataPayload, *, rank: int | None = None) -> None:
        if rank is not None:
            self.rank = rank

        self.level = data['level']
        self.xp = data['xp']

    @property
    def max_xp(self) -> int:
        return self.level_config.spec.level_requirement_for(self.level + 1)

    async def send_level_up_message(self, level: int, channel: discord.TextChannel) -> None:
        if not self.level_config.level_up_channel or not self.level_config.level_up_message:
            return

        match self.level_config.level_up_channel:
            case 1:
                channel_id = channel.id
            case 2:
                dm_channel = await self.user.create_dm()
                channel_id = dm_channel.id
            case custom:
                channel_id = custom



    async def add_xp(self, xp: int, *, channel: discord.TextChannel) -> tuple[int, int]:
        await self.fetch_if_necessary()

        self.xp += xp

        if xp > 0 and self.xp > self.max_xp:
            while self.xp > self.max_xp:
                self.xp -= self.max_xp
                self.level += 1

            # TODO: Send level-up message here

        elif xp < 0:
            while self.xp < 0 and self.level >= 0:
                self.xp += self.max_xp
                self.level -= 1

        await self.update(level=self.level, xp=self.xp)
        return self.level, self.xp

    async def fetch(self) -> LevelingStats:
        data = await self._bot.db.get_leveling_stats(self.user.id, self.guild.id)
        self._from_data(data, rank=data['rank'])
        return self

    async def fetch_if_necessary(self) -> None:
        if self.level is None or self.xp is None:
            await self.fetch()

    @overload
    async def update(self, *, level: int, xp: int) -> None:
        ...

    async def update(self, **kwargs: Any) -> None:
        if not len(kwargs):
            return

        kwargs = OrderedDict(kwargs)
        chunk = ', '.join(f'{key} = ${i}' for i, key in enumerate(kwargs, start=2))

        data = await self._bot.db.fetchrow(
            f'UPDATE levels SET {chunk} WHERE guild_id = $1 AND user_id = $2 RETURNING levels.*',
            self.guild.id,
            *kwargs.values(),
        )
        self._from_data(data)  # type: ignore


class LevelingManager:
    """Manages Lambda's leveling system."""

    def __init__(self, *, bot: Bot) -> None:
        self.bot: Bot = bot
        self.configs: dict[Snowflake, LevelingConfig] = {}
        self.rank_cards: dict[Snowflake, RankCard] = {}
        self.stats: defaultdict[Snowflake, dict[Snowflake, LevelingStats]] = defaultdict(dict)

    async def _load_data(self) -> None:
        for entry in await self.bot.db.get_all_leveling_configurations():
            resolved = LevelingConfig(data=entry, bot=self.bot)
            self.configs[resolved.guild_id] = resolved

    def user_stats_for(self, member: Member, /) -> LevelingStats:
        try:
            return self.stats[member.guild.id][member.id]
        except KeyError:
            self.stats[member.guild.id][member.id] = res = LevelingStats(
                user=member,
                bot=self.bot,
                config=self.configs[member.guild.id],
            )
            return res

    async def fetch_guild_config(self, guild: HasId | int) -> LevelingConfig:
        if not isinstance(guild, int):
            guild = guild.id
        try:
            return self.configs[guild]
        except KeyError:
            data = await self.bot.db.get_level_config(guild)
            self.configs[guild] = res = LevelingConfig(data=data, bot=self.bot)
            return res

    async def fetch_rank_card(self, member: Member) -> RankCard:
        try:
            return self.rank_cards[member.id]
        except KeyError:
            data = await self.bot.db.get_rank_card(member.id)
            self.rank_cards[member.id] = res = RankCard(data=data, bot=self.bot, user=member)
            return res
