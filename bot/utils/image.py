from __future__ import annotations

import functools
import re
from typing import TYPE_CHECKING

import aiohttp
import discord
import humanize
from discord.asset import AssetMixin
from discord.ext import commands

from .common import url_from_emoji

if TYPE_CHECKING:
    from io import BufferedIOBase
    from os import PathLike

    from aiohttp import ClientSession
    from discord.ext.commands import Context

    from typing import Any, Protocol

    QueryT = discord.Member | discord.Emoji | discord.PartialEmoji | str
    SaveT = str | bytes | PathLike | BufferedIOBase

    class AssetLike(Protocol):
        url: str
        _state: Any | None

        async def read(self) -> bytes:
            ...

        async def save(self, fp: SaveT, *, seek_begin: bool = True) -> int:
            ...

    class SupportsAvatar(Protocol):
        avatar: discord.Asset

BadArgument = commands.BadArgument

__all__ = 'ImageFinder',


class ImageFinder:
    """A class that retrieves the bytes of an image given a message and it's context."""

    DEFAULT_MAX_WIDTH = 2048
    DEFAULT_MAX_HEIGHT = DEFAULT_MAX_WIDTH
    DEFAULT_MAX_SIZE = 1024 * 1024 * 6  # 6 MiB

    URL_REGEX = re.compile(r'https?://\S+')
    TENOR_REGEX = re.compile(r'https?://(www\.)?tenor\.com/view/\S+/?')
    GIPHY_REGEX = re.compile(r'https?://(www\.)?giphy\.com/gifs/[A-Za-z0-9]+/?')

    ALLOWED_CONTENT_TYPES = {
        'image/png',
        'image/jpeg',
        'image/jpg',
        'image/webp'
    }

    ALLOWED_SUFFIXES = {
        '.png',
        '.jpg',
        '.jpeg',
        '.webp'
    }

    CONVERTERS = (
        commands.MemberConverter,
        commands.EmojiConverter,
        commands.PartialEmojiConverter
    )

    def __init__(
        self,
        *,
        max_width: int = DEFAULT_MAX_WIDTH,
        max_height: int = DEFAULT_MAX_HEIGHT,
        max_size: int = DEFAULT_MAX_SIZE
    ) -> None:
        self.max_width: int = max_width
        self.max_height: int = max_height
        self.max_size: int = max_size

    @property
    def max_size_humanized(self) -> str:
        return humanize.naturalsize(self.max_size, binary=True, format='%.2f')

    async def _scrape_tenor(self, url: str, *, session: ClientSession) -> Optional[str]:
        async with session.get(url) as response:
            if response.ok:
                text = await response.text(encoding='utf-8')
                return (
                    text  # I cannot figure out a way to make this look good
                    .split('contentUrl')[1].split('content')[0][2:]
                    .split('"')[1].replace(r'\u002F', '/')
                )

    async def _scrape_giphy(self, url: str, *, session: ClientSession) -> Optional[str]:
        async with session.get(url) as response:
            if response.ok:
                text = await response.text(encoding='utf-8')
                return 'https://media' + text.split('https://media')[2].split('"')[0]

    async def sanitize_image_url(self, url: str, *, session: aiohttp.ClientSession) -> bytes:
        url = url.strip('<>')
        if self.TENOR_REGEX.match(url):
            result = await self._scrape_tenor(url, session=session)
        elif self.GIPHY_REGEX.match(url):
            result = await self._scrape_giphy(url, session=session)

        try:
            async with session.get(result) as response:
                if response.status != 200:
                    raise BadArgument(
                        f'Could not fetch your image. ({response.status}: {response.reason})'
                    )

                if response.content_type not in allowed_content_types:
                    raise BadArgument(f'Content type of `{response.content_type}` not supported.')

                if length := response.headers.get('Content-Length'):
                    length = int(length)
                    if length > self.max_size:
                        their_size = humanize.naturalsize(length, binary=True, format='%.2f')
                        raise BadArgument(f'Image is too large. ({their_size} > {self.max_size_humanized})')

                return await response.read()

        except aiohttp.InvalidURL:
            raise BadArgument('Invalid image/image URL.')
