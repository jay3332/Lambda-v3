from __future__ import annotations

import json
import math

from discord.ext.commands import BucketType, CooldownMapping

from bot.types.common import Snowflake, StrSnowflake
from bot.types.leveling import (
    LevelingData as LevelingDataPayload,
    LevelingConfig as LevelingConfigPayload,
)

from typing import NamedTuple, TypeVar, TYPE_CHECKING

if TYPE_CHECKING:
    from bot import Lambda

    T = TypeVar('T', bound=int)


class GainRange(NamedTuple):
    minimum: int
    maximum: int


class LevelingSpec(NamedTuple):
    """Represents the information of a leveling specification."""
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

    def __init__(self, *, data: LevelingConfigPayload, bot: Lambda) -> None:
        self._bot: Lambda = bot
        self._from_data(data)

    def _from_data(self, data: LevelingConfigPayload) -> None:
        self.guild_id: Snowflake = data['guild_id']
        self.module_enabled: bool = data['module_enabled']
        self.role_stack: bool = data['role_stack']

        gain = GainRange(data['min_gain'], data['max_gain'])
        self.spec: LevelingSpec = LevelingSpec(
            guild_id=guild_id,
            base=data['base'],
            factor=data['factor'],
            gain=gain,
        )

        self.cooldown_manager: CooldownManager = CooldownManager(
            guild_id,
            rate=data['cooldown_rate'],
            per=data['cooldown_per']
        )

        self.level_up_message: str = data['level_up_message']
        self.level_up_channel: Snowflake | int = data['level_up_channel']

        self.blacklisted_roles: list[int] = data['bl_roles']
        self.blacklisted_channels: list[int] = data['bl_channels']
        self.blacklisted_users: list[int] = data['bl_users']

        def _(d: str) -> dict[Snowflake, int]:
            return self._sanitize_snowflakes(json.loads(d))

        self.level_roles: dict[Snowflake, int] = _(data['level_roles'])
        self.multiplier_roles: dict[Snowflake, int] = _(data['multiplier_roles'])
        self.multiplier_channels: dict[Snowflake, int] = _(data['multiplier_channels'])
        self.reset_on_leave: bool = data['reset_on_leave']

    def _sanitize_snowflakes(self, mapping: dict[StrSnowflake, T]) -> dict[Snowflake, T]:
        return {int(k): v for k, v in mapping.items()}

    async def edit(self, **kwargs) -> None:
        if not len(kwargs):
            return

        chunk = ', '.join(f'{key} = ${i}' for i, key in enumerate(kwargs, start=2))

        data = await self._bot.db.fetchrow(
            f'UPDATE level_config SET {chunk} WHERE guild_id=$1 RETURNING level_config.*;',
            self.guild_id, *kwargs.values()
        )
        self._from_data(data)


class LevelingManager:
    """Manages Lambda's leveling system."""

    def __init__(self, bot: Lambda) -> None:
        self.bot: Lambda = bot
        self.configs: dict[Snowflake, LevelingConfig] = {}

    async def _load_data(self) -> None:
        for entry in await self.bot.db.get_leveling_configurations():
            resolved = LevelingConfig(data=entry, bot=self.bot)
            self.configs[resolved.guild_id] = resolved
