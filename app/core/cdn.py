from __future__ import annotations

from typing import BinaryIO, Final, NamedTuple, TYPE_CHECKING

from aiohttp import ClientSession, FormData

from config import cdn_authorization

if TYPE_CHECKING:
    from discord import File
    from app.core import Bot

__all__ = (
    'CDNClient',
    'CDNEntry',
)

BASE_URL: Final[str] = 'https://cdn.lambdabot.cf'
HEADERS: Final[dict[str, str]] = {
    'Authorization': f'Bearer {cdn_authorization}',
    'User-Agent': 'LambdaBot/1.0',
}


class CDNEntry(NamedTuple):
    filename: str
    session: ClientSession | None = None

    @property
    def url(self) -> str:
        return BASE_URL + f'/uploads/{self.filename}'

    def __str__(self) -> str:
        return self.url

    def __repr__(self) -> str:
        return f'<CDNEntry filename={self.filename!r} url={self.url!r}>'

    async def read(self) -> bytes:
        if self.session is None:
            raise RuntimeError('no session attached to this entry')

        async with self.session.get(self.url) as resp:
            resp.raise_for_status()
            return await resp.read()

    async def delete(self) -> None:
        if self.session is None:
            raise RuntimeError('no session attached to this entry')

        async with self.session.delete(self.url, headers=HEADERS) as resp:
            resp.raise_for_status()


class CDNClient:
    """An interface for requests to Lambda's CDN, cdn.lambdabot.cf."""

    def __init__(self, bot: Bot) -> None:
        self._session: ClientSession = bot.session

    async def upload(self, fp: BinaryIO, filename: str = None) -> CDNEntry:
        """Upload a file to the CDN."""
        filename = filename or 'unknown.png'

        form = FormData()
        form.add_field('file', fp, filename=filename)

        async with self._session.post('https://cdn.lambdabot.cf/upload', data=form, headers=HEADERS) as resp:
            resp.raise_for_status()
            return CDNEntry(filename=filename, session=self._session)

    async def upload_file(self, file: File) -> CDNEntry:
        """Upload a file to the CDN from a discord.File object."""
        return await self.upload(file.fp, file.filename)

    async def delete(self, entry: CDNEntry | str) -> None:
        """Deletes a file from the CDN. Entry can be a :class:`CDNEntry` object or a filename."""
        if isinstance(entry, CDNEntry):
            entry = entry.filename

        entry = entry.removeprefix(BASE_URL + '/uploads/')

        async with self._session.delete(f'{BASE_URL}/uploads/{entry}', headers=HEADERS) as resp:
            resp.raise_for_status()
