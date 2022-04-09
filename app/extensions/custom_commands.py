from __future__ import annotations

import discord
import discord.utils as utils
from discord.ext.commands import BadArgument, MissingPermissions
from jishaku.codeblocks import codeblock_converter

from app.core import BAD_ARGUMENT, Cog, Context, Flags, REPLY, flag, group, store_true
from app.features.custom_commands import CustomCommandManager
from app.util import converter
from app.util.common import cutoff, pluralize
from app.util.pagination import FieldBasedFormatter, Paginator
from app.util.types import CommandResponse
from config import Colors, Emojis


# TODO: possibly support entering Python code through this modal.
# TODO: support for other options (i.e. toggled users) when select option for modals becomes available.
class CustomCommandCreateModal(discord.ui.Modal, title='Create Custom Command'):
    name: discord.ui.TextInput = discord.ui.TextInput(
        label='Command Name',
        placeholder='my-cool-command',
        min_length=1,
        max_length=50,
        required=True,
    )

    response: discord.ui.TextInput = discord.ui.TextInput(
        label='Response',
        style=discord.TextStyle.paragraph,
        placeholder='Hello {user.mention}!',
        min_length=1,
        required=True,
    )

    def __init__(self, manager: CustomCommandManager) -> None:
        super().__init__()

        self.manager: CustomCommandManager = manager

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = self.name.value
        try:
            name = self.manager.validate_name(name)
        except ValueError as exc:
            return await interaction.response.send_message(str(exc), ephemeral=True)

        response = self.response.value
        await self.manager.add_command(name=name, guild=discord.Object(id=interaction.guild_id), response=response)

        await interaction.response.send_message(f'Successfully created custom command `{name}`.', ephemeral=True)


class CustomCommandCreateButton(discord.ui.Button):
    def __init__(self, manager: CustomCommandManager, user: discord.Member) -> None:
        super().__init__(label='Add Command', style=discord.ButtonStyle.blurple, emoji=Emojis.plus)

        self.manager: CustomCommandManager = manager
        self.user: discord.Member = user

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user != self.user:
            return await interaction.response.send_message(
                content=f'Only {self.user.mention} can use this button.',
                ephemeral=True,
            )

        modal = CustomCommandCreateModal(self.manager)
        await interaction.response.send_modal(modal)


@converter
async def PermissionConverter(ctx: Context, argument: str) -> int:
    if not ctx.guild:
        raise BadArgument('This command can only be used in a guild.')

    if argument.isdigit():  # allow permissions integers. no input checking so this should be fixed
        return int(argument)

    argument = argument.lower().replace(' ', '_').replace('server', 'guild')
    try:
        permission = getattr(discord.Permissions, argument).flag
    except AttributeError:
        raise BadArgument(f'invalid permission: {argument!r}')

    authorized = ctx.channel.permissions_for(ctx.author)
    if authorized.administrator:
        return permission

    if not getattr(authorized, argument):
        raise BadArgument(f'you do not have the {argument!r} permission so you cannot use it.')

    return permission


class CustomCommandCreateFlags(Flags):
    permissions: list[PermissionConverter] = flag(short='p', aliases=('perms', 'perm', 'required-permissions'))
    whitelist: list[discord.Member | discord.Role | discord.TextChannel] = flag(
        short='w',
        aliases=('whitelisted', 'wl', 'allow'),
    )
    blacklist: list[discord.Member | discord.Role | discord.TextChannel] = flag(
        short='b',
        aliases=('blacklisted', 'bl', 'deny', 'disallow', 'forbid'),
    )
    python: bool = store_true(short='y', alias='py')


class CustomCommands(Cog, name='Custom Commands'):
    """An extension that aids in interfacing around custom commands."""

    emoji = '\U0001f680'

    def __setup__(self) -> None:
        self.manager: CustomCommandManager = CustomCommandManager(self.bot)

    @group('custom-commands', aliases=('custom-command', 'cc', 'ccmd', 'ccmds'))
    async def custom_commands(self, ctx: Context) -> CommandResponse:
        """Interface around Lambda's custom command system.

        Run this command without subcommands to view all current custom commands.
        """
        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f'Custom Commands for {ctx.guild.name}', icon_url=ctx.guild.icon.url)
        try:
            show_button = self.make_custom_command._permissions.check(ctx)  # type: ignore
        except MissingPermissions:
            show_button = False

        records = await self.manager.fetch_records(guild=ctx.guild)
        if not records:
            if show_button:
                view = discord.ui.View()
                view.add_item(CustomCommandCreateButton(self.manager, ctx.author))
            else:
                view = None

            embed.description = (
                'No custom commands have been set up yet.\n'
                f'Use `{ctx.clean_prefix}custom-commands make` to make a custom command.'
            )
            return embed, view, REPLY

        embed.set_footer(text=pluralize(f'{len(records)} command(s)'))

        fields = [
            {
                'name': f'**{utils.escape_markdown(name)}** (Created {utils.format_dt(record.created_at, "R")})',
                'value': '*Python-based Response*' if record.is_python else f'Response: {cutoff(record.response_content, 50)}',
                'inline': False,
            }
            for name, record in records.items()
        ]
        return Paginator(
            ctx,
            FieldBasedFormatter(embed, fields, per_page=6),
            other_components=[
                CustomCommandCreateButton(self.manager, ctx.author),
            ] if show_button else None,
        ), REPLY

    @custom_commands.command('make', aliases=('interactive', 'new'), user_permissions=('manage_guild',))
    async def make_custom_command(self, ctx: Context) -> CommandResponse:
        """Interactively create a custom command.

        This command will prompt you for information. Use `{PREFIX}cc create`
        to create a custom command in one command.
        """
        return f'WIP, use `{ctx.clean_prefix}cc create` instead', REPLY

    @custom_commands.command('create', aliases=('add', 'quick', '+'), user_permissions=('manage_guild',))
    async def create_custom_command(self, ctx: Context, name: str, *, response: str, flags: CustomCommandCreateFlags) -> CommandResponse:
        """Create a custom command in one message.

        All information about this custom command is to be provided in this message.

        Examples:
        - `{PREFIX}cc create my-command Hello, world!`
        - `{PREFIX}cc create greet Hello, {target.mention}!`
        - `{PREFIX}cc create dangerous-command {target.mention} has been slain! --permissions Administrator`

        Arguments:
        - `name`: The name of the command.
        - `response`: The response content to the command. You may format your responses with [Tag Formatting](https://docs.lambdabot.cf/master).

        Flags:
        - `--permissions <permissions...>`: A list of permissions that the command requires the user to have.
        - `--whitelist <users/roles/channels...>`: A list of users, roles, or channels whitelisted for the command.
        - `--blacklist <users/roles/channels...>`: A list of users, roles, or channels blacklisted from the command.
        - `--python`: Indicates that the response is a [Python-based response](https://docs.lambdabot.cf/master/advanced-tags).

        `--whitelist` and `--blacklist` cannot be used together.
        All "lists" are separated by space.
        """
        if flags.whitelist and flags.blacklist:
            return 'Cannot use both `--whitelist` and `--blacklist`', REPLY

        if flags.python:
            response = codeblock_converter(response).content

        try:
            name = self.manager.validate_name(name)
        except ValueError as exc:
            return str(exc), BAD_ARGUMENT

        kwargs = {'name': name, 'response': response, 'is_python': flags.python}

        if flags.permissions:
            kwargs['required_permissions'] = discord.Permissions(sum(flags.permissions))

        entities = None
        if flags.whitelist:
            kwargs['is_whitelist_toggle'] = True
            entities = flags.whitelist

        if flags.blacklist:
            kwargs['is_whitelist_toggle'] = False
            entities = flags.blacklist

        if entities:
            kwargs['toggled_users'] = [u.id for u in entities if isinstance(u, discord.Member)]
            kwargs['toggled_roles'] = [r.id for r in entities if isinstance(r, discord.Role)]
            kwargs['toggled_channels'] = [c.id for c in entities if isinstance(c, discord.TextChannel)]

        await self.manager.add_command(guild=ctx.guild, **kwargs)
        return f'Successfully created custom command `{name}`.', REPLY
