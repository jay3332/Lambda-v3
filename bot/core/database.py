from __future__ import annotations

import asyncio
import os
import platform

import asyncpg

from bot.types.leveling import LevelingConfig

from typing import Any, Awaitable, overload

__all__ = (
    'Database',
)


class _Database:
    _internal_pool: asyncpg.Pool

    def __init__(self, *, loop: asyncio.AbstractEventLoop = None) -> None:
        self.loop: asyncio.AbstractEventLoop = loop or asyncio.get_event_loop()
        self.loop.create_task(self._connect())

    async def _connect(self) -> asyncpg.Pool:
        env_entry = 'BETA_DATABASE_PASSWORD' if platform.system() == 'Windows' else 'DATABASE_PASSWORD'

        self._internal_pool = await asyncpg.create_pool(
            host='127.0.0.1',
            user='postgres',
            database='lambda_rewrite',
            password=os.environ[env_entry]
        )
        await self._run_initial_query()

    async def _run_initial_query(self) -> None:
        def wrapper() -> str:
            with open('schema.sql') as fp:
                return fp.read()

        try:
            await self.execute(await asyncio.to_thread(wrapper))
        except OSError:
            pass

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
    def __init__(self, *, loop: asyncio.AbstractEventLoop = None) -> None:
        super().__init__(loop=loop)

    def get_leveling_configurations(self) -> Awaitable[list[LevelingConfig]]:
        return self.fetch('SELECT * FROM level_config;')
