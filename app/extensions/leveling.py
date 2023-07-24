from __future__ import annotations

from difflib import SequenceMatcher
from enum import Enum
from io import BytesIO
from itertools import islice
from textwrap import dedent
from typing import Any, Callable, Coroutine, TypeVar, TYPE_CHECKING

import discord
from discord.app_commands import describe
from discord.ext import commands

from app.core import Cog, Context, Flags, REPLY, command, cooldown, flag, group, store_true
from app.core.helpers import BAD_ARGUMENT, guild_max_concurrency, user_max_concurrency
from app.features.leveling.core import LevelingConfig, LevelingManager, LevelingRecord
from app.features.leveling.rank_card import Font as RankCardFont, RankCard
from app.util import AnsiColor, AnsiStringBuilder, UserView, converter
from app.util.common import progress_bar, sentinel
from app.util.image import ImageFinder
from app.util.pagination import Formatter, Paginator
from app.util.types import CommandResponse, Snowflake, TypedInteraction
from config import Colors, Emojis

if TYPE_CHECKING:
    T = TypeVar('T')

RESET_BACKGROUND = sentinel('RESET_BACKGROUND')


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
async def ImageUrlConverter(ctx: Context, argument: str) -> str | object:
    if argument.lower() in ('none', 'remove', 'reset'):
        return RESET_BACKGROUND

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
        self._codeblock_style = RankCardExportCodeblockStyle.none
        await interaction.response.edit_message(content=self.render(), view=self)

    @discord.ui.button(label='Codeblock', style=discord.ButtonStyle.primary, row=0)
    async def codeblock(self, interaction: discord.Interaction, _) -> None:
        self._codeblock_style = RankCardExportCodeblockStyle.plain
        await interaction.response.edit_message(content=self.render(), view=self)

    @discord.ui.button(label='ANSI Codeblock', style=discord.ButtonStyle.primary, row=0)
    async def ansi_codeblock(self, interaction: discord.Interaction, _) -> None:
        self._codeblock_style = RankCardExportCodeblockStyle.ansi
        await interaction.response.edit_message(content=self.render(), view=self)

    @discord.ui.button(label='Show Command Header', style=discord.ButtonStyle.success, row=1)
    async def show_command_header(self, interaction: discord.Interaction, _) -> None:
        self._show_command_header = not self._show_command_header
        await interaction.response.edit_message(content=self.render(), view=self)


class AddLevelRoleModal(discord.ui.Modal):
    level = discord.ui.TextInput(
        label='<placeholder>',
        placeholder='Enter a level, e.g. 10',
        required=True,
        min_length=1,
        max_length=3,
    )

    def __init__(self, view: InteractiveLevelRolesView, *, role: discord.Role) -> None:
        self.view = view
        self.role = role
        self.level.label = 'At what level will this role be assigned at?'
        super().__init__(title="Configure Level Role", timeout=120)

    async def on_submit(self, interaction: TypedInteraction) -> None:
        level = int(self.level.value)
        if not 1 <= level <= 500:
            return await interaction.response.send_message('Level must be between 1 and 500.', ephemeral=True)

        self.view._roles[self.role.id] = level
        self.view.remove_select.update()
        await interaction.response.edit_message(embed=self.view.make_embed(), view=self.view)


class RemoveLevelRolesSelect(discord.ui.Select['InteractiveLevelRolesView']):
    def __init__(self, roles_ref: dict[Snowflake, int], ctx: Context) -> None:
        self._roles_ref = roles_ref
        self._ctx = ctx
        super().__init__(placeholder='Remove level roles...', row=1)
        self.update()

    def update(self) -> None:
        self.options = [
            discord.SelectOption(
                label=f'Level {level:,}',
                description=f'@{self._ctx.guild.get_role(role_id)}',
                value=str(role_id),
                emoji=Emojis.trash,
            )
            for role_id, level in sorted(self._roles_ref.items(), key=lambda pair: pair[1])
        ]
        self.disabled = not self.options
        # Placeholder if no options
        if self.disabled:
            self.options = [discord.SelectOption(label='.')]
        self.max_values = len(self.options)

    async def callback(self, interaction: TypedInteraction) -> Any:
        for value in self.values:
            self._roles_ref.pop(int(value))
        self.update()
        await interaction.response.edit_message(embed=self.view.make_embed(), view=self.view)


class RoleStackToggle(discord.ui.Button['InteractiveLevelRolesView']):
    def __init__(self, current: bool) -> None:
        super().__init__(
            style=discord.ButtonStyle.primary,
            label=f'{"Disable" if current else "Enable"} Role Stack',
            row=2,
        )

    async def callback(self, interaction: TypedInteraction) -> None:
        self.view._role_stack = new = not self.view._role_stack
        self.label = f'{"Disable" if new else "Enable"} Role Stack'
        await interaction.response.edit_message(embed=self.view.make_embed(), view=self.view)


class InteractiveLevelRolesView(discord.ui.View):
    def __init__(self, ctx: Context, *, config: LevelingConfig) -> None:
        super().__init__(timeout=300)  # Automatically save after 5 minutes
        self.ctx = ctx
        self.config = config
        self._roles = config.level_roles.copy()  # role_id => level
        self._role_stack = config.role_stack

        self.remove_select = RemoveLevelRolesSelect(self._roles, ctx)
        self.add_item(self.remove_select)
        self.add_item(RoleStackToggle(self._role_stack))

    def make_embed(self) -> discord.Embed:
        embed = discord.Embed(color=Colors.primary, timestamp=self.ctx.now)
        embed.set_author(name=f'{self.ctx.guild} Level Role Rewards', icon_url=self.ctx.guild.icon.url)
        embed.set_footer(text='Make sure to save your changes by pressing the Save button!')

        indicator = 'Users can accumulate multiple level roles.' if self._role_stack else 'Users can only have the highest level role.'
        embed.add_field(name='Role Stack', value=f'{Emojis.enabled if self._role_stack else Emojis.disabled} {indicator}')

        if not self._roles:
            embed.description = 'You have not configured any level role rewards yet.'
            return embed

        embed.insert_field_at(
            index=0,
            name=f'Level Roles ({len(self._roles)}/25 slots)',
            value='\n'.join(
                f'- Level {level:,}: <@&{role_id}>'
                for role_id, level in sorted(self._roles.items(), key=lambda pair: pair[1])
            ),
            inline=False
        )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.guild_permissions.manage_guild

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder='Add a new level role reward...',
        min_values=1,
        max_values=1,
        row=0,
    )
    async def add_level_role(self, interaction: TypedInteraction, select: discord.ui.RoleSelect) -> None:
        role = select.values[0]
        if role.is_default() or role.managed:
            return await interaction.response.send_message(
                f'That role is a default role or managed role, which means I am unable to assign it.\n'
                'Try using a different role or creating a new one.',
                ephemeral=True,
            )

        if not role.is_assignable():
            return await interaction.response.send_message(
                f'That role is lower than or equal to my top role ({self.ctx.me.top_role.mention}) in the role hierarchy, '
                f'which means I am unable to assign it.\nTry moving the role to be lower than {self.ctx.me.top_role.mention}, '
                'and then try again.',
                ephemeral=True,
            )
        await interaction.response.send_modal(AddLevelRoleModal(self, role=role))

    @discord.ui.button(label='Save', style=discord.ButtonStyle.success, row=2)
    async def save(self, interaction: TypedInteraction, _) -> None:
        await self.config.update(level_roles=self._roles, role_stack=self._role_stack)
        for child in self.children:
            child.disabled = True

        embed = self.make_embed()
        embed.colour = Colors.warning
        await interaction.response.edit_message(content='Updating roles...', embed=embed, view=self)

        # Update everyone's roles
        await self.ctx.cog.manager.update_all_roles(self.ctx.guild)

        embed.colour = Colors.success
        await interaction.edit_original_response(content='Saved and updated level roles.', embed=embed, view=self)
        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.danger, row=2)
    async def cancel(self, interaction: TypedInteraction, _) -> None:
        for child in self.children:
            child.disabled = True

        embed = self.make_embed()
        embed.colour = Colors.error
        await interaction.response.edit_message(content='Cancelled. Changes were discarded.', embed=embed, view=self)

    # Save on timeout
    async def on_timeout(self) -> None:
        await self.config.update(level_roles=self._roles, role_stack=self._role_stack)
        await self.ctx.cog.manager.update_all_roles(self.ctx.guild)


class LeaderboardFormatter(Formatter[LevelingRecord]):
    def __init__(self, rank_card: RankCard, entries: list[LevelingRecord]) -> None:
        self.rank_card = rank_card
        super().__init__(entries=entries, per_page=RankCard.LB_COUNT)

    async def format_page(self, paginator: Paginator, entry: list[LevelingRecord]) -> discord.File:
        fp = await self.rank_card.render_leaderboard(
            records=entry,
            rank_offset=paginator.current_page * RankCard.LB_COUNT,
        )
        return discord.File(fp, f'leaderboard_{self.rank_card.user_id}.png')


class LeaderboardEmbedFormatter(Formatter[LevelingRecord]):
    def __init__(self, entries: list[LevelingRecord]) -> None:
        super().__init__(entries=entries, per_page=10)

    async def format_page(self, paginator: Paginator, entry: list[LevelingRecord]) -> discord.Embed:
        embed = discord.Embed(color=Colors.primary, timestamp=paginator.ctx.now)
        embed.set_author(name=f'Top Members in {paginator.ctx.guild}', icon_url=paginator.ctx.guild.icon.url)

        description = []
        for i, record in enumerate(entry, start=paginator.current_page * self.per_page):
            ratio = record.xp / record.max_xp
            description.append(
                f'{i + 1}. {record.user.mention}: Level **{record.level:,}** '
                f'*({record.xp:,}/{record.max_xp:,} XP) [{ratio:.1%}]*'
            )

        embed.description = '\n'.join(description)
        return embed


class LeaderboardFlags(Flags):
    embed: bool = store_true(short='e')
    page: int = flag(short='p', alias='jump', default=1)


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

    @group('level-config', aliases=('lc', 'level-configuration', 'level-configs', 'leveling-config'), hybrid=True)
    async def level_config(self, _ctx: Context) -> CommandResponse:
        """Commands for configuring the leveling module."""
        return 'WIP', REPLY

    @staticmethod
    def _enabled_text(toggle: bool) -> str:
        return f'{Emojis.enabled} Enabled' if toggle else f'{Emojis.disabled} Disabled'

    @level_config.command('module', aliases=('m', 'mod', 'toggle', 'status'), user_permissions=('manage_guild',), hybrid=True)
    @describe(toggle='Whether to enable or disable the module.')
    @guild_max_concurrency(1)
    async def level_config_module(self, ctx: Context, toggle: bool = None) -> CommandResponse:
        """Toggles the leveling module on or off."""
        config = await self.manager.fetch_guild_config(ctx.guild.id)

        if toggle is None:
            return f'The leveling module is currently **{self._enabled_text(config.module_enabled)}**.', REPLY

        if toggle is not config.module_enabled:
            await config.update(module_enabled=toggle)

        return f'Leveling module now set to **{self._enabled_text(toggle)}**.', REPLY

    @level_config.command('roles', aliases=('role', 'r', 'reward', 'rewards'), user_permissions=('manage_guild',), hybrid=True)
    @guild_max_concurrency(1)
    @module_enabled()
    async def level_config_roles(self, ctx: Context) -> CommandResponse:
        """Interactively configure level role rewards.

        Level role rewards are roles that are automatically given to users when they reach a certain level.
        Roles can be stacked (keep their previous roles) or not stacked (only have the highest role).
        """
        view = InteractiveLevelRolesView(ctx, config=await self.manager.fetch_guild_config(ctx.guild.id))
        yield (
            'This interactive configurator can be used simutaneously by anyone with the *Manage Server* permission.',
            view.make_embed(),
            view,
            REPLY,
        )
        await view.wait()

    @command(aliases=('r', 'level', 'lvl', 'lv', 'xp', 'exp'), bot_permissions=('attach_files',))
    @describe(user='The user to view the level of. Defaults to yourself.', flags='Flags to modify the command.')
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
        if user.bot:
            return 'Bots do not have levels.', BAD_ARGUMENT

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

    @command(aliases=('lb', 'top'), hybrid=True)
    @cooldown(1, 10)
    @user_max_concurrency(1)
    @module_enabled()
    async def leaderboard(self, ctx: Context, *, flags: LeaderboardFlags) -> CommandResponse:
        """View the leaderboard of members with the highest levels in this server.

        Flags:
        - `--embed`: Whether to render the response as a simple embed.
          This is useful if you don't want to wait rendering or if your connection is slow.
        - `--page <page>`: The page of the leaderboard to jump to. Defaults to `1`.
        """
        await self.manager.ensure_cached_user_stats(ctx.guild)
        non_zero = (record for record in self.manager.walk_stats(ctx.guild) if (record.level, record.xp) > (0, 0))
        records = sorted(islice(non_zero, 100), key=lambda record: (record.level, record.xp), reverse=True)
        record = self.manager.user_stats_for(ctx.author)
        await record.fetch()

        message = (
            f'Here is the XP Leaderboard for **{ctx.guild}** '
            f'(you are rank **#{record.rank:,}** out of {ctx.guild.member_count:,} members.)'
        )
        if flags.embed:
            paginator = Paginator(ctx, LeaderboardEmbedFormatter(records), page=flags.page - 1)
            return message, paginator, REPLY
        else:
            message += (
                f'\n*Please give Lambda some time to render pages. If you are on a bad connection or '
                f'do not want to wait for pages to render, use `{ctx.clean_prefix}leaderboard --embed` instead.*'
            )

        rank_card = await self.manager.fetch_rank_card(ctx.author)
        paginator = Paginator(ctx, LeaderboardFormatter(rank_card, records), page=flags.page - 1)
        return message, paginator, REPLY

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
        - `--background <url>`: The new background image, specified by its URL. *ONLY* takes URLs as arguments.
          Pass in `attachment://filename` to reference an attachment. Pass in `none` to remove the background image.
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
        def transform(k: str, v: Any) -> str | None:
            if v is RESET_BACKGROUND:
                return None
            return v.value if k.endswith('color') or isinstance(v, RankCardFont) else v

        kwargs = {
            k: transform(k, v)
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
    async def rank_card_export(self, ctx: Context, *, user: discord.Member = None) -> CommandResponse:
        """Sends your current rank card configuration (or that of the specified user's) as command flags.

        This exports your current rank card configuration as an invocation of `{PREFIX}rank-card edit`.
        You can then invoke this command to import the configuration back into your rank card at a later time.

        Arguments:
        - `user`: The user to export the rank card configuration of. Defaults to yourself.
        """
        view = RankCardExportView(ctx, await self.manager.fetch_rank_card(user or ctx.author))
        return view.render(), view, REPLY, dict(suppress_embeds=True)
