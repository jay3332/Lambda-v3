from __future__ import annotations

from datetime import timedelta
from typing import Literal, TYPE_CHECKING

import discord
from discord.ext import commands

from app.core import Cog, Context, Flags, command, cooldown, store_true
from app.core.helpers import BAD_ARGUMENT
from app.util.common import humanize_duration
from app.util.converters import IntervalConverter

if TYPE_CHECKING:
    from app.core.timers import Timer
    from app.util.types import CommandResponse

__all__ = ('Management',)


class SlowmodeFlags(Flags):
    silent: bool = store_true(short='s', alias='quiet')
    reset_after: IntervalConverter = store_true(
        name='reset-after',
        short='r',
        aliases=('reset', 'temporary', 'temp', 'tmp'),
    )


class Management(Cog):
    """Commands that aid in managing your server and channels."""

    async def cog_check(self, ctx: Context) -> bool:
        return ctx.guild is not None

    @command('slowmode', aliases=('slow', 'sm'), user_permissions=('manage_channels',))
    @cooldown(3, 3, commands.BucketType.member)
    async def slowmode(
        self,
        ctx: Context,
        channel: discord.TextChannel | None,
        *,
        interval: IntervalConverter | int | Literal['off'],
        flags: SlowmodeFlags,
    ) -> CommandResponse | None:
        """Changes the slowmode interval of a channel.

        Arguments:
        - `channel`: The channel to change the slowmode interval of. Defaults to the current channel.
        - `interval`: The interval to set the channel slowmode to.
          Can be a number of seconds, a time interval such as "30 minutes", or "off" to disable slowmode.
          Must be between 0 (off) and 6 hours (21600 seconds).

        Flags:
        - `--silent`: Do not send a public message confirming the change.
        - `--reset-after <duration>`: Automatically reset the slowmode interval after a certain amount of time.
          This is used for temporary slowmodes.
        """
        channel = channel or ctx.channel
        if isinstance(interval, timedelta):
            interval = int(interval.total_seconds())
        elif interval == 'off':
            interval = 0

        if interval < 0:
            return 'Interval cannot be negative. Set the interval to "off" or "0" to remove slowmode.', BAD_ARGUMENT
        elif interval > 21600:
            return 'Interval cannot be longer than 6 hours (21600 seconds).', BAD_ARGUMENT

        await channel.edit(
            slowmode_delay=interval,
            reason=f'Slowmode interval changed by {ctx.author} ({ctx.author.id}).',
        )
        ctx.bot.loop.create_task(ctx.thumbs())

        message = f'Slowmode interval of {channel.name} set to **{humanize_duration(interval)}**.'

        if reset_after := flags.reset_after:
            await ctx.bot.timers.create(when=reset_after, event='reset_slowmode', channel_id=channel.id)
            message += f'\nSlowmode interval will be reset after **{humanize_duration(reset_after)}**.'

        if flags.silent:
            if ctx.is_interaction:
                return message, dict(ephemeral=True)
            return
        return message

    @Cog.listener()
    async def on_reset_slowmode_timer_complete(self, timer: Timer) -> None:
        try:
            await self.bot.http.edit_channel(
                timer.metadata['channel_id'],
                reason='Temporary slowmode reset',
                rate_limit_per_user=0,
            )
        except discord.HTTPException:
            pass
