from typing import TypedDict

from .common import Snowflake, StrSnowflake

__all__ = (
    'LevelingData',
    'LevelingConfig',
)


class LevelingData(TypedDict):
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
    level_up_channel: Snowflake | int  # default: 1
    bl_roles: list[int]  # default: []
    bl_channels: list[int]  # default: []
    bl_users: list[int]  # default: []
    level_roles: dict[StrSnowflake, int]  # default: {}
    multiplier_roles: dict[StrSnowflake, int]  # default: {}
    multiplier_channels: dict[StrSnowflake, int]  # default: {}
    reset_on_leave: bool  # default: False

# LevelingConfig.level_up_channel is either a channel's ID, or:
# 0 for disabled, 1 for source channel, or 2 for DM
