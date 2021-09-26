import discord
from discord.application_commands import ApplicationCommand as Command, option

from bot.types.subclasses import Interaction


class RankCard(Command, name='rank-card'):
    """Customize your rank card!"""


class RankCardBackground(Command, name='background', parent=RankCard):
    """Customize the background aspects of your rank card."""
    url: str = option(description="URL of the background image")
    blur: int = option(description="Blur intensity of the background image")
    color: str = option(description="Background color")
    opacity: float = option(description="Opacity of the background image")

    async def command_check(self, interaction: Interaction) -> bool:
        send = interaction.response.send_message

        if all(obj is None for obj in (self.url, self.blur, self.color, self.opacity)):
            await send('Must provide at least one option.', ephemeral=True)

        if blur is not None and not 0 <= blur <= 20:
            await send('Blur intensity must be between 0 and 20.', ephemeral=True)

        if opacity is not None and not 0 <= opacity <= 1:
            await send('Opacity must be between 0 and 1.', ephemeral=True)




class Rank(Command, name='rank'):
    """View yours or someone else's rank card! See your rank, level, and XP."""
    user: discord.Member = option(description="Who's rank card you want to see, defaults to your own.")

    async def command_check(self, interaction: Interaction) -> bool:
        config = await interaction.client.leveling.fetch_guild_config(interaction.guild_id)
        return bool(config)  # config.module_enabled

    async def callback(self, interaction: Interaction) -> None:
        user = self.user or interaction.user

        if isinstance(user, discord.User):
            return await interaction.response.send_message(
                'Could not resolve the user into a member in this server, try that again maybe?',
                ephemeral=True,
            )

        await interaction.response.defer()

        leveling = interaction.client.leveling
        stats = await leveling.user_stats_for(user).fetch()
        rank_card = await leveling.fetch_rank_card(user)

        fp = await rank_card.render(rank=stats.rank, level=stats.level, xp=stats.xp, max_xp=stats.max_xp)
        await interaction.followup.send(
            content=f"Rank Card for **{user.name}**:",
            file=discord.File(fp, filename=f'rank-card-{user.id}.png'),
        )


__commands__ = {
    Rank,
}

__guild_id__ = 728341827022749797
