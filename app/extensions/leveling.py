from __future__ import annotations

from difflib import SequenceMatcher
from enum import Enum
from io import BytesIO
from textwrap import dedent
from typing import Any, Callable, Coroutine, TypeVar, TYPE_CHECKING

import discord
from discord.ext import commands

from app.core import Cog, Context, Flags, REPLY, command, cooldown, flag, group, store_true
from app.core.helpers import user_max_concurrency
from app.features.leveling.core import LevelingManager
from app.features.leveling.rank_card import Font as RankCardFont, RankCard
from app.util import AnsiColor, AnsiStringBuilder, UserView, converter
from app.util.common import progress_bar
from app.util.image import ImageFinder
from app.util.types import CommandResponse
from config import Colors, Emojis

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


class RankCardFlags(Flags):
    embed: bool = store_true(short='e')
    my_card: bool = store_true(name='my-card', aliases=('my-rank-card', 'mrc', 'mc'), short='m')


@converter
async def RankCardFontConverter(_, argument: str) -> RankCardFont:
    try:
        return RankCardFont[argument := argument.upper()]
    except KeyError:
        pass

    for name, font in RankCardFont._member_map_.items():
        font: RankCardFont
        matcher = SequenceMatcher(None, name, argument)
        if matcher.ratio() > 0.85:
            return font

    raise commands.BadArgument(
        f'{argument!r} is not a valid rank card font. Choices: {", ".join(RankCardFont._member_names_)}',
    )


@converter
async def ImageUrlConverter(ctx: Context, argument: str) -> str:
    if argument.startswith('attachment://'):
        filename = argument[13:]
        argument = discord.utils.get(ctx.message.attachments, url=filename)
        if not argument:
            raise commands.BadArgument(f'attachment {filename!r} not found')

    elif not argument.startswith(('https://', 'http://')):
        raise commands.BadArgument('image url must start with https:// or http://')

    finder = ImageFinder(max_size=3 * 1024 * 1024)  # 3 MB
    result = await finder.sanitize(
        argument,
        session=ctx.bot.session,
        allowed_suffixes={'.png', '.jpg', '.jpeg'},
        allowed_content_types={'image/png', 'image/jpg', 'image/jpeg'},
    )

    response = await ctx.bot.cdn.upload(
        BytesIO(result),
        filename=f'rank_card_background_{ctx.author.id}_{ctx.message.id}_.png',
    )
    return response.url


def between(lower: int, upper: int, arg: str) -> Callable[[Any, str], Coroutine[Any, Any, int]]:
    @converter
    async def Wrapper(_, argument: str) -> int:
        try:
            argument = int(argument)
        except ValueError:
            raise commands.BadArgument(f'{arg} must be an integer')

        if not lower <= argument <= upper:
            raise commands.BadArgument(f'{arg} must be between {lower} and {upper}')

        return argument

    return Wrapper


@converter
async def AlphaQuantity(_, argument: str) -> float:
    try:
        argument = float(argument)
    except ValueError:
        raise commands.BadArgument('alpha must be a float')

    if not 0 <= argument <= 1:
        raise commands.BadArgument('alpha must be between 0 and 1')

    return argument


class RankCardEditFlags(Flags, compress_usage=True):
    font: RankCardFontConverter = flag(short='f')

    # Background
    background_url: ImageUrlConverter = flag(
        name='background',
        aliases=('bg', 'background-image', 'background-url', 'img', 'image'),
        short='b',
    )
    background_color: discord.Colour = flag(
        name='background-color',
        aliases=('background-colour', 'color', 'colour', 'bc'),
        short='c',
    )
    background_alpha: AlphaQuantity = flag(name='background-alpha', aliases=('alpha', 'a'))
    background_blur: between(0, 20, 'blur') = flag(name='background-blur', aliases=('bb', 'bblur', 'blur'), short='l')

    # Theme
    primary_color: discord.Colour = flag(name='primary-color', alias='primary', short='p')
    secondary_color: discord.Colour = flag(name='secondary-color', alias='secondary', short='s')
    tertiary_color: discord.Colour = flag(
        name='tertiary-color',
        aliases=('tertiary', 'progress-bar-foreground', 'progress-bar-fg', 'pb-fg'),
        short='t',
    )

    # Overlay
    overlay_color: discord.Colour = flag(name='overlay-color', aliases=('overlay', 'overlay-colour', 'ol'), short='o')
    overlay_alpha: AlphaQuantity = flag(name='overlay-alpha', alias='oa')
    overlay_border_radius: between(0, 80, 'border radius') = flag(
        name='overlay-border-radius',
        aliases=('obr', 'radius', 'border-radius'),
        short='r',
    )

    # Avatar
    avatar_border_color: discord.Colour = flag(
        name='avatar-border-color',
        aliases=('avatar-border-colour', 'avatar-color', 'avatar-colour', 'ac'),
        short='a',
    )
    avatar_border_alpha: AlphaQuantity = flag(name='avatar-border-alpha', aliases=('aa', 'aba', 'avatar-alpha'))
    avatar_border_radius: between(0, 139, 'border radius') = flag(
        name='avatar-border-radius',
        aliases=('abr', 'avatar-radius', 'ar', 'avatar-r'),
        short='d',
    )

    # Progress Bar
    progress_bar_color: discord.Colour = flag(name='progress-bar-color', aliases=('progress-bar', 'pb'))
    progress_bar_alpha: AlphaQuantity = flag(name='progress-bar-alpha', alias='h')


class RankCardExportCodeblockStyle(Enum):
    none  = 0
    plain = 1
    ansi  = 2


class RankCardExportView(UserView):
    def __init__(self, ctx: Context, rank_card: RankCard) -> None:
        super().__init__(ctx.author)
        self.clean_prefix = ctx.clean_prefix
        self.rank_card = rank_card
        self._codeblock_style = RankCardExportCodeblockStyle.ansi
        self._show_command_header = True
        self.update()

    def update(self) -> None:
        switch = lambda target: (
            discord.ButtonStyle.primary
            if self._codeblock_style is target
            else discord.ButtonStyle.secondary
        )
        self.raw.style = switch(RankCardExportCodeblockStyle.none)
        self.codeblock.style = switch(RankCardExportCodeblockStyle.plain)
        self.ansi_codeblock.style = switch(RankCardExportCodeblockStyle.ansi)

        self.show_command_header.label = (
            'Hide Command Header'
            if self._show_command_header
            else 'Show Command Header'
        )
        self.show_command_header.style = (
            discord.ButtonStyle.danger
            if self._show_command_header
            else discord.ButtonStyle.success
        )

    def render(self) -> str:
        self.update()
        buffer = AnsiStringBuilder()
        if self._show_command_header:
            buffer.append(self.clean_prefix, color=AnsiColor.white, bold=True)
            buffer.append('rank-card edit', color=AnsiColor.green).newline()

        for name, value in self.rank_card.data.items():
            if name == 'user_id':
                continue
            if self._show_command_header:
                buffer.append('  ')

            if name.endswith('_color'):
                value = f'#{value:06x}'
            elif name == 'font':
                value = str(RankCardFont(value))
            else:
                value = str(value)

            buffer.append(f"--{name.replace('_', '-')} ", color=AnsiColor.blue)
            buffer.append(value, color=AnsiColor.cyan, bold=True).newline()

        match self._codeblock_style:
            case RankCardExportCodeblockStyle.none:
                return buffer.raw
            case RankCardExportCodeblockStyle.plain:
                return buffer.ensure_codeblock().raw
            case RankCardExportCodeblockStyle.ansi:
                return buffer.ensure_codeblock().build()

    @discord.ui.button(label='Raw', style=discord.ButtonStyle.primary, row=0)
    async def raw(self, interaction: discord.Interaction, _) -> None:
        self.codeblock_style = RankCardExportCodeblockStyle.none
        await interaction.response.edit_message(content=self.render(), view=self)

    @discord.ui.button(label='Codeblock', style=discord.ButtonStyle.primary, row=0)
    async def codeblock(self, interaction: discord.Interaction, _) -> None:
        self.codeblock_style = RankCardExportCodeblockStyle.plain
        await interaction.response.edit_message(content=self.render(), view=self)

    @discord.ui.button(label='ANSI Codeblock', style=discord.ButtonStyle.primary, row=0)
    async def ansi_codeblock(self, interaction: discord.Interaction, _) -> None:
        self.codeblock_style = RankCardExportCodeblockStyle.ansi
        await interaction.response.edit_message(content=self.render(), view=self)

    @discord.ui.button(label='Show Command Header', style=discord.ButtonStyle.success, row=1)
    async def show_command_header(self, interaction: discord.Interaction, _) -> None:
        self.show_command_header = not self.show_command_header
        await interaction.response.edit_message(content=self.render(), view=self)


class Leveling(Cog):
    """Interact with Lambda's robust and feature-packed leveling system."""

    emoji = '\U0001f3c5'

    if TYPE_CHECKING:
        manager: LevelingManager

    def cog_load(self) -> None:
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

    @group('level-config', aliases=('lc', 'level-configuration', 'level-configs', 'leveling-config'))
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
            return f'The leveling module is currently **{self._enabled_text(config.module_enabled)}**.', REPLY

        if toggle is not config.module_enabled:
            await config.update(module_enabled=toggle)

        return f'Leveling module now set to **{self._enabled_text(toggle)}**.', REPLY

    @command(aliases=('r', 'level', 'lvl', 'lv', 'xp', 'exp'), bot_permissions=('attach_files',))
    @cooldown(1, 5)
    @user_max_concurrency(1)
    @module_enabled()
    async def rank(self, ctx: Context, *, user: discord.Member | None = None, flags: RankCardFlags) -> CommandResponse:
        """View your or another user's level, rank, XP.

        If you don't specify a user, your own level will be shown.

        Arguments:
        - `user`: The user to view the level of. Defaults to yourself.

        Flags:
        - `--embed`: Whether to return the response as an embed. This is useful if you don't want to wait rendering.
        - `--my-card`: Whether to show the rank card as based off of your rank card instead of the user's.
        """
        user = user or ctx.author
        rank_card = await self.manager.fetch_rank_card(ctx.author if flags.my_card else user)
        record = self.manager.user_stats_for(user)
        await record.fetch()

        if flags.embed:
            embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
            embed.set_author(name=str(user), icon_url=user.avatar.url)

            ratio = record.xp / record.max_xp
            embed.add_field(
                name=f'Level {record.level:,} ({record.xp:,}/{record.max_xp:,} XP - {ratio:.1%})',
                value=progress_bar(ratio),
                inline=False,
            )
            embed.add_field(name='Rank', value=f'**#{record.rank:,}** out of {user.guild.member_count:,}')

            return embed, REPLY

        async with ctx.typing():
            result = await rank_card.render(user=user, rank=record.rank, level=record.level, xp=record.xp, max_xp=record.max_xp)

        return f'Rank card for **{user}**:', discord.File(result, filename=f'rank_card_{user.id}.png'), REPLY

    @group('rank-card', aliases=('rc', 'card', 'rankcard', 'levelcard', 'level-card'), bot_permissions=('attach_files',))
    async def rank_card(self, ctx: Context) -> None:
        """Commands for modifying your rank card."""
        await ctx.send_help(ctx.command)

    @rank_card.command('edit', aliases=('e', 'overwrite', 'override', 'set', 'update'), bot_permissions=('attach_files',))
    @cooldown(1, 5)
    @user_max_concurrency(1)
    async def rank_card_edit(self, ctx: Context, *, flags: RankCardEditFlags) -> CommandResponse:
        """Edits your rank card. See `{PREFIX}help rank-card` to view all subcommands, else this command will update everything in one go.

        Every configuration option in this command is passed via flags, see the command signature above for more information.
        All flags are optional, and if you don't pass in a flag, the corresponding value will not be updated.

        Flags:
        - `--background <url>`: The new background image, specified by its URL. *ONLY* takes URLs as arguments. Pass in `attachment://filename` to reference an attachment.
        - `--background-color <hex>`: The new background color, specified by its hex code. [see note 1]
        - `--background-alpha <alpha>`: The opacity of the background image. [see note 2]
        - `--background-blur <blur>`: The factor to blur the background image by. Must be between 0 and 20.
        - `--primary-color <hex>`: The new primary color, specified by its hex code. [see note 1]
          This is the primary *text* color (username, level number, XP number, and rank number).
        - `--secondary-color <hex>`: The new secondary color, specified by its hex code. [see note 1]
          This is the secondary *text* color ("Level" and "RANK" labels, discriminator if applicable, and max XP number).
        - `--tertiary-color <hex>`: The new tertiary color, specified by its hex code. [see note 1]
          This is the color of the progress bar *foreground*, i.e. the "progressing" or "moving" part of the progress bar.
        - `--overlay-color <hex>`: The new color of the large overlay over the background, specified by its hex code. [see note 1]
        - `--overlay-alpha <alpha>`: The opacity of the large overlay over the background. [see note 2]
        - `--overlay-border-radius <radius>`: The roundedness of the large overlay over the background, in pixels. Must be between 0 and 80.
        - `--avatar-border-color <hex>`: The new color of the avatar "island", specified by its hex code. [see note 1]
        - `--avatar-border-alpha <alpha>`: The opacity of the avatar "island". [see note 2]
        - `--avatar-border-radius <radius>`: The roundedness of the avatar "island" and the avatar itself, in pixels. Must be between 0 and 139.
        - `--progress-bar-color <hex>`: The new color of the progress bar *background*. [see note 1]
        - `--progress-bar-alpha <alpha>`: The opacity of the progress bar *background*. [see note 2]

        Notes:
        1. You MUST put a `#` before the hex value (if you pass in hex) in all flags with names ending in "color".
           If you aren't passing in hex you may disregard this message.
        2. All flags with names ending in "alpha" take floating-point numbers from 0.0 to 1.0, inclusive.
           0.0 is fully transparent, and 1.0 is fully opaque.
        """
        kwargs = {
            k: v.value if k.endswith('color') or isinstance(v, RankCardFont) else v
            for k, v in flags  # type: ignore
            if v is not None
        }
        card = await self.manager.fetch_rank_card(ctx.author)
        await card.update(**kwargs)

        record = self.manager.user_stats_for(ctx.author)
        await record.fetch()

        async with ctx.typing():
            result = await card.render(rank=record.rank, level=record.level, xp=record.xp, max_xp=record.max_xp)

        return 'Rank card updated:', discord.File(result, f'rank_card_preview_{ctx.author.id}.png'), REPLY

    @rank_card.command('export', aliases=('ex', 'flags', 'freeze'))
    @cooldown(1, 3)
    async def rank_card_export(self, ctx: Context) -> CommandResponse:
        """Sends your current rank card configuration as command flags.

        This exports your current rank card configuration as an invocation of `{PREFIX}rank-card edit`.
        You can then invoke this command to import the configuration back into your rank card at a later time.
        """
        view = RankCardExportView(ctx, await self.manager.fetch_rank_card(ctx.author))
        return view.render(), view, REPLY
