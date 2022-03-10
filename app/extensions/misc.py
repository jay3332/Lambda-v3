from app.core import Cog, Context, command
from app.util.types import CommandResponse


class Miscellaneous(Cog):
    """Miscellaneous commands that don't really belong in any other category."""

    @command(aliases={'pong', 'latency'})
    async def ping(self, _ctx: Context) -> CommandResponse:
        """Pong! Sends detailed information about the bot's latency."""
        return 'pong'
