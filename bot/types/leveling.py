from typing import TypedDict

from .common import Snowflake, StrSnowflake

__all__ = (
    'LevelingData',
    'LevelingConfig',
    'RankCard',
)


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
