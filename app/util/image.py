from __future__ import annotations

import functools
import re
from typing import TYPE_CHECKING, TypeAlias

import aiohttp
import discord
from discord.asset import AssetMixin
from discord.ext import commands

from .common import humanize_size, image_url_from_emoji

if TYPE_CHECKING:
    from io import BufferedIOBase
    from os import PathLike
    from typing import Any, Protocol

    from aiohttp import ClientSession

    from app.core import Context

    Query: TypeAlias = discord.Member | discord.Emoji | discord.PartialEmoji | str
    SaveBuffer: TypeAlias = str | bytes | PathLike | BufferedIOBase


    class AssetLike(Protocol):
        url: str
        _state: Any | None

        async def read(self) -> bytes:
            ...

        async def save(self, fp: SaveBuffer, *, seek_begin: bool = True) -> int:
            ...


    class SupportsAvatar(Protocol):
        avatar: discord.Asset

BadArgument = commands.BadArgument

__all__ = 'ImageFinder',


class ImageFinder:
    """A class that retrieves the bytes of an image given a message, and it's context."""

    DEFAULT_MAX_WIDTH = 2048
    DEFAULT_MAX_HEIGHT = DEFAULT_MAX_WIDTH
    DEFAULT_MAX_SIZE = 1024 * 1024 * 6  # 6 MiB

    URL_REGEX = re.compile(r'https?://\S+')
    TENOR_REGEX = re.compile(r'https?://(www\.)?tenor\.com/view/\S+/?')
    GIPHY_REGEX = re.compile(r'https?://(www\.)?giphy\.com/gifs/[A-Za-z\d]+/?')

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
        max_size: int = DEFAULT_MAX_SIZE,
    ) -> None:
        self.max_width: int = max_width
        self.max_height: int = max_height
        self.max_size: int = max_size

    @property
    def max_size_humanized(self) -> str:
        return humanize_size(self.max_size)

    @staticmethod
    async def _scrape_tenor(url: str, *, session: ClientSession) -> str | None:
        async with session.get(url) as response:
            if not response.ok:
                return

            text = await response.text(encoding='utf-8')
            return (
                text
                .split('contentUrl')[1]
                .split('content')[0][2:]
                .split('"')[1]
                .replace(r'\u002F', '/')
            )

    @staticmethod
    async def _scrape_giphy(url: str, *, session: ClientSession) -> str | None:
        async with session.get(url) as response:
            if not response.ok:
                return

            text = await response.text(encoding='utf-8')
            return 'https://media' + text.split('https://media')[2].split('"')[0]

    async def _run_conversions(self, ctx: commands.Context, text: str) -> Query:
        for converter in self.CONVERTERS:
            try:
                result = await converter().convert(ctx, text)
            except commands.ConversionError:
                continue
            else:
                return result

        return text

    async def sanitize(
        self,
        result: AssetLike | discord.Attachment | bytes | str,
        session: ClientSession,
        *,
        allowed_content_types: set[str] | None = None,
        allowed_suffixes: set[str] | None = None,
    ) -> bytes:  # sourcery no-metrics
        if isinstance(result, AssetMixin):
            result = await result.read()

        if isinstance(result, discord.Attachment):
            if not result.filename.endswith(tuple(allowed_suffixes)):
                suffix = result.filename.split('.')
                suffix = suffix[-1] if len(suffix) > 1 else 'none'
                raise BadArgument(f'Attachment file extension of `{suffix}` not supported.')

            if result.size > self.max_size:
                their_size = humanize_size(result.size)
                raise BadArgument(f'Attachment is too large. ({their_size} > {self.max_size_humanized})')

            if not (result.width and result.height):
                raise BadArgument('Invalid attachment. (Could not get a width or height from it)')

            if result.width > self.max_width:
                raise BadArgument(
                    f'Attachment width of {result.width:,} surpasses the maximum of {self.max_width:,}.'
                )
            if result.height > self.max_height:
                raise BadArgument(
                    f'Attachment height of {result.height:,} surpasses the maximum of {self.max_height:,}.'
                )

            return await result.read()

        elif isinstance(result, bytes):
            if len(result) > self.max_size:
                their_size = humanize_size(len(result))
                raise BadArgument(f'Image is too large. ({their_size} > {self.max_size_humanized})')

            return result

        elif isinstance(result, str):
            result = result.strip('<>')
            if self.TENOR_REGEX.match(result):
                result = await self._scrape_tenor(result, session=session)
            elif self.GIPHY_REGEX.match(result):
                result = await self._scrape_giphy(result, session=session)

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
                            their_size = humanize_size(length)
                            raise BadArgument(f'Image is too large. ({their_size} > {self.max_size_humanized})')

                    return await response.read()

            except aiohttp.InvalidURL:
                raise BadArgument('Invalid image/image URL.')

    async def find(
        self,
        ctx: Context,
        query: str = None,
        *,
        allow_gifs: bool = True,
        user_avatars: bool = True,
        fallback_to_user: bool = True,
        run_conversions: bool = True,
    ) -> bytes:  # sourcery no-metrics
        if query is not None and run_conversions and isinstance(query, str):
            query = await self._run_conversions(ctx, query)

        query: Query | None

        allowed_content_types = self.ALLOWED_CONTENT_TYPES.copy()
        allowed_suffixes = self.ALLOWED_SUFFIXES.copy()

        if allow_gifs:
            allowed_content_types.add('image/gif')
            allowed_suffixes.add('.gif')

        sanitize = functools.partial(
            self.sanitize,
            session=ctx.bot.session,
            allowed_content_types=allowed_content_types,
            allowed_suffixes=allowed_suffixes,
        )

        async def do_user_avatar(user: SupportsAvatar) -> bytes:
            avatar = user.avatar
            if not allow_gifs:
                avatar = user.avatar.with_format('png')

            return await sanitize(avatar)

        async def fallback() -> bytes | None:
            # sourcery skip: merge-duplicate-blocks
            # I cannot figure out a way to make this code look good

            message: discord.Message = ctx.message

            if attachments := message.attachments:
                return await sanitize(attachments[0])

            if reference := message.reference:
                resolved = reference.resolved
                if attachments := resolved.attachments:
                    return await sanitize(attachments[0])

                if embeds := resolved.embeds:
                    embed: discord.Embed = embeds[0]

                    if embed.type == 'image':
                        if url := embed.thumbnail.url:
                            return await sanitize(url)

                    elif embed.type == 'rich':
                        if url := embed.image.url:
                            return await sanitize(url)

                        elif url := embed.thumbnail.url:
                            return await sanitize(url)

                if match := self.URL_REGEX.match(resolved.content):
                    return await sanitize(match.group())

            if user_avatars and fallback_to_user:
                return await do_user_avatar(ctx.author)

            raise BadArgument('No attachment or link given.')

        if not query:
            return await fallback()

        if isinstance(query, bytes):
            return await sanitize(query)

        if isinstance(query, str):
            if len(query) < 8:
                return await sanitize(image_url_from_emoji(query))

            return await sanitize(query)

        if isinstance(query, (discord.Emoji, discord.PartialEmoji)):
            return await sanitize(await query.read())

        if isinstance(query, (discord.Member, discord.User)) and user_avatars:
            return await do_user_avatar(query)
