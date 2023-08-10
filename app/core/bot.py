from __future__ import annotations

import asyncio
import importlib
import inspect
import logging
import os
import re
import sys
from platform import system
from time import perf_counter
from typing import Any, Awaitable, Callable, ClassVar, Final, TYPE_CHECKING, Type

import discord
import jishaku
from aiohttp import ClientSession
from discord.ext import commands

from app.core.cdn import CDNClient
from app.core.flags import FlagMeta
from app.core.help import HelpCommand
from app.core.helpers import GenericCommandError
from app.core.models import Cog, Command, GroupCommand, Context, PermissionSpec
from app.core.timers import TimerManager
from app.database import Database
from app.util import AnsiColor, AnsiStringBuilder
from app.util.pillow import FontManager
from app.web import app as web_app
from config import allowed_mentions, default_prefix, description, name as bot_name, owner, resolved_token, test_guild, \
    version

if TYPE_CHECKING:
    from quart import Quart

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

        bypass_checks: bool
        cdn: CDNClient
        db: Database
        fonts: FontManager
        session: ClientSession
        startup_timestamp: datetime
        user_to_member_mapping: dict[int, discord.Member]
        timers: TimerManager
        # translator: AsyncTranslator
        web: Quart

    # TODO: if guild logging is enabled then Intents.all() may have to be used instead
    INTENTS: Final[ClassVar[discord.Intents]] = discord.Intents(
        emojis_and_stickers=True,
        guilds=True,
        members=True,
        messages=True,
        message_content=True,
        presences=True,
        reactions=True,
        voice_states=True,
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
        self._web_run_task: asyncio.Task | None = None

    async def resolve_command_prefix(self, message: discord.Message) -> list[str]:
        """Resolves a command prefix from a message."""
        if not message.guild:
            return commands.when_mentioned_or(default_prefix)(self, message)

        record = await self.db.get_guild_record(message.guild.id)
        prefixes = sorted(record.prefixes, key=len, reverse=True)

        return commands.when_mentioned_or(*prefixes)(self, message)

    async def _dispatch_first_ready(self) -> None:
        """Waits for the inbound READY gateway event, then dispatches the `first_ready` event."""
        await self.wait_until_ready()
        self.dispatch('first_ready')

    # noinspection PyUnresolvedReferences
    async def _load_from_module_spec(self, spec: importlib.machinery.ModuleSpec, key: str) -> None:
        # An awfully hacky solution and I really don't like it this way.
        # Maybe I'll come up with a better implementation later.
        try:
            await super()._load_from_module_spec(spec, key)
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
        await cog.simple_setup(self)

        self._BotBase__extensions[key] = lib

    async def _load_extensions(self) -> None:
        """Loads all command extensions, including Jishaku."""
        await self.load_extension('jishaku')

        for file in os.listdir('./app/extensions'):
            if file == 'compat.py' or file.startswith('_') or not file.endswith('.py'):
                continue

            extension = f'app.extensions.{file[:-3]}'
            try:
                await self.load_extension(extension)
            except Exception as exc:
                self.log.critical(f'Failed to load extension {extension}: {exc}', exc_info=True)
            else:
                self.log.info(f'Loaded extension: {extension}')

        await self.load_extension('app.extensions.compat')  # Load this last

    async def reload_extension(self, name: str, *, package: str | None = None) -> None:
        """Reloads an extension."""
        await super().reload_extension(name, package=package)
        self.prepare_jishaku_flags()

    def add_command(self, command: Command, /) -> None:
        if isinstance(command, Command):
            command.transform_flag_parameters()

        if isinstance(command, GroupCommand):
            for child in command.walk_commands():
                if isinstance(child, Command):
                    child.transform_flag_parameters()  # type: ignore

        super().add_command(command)

    async def setup_hook(self) -> None:
        """Prepares the bot for startup."""
        self.prepare_jishaku_flags()
        self.prepare_logger()

        self.bypass_checks = False
        self.db = await Database(loop=self.loop).wait()
        self.fonts = FontManager()
        self.session = ClientSession()
        self.user_to_member_mapping = {}
        self.timers = TimerManager(self)
        self.cdn = CDNClient(self)
        # self.translator = AsyncTranslator()
        #
        # await self.translator._session.close()
        # self.translator._AsyncTranslator__session = self.session

        web_app.bot = self
        self.web = web_app

        # Run the following in a task so that setup_hook is not blocked
        self.loop.create_task(self._dispatch_first_ready())

        # Only run the web app on Linux (remote server) for now
        if system() == 'Linux':
            self._web_run_task = self.loop.create_task(self.web.run_task('0.0.0.0', 8080))

        self.loop.create_task(self._setup_hook_task())

    async def _setup_hook_task(self) -> None:
        await self._load_extensions()

        if test_guild is not None:
            self.tree.copy_global_to(guild=discord.Object(id=test_guild))

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

    async def get_context(
        self,
        origin: discord.Message | discord.Interaction,
        /,
        *,
        cls: Type[Context] = Context,
    ) -> Context:
        return await super().get_context(origin, cls=cls)

    async def process_commands(self, message: discord.Message, /) -> None:
        if message.author.bot:
            return

        ctx = await self.get_context(message)
        await self.invoke(ctx)

    async def gather_messages(
        self,
        check: Callable[[discord.Message], bool | Awaitable[bool]] | None = None,
        *,
        limit: int | None = None,
        timeout: float | None = None,
        callback: Callable[[discord.Message], Any | Awaitable[Any]] | None = None,
    ) -> list[discord.Message]:
        """Creates a temporary concurrent listener to gather messages."""
        can_return = BooleanLike()
        messages_received = []
        start_time = perf_counter()

        def should_i_stop():
            if timeout and perf_counter() - start_time >= timeout:
                return True
            if limit and len(messages_received) >= limit:
                return True
            return False

        async def temporary_listener(message):
            if should_i_stop():
                can_return.unlock()
                return self.remove_listener(temporary_listener, 'on_message')

            if check and not await discord.utils.maybe_coroutine(check, message):  # type: ignore
                return

            messages_received.append(message)

            if callback:
                await discord.utils.maybe_coroutine(callback, message)

        self.add_listener(temporary_listener, 'on_message')
        while can_return.locked:
            await asyncio.sleep(0)
        return messages_received

    async def on_first_ready(self) -> None:
        """Prints startup information to the console."""
        self.startup_timestamp = discord.utils.utcnow()

        text = f'Ready as {self.user} ({self.user.id})'
        center = f' {bot_name} v{version} '

        print(format(center, f'=^{len(text)}'))
        print(text)

        self.log.info(f'Gateway received READY @ {self.startup_timestamp}')

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if message.content in {f'<@{self.user.id}>', f'<@!{self.user.id}>'}:
            prefix = await self.get_prefix(message)
            if prefix is None:
                prefix = default_prefix
            if isinstance(prefix, list):
                prefix = prefix[0]

            await message.reply(
                f"Hey, I'm {self.user.name}. My prefix here is **`{prefix}`**\nAdditionally, "
                f"most of my commands are available as slash commands.\n\nFor more help, run `{prefix}help`."
            )

        await self.process_commands(message)

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        if record := self.db.get_guild_record(guild.id, fetch=False):
            await record.delete()

    async def on_command_error(self, ctx: Context, error: Exception) -> Any:
        # sourcery no-metrics
        """Handles command errors."""
        error = getattr(error, 'original', error)

        blacklist = (
            commands.CommandNotFound,
        )
        if isinstance(error, blacklist):
            return

        if isinstance(error, commands.CommandOnCooldown):
            if not ctx.guild and ctx.channel.permissions_for(ctx.me).add_reactions:
                return await ctx.message.add_reaction('\U000023f3')

            return await ctx.send(
                'You are currently on cooldown.', reference=ctx.message, delete_after=15, ephemeral=True,
            )

        if isinstance(error, (commands.MaxConcurrencyReached, commands.CheckFailure)):
            return await ctx.send(error, reference=ctx.message, delete_after=15, ephemeral=True)

        if isinstance(error, (commands.MissingPermissions, commands.BotMissingPermissions)):
            if isinstance(error, commands.MissingPermissions):
                message = 'You are missing the following permissions required to run this command:'
            else:
                message = 'I am missing the following permissions required to execute this command:'

            missing = '\n'.join(f'- {PermissionSpec.permission_as_str(perm)}' for perm in error.missing_permissions)
            message += '\n' + missing

            permissions = ctx.channel.permissions_for(ctx.me)
            if ctx.guild and (permissions.administrator or permissions.send_messages and permissions.read_message_history):
                await ctx.send(message, reference=ctx.message, ephemeral=True)
                return

            if permissions.administrator or permissions.add_reactions:
                await ctx.message.add_reaction('\U000026a0')

            try:
                await ctx.author.send(message)
            except discord.Forbidden:
                pass

            return

        # Parameter-based errors.

        if isinstance(error, commands.BadArgument):
            if isinstance(error, GenericCommandError):
                return await ctx.send(error, reference=ctx.message, delete_after=15, ephemeral=True)

            ctx.command.reset_cooldown(ctx)
            param = ctx.current_parameter

        elif isinstance(error, commands.MissingRequiredArgument):
            param = error.param

        else:
            self.log.critical(f'Uncaught error occured when trying to invoke {ctx.command.name}: {error}', exc_info=error)

            await ctx.send(f'panic!({error})', reference=ctx.message)
            raise error

        builder = AnsiStringBuilder()
        builder.append('Attempted to parse command signature:').newline(2)
        builder.append('    ' + ctx.clean_prefix, color=AnsiColor.white, bold=True)

        if ctx.invoked_parents and ctx.invoked_subcommand:
            invoked_with = ' '.join((*ctx.invoked_parents, ctx.invoked_with))
        elif ctx.invoked_parents:
            invoked_with = ' '.join(ctx.invoked_parents)
        else:
            invoked_with = ctx.invoked_with

        builder.append(invoked_with + ' ', color=AnsiColor.green, bold=True)

        command = ctx.command
        signature = Command.ansi_signature_of(command)
        builder.extend(signature)
        signature = signature.raw

        if match := re.search(
            fr"[<\[](--)?{re.escape(param.name)}((=.*)?| [<\[]\w+(\.{{3}})?[>\]])(\.{{3}})?[>\]](\.{{3}})?",
            signature,
        ):
            lower, upper = match.span()
        elif isinstance(param.annotation, FlagMeta):
            param_store = command.params
            old = command.params.copy()

            flag_key, _ = next(filter(lambda p: p[1].annotation is command.custom_flags, param_store.items()))

            del param_store[flag_key]
            lower = len(command.raw_signature) + 1

            command.params = old
            del param_store

            upper = len(command.signature) - 1
        else:
            lower, upper = 0, len(command.signature) - 1

        builder.newline()

        offset = len(ctx.clean_prefix) + len(invoked_with)
        content = f'{" " * (lower + offset + 5)}{"^" * (upper - lower)} Error occured here'
        builder.append(content, color=AnsiColor.gray, bold=True).newline(2)
        builder.append(str(error), color=AnsiColor.red, bold=True)

        if invoked_with != ctx.command.qualified_name:
            builder.newline(2)
            builder.append('Hint: ', color=AnsiColor.white, bold=True)

            builder.append('command alias ')
            builder.append(repr(invoked_with), color=AnsiColor.cyan, bold=True)
            builder.append(' points to ')
            builder.append(ctx.command.qualified_name, color=AnsiColor.green, bold=True)
            builder.append(', is this correct?')

        ansi = builder.ensure_codeblock().dynamic(ctx)
        await ctx.send(f'Could not parse your command input properly:\n{ansi}', reference=ctx.message, ephemeral=True)

    async def close_web_app(self) -> None:
        """Closes the Quart web application."""
        if self._web_run_task is not None:
            await self.web.shutdown()

            if not self._web_run_task.done():
                self._web_run_task.cancel()

    async def close(self) -> None:
        """Closes this bot and it's aiohttp ClientSession."""
        await self.session.close()
        await self.close_web_app()
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


class BooleanLike:
    """Simple class (Emulates a boolean)"""
    def __init__(self, locked=True):
        self._locked = locked

    @property
    def locked(self):
        return self._locked

    def lock(self):
        self._locked = True

    def unlock(self):
        self._locked = False

    def switch(self):
        self._locked = not self._locked

    def __bool__(self):
        return self.locked

    def __int__(self):
        return int(self.locked)

    def __repr__(self):
        return f'<{self.__class__.__name__} locked={self.locked}>'
