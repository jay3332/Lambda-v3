from __future__ import annotations

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

from app.core.models import Cog, Context
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

    # TODO: if guild logging is enabled then Intents.all() may have to be used instead
    INTENTS: Final[ClassVar[discord.Intents]] = discord.Intents(
        emojis_and_stickers=True,
        guilds=True,
        members=True,
        messages=True,
        message_content=True,
        reactions=True,
    )

    def __init__(self) -> None:
        key = 'owner_id' if isinstance(owner, int) else 'owner_ids'

        super().__init__(
            command_prefix=self.__class__.resolve_command_prefix,
            # help_command=HelpCommand(),
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

    def _try_load_extension(self, extension: str) -> None:
        """Attemps to load an extension. If there is no setup function, a cog lookup is attempted."""
        try:
            self.load_extension(extension)
        except commands.NoEntryPointError:
            pass
        else:
            return

        module = importlib.import_module(extension)
        members = inspect.getmembers(module, lambda member: isinstance(member, Cog))

        if not members:
            raise

        cog = members[0][1]  # (_cls_name, cog), *_other_cogs
        module.setup = cog.simple_setup
        self.load_extension(extension)

    def _load_extensions(self) -> None:
        """Loads all command extensions, including Jishaku."""
        self.load_extension('jishaku')

        for file in os.listdir('./app/extensions'):
            if file == 'compat.py' or file.startswith('_') or not file.endswith('.py'):
                continue

            extension = f'app.extensions.{file[:-3]}'
            try:
                self._try_load_extension(extension)
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
            return await ctx.send(error)

        if isinstance(error, commands.CommandOnCooldown):
            if not ctx.guild and ctx.channel.permissions_for(ctx.me).add_reactions:
                return await ctx.message.add_reaction('\U000023f3')

            return await ctx.reply('You are currently on cooldown.')

        self.log.critical(f'Uncaught error occured when trying to invoke {ctx.command.name}: {error}', exc_info=error)
        raise error

    async def close(self) -> None:
        """Closes this bot and it's aiohttp ClientSession."""
        await self.session.close()
        await super().close()

    def run(self) -> None:
        """Runs the bot."""
        return super().run(resolved_token)
