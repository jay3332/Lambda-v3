import discord

from app.core import Cog, Context, REPLY, command, cooldown
from app.util.ansi import AnsiStringBuilder, AnsiColor
from app.util.common import humanize_small_duration
from app.util.structures import Timer
from app.util.types import CommandResponse
from config import support_server


class Miscellaneous(Cog):
    """Miscellaneous commands that don't really belong in any other category."""

    @staticmethod
    def _ping_metric(latency: float, bad: float, good: float) -> AnsiColor:
        if latency > bad:
            return AnsiColor.red

        if latency < good:
            return AnsiColor.green

        return AnsiColor.yellow

    @command(aliases={'pong', 'latency'})
    @cooldown(rate=2, per=3)
    async def ping(self, ctx: Context) -> CommandResponse:
        """Pong! Sends detailed information about the bot's latency."""
        with Timer() as api:
            await ctx.trigger_typing()

        with Timer() as database:
            await ctx.db.execute('SELECT 1')

        api = api.elapsed
        database = database.elapsed
        ws = ctx.bot.latency

        round_trip = api + database + ws
        result = AnsiStringBuilder()

        result.append("Pong! ", color=AnsiColor.white, bold=True)
        result.append(humanize_small_duration(round_trip) + ' ', color=self._ping_metric(round_trip, 1, 0.4), bold=True)
        result.append("(Round-trip)", color=AnsiColor.gray).newline(2)

        result.append("API:      ", color=AnsiColor.gray)
        result.append(humanize_small_duration(api), color=self._ping_metric(api, 0.7, 0.3), bold=True).newline()

        result.append("Gateway:  ", color=AnsiColor.gray)
        result.append(humanize_small_duration(ws), color=self._ping_metric(ws, 0.25, 0.1), bold=True).newline()

        result.append("Database: ", color=AnsiColor.gray)
        result.append(humanize_small_duration(database), color=self._ping_metric(database, 0.25, 0.1), bold=True)

        return result, REPLY

    @command(aliases={'inv', 'i', 'link', 'addbot'})
    @cooldown(rate=2, per=3)
    async def invite(self, ctx: Context) -> CommandResponse:
        """Invite me to your server!"""
        url = discord.utils.oauth_url(
            client_id=ctx.bot.user.id,
            permissions=discord.Permissions(8),
            scopes=('bot', 'applications.commands'),
        )
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label='Invite me to your server', url=url))
        view.add_item(discord.ui.Button(label='Join the support server', url=support_server))

        return 'You can right click on one of the buttons below to copy the link.', view, REPLY
