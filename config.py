from os import getenv as env
from platform import system
from typing import Collection, Literal, NamedTuple

from discord import AllowedMentions
from dotenv import load_dotenv

load_dotenv()

__all__ = (
    'beta',
    'name',
    'version',
    'description',
    'owner',
    'default_prefix',
    'allowed_mentions',
    'resolved_token',
    'DatabaseConfig',
    'Colors',
    'Emojis',
)


def txt(path: str) -> str | None:
    try:
        with open(path, 'r') as fp:
            return fp.read().strip()
    except FileNotFoundError:
        return


class VersionInfo(NamedTuple):
    """Represents versioning information.

    This is represented as follows:
    <major>.<minor>.<micro><releaselevel*><serial**>

    * releaselevel will be represented as one of 'a', 'b', or 'rc'.
    ** serial (and releaselevel) will not be present if the releaselevel is final.
    """
    major: int
    minor: int
    micro: int
    releaselevel: Literal['alpha', 'beta', 'candidate', 'final'] = 'final'
    serial: int = 0

    def __str__(self) -> str:
        mapping: dict[str, str] = {
            'alpha': 'a',
            'beta': 'b',
            'candidate': 'rc',
            'final': '',
        }

        serial = self.serial if self.releaselevel != 'final' else ''
        return f'{self.major}.{self.minor}.{self.micro}{mapping[self.releaselevel]}{serial}'


# Below this comment are defined the configuration variables for this application.

beta: bool = system() == 'Windows'  # can be changed to liking

name: str = 'Lambda'
version: VersionInfo = VersionInfo(major=3, minor=0, micro=0, releaselevel='alpha', serial=0)
description: str = 'A multipurpose bot for Discord'
support_server: str = 'https://discord.gg/vuAPY6MQF5'
website: str = 'https://lambdabot.cf'

owner: Collection[int] | int = 414556245178056706
default_prefix: Collection[str] | str = '>'
allowed_mentions: AllowedMentions = AllowedMentions(everyone=False, users=True, roles=False, replied_user=False)

token: str = env('DISCORD_TOKEN')
beta_token: str = env('DISCORD_BETA_TOKEN')
client_secret: str = env('DISCORD_CLIENT_SECRET')

resolved_token: str = beta_token if beta else token


class DatabaseConfig:
    """Database configuration variables."""
    database: str = 'lambda_v3'
    user: str = 'postgres'

    host: str | None = '127.0.0.1' if beta else 'localhost'
    port: int | None = None

    password: str | None = env('DB_PASSWORD')
    beta_password: str | None = txt('local_db_password.txt')

    resolved_password: str = beta_password if beta else password

    @classmethod
    def as_kwargs(cls) -> dict[str, str | int | None]:
        return {
            'database': cls.database,
            'user': cls.user,
            'host': cls.host,
            'port': cls.port,
            'password': cls.resolved_password,
        }


class Colors:
    """The color scheme for Lambda."""
    primary: int = 0x6bcbe8
    success: int = 0x4dff76
    warning: int = 0xfcba03
    error: int = 0xff576a
    blend: int = 0x2f3136


class Emojis:
    """Emojis used by Lambda."""

    class Arrows:
        previous: str = '\u25c0'
        forward: str = '\u25b6'
        first: str = '\u23ea'
        last: str = '\u23e9'
