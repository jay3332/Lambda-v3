from __future__ import annotations

import asyncio
import os
import platform
from typing import Any, Awaitable, overload

import asyncpg

from bot.types.common import Snowflake
from bot.types.leveling import LevelingConfig, LevelingData, RankCard

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

    def fetchrow(self, query: str, *args: Any, timeout: float = None) -> Awaitable[asyncpg.Record | None]:
        return self._internal_pool.fetchrow(query, *args, timeout=timeout)

    def fetchval(self, query: str, *args: Any, column: str | int = 0, timeout: float = None) -> Awaitable[Any | None]:
        return self._internal_pool.fetchval(query, *args, column=column, timeout=timeout)


class Database(_Database):
    def __init__(self, *, loop: asyncio.AbstractEventLoop | None = None) -> None:
        super().__init__(loop=loop)

    def get_all_leveling_configurations(self, *, connection: asyncpg.Connection | None = None) -> Awaitable[list[LevelingConfig]]:
        connection = connection or self
        return connection.fetch('SELECT * FROM level_config;')

    async def get_leveling_stats(
        self,
        user_id: Snowflake,
        guild_id: Snowflake,
        *,
        connection: asyncpg.Connection | None = None,
    ) -> LevelingData:
        async with (connection or self.acquire()) as connection:
            query = """
                    SELECT user_id, level, xp, 
                    RANK () OVER (ORDER BY level DESC, xp DESC) AS rank 
                    FROM levels
                    WHERE guild_id = $2
                    ORDER BY (user_id = $1) DESC;
                    """

            data = await connection.fetchrow(query, user_id, guild_id)
            if data['user_id'] == user_id:
                return data

            query = """
                    INSERT INTO levels (user_id, guild_id, level, xp)
                    VALUES ($1, $2, 0, 0);
                    """

            await connection.execute(query, user_id, guild_id)
            return await self.get_leveling_stats(user_id, guild_id, connection=connection)

    async def get_rank_card(self, user_id: Snowflake, *, connection: asyncpg.Connection | None = None) -> RankCard:
        async with connection or self.acquire() as connection:
            query = 'SELECT * FROM rank_cards WHERE user_id = $1'

            if data := await connection.fetchrow(query, user_id):
                return data

            query = 'INSERT INTO rank_cards (user_id) VALUES ($1) RETURNING rank_cards.*;'
            return await connection.fetchrow(query, user_id)

    async def get_level_config(self, guild_id: Snowflake, *, connection: asyncpg.Connection | None = None) -> LevelingConfig:
        async with connection or self.acquire() as connection:
            query = 'SELECT * FROM level_config WHERE guild_id = $1'

            if data := await connection.fetchrow(query, guild_id):
                return data

            query = 'INSERT INTO level_config (guild_id) VALUES ($1) RETURNING level_config.*;'
            return await connection.fetchrow(query, guild_id)
