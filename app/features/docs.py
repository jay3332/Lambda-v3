from __future__ import annotations

import asyncio
import re
import zlib
from enum import Enum
from io import BytesIO
from urllib.parse import urlparse
from typing import Any, AsyncGenerator, ClassVar, Collection, Final, Iterator, NamedTuple, TYPE_CHECKING

import discord
from aiohttp import ClientTimeout
from discord.ext.commands import BadArgument

from app.core.helpers import BAD_ARGUMENT
from app.util import AnsiColor, AnsiStringBuilder, UserView, cutoff
from app.util.common import wrap_exceptions
from app.util.pagination import LineBasedFormatter, Paginator
from app_native import SphinxDocumentResult, has_document, scrape_document
from config import Colors

if TYPE_CHECKING:
    from asyncio import AbstractEventLoop
    from logging import Logger

    from aiohttp import ClientSession

    from app.core import Bot, Context
    from app.util.types import CommandResponse


class DocumentationType(Enum):
    sphinx = 0


class DocumentationSource(NamedTuple):
    """Stores information for a documentation source."""
    key: str
    name: str
    url: str
    aliases: Collection[str] = ()
    type: DocumentationType = DocumentationType.sphinx

    @classmethod
    async def convert(cls, _ctx: Context, argument: str) -> DocumentationSource:
        """Converts a string to a documentation source."""
        argument = argument.lower()
        sources = DocumentationManager.SOURCES
        try:
            return sources[argument]
        except KeyError:
            if result := discord.utils.find(
                lambda source: argument in source.aliases,
                sources.values(),
            ):
                return result

            available = '`' + '` `'.join(source.key for source in sources.values()) + '`'
            raise BadArgument(f"Unknown documentation source: {argument!r}\n\nAvailable sources: {available}")


class ZlibStreamView:
    """Parses a zlib stream of Sphinx inventory."""

    CHUNK_SIZE: int = 16 * 1024  # 16 KiB

    def __init__(self, buffer: bytes) -> None:
        self.stream: BytesIO = BytesIO(buffer)

    async def _walk_chunks(self) -> AsyncGenerator[Any, Any, bytes]:
        """Read the stream by its confirgured chunk size."""
        decompressor = zlib.decompressobj()
        while True:
            chunk = self.stream.read(self.CHUNK_SIZE)
            if not chunk:
                break
            yield decompressor.decompress(chunk)

        yield decompressor.flush()

    async def walk(self) -> AsyncGenerator[Any, Any, str]:
        """Forms lines and yields them as they are formed."""
        buffer = b''

        async for chunk in self._walk_chunks():
            buffer += chunk
            idx = buffer.find(b'\n')

            while idx != -1:
                yield buffer[:idx].decode()
                buffer = buffer[idx + 1:]
                idx = buffer.find(b'\n')

        yield buffer.decode()


class SphinxDocumentationEntry(NamedTuple):
    """Stores information for a Sphinx documentation entry."""
    name: str
    url: str
    signature: AnsiStringBuilder | None
    embed: discord.Embed


class IndexingFailure(Exception):
    """Raised when an error occurs during indexing."""

    def __init__(self, inner: BaseException | str) -> None:
        if isinstance(inner, str):
            super().__init__(inner)
        else:
            super().__init__(f'{inner.__class__.__name__}: {inner}')

        self.original: BaseException | str = inner


class SphinxInventory:
    """Stores an inventory for Sphinx-based documentation."""

    ENTRY_REGEX: Final[ClassVar[re.Pattern[str]]] = re.compile(r'(?x)(.+?)\s+(\S*:\S*)\s+(-?\d+)\s+(\S+)\s+(.*)')

    def __init__(self, bot: Bot, source: DocumentationSource) -> None:
        self.log: Logger = bot.log
        self.loop: AbstractEventLoop = bot.loop
        self.session: ClientSession = bot.session
        self.source: DocumentationSource = source

        self.inventory: dict[str, str] = {}  # display name -> url
        self._key_lookup: dict[str, str] = {}  # display name -> key

        # Indexing
        self.entries: dict[str, SphinxDocumentationEntry] = {}  # key -> SphinxDocumentationEntry

    async def build(self) -> bool:
        """Fetches and builds the inventory from the source."""
        async with self.session.get(self.source.url + '/objects.inv') as response:
            if response.status != 200:
                self.log.error(f'Failed to fetch sphinx inventory from {self.source.url}')
                return False

            buffer = await response.read()
            view = ZlibStreamView(buffer)
            stream = view.stream

            header = stream.readline().decode().rstrip()
            try:
                version = int(header[-1])
            except (IndexError, ValueError):
                self.log.error(f'Failed to parse sphinx inventory version from {header}')
                return False

            maybe_project = stream.readline()
            maybe_version = stream.readline()

            if not (
                maybe_project.startswith(b'# Project')
                and maybe_version.startswith(b'# Version')
            ):
                self.log.error(f'Failed to parse sphinx inventory header from {header}')
                return False

            if version != 2:
                self.log.error(f'Unsupported sphinx inventory version {version}')
                return False

            if b'zlib' not in stream.readline():
                self.log.error('Incompatible sphinx inventory compression')
                return False

            await self._parse_inventory(view)

    async def _parse_inventory(self, view: ZlibStreamView) -> None:
        """Parses the inventory from the given stream view."""
        self.inventory.clear()

        async for line in view.walk():
            match = self.ENTRY_REGEX.match(line)
            if not match:
                continue

            name, directive, prio, location, dispname = match.groups()
            domain, _, subdirective = directive.partition(':')
            if directive == 'py:module' and name in self.inventory:
                continue

            if directive == 'std:doc':
                subdirective = 'label'

            if location.endswith('$'):
                location = location[:-1] + name

            key = name if dispname == '-' else dispname
            prefix = f'{subdirective}:' if domain == 'std' else ''

            if self.source.key == 'discord.py':
                key = (
                    key
                    .replace('discord.ext.commands.', 'commands.')
                    .replace('discord.commands.', 'commands.')  # Command-related listeners
                    .replace('discord.ext.tasks.', 'tasks.')
                )

            key = prefix + key
            self.inventory[prefix + key] = self.source.url + '/' + location
            self._key_lookup[key] = name

    def search(self, query: str) -> Iterator[tuple[str, str]]:
        """Searches for entries for the given query.

        Returns a generator of tuples (name, url).
        """
        matches = []
        regex = re.compile('.*?'.join(map(re.escape, query)), flags=re.IGNORECASE)

        for key, url in self.inventory.items():
            if match := regex.search(key):
                matches.append((len(match.group()), match.start(), (key, url)))

        for *_, match in sorted(matches):
            yield match

    @staticmethod
    def _get_base_url(url: str) -> str:
        """Gets the base url for the given url, stripping of query parameters and fragments"""
        scheme, netloc, path, *_ = urlparse(url)
        return scheme + '://' + netloc + path

    async def _get_html(self, url: str) -> str:
        """Gets the HTML content for the given url."""
        async with self.session.get(url, timeout=ClientTimeout(sock_connect=10, sock_read=10)) as response:
            if not response.ok:
                raise IndexingFailure(f'Failed to fetch contents {url!r} ({response.status} {response.reason})')

            return await response.text(encoding='utf-8')

    @wrap_exceptions(IndexingFailure)
    async def get_entry(self, name: str) -> SphinxDocumentationEntry:
        """Finds and returns the documentation for the given key."""
        if name in self.entries:
            return self.entries[name]

        page = self._get_base_url(url := self.inventory[name])
        html = await self._get_html(page) if not has_document(page) else ""

        key = self._key_lookup[name]
        response: SphinxDocumentResult = await asyncio.to_thread(scrape_document, page, html, key)

        embed = discord.Embed(
            color=Colors.primary,
            title=discord.utils.escape_markdown(name),
            url=url,
        )
        embed.description = response.description
        embed.description = re.sub(r'\n{3,}', '\n\n', embed.description)  # Weird fix for odd formatting issues
        if len(embed) > 6000:
            embed.description = cutoff(embed.description, 2048)  # Last resort if embed length is still too long

        embed.set_author(name=f'{self.source.name} Documentation')

        for field in response.fields:
            embed.add_field(name=field.name, value=cutoff(field.value, 1024, exact=True), inline=field.inline)

        builder = AnsiStringBuilder()
        for section in response.signature:
            builder.append(section.content.strip('\n'), bold=section.bold, color=getattr(AnsiColor, section.color))

        builder.ensure_codeblock(fallback='py')

        self.entries[name] = result = SphinxDocumentationEntry(name=name, url=url, signature=builder, embed=embed)
        return result


class RTFMDocumentationSelect(discord.ui.Select):
    def __init__(self, ctx: Context, inventory: SphinxInventory, matches: list[tuple[str, str]]) -> None:
        super().__init__(
            placeholder='View documentation for...',
            options=[
                discord.SelectOption(label=name, value=name) for name, _ in matches
            ],
        )
        self.ctx: Context = ctx
        self.inventory: SphinxInventory = inventory

    @staticmethod
    def form_embed(ctx: Context, entry: SphinxDocumentationEntry) -> discord.Embed:
        embed = entry.embed.copy()
        if entry.signature:
            embed.description = entry.signature.dynamic(ctx) + '\n' + embed.description

        embed.timestamp = ctx.now
        return embed

    async def callback(self, interaction: discord.Interaction) -> None:
        name = self.values[0]
        if name not in self.inventory.entries:
            await interaction.response.defer()

        async with self.ctx.typing():
            entry = await self.inventory.get_entry(name)

        embed = self.form_embed(self.ctx, entry)
        await interaction.edit_original_message(embed=embed)


class RelatedEntriesSelect(discord.ui.Select):
    def __init__(self, ctx: Context, inventory: SphinxInventory, name: str) -> None:
        parent = name
        if self._count_matches(parent, inventory) <= 1:
            # Probably a specific attribute or method, search through the parent instead.
            parent = name.rpartition('.')[0]

        super().__init__(
            placeholder='View documentation for...',
            options=[
                discord.SelectOption(label=name, value=name)
                for name in inventory.inventory
                if name.startswith(parent)
            ][:25],
        )
        self.ctx: Context = ctx
        self.inventory: SphinxInventory = inventory

    @staticmethod
    def _count_matches(parent: str, inventory: SphinxInventory) -> int:
        return sum(name.startswith(parent) for name in inventory.inventory)

    async def callback(self, interaction: discord.Interaction) -> None:
        name = self.values[0]
        if name not in self.inventory.entries:
            await interaction.response.defer()

        async with self.ctx.typing():
            entry = await self.inventory.get_entry(name)

        embed = RTFMDocumentationSelect.form_embed(self.ctx, entry)
        await interaction.edit_original_message(embed=embed)


class DocumentationManager:
    """Stores and manages documentation search and RTFM requests."""

    SOURCES: Final[ClassVar[dict[str, DocumentationSource]]] = {
        'discord.py': DocumentationSource(
            key='discord.py',
            name='discord.py',
            url='https://discordpy.readthedocs.io/en/master',
            aliases=('dpy', 'discordpy', 'discord-py'),
        ),
        'python': DocumentationSource(
            key='python',
            name='Python 3',
            url='https://docs.python.org/3',
            aliases=('py', 'python3', 'python-3', 'py3'),
        ),
        'pillow': DocumentationSource(
            key='pillow',
            name='Pillow',
            url='https://pillow.readthedocs.io/en/stable',
            aliases=('pil',)
        ),
        'aiohttp': DocumentationSource(
            key='aiohttp',
            name='aiohttp',
            url='https://docs.aiohttp.org/en/stable',
            aliases=('ahttp',),
        ),
        'asyncpg': DocumentationSource(
            key='asyncpg',
            name='asyncpg',
            url='https://magicstack.github.io/asyncpg/current',
            aliases=('apg',)
        ),
        'wand': DocumentationSource(
            key='wand',
            name='Wand',
            url='https://docs.wand-py.org/en/latest',
            aliases=('wand-py',),
        ),
        'numpy': DocumentationSource(
            key='numpy',
            name='NumPy',
            url='https://numpy.org/doc/stable',
            aliases=('np',),
        ),
        'sympy': DocumentationSource(
            key='sympy',
            name='SymPy',
            url='https://docs.sympy.org/latest',
        ),
        'matplotlib': DocumentationSource(
            key='matplotlib',
            name='Matplotlib',
            url='https://matplotlib.org/stable',
            aliases=('mpl',),
        ),
        'pygame': DocumentationSource(
            key='pygame',
            name='PyGame',
            url='https://www.pygame.org/docs',
        ),
        'opencv': DocumentationSource(
            key='opencv',
            name='OpenCV',
            url='https://docs.opencv.org/2.4.13.7',
            aliases=('cv', 'cv2', 'opencv', 'opencv-python'),
        ),
        'selenium': DocumentationSource(
            key='selenium',
            name='Selenium',
            url='https://selenium-python.readthedocs.io/en/latest',
            aliases=('selenium-python',),
        ),
        'requests': DocumentationSource(
            key='requests',
            name='Requests',
            url='https://docs.python-requests.org/en/master',
        ),
        'magmatic': DocumentationSource(
            key='magmatic',
            name='magmatic',
            url='https://magmatic.readthedocs.io/en/latest',
            aliases=('magma',),
        ),
    }

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot
        self.sphinx_inventories: dict[str, SphinxInventory] = {}

    async def fetch_sphinx_inventory(self, key: str) -> SphinxInventory:
        """Fetches the Sphinx inventory for the given key."""
        if key in self.sphinx_inventories:
            return self.sphinx_inventories[key]

        source = self.SOURCES[key]
        self.sphinx_inventories[key] = inv = SphinxInventory(self.bot, source)

        await inv.build()
        return inv

    async def _execute_sphinx_rtfm(self, ctx: Context, source: DocumentationSource, query: str) -> CommandResponse:
        inv = await self.fetch_sphinx_inventory(source.key)
        matches = list(inv.search(query=query))

        entries = [
            f'\u2022 [**{discord.utils.escape_markdown(name)}**]({url})' for name, url in matches
        ]
        if not entries:
            return 'No results found.'

        count = len(entries)
        embed = discord.Embed(color=Colors.primary, title=f'RTFM: **{source.name}**', timestamp=ctx.now)

        es = '' if count == 1 else 'es'
        embed.set_author(name=f'{count:,} match{es}')

        return Paginator(ctx, LineBasedFormatter(embed, entries), other_components=[
            RTFMDocumentationSelect(ctx, inv, matches[:25])
        ])

    async def _execute_sphinx_doc(self, ctx: Context, source: DocumentationSource, name: str) -> CommandResponse:
        async with ctx.typing():
            inv = await self.fetch_sphinx_inventory(source.key)
            if name in inv.inventory:
                entry = await inv.get_entry(name)
            else:
                for name, _ in inv.search(query=name):
                    try:
                        entry = await inv.get_entry(name)
                    except IndexingFailure as exc:
                        exc = exc.original

                        if not isinstance(exc, KeyError):
                            raise
                    else:
                        break
                else:
                    # If we went through the search results without breaking, then no results worked.
                    return BAD_ARGUMENT

        view = UserView(ctx.author)
        view.add_item(RelatedEntriesSelect(ctx, inv, name))

        embed = RTFMDocumentationSelect.form_embed(ctx, entry)
        return embed, view

    async def execute_rtfm(self, ctx: Context, *, source: DocumentationSource, query: str) -> CommandResponse:
        """Sends a list of documentation nodes for the given query."""
        async with ctx.typing():
            if source.type is DocumentationType.sphinx:
                return await self._execute_sphinx_rtfm(ctx, source, query)

        return 'Invalid source on our side, sorry.'

    async def execute_doc(self, ctx: Context, *, source: DocumentationSource, name: str) -> CommandResponse:
        """Sends documentation for the given node name."""
        if source.type is DocumentationType.sphinx:
            return await self._execute_sphinx_doc(ctx, source, name)

        return 'Invalid source on our side, sorry.'
