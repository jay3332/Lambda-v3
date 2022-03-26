from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Literal, TYPE_CHECKING, overload

import asyncpg

from config import DatabaseConfig
from .migrations import Migrator

if TYPE_CHECKING:
    pass

__all__ = (
    'Database',
)


class _Database:
    __slots__ = ('_internal_pool', '_connect_task', 'loop')

    _internal_pool: asyncpg.Pool

    def __init__(self, *, loop: asyncio.AbstractEventLoop = None) -> None:
        self.loop: asyncio.AbstractEventLoop = loop or asyncio.get_event_loop()
        self._connect_task: asyncio.Task = self.loop.create_task(self._connect())

    async def _connect(self) -> None:
        self._internal_pool = await asyncpg.create_pool(**DatabaseConfig.as_kwargs())

        async with self.acquire() as conn:
            migrator = Migrator(conn)
            await migrator.run_migrations()

    async def wait(self) -> None:
        await self._connect_task

    @overload
    def acquire(self, *, timeout: float = None) -> Awaitable[asyncpg.Connection]:
        ...

    def acquire(self, *, timeout: float = None) -> asyncpg.pool.PoolAcquireContext:
        return self._internal_pool.acquire(timeout=timeout)

    def execute(self, query: str, *args: Any, timeout: float = None) -> Awaitable[str]:
        return self._internal_pool.execute(query, *args, timeout=timeout)

    def fetch(self, query: str, *args: Any, timeout: float = None) -> Awaitable[list[asyncpg.Record]]:
        return self._internal_pool.fetch(query, *args, timeout=timeout)

    def fetchrow(self, query: str, *args: Any, timeout: float = None) -> Awaitable[asyncpg.Record]:
        return self._internal_pool.fetchrow(query, *args, timeout=timeout)

    def fetchval(self, query: str, *args: Any, column: str | int = 0, timeout: float = None) -> Awaitable[Any]:
        return self._internal_pool.fetchval(query, *args, column=column, timeout=timeout)


class Database(_Database):
    """Manages transactions to and from the database.

    Additionally, this is where you will find the cache which stores records to be used later.
    """

    def __init__(self, *, loop: asyncio.AbstractEventLoop = None) -> None:
        super().__init__(loop=loop)

        self._guild_records: dict[int, GuildRecord] = {}

    @overload
    def get_guild_record(self, guild_id: int, *, fetch: Literal[True] | None = None) -> Awaitable[GuildRecord]:
        ...

    @overload
    def get_guild_record(self, guild_id: int, *, fetch: Literal[False] = None) -> GuildRecord | None:
        ...

    def get_guild_record(self, guild_id: int, *, fetch: bool | None = None) -> GuildRecord | Awaitable[GuildRecord]:
        """Fetches a guild record."""
        try:
            record = self._guild_records[guild_id]
        except KeyError:
            record = self._guild_records[guild_id] = GuildRecord(guild_id, db=self)

        if fetch:
            return record.fetch()

        elif fetch is None:
            return record.fetch_if_necessary()

        return record


class GuildRecord:
    """Represents a guild record in the database."""

    def __init__(self, guild_id: int, *, db: Database) -> None:
        self.guild_id: int = guild_id
        self.data: dict[str, Any] = {}
        self.db: Database = db

    async def fetch(self) -> GuildRecord:
        """Fetches the guild record from the database."""
        query = """
                INSERT INTO guilds (guild_id) VALUES ($1)
                ON CONFLICT (guild_id)
                DO UPDATE SET guild_id = $1
                RETURNING *
                """

        self.data.update(await self.db.fetchrow(query, self.guild_id))
        return self

    async def fetch_if_necessary(self) -> GuildRecord:
        """Fetches the guild record from the database if it is not already cached."""
        if not self.data:
            await self.fetch()
        return self

    async def _update(self, key: Callable[[tuple[int, str]], str], values: dict[str, Any], *, connection: asyncpg.Connection | None = None) -> GuildRecord:
        query = """
                UPDATE guilds SET {} WHERE guild_id = $1
                RETURNING *;
                """

        # noinspection PyTypeChecker
        self.data.update(
            await (connection or self.db).fetchrow(
                query.format(', '.join(map(key, enumerate(values.keys(), start=2)))),
                self.guild_id,
                *values.values(),
            ),
        )
        return self

    def update(self, *, connection: asyncpg.Connection | None = None, **values: Any) -> Awaitable[GuildRecord]:
        return self._update(lambda o: f'"{o[1]}" = ${o[0]}', values, connection=connection)

    def append(self, *, connection: asyncpg.Connection | None = None, **values: Any) -> Awaitable[GuildRecord]:
        return self._update(lambda o: f'"{o[1]}" = ARRAY_APPEND("{o[1]}", ${o[0]})', values, connection=connection)

    @property
    def prefixes(self) -> list[str]:
        """Returns the guild's prefixes."""
        return self.data['prefixes']
