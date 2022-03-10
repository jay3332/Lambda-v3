from __future__ import annotations

import asyncio
from typing import Any, Awaitable, TYPE_CHECKING, overload

import asyncpg

from config import DatabaseConfig
from .migrations import Migrator

if TYPE_CHECKING:
    pass

__all__ = (
    'Database',
)


class _Database:
    _internal_pool: asyncpg.Pool

    def __init__(self, *, loop: asyncio.AbstractEventLoop = None) -> None:
        self.loop: asyncio.AbstractEventLoop = loop or asyncio.get_event_loop()
        self.loop.create_task(self._connect())

    async def _connect(self) -> None:
        self._internal_pool = await asyncpg.create_pool(**DatabaseConfig.as_kwargs())

        async with self.acquire() as conn:
            migrator = Migrator(conn)
            await migrator.run_migrations()

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
