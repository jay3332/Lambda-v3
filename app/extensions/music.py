from __future__ import annotations

import asyncio
from typing import Any, Callable, ClassVar, Coroutine, TYPE_CHECKING

import discord
import magmatic
from discord.ext import commands

from app.core import Cog, ERROR, MISSING, REPLY, command
from app.core.helpers import GenericCommandError
from app.util import converter
from app.util.common import humanize_list, ordinal
from config import Colors, lavalink_nodes

if TYPE_CHECKING:
    from app.core import Bot, Command, Context
    from app.util.types import CommandResponse, OptionalCommandResponse

    class MusicContext(Context):
        voice_client: Player


@converter
async def TrackContext(ctx: MusicContext, _) -> MusicContext:
    return ctx


class Player(magmatic.Player[Bot]):
    ctx: MusicContext

    def __init__(self, *, node: magmatic.Node, guild: discord.abc.Snowflake) -> None:
        super().__init__(node=node, guild=guild)

        self.queue: magmatic.WaitableQueue = magmatic.WaitableQueue()  # type: ignore
        self.ctx: MusicContext = MISSING
        self.djs: list[discord.Member] = []

        self._initial_task = self.ctx.bot.loop.create_task(self._initial_disconnect_runner())

    async def _initial_disconnect_runner(self) -> None:
        await asyncio.sleep(300)
        try:
            await self.ctx.send('[Music] I\'m disconnecting from voice chat because there are no tracks in the queue.')
        finally:
            await self.destroy()

    async def start(self, ctx: MusicContext, track: magmatic.Track[MusicContext] | magmatic.Playlist[MusicContext]) -> None:
        self.ctx = ctx
        self.djs = [ctx.author]

        self.queue.add(track)
        self._initial_task.cancel()
        await self.play_next()

    async def _play(self, coro: Coroutine[Any, Any, magmatic.Track[MusicContext]], /) -> None:
        try:
            track = await asyncio.wait_for(coro, timeout=300)
        except asyncio.TimeoutError:
            try:
                await self.ctx.send('[Music] Exhaused queue. Disconnecting...')
            finally:
                await self.destroy()
            return

        await self.play(track)

    async def play_next(self) -> None:
        await self._play(self.queue.get_wait())

    async def play_skip(self) -> None:
        await self._play(self.queue.skip_wait())

    async def on_track_end(self, event: magmatic.TrackEndEvent) -> None:
        if event.may_start_next:
            return await self.play_next()

        try:
            await self.ctx.send(f'[Music] Track ended unexpectedly. ({event.reason}) Disconnecting...')
        finally:
            await self.destroy()

    async def on_track_exception(self, event: magmatic.TrackExceptionEvent) -> None:
        await self.ctx.send(f'[Music] Track exception({event.severity.value}): {event.message}')

    async def on_track_stuck(self, event: magmatic.TrackStuckEvent) -> None:
        await self.ctx.send(
            f'[Music] Track is stuck (no audio packets received for {event.threshold} seconds). Moving on...'
        )
        await self.play_next()

    @staticmethod
    def _format_duration(duration: int) -> str:
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)

        if hours:
            return f'{hours}:{minutes:02d}:{seconds:02d}'

        return f'{minutes:02d}:{seconds:02d}'

    def build_embed(self, index: int, *, title: str = 'Now playing:', show_bar: bool = True) -> discord.Embed:
        track: magmatic.Track[MusicContext] = self.queue[index]

        embed = discord.Embed(color=Colors.primary, timestamp=discord.utils.utcnow(), title=track.title, url=track.uri)
        embed.set_author(name=title, icon_url=track.metadata.author.display_avatar)
        embed.set_footer(text=f'{ordinal(index + 1)} track of {len(self.queue)} in queue')

        if thumbnail := track.thumbnail:
            embed.set_thumbnail(url=thumbnail)

        if author := track.author:
            embed.add_field(name='Author', value=author)

        embed.add_field(name='Duration', value=self._format_duration(round(track.duration)))
        embed.add_field(name='Requested By', value=track.metadata.author.mention)
        embed.add_field(name='DJs' if len(self.djs) != 1 else 'DJ', value=humanize_list([dj.mention for dj in self.djs]))

        if show_bar:  # TODO
            ...


def dj_only() -> Callable[[Command], Command]:
    def predicate(ctx: MusicContext) -> bool:
        if not ctx.voice_client:
            raise GenericCommandError('You must be in a voice channel to use this command.')

        if ctx.author not in ctx.voice_client.djs:
            raise GenericCommandError('You must be a DJ to use this command.')

        return True

    return commands.check(predicate)


def has_player() -> Callable[[Command], Command]:
    def predicate(ctx: MusicContext) -> bool:
        if not ctx.voice_client:
            raise GenericCommandError('You must be in a voice channel to use this command.')

        return True

    return commands.check(predicate)


def ensure_player() -> Callable[[Command], Command]:
    async def predicate(ctx: MusicContext) -> bool:
        if not ctx.voice_client:
            join = ctx.bot.get_command('join')
            if join is None:
                raise RuntimeError

            await join.invoke(ctx)

        return True

    return commands.check(predicate)


class Music(Cog):
    """Commands that interface around Lambda's music system."""

    emoji = '\U0001f3b5'
    REQUIRED_PERMISSIONS: ClassVar[discord.Permissions] = discord.Permissions(connect=True, speak=True)

    async def cog_load(self) -> None:
        self.pool: magmatic.NodePool = magmatic.DefaultNodePool

        for host, port, password, secure in lavalink_nodes:
            await self.pool.start_node(
                bot=self.bot,
                host=host,
                port=port,
                password=password,
                secure=secure,
            )

    async def cog_unload(self) -> None:
        await self.pool.destroy()

    def _check_channel(self, ctx: MusicContext, channel: discord.VoiceChannel) -> OptionalCommandResponse:
        if channel is None and ctx.author.voice is None:
            return 'You must be in a voice channel to use this command.', ERROR

        channel = channel or ctx.author.voice.channel
        if channel.permissions_for(ctx.author) < self.REQUIRED_PERMISSIONS:
            return 'You do not have permission to join this voice channel.', ERROR

        elif channel.permissions_for(ctx.me) < self.REQUIRED_PERMISSIONS:
            return 'I do not have permission to connect to that voice channel.', ERROR

        return None

    @command(name='join', aliases=('connect', 'j', 'summon', 'move-to'))
    async def join(self, ctx: MusicContext, channel: discord.VoiceChannel = None) -> CommandResponse:
        """Connects or moves the Lambda music player to the specified voice channel.

        Arguments:
        - `channel`: The voice channel to connect to. Defaults to the channel you are in.
        """
        if response := self._check_channel(ctx, channel):
            return response

        if ctx.voice_client is not None:
            await ctx.voice_client.move_to(channel)
        else:
            player: Player = self.pool.get_player(guild=ctx.guild, cls=Player)
            await player.connect(channel)

        try:
            await ctx.thumbs()
        finally:
            return f'\U0001f50a Joined {channel.mention}', REPLY

    @dj_only()
    @command(name='leave', aliases=('disconnect', 'dis', 'go-away'))
    async def leave(self, ctx: MusicContext) -> CommandResponse:
        """Disconnects the Lambda music player from the voice channel."""
        if ctx.voice_client is None:
            return 'I am not connected to a voice channel.', ERROR

        channel = ctx.voice_client.channel
        await ctx.voice_client.destroy()
        try:
            await ctx.thumbs()
        finally:
            return f'\U0001f50a Disconnected from {channel.mention}.', REPLY

    @ensure_player()
    @command(name='play', aliases=('p', 'enqueue'))
    async def play(self, ctx: MusicContext, *, track: magmatic.YoutubeTrack[TrackContext]) -> CommandResponse:
        """Plays the specified track. If a track is already playing, it is added to the queue."""
        track: magmatic.Track[MusicContext] | magmatic.Playlist[MusicContext]

        if ctx.voice_client.ctx is MISSING:
            await ctx.voice_client.start(ctx, track)
        else:
            ctx.voice_client.queue.add(track)


