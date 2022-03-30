from __future__ import annotations

import random

from discord import File, Member, User, Webhook
from typing import BinaryIO, TYPE_CHECKING

from config import cdn_buckets

if TYPE_CHECKING:
    from app.core import Bot


class PsuedoCDN:
    """A CDN-like interface that stores messages on Discord rather than host my own CDN.

    This is a temporary solution until I go out of my way and actually make a CDN.
    """

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot
        self.webhooks: list[Webhook] = []

    async def _cache_webhooks(self) -> None:
        for bucket in cdn_buckets:
            data = await self.bot.http.channel_webhooks(bucket)
            self.webhooks.extend(Webhook.from_state(d, state=self.bot._connection) for d in data)

    async def upload(self, fp: BinaryIO, filename: str = None, *, owner: Member | User | None = None) -> str:
        if not self.webhooks:
            await self._cache_webhooks()

        filename = filename or 'unknown.png'
        webhook = random.choice(self.webhooks)

        message = await webhook.send(
            f'Logged owner: {owner} ({owner.id})' if owner else '',
            file=File(fp, filename),  # type: ignore
            wait=True,
        )
        return message.attachments[0].url

    async def upload_file(self, file: File, *, owner: Member | User | None = None) -> str:
        return await self.upload(file.fp, file.filename, owner=owner)
