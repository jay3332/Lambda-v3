from __future__ import annotations

from textwrap import dedent
from typing import Callable, TypeVar, TYPE_CHECKING

import discord
from discord.ext import commands

from app.core import Cog, Context, REPLY, command, cooldown, group
from app.features.leveling.core import LevelingManager
from app.util.types import CommandResponse
from config import Emojis

if TYPE_CHECKING:
    T = TypeVar('T')


def module_enabled() -> Callable[[T], T]:
    async def predicate(ctx: Context) -> bool:
        config = await ctx.cog.manager.fetch_guild_config(ctx.guild.id)  # type: ignore

        if not config.module_enabled:
            raise commands.CheckFailure(dedent(f"""
                You cannot run this command because the leveling module is currently disabled for this server.
                Please run `{ctx.clean_prefix}level-config module enable` to enable it.
            """))

        return True

    return commands.check(predicate)


class Leveling(Cog):
    """Interact with Lambda's robust and feature-packed leveling system."""

    if TYPE_CHECKING:
        manager: LevelingManager

    def __setup__(self) -> None:
        self.manager: LevelingManager = LevelingManager(bot=self.bot)

    @Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return

        record = await self.manager.fetch_guild_config(message.guild)
        if not record.module_enabled:
            return

        record = self.manager.user_stats_for(message.author)
        await record.fetch_if_necessary()
        await record.execute(message)

    @group(aliases=('lc', 'level-configuration', 'level-configs', 'leveling-config'))
    async def level_config(self, _ctx: Context) -> CommandResponse:
        """Commands for configuring the leveling module."""
        return 'WIP', REPLY

    @staticmethod
    def _enabled_text(toggle: bool) -> str:
        return f'{Emojis.enabled} Enabled' if toggle else f'{Emojis.disabled} Disabled'

    @level_config.command('module', aliases=('m', 'mod', 'toggle', 'status'), user_permissions=('manage_guild',))
    async def level_config_module(self, ctx: Context, toggle: bool = None) -> CommandResponse:
        """Toggles the leveling module on or off."""
        config = await self.manager.fetch_guild_config(ctx.guild.id)

        if toggle is None:
            return f'The leveling module is currently **{self._enabled_text(config.module_enabled)}**.'

        if toggle is not config.module_enabled:
            await config.update(module_enabled=toggle)

        return f'Leveling module now set to **{self._enabled_text(toggle)}**.'

    @command(aliases=('level', 'lvl', 'lv', 'xp', 'exp'))
    @cooldown(1, 5)
    @module_enabled()
    async def rank(self, ctx: Context, *, user: discord.Member = None) -> CommandResponse:
        """View your or another user's level, rank, XP.

        If you don't specify a user, your own level will be shown.

        Arguments:
        - `user`: The user to view the level of. Defaults to yourself.
        """
        user = user or ctx.author
        rank_card = await self.manager.fetch_rank_card(user)
        record = self.manager.user_stats_for(user)
        await record.fetch_if_necessary()

        result = await rank_card.render(rank=record.rank, level=record.level, xp=record.xp, max_xp=record.max_xp)
        return f'Rank card for **{user}**:', discord.File(result, filename=f'rank_card_{user.id}.png'), REPLY
