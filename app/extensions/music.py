from __future__ import annotations

import asyncio
import random
from collections import defaultdict
from copy import deepcopy
from math import ceil
from typing import Any, Callable, ClassVar, Coroutine, Iterator, TYPE_CHECKING, Type, TypeAlias

import discord
import magmatic
from discord.app_commands import Choice, autocomplete, describe
from discord.ext import commands

from app.core import BAD_ARGUMENT, Bot, Cog, ERROR, Flags, MISSING, REPLY, command, store_true
from app.core.helpers import GenericCommandError
from app.util import converter, cutoff
from app.util.common import expansion_list, humanize_list, ordinal, pluralize
from app.util.pagination import Formatter, Paginator
from app.util.views import ConfirmationView
from config import Colors, Emojis, lavalink_nodes

if TYPE_CHECKING:
    from discord.guild import VocalGuildChannel

    from app.core import Command, Context
    from app.util.types import CommandResponse, OptionalCommandResponse

    class MusicContext(Context):
        voice_client: Player

    MusicTrack: TypeAlias = magmatic.Track[MusicContext]


@converter
async def TrackContext(ctx: MusicContext, _) -> MusicContext:
    return ctx


class VoteSkip(discord.ui.Button):
    def __init__(self, player: Player) -> None:
        super().__init__(
            label=f'Vote to Skip ({len(player._votes)}/{player.skip_threshold})',
            emoji='⏭️',
            style=discord.ButtonStyle.primary,
        )
        self.player: Player = player

    async def callback(self, interaction: discord.Interaction) -> None:
        if not self.player.queue.current:
            return await interaction.response.send_message(
                'I\'m not playing any tracks that you can skip right now.',
                ephemeral=True,
            )

        if interaction.user in self.player._votes:
            return await interaction.response.send_message(
                'You have already voted to skip this track.',
                ephemeral=True,
            )

        self.player._votes.add(interaction.user)
        if len(self.player._votes) >= self.player.skip_threshold:
            await interaction.response.send_message(
                'Vote to skip passed. Skipping track...',
                ephemeral=True,
            )
            await self.player.skip()

            self.disabled = True
            self.label = 'Vote to Skip (Passed)'
            await interaction.edit_original_message(
                view=self.view,
            )
            await self.player.ctx.send(
                f'[Music] {interaction.user.mention} casted the winning vote to skip the current track, so I\'ve skipped the track.',
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await interaction.message.edit(
                view=self.view,
            )
            return

        await interaction.response.send_message(
            f'You have voted to skip this track. ({len(self.player._votes)}/{self.player.skip_threshold})',
            ephemeral=True,
        )
        self.label = f'Vote to Skip ({len(self.player._votes)}/{self.player.skip_threshold})'

        await interaction.message.edit(
            view=self.view,
        )
        await self.player.ctx.send(
            f'[Music] {interaction.user.mention} voted to skip the current track. ({len(self.player._votes)}/{self.player.skip_threshold})',
            allowed_mentions=discord.AllowedMentions.none(),
        )


class VolumeChangeModal(discord.ui.Modal, title='Change Volume'):
    volume = discord.ui.TextInput(label='Volume (In percent, 0-1000)', placeholder='Enter new volume...')

    def __init__(self, view: DJControlsView) -> None:
        super().__init__()
        if view.player.volume != 100:
            self.volume.default = str(view.player.volume)

        self.view = view

    @staticmethod
    async def propagate(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            'Invalid volume specified. Try entering a valid number between 0 and 1000.',
            ephemeral=True,
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            volume = float(self.volume.value.removesuffix('%'))
        except ValueError:
            return await self.propagate(interaction)

        if not 0 <= volume <= 1000:
            return await self.propagate(interaction)

        volume = int(volume)
        await self.view.player.set_volume(volume)
        self.view.change_volume.emoji = self.view.volume_speaker_emoji(volume)

        await interaction.response.edit_message(
            embed=self.view.build_embed(),
            view=self.view,
        )
        await self.view.player.ctx.send(
             f'[Music] Volume set to **{volume}%** by {interaction.user.mention}.',
             allowed_mentions=discord.AllowedMentions.none(),
        )


class LoopTypeSelect(discord.ui.Select):
    def __init__(self, original: DJControlsView) -> None:
        emojis = list(DJControlsView.LOOP_EMOJIS.values())
        super().__init__(
            placeholder='Select a loop type...',
            options=[
                discord.SelectOption(label='None', description='Do not loop the track or queue.', value='0', emoji=emojis[0]),
                discord.SelectOption(label='Track', description='Loop the current track.', value='1', emoji=emojis[1]),
                discord.SelectOption(label='Queue', description='Loop the entire queue.', value='2', emoji=emojis[2]),
            ],
        )
        self.original: DJControlsView = original
        self.interaction: discord.Interaction = original.original_interaction

    async def callback(self, interaction: discord.Interaction) -> Any:
        value = magmatic.LoopType(int(self.values[0]))
        self.original.player.queue.loop_type = value
        self.original.change_loop_type.emoji = emoji = DJControlsView.LOOP_EMOJIS[value]

        await interaction.response.edit_message(
            content=f'Updated loop type to {emoji} **{value.name.title()}**. You can dismiss this now.',  # type: ignore
            view=None,
        )
        await self.interaction.edit_original_message(
            embed=self.original.build_embed(),
            view=self.original,
        )
        await self.original.player.ctx.send(
            f'[Music] Loop type set to {emoji} **{value.name.title()}** by {interaction.user.mention}.',  # type: ignore
            allowed_mentions=discord.AllowedMentions.none(),
        )


class DJControlsView(discord.ui.View):
    LOOP_EMOJIS: ClassVar[dict[magmatic.LoopType, str]] = {
        magmatic.LoopType.none: '\U0001f6ab',
        magmatic.LoopType.queue: '\U0001f501',
        magmatic.LoopType.track: '\U0001f502',
    }

    FILTER_NAMES: ClassVar[dict[Type[magmatic.BaseFilter], str]] = {
        magmatic.TimescaleFilter: 'Timescale',
        magmatic.VibratoFilter: 'Vibrato',
        magmatic.TremoloFilter: 'Tremolo',
    }

    def __init__(self, player: Player, interaction: discord.Interaction) -> None:
        super().__init__()
        self.player: Player = player
        self.original_interaction: discord.Interaction = interaction

        self.change_volume.emoji = self.volume_speaker_emoji(player.volume)
        self.change_loop_type.emoji = self.LOOP_EMOJIS[player.queue.loop_type]
        self._update_pause_button()
        self.skip_track.disabled = player.queue.current is None

    @staticmethod
    def volume_speaker_emoji(volume: int) -> str:
        if volume <= 0:
            return '\U0001f507'
        elif volume <= 20:
            return '\U0001f508'
        elif volume <= 50:
            return '\U0001f509'

        return '\U0001f50a'

    def get_filter_description(self) -> str:
        filters: list[magmatic.BaseFilter] = list(self.player.filters)
        if not filters:
            return 'No filters!'

        result = []
        for entity in filters:
            try:
                name = self.FILTER_NAMES[type(entity)]  # type: ignore
            except KeyError:
                continue

            items = ', '.join(f'{attr.title()}: {value}' for attr, value in entity._BaseFilter__walk_repr_attributes())  # type: ignore
            result.append(f'**{name}:** {items}' if items else f'\u2022 **{name}**')

        return expansion_list(result)

    def build_embed(self) -> discord.Embed:
        ctx = self.player.ctx
        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f'{ctx.guild.name}: Music Controls', icon_url=ctx.guild.icon)

        embed.add_field(name='Volume', value=f'{self.volume_speaker_emoji(self.player.volume)} {self.player.volume}%')
        embed.add_field(name='Paused?', value=f"\U000023f8 {'Yes' if self.player.is_paused() else 'No'}")

        loop_type = self.player.queue.loop_type
        embed.add_field(name='Loop Type', value=f'{self.LOOP_EMOJIS[loop_type]} {loop_type.name.title()}')
        embed.add_field(name='DJs', value=self.player.dj_list)

        if equalizer := self.player.equalizer:
            value = equalizer.name or 'Custom: ' + ' '.join(f'`{band:+.3}`' for band in equalizer.bands)
            embed.add_field(name='Equalizer', value=value, inline=False)

        embed.add_field(name='Filters', value=self.get_filter_description(), inline=False)
        return embed

    @discord.ui.button(label='Change Volume', row=0)
    async def change_volume(self, interaction: discord.Interaction, _) -> Any:
        await interaction.response.send_modal(VolumeChangeModal(self))

    @discord.ui.button(label='Change Loop Type', row=0)
    async def change_loop_type(self, interaction: discord.Interaction, _) -> Any:
        view = discord.ui.View()
        view.add_item(LoopTypeSelect(self))

        await interaction.response.send_message('Choose a loop type:', view=view, ephemeral=True)

    def _update_pause_button(self) -> None:
        if self.player.is_paused():
            self.pause.label = 'Resume'
            self.pause.emoji = '\U000025b6'
            self.pause.style = discord.ButtonStyle.danger
            return

        self.pause.label = 'Pause'
        self.pause.emoji = '\U000023f8'
        self.pause.style = discord.ButtonStyle.primary

    @discord.ui.button(label='Pause', style=discord.ButtonStyle.primary, row=0)
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button) -> Any:
        await self.player.toggle_pause()
        emoji, label = button.emoji, button.label
        self._update_pause_button()

        await interaction.response.edit_message(
            embed=self.build_embed(),
            view=self,
        )
        await self.player.ctx.send(
            f'[Music] Track was {emoji} **{label.lower()}d** by {interaction.user.mention}.',
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label='Skip Track', style=discord.ButtonStyle.success, emoji='\U000023ed', row=0)
    async def skip_track(self, interaction: discord.Interaction, button: discord.ui.Button) -> Any:
        view = ConfirmationView(user=interaction.user, true='Skip!', defer=False)
        await interaction.response.send_message(
            f'I\'m currently playing **{self.player.queue.current.title}**.\n'
            'Are you sure you want to skip this track?',
            view=view,
            ephemeral=True,
        )
        await view.wait()

        if not view.value:
            return await view.interaction.response.edit_message(content='Cancelled.', view=view)

        button.disabled = self.player.queue.current is None
        await self.player.skip()

        await self.original_interaction.edit_original_message(
            embed=self.build_embed(),
            view=self,
        )
        await view.interaction.response.edit_message(
            content='\U000023ed Track skipped!',
            view=view,
        )
        await self.player.ctx.send(
            f'[Music] Track was \U000023ed **skipped** by {interaction.user.mention}.',
            allowed_mentions=discord.AllowedMentions.none(),
        )


class DJControlsEntrypoint(discord.ui.Button):
    def __init__(self, player: Player) -> None:
        self.player: Player = player

        super().__init__(
            label='Music Controls',
            style=discord.ButtonStyle.primary,
            emoji='\U0001f3b6',
        )

    async def callback(self, interaction: discord.Interaction) -> Any:
        if interaction.user not in self.player.djs:
            return await interaction.response.send_message('Only DJs can use this button.', ephemeral=True)

        view = DJControlsView(self.player, interaction)

        await interaction.response.send_message(
            f'Controlling music for **{self.player.ctx.guild.name}**:',
            embed=view.build_embed(),
            view=view,
            ephemeral=True,
        )


class NowPlayingView(discord.ui.View):
    def __init__(self, player: Player, track: MusicTrack) -> None:
        super().__init__(timeout=900)

        self.add_item(discord.ui.Button(label='Jump to Message', url=track.metadata.message.jump_url))
        self.add_item(VoteSkip(player))
        self.add_item(DJControlsEntrypoint(player))

        self.player: Player = player
        self.track: MusicTrack = track

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user not in self.player.djs:
            await interaction.response.send_message('Only DJs can use this button.', ephemeral=True)
            return False

        return True


class SearchResultSelect(discord.ui.Select):
    def __init__(self, ctx: MusicContext, player: Player, tracks: list[magmatic.Track[MusicContext]]) -> None:
        super().__init__(
            placeholder='Choose a track (or multiple) to play...',
            options=[
                discord.SelectOption(
                    label=cutoff(track.title, max_length=50, exact=True),
                    description=f'{Player.format_duration(track.duration)} \u2014 {(cutoff(track.author, max_length=50))}',
                    value=str(i),
                    emoji=Emojis.soundcloud if track.source is magmatic.LoadSource.soundcloud else Emojis.youtube,
                )
                for i, track in enumerate(tracks)
            ],
            min_values=1,
            max_values=len(tracks),
        )
        self.ctx: MusicContext = ctx
        self.player: Player = player
        self.mapping = tracks

    async def callback(self, interaction: discord.Interaction) -> Any:
        tracks = [self.mapping[int(value)] for value in self.values]
        if len(tracks) > 1:
            track = magmatic.Playlist(tracks, data={
                'name': 'selected tracks',
                'selectedTrack': -1,
            }, metadata=self.ctx)
        else:
            track = tracks[0]

        self.disabled = True
        await interaction.response.edit_message(view=self.view)

        self.ctx.interaction = interaction
        await self.ctx.invoke(self.ctx.cog.play, track=track)  # type: ignore


class QueueFormatter(Formatter[magmatic.Track['MusicContext']]):
    def __init__(self, player: Player, embed: discord.Embed) -> None:
        super().__init__(list(player.queue), per_page=4)

        self.player: Player = player
        self.embed: discord.Embed = embed

    @property
    def queue(self) -> magmatic.WaitableQueue[MusicContext]:
        return self.player.queue

    async def format_page(self, paginator: Paginator, entries: list[magmatic.Track[MusicContext]]) -> discord.Embed:
        embed = discord.Embed.from_dict(deepcopy(self.embed.to_dict()))
        escape = discord.utils.escape_markdown

        embed.description += '\n\n'
        if current := self.queue.current:
            remaining = Player.format_duration(current.duration - self.player.position)
            embed.description += (
                f'**Currently playing:** ({self.queue.current_index + 1}) [{escape(current.title)}]({current.uri})\n'
                f'{Emojis.ExpansionEmojis.first} Author: **{escape(current.author)}** \u2014 **{remaining}** remaining\n'
                f'{Emojis.ExpansionEmojis.last} Requested by {current.metadata.author.mention}'
            )
        else:
            embed.description += 'No tracks are currently playing!'

        if up_next := self.queue.up_next:
            embed.description += f'\n\n*Up next: [{escape(up_next.title)}]({up_next.uri})* ({Player.format_duration(up_next.duration)})'

        for i, track in enumerate(entries, start=paginator.current_page * self.per_page):
            title = f'**{i + 1}.** [{escape(track.title)}]({track.uri})'

            if i == self.queue.current_index:
                title = f'{Emojis.arrow} {title}'

            embed.description += '\n\n' + (
                f'{title}\n'
                f'{Emojis.ExpansionEmojis.first} Author: **{escape(track.author)}** \u2014 '
                f'Duration: **{Player.format_duration(track.duration)}**\n'
                f'{Emojis.ExpansionEmojis.last} Requested by {track.metadata.author.mention}'
            )

        return embed


class Player(magmatic.Player[Bot]):
    ctx: MusicContext

    def __init__(self, *, node: magmatic.Node, guild: discord.abc.Snowflake) -> None:
        super().__init__(node=node, guild=guild)

        self.queue: magmatic.WaitableQueue = magmatic.WaitableQueue()  # type: ignore
        self.ctx: MusicContext = MISSING
        self.djs: list[discord.Member] = []
        self.started: bool = False
        self.suppress_messages: bool = False

        self._votes: set[discord.Member] = set()
        self._tracks: dict[str, MusicTrack] = {}
        self._initial_task = node.bot.loop.create_task(self._initial_disconnect_runner())
        self._skip_task: asyncio.Task | None = None

    async def _initial_disconnect_runner(self) -> None:
        await asyncio.sleep(300)
        try:
            await self.ctx.send('[Music] I\'m disconnecting from voice chat because there are no tracks in the queue.')
        finally:
            await self.destroy()

    async def start(self, ctx: MusicContext, track: MusicTrack | magmatic.Playlist[MusicContext]) -> None:
        self.ctx = ctx
        self.djs = [ctx.author]
        self.started = True

        self.queue.add(track)
        self._initial_task.cancel()
        await self.play_next()

    async def _play(self, coro: Coroutine[Any, Any, MusicTrack], /) -> None:
        try:
            track: MusicTrack = await asyncio.wait_for(coro, timeout=300)
        except asyncio.TimeoutError:
            try:
                await self.ctx.send('[Music] Exhaused queue. Disconnecting...')
            finally:
                await self.destroy()
            return

        self._tracks[track.id] = track
        await self.play(track)

    def resolve_track(self, track_id: str) -> MusicTrack | None:
        return self._tracks.get(track_id)

    async def play_next(self) -> None:
        await self._play(self.queue.get_wait())

    async def play_skip(self) -> None:
        await self._play(self.queue.skip_wait())

    async def skip(self) -> None:
        self._votes.clear()

        if self.queue.up_next is None:
            await self.stop()

        if self._skip_task is not None:
            self._skip_task.cancel()

        self._skip_task = self.bot.loop.create_task(self.play_skip())

    async def on_track_start(self, event: magmatic.TrackStartEvent) -> None:
        if self.suppress_messages:
            self.suppress_messages = False
            return

        track = self.resolve_track(event.track_id)
        if track is None:
            return

        await self.ctx.send(
            f'[Music] {Emojis.youtube} Now playing: **{track.title}**',
            embed=self.build_embed(self.queue.current_index, show_bar=False),
            view=NowPlayingView(self, track),
        )

    async def on_track_end(self, event: magmatic.TrackEndEvent) -> None:
        if event.may_start_next:
            return await self.play_next()

        if event.reason in (
            magmatic.TrackEndReason.replaced,
            magmatic.TrackEndReason.stopped,
            magmatic.TrackEndReason.cleanup,
        ):
            return
        try:
            await self.ctx.send(f'[Music] Track ended unexpectedly ({event.reason.name}). Disconnecting...')
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
    def format_duration(duration: int | float) -> str:
        duration = int(duration)
        minutes, seconds = divmod(duration, 60)
        hours, minutes = divmod(minutes, 60)

        if hours:
            return f'{hours}:{minutes:02d}:{seconds:02d}'

        return f'{minutes:02d}:{seconds:02d}'

    @property
    def dj_list(self) -> str:
        return humanize_list([dj.mention for dj in self.djs])

    def _generate_progress_bar(self, track: MusicTrack) -> str:
        if track.is_stream():
            return Emojis.MusicBarEmojis.LIVE + ' LIVE'

        bar = [Emojis.MusicBarEmojis.L0] + [Emojis.MusicBarEmojis.M0] * 8 + [Emojis.MusicBarEmojis.R0]
        circle_position = max(1, ceil((self.position / track.duration) * 10))

        if circle_position == 1:
            bar[0] = Emojis.MusicBarEmojis.L1
        elif circle_position == 10:
            bar[-1] = Emojis.MusicBarEmojis.R1
        else:
            bar[circle_position - 1] = Emojis.MusicBarEmojis.M1

        left = self.format_duration(self.position)
        right = self.format_duration(track.duration)
        return f'{left} {"".join(bar)} {right}'

    def build_embed(self, index: int, *, title: str = 'Now playing:', show_bar: bool = True) -> discord.Embed:
        track: MusicTrack = self.queue[index]

        embed = discord.Embed(color=Colors.primary, timestamp=discord.utils.utcnow(), title=track.title, url=track.uri)
        embed.set_author(name=title, icon_url=track.metadata.author.display_avatar)

        footer = f'{ordinal(index + 1)} track of {len(self.queue)} in queue'
        if self.queue.loop_type is not magmatic.LoopType.none:
            footer += f' | Looping the {self.queue.loop_type.name}'

        embed.set_footer(text=footer)

        if thumbnail := track.thumbnail:
            embed.set_thumbnail(url=thumbnail)

        if author := track.author:
            embed.add_field(name='Author', value=author)

        if not track.is_stream():
            embed.add_field(name='Duration', value=self.format_duration(track.duration))

        embed.add_field(name='Volume', value=f'{self.volume}%')
        embed.add_field(name='Requested By', value=track.metadata.author.mention)
        embed.add_field(name='DJs' if len(self.djs) != 1 else 'DJ', value=self.dj_list)

        if show_bar:
            embed.description = self._generate_progress_bar(track)

        return embed

    def is_dj(self, user: discord.Member) -> bool:
        return (
            user in self.djs
            or user.guild_permissions.administrator
            or user.guild_permissions.manage_guild
            or discord.utils.get(user.roles, name='DJ') is not None
        )

    @property
    def skip_threshold(self) -> int:
        return Music.count_members(self.channel) // 2 + 1  # type: ignore


def dj_only() -> Callable[[Command], Command]:
    def predicate(ctx: MusicContext) -> bool:
        if not ctx.voice_client:
            raise GenericCommandError('I must be in a voice channel to use this command.')

        if not ctx.voice_client.is_dj(ctx.author):
            raise GenericCommandError('You must be a DJ to use this command.')

        return True

    return commands.check(predicate)


def has_player() -> Callable[[Command], Command]:
    def predicate(ctx: MusicContext) -> bool:
        if not ctx.voice_client:
            raise GenericCommandError('I must be in a voice channel to use this command.')

        return True

    return commands.check(predicate)


def track_playing() -> Callable[[Command], Command]:
    def predicate(ctx: MusicContext) -> bool:
        if not ctx.voice_client:
            raise GenericCommandError('I must be in a voice channel to use this command.')

        if not ctx.voice_client.queue.current:
            raise GenericCommandError('There is no track playing.')

        return True

    return commands.check(predicate)


def ensure_player() -> Callable[[Command], Command]:
    async def predicate(ctx: MusicContext) -> bool:
        if not ctx.voice_client:
            join = ctx.bot.get_command('join')
            if join is None:
                raise RuntimeError

            await ctx.invoke(join)  # type: ignore

        return True

    return commands.check(predicate)


class SkipFlags(Flags):
    force: bool = store_true(short='f')


def convert_position(argument: str, *, current_position: float, max_position: float) -> int:
    factor = 1

    if argument.startswith('+'):
        base = current_position
        argument = argument[1:]
    elif argument.startswith('-'):
        base = current_position
        factor = -1
        argument = argument[1:]
    else:
        base = 0

    try:
        return base + int(argument) * factor
    except ValueError:
        pass

    if ':' not in argument:
        raise commands.BadArgument(f'{argument!r} is not a valid position/seek value.')

    count = argument.count(':')
    if count > 2:
        raise commands.BadArgument('Only HH:MM:SS or MM:SS are accepted formats for rich position values.')

    elif count == 2:
        hours, minutes, seconds = argument.split(':')
    else:
        hours = 0
        minutes, _, seconds = argument.partition(':')

    try:
        seconds = int(hours) * 3600 + int(minutes) * 60 + int(seconds)
    except ValueError:
        raise commands.BadArgument(f'{argument!r} is not a valid position/seek value.')

    if seconds < 0:
        raise commands.BadArgument('Position/seek value may not be negative.')

    new = base + seconds * factor
    if new < 0:
        raise commands.BadArgument('This seeks to a position before the start of the track.')
    elif new > max_position:
        raise commands.BadArgument('This seeks to a position beyond the end of the track.')

    return int(new)


class Music(Cog):
    """Commands that interface around Lambda's music system."""

    emoji = '\U0001f3b5'
    REQUIRED_PERMISSIONS: ClassVar[discord.Permissions] = discord.Permissions(connect=True, speak=True)

    async def cog_load(self) -> None:
        self.pool: magmatic.NodePool = magmatic.DefaultNodePool
        self.join_locks: dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

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

    @staticmethod
    def walk_members(channel: VocalGuildChannel) -> Iterator[discord.Member]:
        return (member for member in channel.members if not member.bot)

    @staticmethod
    def count_members(channel: VocalGuildChannel) -> int:
        return sum(1 for _ in Music.walk_members(channel))

    @Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        _before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return

        player: Player | None = member.guild.voice_client
        if player is None:
            return

        if member not in player.djs or after.channel == player.channel:
            return

        player.djs.remove(member)
        if player.djs:
            return

        # Attempt to swap DJs
        try:
            new = next(self.walk_members(player.channel))  # type: ignore
        except StopIteration:
            await player.destroy()
            await player.ctx.send('[Music] All users have left! Disconnecting...')
            return

        player.djs.append(new)
        await player.ctx.send(
            f'[Music] {new.mention} is now the DJ since the old DJ has left the channel.',
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @command(name='join', aliases=('connect', 'j', 'summon', 'move-to'), hybrid=True)
    @describe(channel='The voice channel to join.')
    async def join(self, ctx: MusicContext, channel: discord.VoiceChannel = None) -> CommandResponse:
        """Connects or moves the Lambda music player to the specified voice channel.

        Arguments:
        - `channel`: The voice channel to connect to. Defaults to the channel you are in.
        """
        async with self.join_locks[ctx.guild.id]:
            if response := self._check_channel(ctx, channel):
                return response

            channel = channel or ctx.author.voice.channel

            if ctx.voice_client is None:
                player: Player = self.pool.get_player(guild=ctx.guild, cls=Player)
                await player.connect(channel)

            elif ctx.voice_client.is_dj(ctx.author):
                if channel == ctx.voice_client.channel:
                    return f'Already connected to {channel.mention}.', REPLY

                await ctx.voice_client.move_to(channel)
            else:
                return 'You must be a DJ in order to move me.', ERROR

            ctx.voice_client.ctx = ctx
            try:
                await ctx.thumbs()
            finally:
                return f'\U0001f50a Joined {channel.mention}', REPLY

    @dj_only()
    @command(name='leave', aliases=('disconnect', 'dis', 'go-away'), hybrid=True)
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

    async def play_autocomplete(self, _interaction: discord.Interaction, value: str) -> list[Choice]:
        choices = [Choice(name=repr(value), value=value)]
        node = self.pool.get_node()

        try:
            results = await node.search_tracks(value, limit=10, source=magmatic.Source.youtube)
        except magmatic.NoMatches:
            return choices

        choices.extend(
            Choice(
                name=cutoff(
                    f'{result.title} - {result.author} ({Player.format_duration(result.duration)})',
                    max_length=100,
                    exact=True,
                ),
                value=result.uri,
            )
            for result in results
        )

        return choices

    @ensure_player()
    @command(name='play', aliases=('p', 'enqueue'), hybrid=True)
    @describe(track='The name of the track to play. Can be a YouTube URL or a search query.')
    @autocomplete(track=play_autocomplete)  # type: ignore
    async def play(self, ctx: MusicContext, *, track: magmatic.YoutubeTrack[TrackContext]) -> CommandResponse:  # type: ignore
        """Plays the specified track. If a track is already playing, it is added to the queue.

        Arguments:
        - `track`: The name of the track to play. Can be the title of a YouTube video, a URL, etc.
        """
        track: MusicTrack | magmatic.Playlist[MusicContext]
        track.metadata = ctx  # For autocomplete

        if isinstance(track, magmatic.Playlist):
            total = Player.format_duration(sum(t.duration for t in track))
            message = f'{Emojis.youtube} Enqueued **{len(track):,}** tracks in **{track.name}** ({total})'
        else:
            message = f'{Emojis.youtube} Enqueued **{track.title}** ({Player.format_duration(track.duration)})'

        if ctx.voice_client.is_dj(ctx.author) or not ctx.voice_client.started:
            view = discord.ui.View()
            view.add_item(DJControlsEntrypoint(ctx.voice_client))
        else:
            view = None

        if not ctx.voice_client.started:
            ctx.voice_client.suppress_messages = True
            await ctx.voice_client.start(ctx, track)

            if not isinstance(track, magmatic.Playlist):
                message = f'{Emojis.youtube} Playing **{track.title}**'

            embed = ctx.voice_client.build_embed(
                index=ctx.voice_client.queue.current_index,
                title='Now playing:',
                show_bar=False,
            )
            return message, embed, view, REPLY

        ctx.voice_client.queue.add(track)
        embed = ctx.voice_client.build_embed(
            index=len(ctx.voice_client.queue) - 1,
            title='Added to queue:',
            show_bar=False,
        )
        return message, embed, view, REPLY

    @command(name='search', aliases=('search-track', 'sch'), hybrid=True)
    @describe(query='The search query to use.')
    async def search(self, ctx: MusicContext, *, query: str) -> CommandResponse:
        # sourcery skip: merge-list-appends-into-extend
        """Searches for multiple music tracks given your query.
        This will search on both YouTube and SoundCloud.

        This will not work with direct URLs, use `{PREFIX}play <url>` directly instead.

        Arguments:
        - `query`: The query to search for.
        """
        # Lots of boilerplate here, CBA to fix
        node = self.pool.get_node()
        try:
            youtube = await node.search_tracks(query, source=magmatic.Source.youtube, limit=10, metadata=ctx, strict=True)
        except magmatic.NoMatches:
            youtube = []
        try:
            soundcloud = await node.search_tracks(query, source=magmatic.Source.soundcloud, limit=5, metadata=ctx, strict=True)
        except magmatic.NoMatches:
            soundcloud = []

        if not youtube and not soundcloud:
            return 'No tracks found with that query.', ERROR

        result = []
        if youtube:
            header = pluralize(f'{Emojis.youtube} **{len(youtube)} YouTube result(s)**')
            header += '\n' + expansion_list(
                f'[{track.title}]({track.uri}) ({Player.format_duration(track.duration)})'
                for track in youtube
            )
            result.append(header)

        if soundcloud:
            header = pluralize(f'{Emojis.soundcloud} **{len(soundcloud)} SoundCloud result(s)**')
            header += '\n' + expansion_list(
                f'[{track.title}]({track.uri}) ({Player.format_duration(track.duration)})'
                for track in soundcloud
            )
            result.append(header)

        result = '\n\n'.join(result)
        if ctx.voice_client:
            view = discord.ui.View()
            view.add_item(SearchResultSelect(ctx, ctx.voice_client, youtube + soundcloud))
        else:
            view = None

        return result, view, REPLY

    @has_player()
    @command(name='now-playing', aliases=('np', 'current', 'current-song', 'playing', 'now'), hybrid=True)
    async def now_playing(self, ctx: MusicContext) -> CommandResponse:
        """Shows information about the currently playing track."""
        embed = ctx.voice_client.build_embed(
            index=ctx.voice_client.queue.current_index,
            title='Currently playing:',
        )
        current = ctx.voice_client.queue.current
        if ctx.voice_client.is_dj(ctx.author):
            view = NowPlayingView(ctx.voice_client, current)
        else:
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label='Jump to Message', url=current.metadata.message.jump_url))

        return embed, view, REPLY

    @has_player()
    @command(name='skip', aliases=('skip-track', 'voteskip', 'sk'))
    async def skip(self, ctx: MusicContext, *, flags: SkipFlags) -> CommandResponse:
        """Vote to skip the currently playing track. Add the ``--force`` flag to skip immediately if you are the DJ."""
        player = ctx.voice_client
        if not player.queue.current:
            return 'No track is currently playing that I can skip.', REPLY

        title = player.queue.current.title

        if not player.is_dj(ctx.author) or not flags.force:
            if ctx.author in player._votes:
                return 'You have already voted to skip this track.', REPLY

            player._votes.add(ctx.author)
            if len(player._votes) < player.skip_threshold:
                view = discord.ui.View()
                view.add_item(VoteSkip(player))

                await ctx.thumbs()
                return (
                    f'Voted to skip **{title}**. ({len(player._votes)}/{player.skip_threshold})',
                    view,
                    REPLY,
                )

        await player.skip()
        try:
            await ctx.thumbs()
        finally:
            return f'Skipped **{title}**.', REPLY

    @has_player()
    @command(name='queue', aliases=('q', 'upcoming'), hybrid=True)
    async def queue(self, ctx: MusicContext) -> CommandResponse:
        """Shows the player's music queue."""
        queue = ctx.voice_client.queue

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=f'Music Queue: {ctx.guild.name}', icon_url=ctx.guild.icon)
        embed.set_footer(text=f'{len(queue)} tracks in queue.')

        embed.description = f'Total duration: {Player.format_duration(sum(track.duration for track in queue))}'
        if queue.loop_type is not magmatic.LoopType.none:
            emoji = DJControlsView.LOOP_EMOJIS[queue.loop_type]
            embed.description += f' | Looping the {emoji} **{queue.loop_type.name.lower()}**'
        else:
            embed.description += ' | Queue is not looping.'

        return Paginator(ctx, QueueFormatter(ctx.voice_client, embed)), REPLY

    @dj_only()
    @command(name='jump', aliases=('jump-to', 'skip-to', 'play-index'), hybrid=True)
    @describe(index='The index of the track to jump to.')
    async def jump(self, ctx: MusicContext, index: int) -> CommandResponse:
        """Jumps to the specific track at the given index in the queue.
        The index of the track can be found by running `{PREFIX}queue`.

        Arguments:
        - `index`: The index of the track to jump to, for example `1` jumps to the first track.
        """
        if index < 1:
            return 'Index must be greater than 0.', BAD_ARGUMENT

        queue = ctx.voice_client.queue
        try:
            queue[index - 1]
        except IndexError:
            return f'No track exists at index {index}.', BAD_ARGUMENT

        if index > 1:
            queue.jump_to(index - 2)
        else:
            queue._index = index - 2
        duration = Player.format_duration(queue.current.duration)
        await ctx.voice_client.play_next()

        return f'Jumped to track at index {index}: **{queue.current.title}** ({duration})', REPLY

    # TODO: this can be a button on Music Controller
    @dj_only()
    @command(name='shuffle', aliases=('shuffle-queue', 'shuf'), hybrid=True)
    async def shuffle(self, ctx: MusicContext) -> CommandResponse:
        """Shuffles the player's music queue."""
        # TODO: [magmatic] add queue.shuffle() and make _InternalQueue protocol support MutableSequence
        random.shuffle(ctx.voice_client.queue.queue)  # type: ignore
        return '\U0001f500 Shuffled the queue.', REPLY

    @dj_only()
    @track_playing()
    @command(name='seek', aliases=('seek-track', 'set-position', 'seek-to', 'ff', 'fast-forward', 'rewind', 'rw'))
    async def seek(self, ctx: MusicContext, position: str) -> CommandResponse:
        """Seeks to the given position in the currently playing track.

        Invoking this command with **ff** or **fast-forward** will automatically prepend ``+`` to your argument.
        Likewise, invoking with **rewind** or **rw** will automatically prepend ``-`` to your argument.

        Examples:
        - `{PREFIX}seek 1:00`: Seeks to 1 minute in the track.
        - `{PREFIX}ff 10`: Fast-forwards the track by 10 seconds.
        - `{PREFIX}seek +10`: Same as above. Fast-forwards the track by 10 seconds.
        - `{PREFIX}rw 0:30`: Rewinds the track by 30 seconds.

        Arguments:
        - `position`: The position to seek to in the track. Can be an integer in seconds, or formatted in
        MM:SS form, e.g. `2:30`. Prepend `+` or `-` to make this relative.
        """
        if ctx.invoked_with.lower() in ('ff', 'fast-forward'):
            position = '+' + position

        elif ctx.invoked_with.lower() in ('rewind', 'rw'):
            position = '-' + position

        track = ctx.voice_client.queue.current
        position = convert_position(position, current_position=ctx.voice_client.position, max_position=track.duration)

        if position == ctx.voice_client.position:
            return 'Track position remained the same.', REPLY
        elif position < ctx.voice_client.position:
            emoji = '\u23ea'
        else:
            emoji = '\u23e9'

        await ctx.voice_client.seek(position)
        position = Player.format_duration(position)

        return f'{emoji} Seeked current track to **{position}**.', REPLY
