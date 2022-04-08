from __future__ import annotations

from typing import Any, Awaitable, Callable, NamedTuple, TYPE_CHECKING

import discord
from discord.ext.commands import MemberConverter, MemberNotFound

from app.core.models import Command
from app.database.core import BaseRecord
from app.util import converter
from app.util.tags import execute_python_tag, execute_tags

if TYPE_CHECKING:
    from asyncpg import Connection
    from discord import Member
    from discord.abc import Snowflake

    from app.core import Bot, Cog, Context, PermissionSpec
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
        spec.user = {p for p, value in discord.Permissions(self.data['required_permissions']) if value}

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

        self._records: dict[int, dict[str, CustomCommandRecord]] = {}

    def fetch_record(self, *, name: str, guild: Snowflake) -> Awaitable[CustomCommandRecord]:
        """Fetches the record of a command if necessary."""
        guild_records = self._records.setdefault(guild.id, {})
        record = guild_records.setdefault(name, CustomCommandRecord(self, name=name, guild_id=guild.id))

        return record.fetch_if_necessary()

    def register_command(self, name: str) -> CustomCommand:
        if command := self.bot.get_command(name):
            if not isinstance(command, CustomCommand):
                raise ValueError(f'existing command {name}')

            return command

        self.bot.add_command(command := CustomCommand(name=name, manager=self))
        return command

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
                    COALESCE($3, 0),
                    $4,
                    $5,
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

        return record


class CustomCommand(Command[Cog]):
    """Represents a custom command created by a user."""

    def __init__(self, name: str, *, manager: CustomCommandManager) -> None:
        super().__init__(self.command_callback, name=name, hidden=True)
        self.manager: CustomCommandManager = manager

    async def get_response(self, guild: Snowflake) -> CustomCommandResponse | None:
        """Gets the response of the command."""
        try:
            record = await self.manager.fetch_record(name=self.name, guild=guild)
        except ValueError:
            return None

        return record.response

    async def command_callback(self, ctx: Context, *args: MemberAndStrConverter) -> None:
        """A custom command."""
        if not ctx.guild:
            return

        response = await self.get_response(ctx.guild)
        if response is None:
            return

        await response.execute(ctx, *args)
