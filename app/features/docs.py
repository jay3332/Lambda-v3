from __future__ import annotations

import re
import zlib
from enum import Enum
from functools import partial
from io import BytesIO
from urllib.parse import urlparse
from typing import Any, AsyncGenerator, ClassVar, Collection, Final, Iterable, NamedTuple, TYPE_CHECKING

import discord
from aiohttp import ClientTimeout
from bs4 import BeautifulSoup
from bs4.element import NavigableString, Tag

from app.util import AnsiColor, AnsiStringBuilder
from app.util.common import executor_function, wrap_exceptions
from config import Colors

if TYPE_CHECKING:
    from asyncio import AbstractEventLoop
    from logging import Logger
    from aiohttp import ClientSession
    from app.core import Bot


class DocumentationType(Enum):
    sphinx = 0


class DocumentationSource(NamedTuple):
    """Stores information for a documentation source."""
    key: str
    name: str
    url: str
    aliases: Collection[str] = ()
    type: DocumentationType = DocumentationType.sphinx


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
        self._html_lookup: dict[str, BeautifulSoup] = {}  # url -> BeautifulSoup(raw html)
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
                key = key.replace('discord.ext.commands.', 'commands.')

            key = prefix + key
            self.inventory[prefix + key] = self.source.url + '/' + location
            self._key_lookup[key] = name

    @staticmethod
    def _get_base_url(url: str) -> str:
        """Gets the base url for the given url, stripping of query parameters and fragments"""
        scheme, netloc, path, *_ = urlparse(url)
        return scheme + '://' + netloc + path

    async def _get_soup(self, url: str) -> tuple[BeautifulSoup, str]:
        url = self._get_base_url(url)
        if url in self._html_lookup:
            return self._html_lookup[url], url

        async with self.session.get(url, timeout=ClientTimeout(sock_connect=10, sock_read=10)) as response:
            if not response.ok:
                raise IndexingFailure(f'Failed to fetch contents {url!r} ({response.status} {response.reason})')

            html = await response.text(encoding='utf-8')
            self._html_lookup[url] = res = await self.loop.run_in_executor(None, BeautifulSoup, html, 'lxml')

            return res, url

    @classmethod
    def _walk_relevant_children(cls, tag: Tag) -> Iterable[NavigableString | Tag]:
        for child in tag.children:
            if isinstance(child, NavigableString):
                yield child
                continue

            if not isinstance(child, Tag):
                continue

            child: Tag
            if child.name in ('p', 'a', 'b', 'i', 'em', 'strong', 'u', 'ul', 'ol', 'code'):
                yield child
                continue

            if child.name == 'dl':
                if 'field-list' not in child.attrs.get('class', ()):
                    break

                yield child
                continue

            if child.name == 'div':
                class_list = child.attrs.get('class', ())
                if 'admonition' in class_list or 'operations' in class_list:
                    yield child
                    continue

            if child.name.startswith('h'):
                yield child
                continue

            yield from cls._walk_relevant_children(child)

    def _parse_tag(self, tag: NavigableString | Tag, embed: discord.Embed, page: str) -> str:
        # sourcery no-metrics
        """Parses the given tag and returns the contents."""
        if isinstance(tag, NavigableString):
            return str(tag)

        parts = []
        parse = partial(self._parse_tag, embed=embed, page=page)

        for child in self._walk_relevant_children(tag):
            if isinstance(child, NavigableString):
                parts.append(str(child))

            elif child.name == 'p':
                parts.append(parse(child))

            elif child.name == 'a':
                inner = parse(child)
                href = child["href"]

                if '://' not in href:
                    href = page + href

                parts.append(f'[{inner}]({href})')

            elif child.name in ('b', 'strong'):
                parts.append(f'**{parse(child)}**')

            elif child.name == ('i', 'em'):
                parts.append(f'*{parse(child)}*')

            elif child.name == 'u':
                parts.append(f'__{parse(child)}__')

            elif child.name == 'code':
                parts.append(f'`{parse(child)}`')

            elif child.name == 'ul':
                parts.append('\n')
                for li in child.find_all('li'):
                    parts.append(f'\u2022 {parse(li)}\n')

            elif child.name == 'ol':
                parts.append('\n')
                for i, li in enumerate(child.find_all('li'), start=1):
                    parts.append(f'{i}. {parse(li)}\n')

            elif child.name == 'div':
                if 'admonition' in child.attrs['class']:
                    first = child.find('p', class_='admonition-title')
                    if first is None:
                        continue

                    title = parse(first)
                    content = parse(first.next_sibling)

                    embed.add_field(name=title, value=content, inline=False)
                    continue

                chunks = []
                for o_child in child.find_all('dl', class_='describe'):
                    operation = parse(o_child.find('dt')).strip()
                    description = parse(o_child.find('dd')).replace('\n', ' ').strip()

                    chunks.append(f'**`{operation}`** - {description}')

                embed.add_field(name='Supported Operations', value='\n'.join(chunks), inline=False)

            elif child.name == 'dl':
                for dt, dd in zip(child.find_all('dt'), child.find_all('dd')):
                    embed.add_field(name=parse(dt), value=parse(dd), inline=False)

        return ''.join(parts)

    @executor_function
    def _parse_tag_async(self, node: Tag, embed: discord.Embed, page: str) -> str:
        return self._parse_tag(node, embed, page)

    def _parse_signature(self, node: Tag) -> AnsiStringBuilder:
        builder = AnsiStringBuilder()

        for child in node.children:
            if isinstance(child, NavigableString):
                builder.append(str(child).lstrip('\n'), bold=True, color=AnsiColor.gray)
                continue

            if not isinstance(child, Tag):
                continue

            classes = child.attrs.get('class', ())
            if child.name == 'em' and 'sig-param' not in classes:
                builder.append(child.text, color=AnsiColor.green)

            elif 'sig-paren' in classes or 'o' in classes:
                builder.append(child.text, bold=True, color=AnsiColor.gray)

            elif 'n' in classes:
                builder.append(child.text, color=AnsiColor.yellow)

            elif 'default_value' in classes:
                builder.append(child.text, color=AnsiColor.cyan)

            elif 'sig-prename' in classes:
                builder.append(child.text, color=AnsiColor.white if 'descclassname' in classes else AnsiColor.red)

            elif 'descname' in classes or 'sig-name' in classes:
                builder.append(child.text, bold=True, color=AnsiColor.white)

            elif 'sig-param' in classes:
                builder.extend(self._parse_signature(child))

        return builder.strip()

    @wrap_exceptions(IndexingFailure)
    async def get_entry(self, name: str) -> SphinxDocumentationEntry:
        """Finds and returns the documentation for the given key."""
        if name in self.entries:
            return self.entries[name]

        soup, page = await self._get_soup(url := self.inventory[name])
        key = self._key_lookup[name]

        signature = soup.find('dt', id=key)
        parent = signature.parent

        embed = discord.Embed(color=Colors.primary, title=name, url=url)
        embed.description = await self._parse_tag_async(parent.find('dd'), embed, page)  # type: ignore
        embed.set_author(name=f'{self.source.name} Documentation')

        signature = signature and self._parse_signature(signature)
        self.entries[name] = result = SphinxDocumentationEntry(name=name, url=url, signature=signature, embed=embed)

        return result


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
    }

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot
