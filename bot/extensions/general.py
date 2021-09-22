from __future__ import annotations

import discord
from discord.application_commands import ApplicationCommand as Command


class Ping(Command, name='ping'):
    """Pong! View the bot's latency."""
    async def callback(self, interaction: discord.Interaction) -> None:
        latency = interaction.client.latency * 1000
        await interaction.response.send_message(f'Pong, {latency:.2f} ms', ephemeral=True)


__commands__ = {
    Ping,
}

__guild_id__ = 728341827022749797
