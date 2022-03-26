import asyncio
from platform import system
from sys import argv

import asyncpg

from app.database.migrations import Migrator
from config import DatabaseConfig


async def run_migrations() -> None:
    conn = await asyncpg.connect(**DatabaseConfig.as_kwargs())
    await Migrator(conn).run_migrations(debug=True)


if __name__ == '__main__':
    if system() == 'Linux':
        try:
            # noinspection PyUnresolvedReferences
            import uvloop
        except ModuleNotFoundError:
            pass
        else:
            uvloop.install()

    match argv:
        case [_, 'migrate' | 'm' | 'migration' | 'migrations', *args]:
            match args:
                case ['add' | 'new' | 'create' | '+', name]:
                    Migrator.create_migration(name)
                case ['run' | 'r' | 'execute' | 'exec']:
                    asyncio.run(run_migrations())
                case _:
                    raise RuntimeError('Invalid command.')
        case _:
            from app.core.bot import Bot

            Bot().run()
