from __future__ import annotations

import discord
import discord.utils as utils
from discord.ext.commands import BadArgument, MissingPermissions
from jishaku.codeblocks import codeblock_converter

from app.core import BAD_ARGUMENT, Cog, Context, ERROR, Flags, PermissionSpec, REPLY, flag, group, store_true
from app.features.custom_commands import CustomCommandManager, CustomCommandRecord
from app.util import converter
from app.util.common import cutoff, pluralize
from app.util.pagination import FieldBasedFormatter, Paginator
from app.util.structures import TimestampFormatter
from app.util.types import CommandResponse
from app.util.views import LinkView
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


@converter
async def CustomCommandConverter(ctx: Context[CustomCommands], argument: str) -> CustomCommandRecord:
    if not ctx.guild:
        raise BadArgument('This command can only be used in a guild.')

    argument = argument.strip().casefold()
    record = await ctx.cog.manager.get_record(name=argument, guild=ctx.guild)

    if not record:
        raise BadArgument(f'custom command {argument!r} not found.')

    return record


class CustomCommands(Cog, name='Custom Commands'):
    """An extension that aids in interfacing around custom commands."""

    emoji = '\U0001f680'

    def cog_load(self) -> None:
        self.manager: CustomCommandManager = CustomCommandManager(self.bot)

    @group('custom-commands', aliases=('custom-command', 'cc', 'ccmd', 'ccmds'))
    async def custom_commands(self, ctx: Context) -> CommandResponse:
        """Interface around Lambda's custom command system.

        Run this command without subcommands to view all current custom commands.
        """
        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f'Custom Commands for {ctx.guild.name}', icon_url=ctx.guild.icon)
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

    @custom_commands.command('delete', aliases=('remove', 'rm', 'del', '-'), user_permissions=('manage_guild',))
    async def delete_custom_command(self, ctx: Context, *names: str) -> CommandResponse:
        """Delete a custom command, or multiple custom commands.

        Arguments:
        - `names`: The name, or names of the commands to delete, separated by space.
        """
        if not names:
            return 'Please provide custom commands to delete.', BAD_ARGUMENT

        names = [name.strip().casefold() for name in names]
        query = """
                DELETE FROM
                    custom_commands
                WHERE
                    guild_id = $1
                AND
                    name = ANY($2)
                """

        result = await ctx.bot.db.execute(query, ctx.guild.id, names)
        action, count = result.split(maxsplit=1)

        if action != 'DELETE':
            return f'Expected DELETE action, got {action!r} instead', ERROR

        try:
            count = int(count)
        except ValueError:
            return f'Expected integer delete count, got {count!r} instead', ERROR

        for name in names:
            self.manager.remove_command(guild=ctx.guild, name=name)

        if not count:
            return 'No custom commands were deleted. Make sure the commands were made in this server.', REPLY

        if count < len(names):
            return f'Successfully deleted {count} out of the {len(names)} specified custom commands.', REPLY

        if count == 1:
            return f'Successfully deleted the custom command {names[0]!r}.', REPLY

        return f'Successfully deleted {count} custom commands.', REPLY

    @custom_commands.command(
        name='edit',
        aliases=('edit-response', 'modify', 'change', 'update', 'e', '~'),
        user_permissions=('manage_guild',),
    )
    async def edit_custom_command(self, _ctx: Context, command: CustomCommandConverter, *, response: str) -> CommandResponse:
        """Edit a custom command's response.

        If you want to switch between standard and Python-based responses,
        remove the command (see `{PREFIX}cc remove`) and add it again.

        This inconvenience was deliberately made due to the fact that the contents of the two responses
        are completely incompatible with each other, so it's best to force a rewrite of the response every time.

        To modify required permissions and/or whitelisted/blacklisted users, roles, or channels, see the following commands:
        - `{PREFIX}cc <entity> add`
        - `{PREFIX}cc <entity> remove`
        - `{PREFIX}cc <entity> reset`

        Replace `<entity>` with `permissions`, `users`, `roles`, or `channels`.
        To switch between a whitelist or a blacklist, see `{PREFIX}cc toggle-type`.

        Arguments:
        - `command`: The name of the custom command to edit.
        - `response`: The new response of the custom command.
        """
        await command.update(response=response)
        return f'Successfully modified custom command {command.name!r}.', REPLY  # TODO: edit diff history

    @custom_commands.command(name='info', aliases=('i', 'view', 'v', 'information', 'details'))
    async def custom_command_info(self, ctx: Context, command: CustomCommandConverter) -> CommandResponse:
        """View information about a custom command.

        Arguments:
        - `command`: The name of the custom command to view.
        """
        command: CustomCommandRecord
        embed = discord.Embed(title=command.name, color=Colors.primary, timestamp=ctx.now)
        embed.description = f'You are {"un" * (not command.can_run(ctx))}able to run this command.'

        created_at = TimestampFormatter(command.created_at)
        embed.add_field(name='Created', value=f'{created_at} ({created_at:R})')

        if perms := command.required_permissions.user:
            embed.add_field(
                name='Required Permissions',
                value=', '.join(map(PermissionSpec.permission_as_str, perms)),
                inline=False,
            )

        prefix = 'Whitelisted' if command.is_whitelist_toggle else 'Blacklisted'
        if ids := command.toggled_user_ids:
            embed.add_field(name=prefix + ' Users', value=', '.join(f'<@{id}>' for id in ids), inline=False)

        if ids := command.toggled_role_ids:
            embed.add_field(name=prefix + ' Roles', value=', '.join(f'<@&{id}>' for id in ids), inline=False)

        if ids := command.toggled_channel_ids:
            embed.add_field(name=prefix + ' Channels', value=', '.join(f'<#{id}>' for id in ids), inline=False)

        response = (
            '*Python-based response*\n'
            f'See `{ctx.clean_prefix}cc source {command.name}` for the source code of this command.'
            if command.is_python
            else cutoff(command.response_content, 1000)
        )
        embed.add_field(name='Response', value=response, inline=False)

        return embed, REPLY

    @custom_commands.command(name='raw', aliases=('source', 's', 'src', 'response'))
    async def custom_command_raw(self, ctx: Context, command: CustomCommandConverter) -> CommandResponse:
        """View the raw response of a custom command.

        Arguments:
        - `command`: The name of the custom command to view.
        """
        command: CustomCommandRecord
        if command.is_python:
            candidate = f'```py\n{command.response_content}```'
        else:
            candidate = utils.escape_markdown(command.response_content)

        if len(candidate) > 2000:
            paste = await ctx.bot.cdn.paste(
                command.response_content,
                extension='py' if command.is_python else 'txt',
                directory='custom_command_source',
            )

            return 'Click below to view the raw response.', LinkView({'View Response': paste.url}), REPLY

        return candidate, REPLY

    # TODO: make (scroll above), permissions/users/roles/channels add, remove, reset, and toggle-type
