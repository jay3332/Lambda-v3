from __future__ import annotations

import importlib
import sys

import discord
from discord.application_commands import (
    ApplicationCommandMeta as Command,
    ApplicationCommandTree as Tree,
)

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    ModuleSpec = importlib.machinery.ModuleSpec

__all__ = (
    '_BaseBot'
)


class _BaseBot(discord.Client):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._extensions: dict[str, ModuleSpec] = {}

    def _load_module(self, module: ModuleSpec) -> None:
        try:
            exports = getattr(module, '__commands__')
        except AttributeError:
            # TODO: this
            return

        guild_id = getattr(module, '__guild_id__', discord.utils.MISSING)

        for member in exports:
            if isinstance(member, Command):
                self.add_application_command(member, guild_id=guild_id)

            elif isinstance(member, Tree):
                member._guild_id = member._guild_id or guild_id
                self.add_application_command_tree(member)

        try:
            getattr(module, '__setup__')()
        except AttributeError:
            pass
        except Exception:
            raise

        self._extensions[module.__name__] = module

    def load_extension(self, name: str) -> None:
        module = importlib.import_module(name)
        self._load_module(module)

    def reload_extension(self, name: str) -> None:
        importlib.reload(self._extensions[name])
