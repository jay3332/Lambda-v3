from __future__ import annotations

import asyncio
import json
import math
import random
from collections import OrderedDict, defaultdict
from typing import Any, Iterable, NamedTuple, TYPE_CHECKING, TypeVar, overload

import discord
from discord.abc import Snowflake as HasId
from discord.ext.commands import BucketType, CooldownMapping

from app.util.tags import execute_tags
from .rank_card import RankCard

if TYPE_CHECKING:
    from discord import Guild, Member, User

    from app.core import Bot
    from app.util.types import (
        LevelingData as LevelingDataPayload,
        LevelingConfig as LevelingConfigPayload,
        Snowflake,
        StrSnowflake,
    )

    T = TypeVar('T', bound=int)

__all__ = (
    'LevelingManager',
    'LevelingConfig',
    'LevelingSpec',
    'LevelingRecord',
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

    def _is_ratelimited(self, message: discord.Message) -> float | None:
        assert message.guild is not None
        current = message.created_at.timestamp()
        bucket = self.mapping.get_bucket(message, current=current)
        return bucket.update_rate_limit(current)

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
        self.special_level_up_messages: dict[int, str] = {
            int(k): v for k, v in json.loads(data['special_level_up_messages']).items()  # type: ignore
        }
        self.level_up_channel: Snowflake | int = data['level_up_channel']

        self.blacklisted_roles: list[int] = data['blacklisted_roles']
        self.blacklisted_channels: list[int] = data['blacklisted_channels']
        self.blacklisted_users: list[int] = data['blacklisted_users']

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
        def normalize(i: int, key: str, value: Any) -> tuple[str, Any]:
            if isinstance(value, dict):
                return f'{key} = ${i}::JSONB', json.dumps(value)
            return f'{key} = ${i}', value

        if not len(kwargs):
            return

        kwargs = OrderedDict(normalize(i, key, value) for i, (key, value) in enumerate(kwargs.items(), start=2))
        chunk = ', '.join(kwargs)
        data = await self._bot.db.fetchrow(
            f'UPDATE level_config SET {chunk} WHERE guild_id = $1 RETURNING level_config.*;',
            self.guild_id,
            *kwargs.values(),
        )
        self._from_data(data)  # type: ignore


class LevelingRecord:
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

    def is_ratelimited(self, message: discord.Message) -> bool:
        return self.level_config.cooldown_manager.can_gain(message)

    def can_gain(self, message: discord.Message) -> bool:
        config = self.level_config
        return (
            config.module_enabled
            and self.user.id not in config.blacklisted_users
            and message.channel.id not in config.blacklisted_channels
            and not any(self.user._roles.has(role) for role in config.blacklisted_roles)
            and not self.is_ratelimited(message)
        )

    def get_multiplier(self, message: discord.Message) -> float:
        multiplier = 1.0

        for role_id, multi in self.level_config.multiplier_roles.items():
            if self.user._roles.has(role_id):
                multiplier += multi

        if message.channel.id in self.level_config.multiplier_channels:
            multiplier += self.level_config.multiplier_channels[message.channel.id]

        return multiplier

    async def execute(self, message: discord.Message) -> None:
        if not self.can_gain(message):
            return

        multiplier = self.get_multiplier(message)
        gain = self.level_config.spec.get_xp_gain(multiplier)
        await self.add_xp(gain, message=message)

    # noinspection PyTypeChecker
    async def send_level_up_message(self, level: int, message: discord.Message) -> None:
        if not self.level_config.level_up_channel or not self.level_config.level_up_message:
            return

        match self.level_config.level_up_channel:
            case 1:
                channel_id = message.channel.id
            case 2:
                dm_channel = await self.user.create_dm()
                channel_id = dm_channel.id
            case custom:
                channel_id = custom

        content = self.level_config.special_level_up_messages.get(
            level,
            self.level_config.level_up_message,
        )
        await execute_tags(bot=self._bot, message=message, channel=channel_id, content=content)

    async def update_roles(self, level: int) -> None:
        roles = self.level_config.level_roles
        if not roles:
            return

        stack = self.level_config.role_stack

        self._bot.log.critical(repr(roles))
        good_roles = [(k, v) for k, v in roles.items() if level >= v]
        if not stack:
            good_roles = max(good_roles, key=lambda r: r[1]),

        good_roles = {k for k, v in good_roles}
        old_roles = set(self.user._roles)

        new = old_roles | good_roles
        new.difference_update(role for role in roles if role not in good_roles)

        if new == self.user.roles:
            return

        reason = f'Advance to level {level:,}'
        try:
            await self.user.edit(roles=list(map(discord.Object, new)), reason=reason)
        except discord.HTTPException:
            pass

    async def add_xp(self, xp: int, *, message: discord.Message) -> tuple[int, int]:
        await self.fetch_if_necessary()

        self.xp += xp
        tasks = []

        if xp > 0 and self.xp > self.max_xp:
            while self.xp > self.max_xp:
                self.xp -= self.max_xp
                self.level += 1

            tasks.extend((
                self.send_level_up_message(self.level, message),
                self.update_roles(self.level),
            ))

        elif xp < 0:
            while self.xp < 0 and self.level >= 0:
                self.xp += self.max_xp
                self.level -= 1

        await self.update(level=self.level, xp=self.xp)
        await asyncio.gather(*tasks)

        return self.level, self.xp

    async def fetch(self) -> LevelingRecord:
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
        chunk = ', '.join(f'{key} = ${i}' for i, key in enumerate(kwargs, start=3))

        data = await self._bot.db.fetchrow(
            f'UPDATE levels SET {chunk} WHERE guild_id = $1 AND user_id = $2 RETURNING levels.*',
            self.guild.id,
            self.user.id,
            *kwargs.values(),
        )
        self._from_data(data)  # type: ignore


class LevelingManager:
    """Manages Lambda's leveling system."""

    def __init__(self, *, bot: Bot) -> None:
        self.bot: Bot = bot
        self.configs: dict[Snowflake, LevelingConfig] = {}
        self.rank_cards: dict[Snowflake, RankCard] = {}
        self.stats: defaultdict[Snowflake, dict[Snowflake, LevelingRecord]] = defaultdict(dict)
        self._cached: set[Snowflake] = set()

    async def _load_data(self) -> None:
        for entry in await self.bot.db.get_all_leveling_configurations():
            resolved = LevelingConfig(data=entry, bot=self.bot)
            self.configs[resolved.guild_id] = resolved

    def user_stats_for(self, member: Member, /) -> LevelingRecord:
        try:
            return self.stats[member.guild.id][member.id]
        except KeyError:
            self.stats[member.guild.id][member.id] = res = LevelingRecord(
                user=member,
                bot=self.bot,
                config=self.configs[member.guild.id],
            )
            return res

    async def ensure_cached_user_stats(self, guild: discord.Guild) -> None:
        if guild.id in self._cached:
            return

        self._cached.add(guild.id)
        data = await self.bot.db.get_all_leveling_stats(guild.id)
        for user_id, entry in data.items():
            member = guild.get_member(user_id)
            if not member:
                continue

            self.stats[guild.id][user_id] = LevelingRecord(
                user=member,
                bot=self.bot,
                config=self.configs[guild.id],
            )

    def walk_stats(self, guild: HasId | int) -> Iterable[LevelingRecord]:
        if not isinstance(guild, int):
            guild = guild.id

        return self.stats[guild].values()

    async def fetch_guild_config(self, guild: HasId | int) -> LevelingConfig:
        if not isinstance(guild, int):
            guild = guild.id
        try:
            return self.configs[guild]
        except KeyError:
            data = await self.bot.db.get_level_config(guild)
            self.configs[guild] = res = LevelingConfig(data=data, bot=self.bot)
            return res

    async def fetch_rank_card(self, member: Member | User) -> RankCard:
        try:
            return self.rank_cards[member.id]
        except KeyError:
            data = await self.bot.db.get_rank_card(member.id)
            self.rank_cards[member.id] = res = RankCard(data=data, bot=self.bot, user=member)
            return res
