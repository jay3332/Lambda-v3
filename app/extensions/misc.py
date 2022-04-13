import discord

from app.core import Cog, Context, REPLY, command, cooldown
from app.util.ansi import AnsiStringBuilder, AnsiColor
from app.util.common import humanize_small_duration
from app.util.structures import Timer
from app.util.types import CommandResponse
from app.util.views import LinkView
from config import support_server, website


class Miscellaneous(Cog):
    """Miscellaneous commands that don't really belong in any other category."""

    emoji = '\U0001f44d'

    @staticmethod
    def _ping_metric(latency: float, bad: float, good: float) -> AnsiColor:
        if latency > bad:
            return AnsiColor.red

        if latency < good:
            return AnsiColor.green

        return AnsiColor.yellow

    @command(aliases={'pong', 'latency'}, hybrid=True)
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

    @command(aliases={'inv', 'i', 'link', 'addbot'}, hybrid=True)
    @cooldown(rate=2, per=3)
    async def invite(self, ctx: Context) -> CommandResponse:
        """Invite me to your server!"""
        url = discord.utils.oauth_url(
            client_id=ctx.bot.user.id,
            permissions=discord.Permissions(8),
            scopes=('bot', 'applications.commands'),
        )
        view = LinkView({
            'Invite me to your server': url,
            'Join the support server': support_server,
            'Website/Dashboard': website,
        })
        return 'You can right click on one of the buttons below to copy its link.', view, REPLY

    @command(aliases={'dash', 'dboard', 'website'}, hybrid=True)
    @cooldown(rate=2, per=3)
    async def dashboard(self, ctx: Context) -> CommandResponse:
        """Link to the dashboard."""
        view = LinkView({
            'Go to Dashboard': f'{website}/guild/{ctx.guild.id}',
            'Website': website,
        })
        return 'You can right click on one of the buttons below to copy its link.', view, REPLY
