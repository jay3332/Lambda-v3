from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
import sys
from typing import Any, ClassVar, Final, TYPE_CHECKING

import discord
import jishaku
from aiohttp import ClientSession
from discord.ext import commands

from app.core.help import HelpCommand
from app.core.models import Cog, Context, PermissionSpec
from app.database import Database
from config import allowed_mentions, default_prefix, description, name as bot_name, owner, resolved_token, version

__all__ = (
    'LOG',
    'Bot',
)

LOG: Final[logging.Logger] = logging.getLogger('Lambda')


class Bot(commands.Bot):
    """Represents Lambda as a bot.

    At its core, this handles and/or sends all events and payloads
    to and from Discord's API.
    """

    log: Final[ClassVar[logging.Logger]] = LOG

    if TYPE_CHECKING:
        from datetime import datetime

        db: Database
        session: ClientSession
        startup_timestamp: datetime
        user_to_member_mapping: dict[int, discord.Member]

    # TODO: if guild logging is enabled then Intents.all() may have to be used instead
    INTENTS: Final[ClassVar[discord.Intents]] = discord.Intents(
        emojis_and_stickers=True,
        guilds=True,
        members=True,
        messages=True,
        message_content=True,
        presences=True,
        reactions=True,
    )

    def __init__(self) -> None:
        key = 'owner_id' if isinstance(owner, int) else 'owner_ids'

        super().__init__(
            command_prefix=self.__class__.resolve_command_prefix,
            help_command=HelpCommand(),
            update_application_commands_at_startup=True,
            description=description,
            case_insensitive=True,
            allowed_mentions=allowed_mentions,
            intents=self.INTENTS,
            status=discord.Status.dnd,
            max_messages=10,
            **{key: owner},
        )

        self._BotBase__cogs = commands.core._CaseInsensitiveDict()
        self.prepare()

    async def resolve_command_prefix(self, message: discord.Message) -> list[str]:
        """Resolves a command prefix from a message."""
        return commands.when_mentioned_or(default_prefix)(self, message)

    async def _dispatch_first_ready(self) -> None:
        """Waits for the inbound READY gateway event, then dispatches the `first_ready` event."""
        await self.wait_until_ready()
        self.dispatch('first_ready')

    # noinspection PyUnresolvedReferences
    def _load_from_module_spec(self, spec: importlib.machinery.ModuleSpec, key: str) -> None:
        # An awfully hacky solution and I really don't like it this way.
        # Maybe I'll come up with a better implementation later.
        try:
            super()._load_from_module_spec(spec, key)
        except commands.NoEntryPointError:
            lib = importlib.util.module_from_spec(spec)
            sys.modules[key] = lib

            try:
                spec.loader.exec_module(lib)
            except Exception as exc:
                del sys.modules[key]
                raise errors.ExtensionFailed(key, exc) from exc

            predicate = lambda member: member is not Cog and isinstance(member, type) and issubclass(member, Cog)
            members = inspect.getmembers(lib, predicate=predicate)

            if not members:
                raise
        else:
            return

        cog = members[0][1]  # (_cls_name, cog), *_other_cogs
        cog.simple_setup(self)

        self._BotBase__extensions[key] = lib

    def _load_extensions(self) -> None:
        """Loads all command extensions, including Jishaku."""
        self.load_extension('jishaku')

        for file in os.listdir('./app/extensions'):
            if file == 'compat.py' or file.startswith('_') or not file.endswith('.py'):
                continue

            extension = f'app.extensions.{file[:-3]}'
            try:
                self.load_extension(extension)
            except Exception as exc:
                self.log.critical(f'Failed to load extension {extension}: {exc}', exc_info=True)
            else:
                self.log.info(f'Loaded extension: {extension}')

        self.load_extension('app.extensions.compat')  # Load this last

    def reload_extension(self, name: str, *, package: str | None = None) -> None:
        """Reloads an extension."""
        super().reload_extension(name, package=package)
        self.prepare_jishaku_flags()

    def prepare(self) -> None:
        """Prepares the bot for startup."""
        self.prepare_jishaku_flags()
        self.prepare_logger()

        self.db = Database(loop=self.loop)
        self.session = ClientSession()
        self.user_to_member_mapping = {}

        self.loop.create_task(self._dispatch_first_ready())
        self._load_extensions()

    def prepare_logger(self) -> None:
        """Configures the bot's logger instance."""
        self.log.setLevel(logging.INFO)

        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
        self.log.addHandler(handler)

    @staticmethod
    def prepare_jishaku_flags() -> None:
        jishaku.Flags.HIDE = True
        jishaku.Flags.NO_UNDERSCORE = True
        jishaku.Flags.NO_DM_TRACEBACK = True

    def find_member_from_user(self, user: discord.abc.Snowflake) -> discord.Member | None:
        """Finds the first member object given a user/object.

        Note that the guild the returned member is associated to will be a random guild.
        Returns ``None`` if the user is not in any mutual guilds.
        """
        if isinstance(user, discord.Member):
            return user

        if user.id in self.user_to_member_mapping:
            return self.user_to_member_mapping[user.id]

        for guild in self.guilds:
            if member := guild.get_member(user.id):
                self.user_to_member_mapping[user.id] = member
                return member

        return None  # not necessary but without this line the nesting becomes relatively ugly

    def user_on_mobile(self, user: discord.abc.Snowflake) -> bool | None:
        """Whether this user object is on mobile.

        If there are no mutual guilds for this user then this will return ``None``.
        Because ``None`` is a falsy value, this will behave as if it defaults to ``False``.
        """
        member = self.find_member_from_user(user)
        if member is not None:
            return member.is_on_mobile()

        return None

    async def process_commands(self, message: discord.Message, /) -> None:
        if message.author.bot:
            return

        ctx = await self.get_context(message, cls=Context)
        await self.invoke(ctx)

    async def on_first_ready(self) -> None:
        """Prints startup information to the console."""
        self.startup_timestamp = discord.utils.utcnow()

        text = f'Ready as {self.user} ({self.user.id})'
        center = f' {bot_name} v{version} '

        print(format(center, f'=^{len(text)}'))
        print(text)

        self.log.info(f'Gateway received READY @ {self.startup_timestamp}')

    async def on_command_error(self, ctx: Context, error: Exception) -> Any:
        """Handles command errors."""
        error = getattr(error, 'original', error)

        blacklist = (
            commands.CommandNotFound,
        )
        if isinstance(error, blacklist):
            return

        if isinstance(error, commands.BadArgument):
            return await ctx.send(error, reference=ctx.message, delete_after=15)

        if isinstance(error, commands.CommandOnCooldown):
            if not ctx.guild and ctx.channel.permissions_for(ctx.me).add_reactions:
                return await ctx.message.add_reaction('\U000023f3')

            return await ctx.send('You are currently on cooldown.', reference=ctx.message, delete_after=15)

        if isinstance(error, commands.MaxConcurrencyReached):
            return await ctx.send(error, reference=ctx.message, delete_after=15)

        if isinstance(error, (commands.MissingPermissions, commands.BotMissingPermissions)):
            if isinstance(error, commands.MissingPermissions):
                message = 'You are missing the following permissions required to run this command:'
            else:
                message = 'I am missing the following permissions required to execute this command:'

            missing = '\n'.join(f'- {PermissionSpec.permission_as_str(perm)}' for perm in error.missing_permissions)
            message += '\n' + missing

            permissions = ctx.channel.permissions_for(ctx.me)
            if ctx.guild and (permissions.administrator or permissions.send_messages and permissions.read_message_history):
                await ctx.send(message, reference=ctx.message)
                return

            if permissions.administrator or permissions.add_reactions:
                await ctx.message.add_reaction('\U000026a0')

            try:
                await ctx.author.send(message)
            except discord.Forbidden:
                pass

            return

        self.log.critical(f'Uncaught error occured when trying to invoke {ctx.command.name}: {error}', exc_info=error)

        await ctx.send(f'panic!({error})', reference=ctx.message)
        raise error

    async def close(self) -> None:
        """Closes this bot and it's aiohttp ClientSession."""
        await self.session.close()
        await super().close()

        pending = asyncio.all_tasks()
        # Wait for all tasks to complete. This usually allows for a graceful shutdown of the bot.
        try:
            await asyncio.wait_for(asyncio.gather(*pending), timeout=0.5)
        except asyncio.TimeoutError:
            # If the tasks take too long to complete, cancel them.
            for task in pending:
                task.cancel()

    def run(self) -> None:
        """Runs the bot."""
        return super().run(resolved_token)
