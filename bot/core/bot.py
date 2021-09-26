from __future__ import annotations

import os
from typing import Any, Generator

import discord
import aiohttp
from dotenv import load_dotenv

from .database import Database
from .models import _BaseBot
from bot.features.leveling.core import LevelingManager
from bot.utils.pillow import FontManager

load_dotenv()

__all__ = (
    'Lambda',
)


class Lambda(_BaseBot):
    """Represents an instance of Lambda."""

    def __init__(self) -> None:
        super().__init__(
            intents=discord.Intents.all(),
            update_application_commands_at_startup=True,
        )
        self._setup()
        self._load_extensions()

    def _setup(self) -> None:
        self.db: Database = Database()
        self.fonts: FontManager = FontManager()
        self.leveling: LevelingManager = LevelingManager(bot=self)
        self.session: aiohttp.ClientSession = aiohttp.ClientSession()

    @staticmethod
    def _walk_extensions() -> Generator[str, Any, Any]:
        yield from (
            f'bot.extensions.{extension[:-3]}'
            for extension in os.listdir('./bot/extensions')
            if extension.endswith('.py') and not extension.startswith('_')
        )

    def _load_extensions(self) -> None:
        for extension in self._walk_extensions():
            self.load_extension(extension)

    async def on_ready(self) -> None:
        print(f'Logged in as {self.user} (ID: {self.user.id})')

    def run(self) -> None:
        super().run(os.environ['TOKEN'])

    async def close(self) -> None:
        await self.session.close()
        await super().close()
