from __future__ import annotations

from difflib import SequenceMatcher
from io import BytesIO
from textwrap import dedent
from typing import Any, Callable, Coroutine, TypeVar, TYPE_CHECKING

import discord
from discord.ext import commands

from app.core import Cog, Context, Flags, REPLY, command, cooldown, flag, group, store_true
from app.core.helpers import user_max_concurrency
from app.features.leveling.core import LevelingManager
from app.features.leveling.rank_card import Font as RankCardFont, RankCard
from app.util import converter
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

    return await ctx.bot.cdn.upload(
        BytesIO(result),
        filename=f'rank_card_background_{ctx.author.id}.png',
        owner=ctx.author,
    )


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


class RankCardEditFlags(Flags):
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
    tertiary_color: discord.Colour = flag(name='tertiary-color', alias='tertiary', short='t')

    # Overlay
    overlay_color: discord.Colour = flag(name='overlay-color', aliases=('overlay', 'overlay-colour', 'ol'), short='o')
    overlay_alpha: AlphaQuantity = flag(name='overlay-alpha', alias='oa')
    overlay_border_radius: between(0, 80, 'border radius') = flag(
        name='overlay-border-radius',
        aliases=('obr', 'radius', 'border-radius'),
        short='r',
    )

    # Avatar
    avatar_color: discord.Colour = flag(name='avatar-color', aliases=('avatar-colour', 'ac'), short='a')
    avatar_alpha: AlphaQuantity = flag(name='avatar-alpha', alias='aa')
    avatar_border_radius: between(0, 139, 'border radius') = flag(
        name='avatar-border-radius',
        aliases=('abr', 'avatar-radius', 'ar', 'avatar-r'),
        short='d',
    )

    # Progress Bar
    progress_bar_color: discord.Colour = flag(name='progress-bar-color', aliases=('progress-bar', 'pb'))
    progress_bar_alpha: AlphaQuantity = flag(name='progress-bar-alpha', alias='h')


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

    @command(aliases=('level', 'lvl', 'lv', 'xp', 'exp'), bot_permissions=('attach_files',))
    @cooldown(1, 5)
    @user_max_concurrency(1)
    @module_enabled()
    async def rank(self, ctx: Context, *, user: discord.Member = None, flags: RankCardFlags) -> CommandResponse:
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
        await record.fetch_if_necessary()

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

        Every configuration option in this command will be passed via flags, see the command signature above for more information.

        Flag Information:
        - The `--background` flag ONLY takes URLs as arguments. Pass in `attachment://filename` to reference and attachment.
        - The `--background-blur` flag only accepts arguments between 0 and 20.
        - The `--overlay-border-radius` flag only accepts arguments between 0 and 80.
        - The `--avatar-border-radius` flag only accepts arguments between 0 and 139.
        - You MUST put a `#` before the hex value (if you pass in hex) in all flags with names ending in "color".\\*
        - All flags with names ending in "alpha" take floating-point numbers from 0.0 to 1.0.

        \\* If you aren't passing in hex you may disregard this message.
        """
        kwargs = {
            k: v.value if k.endswith('color') or isinstance(v, RankCardFont) else v
            for k, v in flags  # type: ignore
            if v is not None
        }
        card = await self.manager.fetch_rank_card(ctx.author)
        await card.update(**kwargs)

        async with ctx.typing():
            result = await card.render(rank=3, level=5, xp=40, max_xp=100)

        return 'Rank card updated:', discord.File(result, f'rank_card_preview_{ctx.author.id}.png'), REPLY
