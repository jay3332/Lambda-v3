from __future__ import annotations

import inspect
from typing import Any, AsyncGenerator, TYPE_CHECKING

import discord
from discord.application_commands import ApplicationCommand as Command, ApplicationCommandOptionChoice, option

from bot.constants import Colors
from bot.utils.common import human_short_delta, limit_generator
from bot.utils.structures import Timer
from bot.types.subclasses import Interaction

if TYPE_CHECKING:
    SourceCommandAutocompleteGenerator = AsyncGenerator[Any, Any, ApplicationCommandOptionChoice]


class Ping(Command, name='ping'):
    """Pong! View the bot's latency."""

    @staticmethod
    def _format(delta: float) -> str:
        return f'```py\n{human_short_delta(delta)}```'

    async def callback(self, interaction: Interaction) -> None:
        ws_latency = interaction.client.latency

        timer = Timer()
        with timer:
            await interaction.response.defer(ephemeral=True)

        http_latency = timer.elapsed

        with timer:
            await interaction.client.db.execute('SELECT 1')

        database_latency = timer.elapsed
        round_trip = ws_latency + http_latency + database_latency

        embed = discord.Embed(timestamp=interaction.created_at)
        embed.set_author(name='Latency', icon_url=interaction.client.user.avatar.url)

        _ = self._format
        embed.add_field(name='**Websocket**', value=_(ws_latency))
        embed.add_field(name='**Response**', value=_(http_latency))
        embed.add_field(name='**Database**', value=_(database_latency))
        embed.add_field(name='**Round Trip**', value=_(round_trip))

        embed.colour = Colors.ERROR if round_trip > 0.7 else Colors.DEFAULT
        await interaction.followup.send(embed=embed)


class Source(Command, name='source'):
    """View source code for a command"""
    command: str = option(description='The command to view source code for', required=True)
    public: bool = option(description='Whether or not to show the result to the public', default=False)

    @command.autocomplete
    @limit_generator(25)
    async def command_autocomplete(self, interaction: Interaction) -> SourceCommandAutocompleteGenerator:
        query = interaction.value.casefold()

        # Should probably make this more elegant by modifiying the actual library
        for id, command in interaction._state._application_commands_store.commands.items():
            if command.__application_command_name__.startswith(query):
                # RESTful API doesn't support snowflakes as integers, cast it to str
                yield ApplicationCommandOptionChoice(name=command.__application_command_name__, value=str(id))

    async def callback(self, interaction: Interaction) -> None:
        try:
            command = interaction._state._application_commands_store.commands[int(self.command)]
        except ValueError:
            return await interaction.response.send_message(
                'Discord sent an invalid response back, try that again', ephemeral=True
            )

        source = inspect.getsource(command).replace('```', '`\u200b``')
        formatted = f'```py\n{source}```'

        if len(formatted) < 4000:
            return await interaction.response.send_message(formatted, ephemeral=not self.public)

        await interaction.response.send_message('Source code too large', ephemeral=not self.public)


__commands__ = {
    Ping,
    Source,
}

__guild_id__ = 728341827022749797
