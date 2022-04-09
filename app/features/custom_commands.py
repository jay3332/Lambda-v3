from __future__ import annotations

import datetime
from collections import defaultdict
from typing import Any, Awaitable, Callable, NamedTuple, TYPE_CHECKING

import discord
from discord.ext.commands import MemberConverter, MemberNotFound
from discord.ext.commands.core import get_signature_parameters, unwrap_function

from app.core.models import Command, PermissionSpec
from app.database.core import BaseRecord
from app.util import converter
from app.util.tags import execute_python_tag, execute_tags

if TYPE_CHECKING:
    from asyncpg import Connection
    from discord import Member
    from discord.abc import Snowflake

    from app.core import Bot, Context
    from app.database import Database


@converter
async def MemberAndStrConverter(ctx: Context, argument: str) -> tuple[str, Member | None]:
    try:
        member = await MemberConverter().convert(ctx, argument)
    except MemberNotFound:
        member = None

    return argument, member


class CustomCommandResponse(NamedTuple):
    """Represents information about a response of a command."""
    content: str
    is_python: bool = False

    async def execute(self, ctx: Context, *args: tuple[str, Member | None]) -> None:
        """Executes the response."""
        text_args = [arg for arg, _ in args]
        target = next((member for _, member in args if member is not None), None)

        kwargs = dict(
            bot=ctx.bot,
            message=ctx.message,
            channel=ctx.channel,
            target=target,
            args=text_args,
        )

        if self.is_python:
            return await execute_python_tag(**kwargs, code=self.content)

        await execute_tags(**kwargs, content=self.content)


class CustomCommandRecord(BaseRecord):
    def __init__(self, manager: CustomCommandManager, *, name: str, guild_id: int) -> None:
        self.manager: CustomCommandManager = manager
        self.data: dict[str, Any] = {}

        self.name: str = name
        self.guild_id: int = guild_id

    async def fetch(self) -> CustomCommandRecord:
        """Fetches the record from the database."""
        db = self.manager.db

        if data := await db.fetchrow(
            """
            SELECT
                *
            FROM
                custom_commands
            WHERE
                name = $1
            AND
                guild_id = $2
            """,
            self.name,
            self.guild_id,
        ):
            self.data.update(data)
        else:
            # TODO: raise something other than ValueError
            raise ValueError(f'unknown command {self.name!r} in guild with ID {self.guild_id}')

        return self

    async def _update(
        self,
        key: Callable[[tuple[int, str]], str],
        values: dict[str, Any],
        *,
        connection: Connection | None = None,
    ) -> CustomCommandRecord:
        query = """
                UPDATE
                    custom_commands
                SET {}
                WHERE
                    name = $1
                AND
                    guild_id = $2
                RETURNING
                    *
                """

        # noinspection PyTypeChecker
        self.data.update(
            await (connection or self.manager.db).fetchrow(
                query.format(', '.join(map(key, enumerate(values.keys(), start=3)))),
                self.name,
                self.guild_id,
                *values.values(),
            ),
        )
        return self

    @property
    def created_at(self) -> datetime.datetime:
        """When this command was created."""
        return self.data['created_at']

    @property
    def response_content(self) -> str:
        """The response content of the command."""
        return self.data['response']

    @property
    def is_python(self) -> bool:
        """Whether the response is Python code."""
        return self.data['is_python']

    @property
    def response(self) -> CustomCommandResponse:
        """The response of the command."""
        return CustomCommandResponse(
            content=self.response_content,
            is_python=self.is_python,
        )

    @property
    def required_permissions(self) -> PermissionSpec:
        """The required permissions of the command."""
        spec = PermissionSpec.new()
        spec.user.update(p for p, value in discord.Permissions(self.data['required_permissions']) if value)

        return spec

    @property
    def toggled_user_ids(self) -> list[int]:
        return self.data['toggled_users']

    @property
    def toggled_role_ids(self) -> list[int]:
        return self.data['toggled_roles']

    @property
    def toggled_channel_ids(self) -> list[int]:
        return self.data['toggled_channels']

    @property
    def is_whitelist_toggle(self) -> bool:
        return self.data['is_whitelist_toggle']


class CustomCommandManager:
    """Manages and registers custom commands."""

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot
        self.db: Database = bot.db

        self._records: defaultdict[int, dict[str, CustomCommandRecord]] = defaultdict(dict)
        self.__task = self.bot.loop.create_task(self.fetch_all_records())

    async def wait(self) -> CustomCommandManager:
        """Waits for the manager to finish loading."""
        await self.__task
        return self

    def validate_name(self, name: str) -> str:
        name = name.strip().casefold()

        if not 1 <= len(name) <= 50:
            raise ValueError('Custom commands can only between 1 and 50 characters long.')

        if any(map(str.isspace, name)):
            raise ValueError('Custom commands cannot contain whitespace.')

        if command := self.bot.get_command(name):
            if isinstance(command, CustomCommand):
                raise ValueError(f'A custom command with the name {name!r} already exists.')

            raise ValueError(f'A command with the name {name!r} already exists.')

        return name

    async def fetch_all_records(self) -> None:
        """Fetchs all custom commands from the database."""
        db = self.db
        await db.wait()

        for data in await db.fetch(
            """
            SELECT
                *
            FROM
                custom_commands
            """
        ):
            guild_id = data['guild_id']
            name = data['name']

            record = self._records[guild_id].setdefault(
                name,
                CustomCommandRecord(name=name, guild_id=guild_id, manager=self),
            )
            record.data = data

            self.register_command(name)

    async def fetch_records(self, *, guild: Snowflake) -> dict[str, CustomCommandRecord]:
        """Fetches all custom commands for the given guild."""
        db = self.db
        records = self._records[guild.id]

        for data in await db.fetch(
            """
            SELECT
                *
            FROM
                custom_commands
            WHERE
                guild_id = $1
            """,
            guild.id,
        ):
            r = records.setdefault(data['name'], CustomCommandRecord(self, name=data['name'], guild_id=guild.id))
            r.data = data

        return records

    def fetch_record(self, *, name: str, guild: Snowflake) -> Awaitable[CustomCommandRecord]:
        """Fetches the record of a command if necessary."""
        guild_records = self._records[guild.id]
        record = guild_records.setdefault(name, CustomCommandRecord(self, name=name, guild_id=guild.id))

        return record.fetch_if_necessary()

    def register_command(self, name: str) -> CustomCommand | None:
        if command := self.bot.get_command(name):
            if not isinstance(command, CustomCommand):
                # this custom command was created before this command was created.
                # in this case the non-custom command should overwrite it.
                # TODO: possibly remove the custom command in the future
                return

            return command

        self.bot.add_command(command := CustomCommand(name=name, manager=self))
        return command

    # TODO: support command aliases
    async def add_command(
        self,
        *,
        name: str,
        guild: Snowflake,
        response: str,
        is_python: bool = False,
        required_permissions: discord.Permissions | None = None,
        toggled_users: list[int] = None,
        toggled_roles: list[int] = None,
        toggled_channels: list[int] = None,
        is_whitelist_toggle: bool = False,
    ) -> CustomCommandRecord:
        """Adds a new command."""
        query = """
                INSERT INTO
                    custom_commands(
                        name,
                        guild_id,
                        response,
                        is_python,
                        required_permissions,
                        toggled_users,
                        toggled_roles,
                        toggled_channels,
                        is_whitelist_toggle
                    )
                VALUES (
                    $1,
                    $2,
                    $3,
                    $4,
                    COALESCE($5, 0),
                    $6,
                    $7,
                    $8,
                    $9
                )
                RETURNING
                    *
                """

        data = await self.db.fetchrow(
            query,
            name,
            guild.id,
            response,
            is_python,
            required_permissions and required_permissions.value,
            toggled_users or [],
            toggled_roles or [],
            toggled_channels or [],
            is_whitelist_toggle,
        )
        record = CustomCommandRecord(self, name=name, guild_id=guild.id)
        record.data = data

        self.register_command(name)
        return record


def _sig(_ctx, *_args: MemberAndStrConverter):
    ...


class CustomCommand(Command):
    """Represents a custom command created by a user."""

    def __init__(self, name: str, *, manager: CustomCommandManager) -> None:
        super().__init__(self.command_callback, name=name, hidden=True)
        self.manager: CustomCommandManager = manager

        unwrap = unwrap_function(_sig)
        self.module = unwrap.__module__  # type: ignore

        try:
            globalns = unwrap.__globals__  # type: ignore
        except AttributeError:
            globalns = {}

        self.params = get_signature_parameters(_sig, globalns)

    async def get_record(self, guild: Snowflake) -> CustomCommandRecord| None:
        """Gets the response of the command."""
        return await self.manager.fetch_record(name=self.name, guild=guild)

    async def command_callback(self, ctx: Context, *args: tuple[str, discord.Member | None]) -> None:
        """A custom command."""
        if not ctx.guild:
            return

        try:
            record = await self.get_record(ctx.guild)
        except ValueError:
            return

        if not record.required_permissions.check(ctx):
            return

        if record.is_whitelist_toggle and ctx.author.id not in record.toggled_user_ids:
            return

        elif ctx.author.id in record.toggled_user_ids:
            return

        if record.is_whitelist_toggle and ctx.channel.id not in record.toggled_channel_ids:
            return

        elif ctx.channel.id in record.toggled_channel_ids:
            return

        if record.is_whitelist_toggle and all(role.id not in record.toggled_role_ids for role in ctx.author.roles):
            return

        elif any(role.id in record.toggled_role_ids for role in ctx.author.roles):
            return

        await record.response.execute(ctx, *args)
