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
test_guild: int = 809972022360539206
default_prefix: Collection[str] | str = '>'
allowed_mentions: AllowedMentions = AllowedMentions(everyone=False, users=True, roles=False, replied_user=False)

token: str = env('DISCORD_TOKEN')
beta_token: str = env('DISCORD_BETA_TOKEN')
client_secret: str = env('DISCORD_CLIENT_SECRET')
cdn_authorization: str = env('CDN_AUTHORIZATION')

resolved_token: str = beta_token if beta else token

# (host, port, password, secure?)
lavalink_nodes: Collection[tuple[str, int, str | None, bool]] = [
    ('lavalink.gaproknetwork.xyz', 2333, 'gaproklavalink', False),
] if beta else [
    ('127.0.0.1', 2333, 'youshallnotpass', False),
]


class DatabaseConfig:
    """Database configuration variables."""
    database: str = 'lambda_v3'
    user: str = 'postgres'

    host: str | None = '127.0.0.1' if beta else 'localhost'
    port: int | None = None

    password: str | None = env('DB_PASSWORD')
    beta_password: str | None = txt('local_db_password.txt')

    resolved_password: str | None = beta_password if beta else password

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

    plus: str = '<:plus:962150204973395998>'
    loading: str = '<a:loading:812768154198736898>'
    youtube: str = '<:youtube:967577973018472458>'
    soundcloud: str = '<:soundcloud:969011164426145895>'
    arrow: str = '<:arrow:831333449562062908>'
    space: str = '<:space:968652789498671104>'

    class Arrows:
        previous: str = '\u25c0'
        forward: str = '\u25b6'
        first: str = '\u23ea'
        last: str = '\u23e9'

    class Statuses:
        online: str = '<:status_online:834163239126433842>'
        idle: str = '<:status_idle:834163240740716624>'
        dnd: str = '<:status_dnd:834163242041475092>'
        offline: str = '<:status_offline:834163243278794762>'
        streaming: str = '<:status_streaming:834167604389347369>'

    enabled: str = Statuses.online
    disabled: str = Statuses.dnd

    class ProgressBar:
        left_empty: str = '<:pb_left_0:937082616333602836>'
        left_low: str = '<:pb_left_1:937082634046173194>'
        left_mid: str = '<:pb_left_2:937082669068595300>'
        left_high: str = '<:pb_left_3:937082728376045598>'
        left_full: str = '<:pb_left_4:937082777927561297>'

        mid_empty: str = '<:pb_mid_0:937082833107828786>'
        mid_low: str = '<:pb_mid_1:937082868226752552>'
        mid_mid: str = '<:pb_mid_2:937082902880083988>'
        mid_high: str = '<:pb_mid_3:937082944655351860>'
        mid_full: str = '<:pb_mid_4:937082993057595473>'

        right_empty: str = '<:pb_right_0:937083054340595803>'
        right_low: str = '<:pb_right_1:937083097969754193>'
        right_mid: str = '<:pb_right_2:937083245173026887>'
        right_high: str = '<:pb_right_3:937083276827439164>'
        right_full: str = '<:pb_right_4:937083328648056862>'

    class MusicBarEmojis:
        L0 = '<:music_bar_left_0:853703674634436629>'
        L1 = '<:music_bar_left_1:853703674593673256>'
        M0 = '<:music_bar_middle_0:853701237802139689>'
        M1 = '<:music_bar_middle_1:853702246957056001>'
        R0 = '<:music_bar_right_0:853703674585153566>'
        R1 = '<:music_bar_right_1:853703674638499871>'
        LIVE = '<:music_live:853724359947714610>'

    class ExpansionEmojis:
        first = '<:expansion_first:968651020097945811>'
        mid = '<:expansion_mid:968652421721120828>'
        last = '<:expansion_last:968652421700124723>'
        ext = '<:expansion_ext:968653920106872842>'
        single = '<:expansion_single:968652421377167371>'
