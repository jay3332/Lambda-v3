from __future__ import annotations

import random
from typing import Any, NamedTuple, TYPE_CHECKING

import discord
from discord.utils import format_dt

from app.core import Bot, Cog, Context, ERROR, Flags, REPLY, Timer, flag, group
from app.util.common import cutoff, pluralize
from app.util.converters import IntervalConverter
from app.util.types import CommandResponse
from config import Colors

if TYPE_CHECKING:
    from typing import Self

    from asyncpg import Record

    from app.util.types import TypedInteraction


class CreateGiveawayFlags(Flags):
    winners: int = flag(aliases=('winner', 'win'), short='w', default=1)
    message: str = flag(aliases=('msg', 'description', 'desc', 'comment'), short='m')
    level: int = flag(aliases=('lvl', 'lv'), short='l', default=0)
    roles: list[discord.Role] = flag(alias='role', short='r')


class GiveawayRecord(NamedTuple):
    id: int
    guild_id: int
    channel_id: int
    message_id: int
    timer_id: int
    level_requirement: int
    roles_requirement: set[int]
    prize: str
    winners: int

    @classmethod
    def from_record(cls, record: Record) -> Self:
        record['roles_requirement'] = set(record['roles_requirement'])
        return cls(**record)


class LeaveGiveawayView(discord.ui.View):
    def __init__(self, parent: GiveawayView) -> None:
        super().__init__(timeout=120)
        self.parent = parent

    @discord.ui.button(label='Leave Giveaway', style=discord.ButtonStyle.danger)
    async def leave_giveaway(self, interaction: TypedInteraction, _button: discord.ui.Button) -> None:
        await self.parent.bot.db.execute(
            'DELETE FROM giveaway_entrants WHERE giveaway_id = $1 AND user_id = $2',
            self.parent.giveaway.id, interaction.user.id,
        )
        await interaction.response.edit_message(
            content=f'You left the giveaway for **{self.parent.giveaway.prize}**.',
            view=None,
        )
        self.stop()


class GiveawayView(discord.ui.View):
    def __init__(self, bot: Bot, giveaway: GiveawayRecord) -> None:
        super().__init__(timeout=None)
        self.giveaway = giveaway
        self.bot = bot

    @discord.ui.button(
        label='Enter Giveaway', style=discord.ButtonStyle.primary, emoji='\U0001f389', custom_id='giveaway:enter',
    )
    async def enter_giveaway(self, interaction: TypedInteraction, _button: discord.ui.Button) -> None:
        prize = self.giveaway.prize
        view = LeaveGiveawayView(self)

        async with self.bot.db.acquire() as conn:
            await conn.execute(
                'INSERT INTO giveaway_entrants (giveaway_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING',
                self.giveaway.id, interaction.user.id,
            )
            entrants = await conn.fetchval(
                'SELECT COUNT(*) FROM giveaway_entrants WHERE giveaway_id = $1', self.giveaway.id,
            )
        await interaction.response.send_message(
            f'Entered the giveaway for **{prize}**! Entrants: **{entrants:,}**', ephemeral=True, view=view,
        )


class Giveaways(Cog):
    """Commands for creating, handling, and managing giveaways."""

    emoji = '\U0001f389'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self._giveaway_cache: dict[int, GiveawayRecord] = {}
        self._load_task = self.bot.loop.create_task(self.fetch_current_giveaways())
        self._views: list[GiveawayView] = []

    async def cog_load(self) -> None:
        # register all persistent views
        await self._load_task
        for giveaway in self._giveaway_cache.values():
            self._views.append(view := GiveawayView(self.bot, giveaway))
            self.bot.add_view(view)

    async def cog_unload(self) -> None:
        # unregister all persistent views
        for view in self._views:
            view.stop()
        self._load_task.cancel()

    # TODO: remove this with scale
    async def fetch_current_giveaways(self) -> None:
        """Fetches all current running giveaways and stores them in the cache"""
        records = await self.bot.db.fetch('SELECT * FROM giveaways')
        self._giveaway_cache = {record['id']: GiveawayRecord.from_record(record) for record in records}

    async def end_giveaway(self, giveaway: GiveawayRecord) -> Any:
        conn = await self.bot.db.acquire()
        await conn.execute('DELETE FROM giveaways WHERE id = $1', giveaway.id)

        partial = self.bot.get_partial_messageable(giveaway.channel_id)
        try:
            message = await partial.fetch_message(giveaway.message_id)
            embed = message.embeds[0]
            embed.colour = Colors.error

            await message.edit(
                content='\U0001f389\U0001f389 **GIVEAWAY ENDED** \U0001f389\U0001f389',
                embed=message.embeds[0],
            )
        except discord.HTTPException:
            return
        else:
            # handle winners
            entrants = await conn.fetch(
                'SELECT user_id FROM giveaway_entrants WHERE giveaway_id = $1',
                giveaway.id,
            )
            if not entrants:
                return await message.reply('No one entered the giveaway.')

            winners = random.sample(entrants, min(giveaway.winners, len(entrants)))
            winner_ids = (winner['user_id'] for winner in winners)
            winner_text = '\n'.join(f'<@{winner_id}>' for winner_id in winner_ids)

            await message.reply(
                f'## Winners of **{giveaway.prize}**:\n{winner_text}',
                allowed_mentions=discord.AllowedMentions(users=True),
            )
        finally:
            # giveaway might still be lingering in the cache, so we need to remove it
            try:
                await conn.execute('DELETE FROM giveaway_entrants WHERE giveaway_id = $1', giveaway.id)
                del self._giveaway_cache[giveaway.id]
            except KeyError:
                pass
            finally:
                await self.bot.db.release(conn)

    @group(aliases=('g', 'gw', 'gaw', 'giveaways'))
    async def giveaway(self, ctx: Context) -> None:
        """Commands for creating, handling, and managing giveaways."""
        await ctx.send_help(ctx.command)

    @giveaway.command(name='role', aliases=('setrole', 'giveawayrole'), user_permissions=('manage_guild',))
    async def giveaway_set_role(self, ctx: Context, *, role: discord.Role) -> CommandResponse:
        """Sets the giveaway role for this guild.

        This role, in addition to anyone with the Manage Server permission, will be able to create giveaways.

        Arguments:
        - `role`: The role to set as the giveaway role.
        """
        record = await ctx.db.get_guild_record(ctx.guild.id)
        await record.update(giveaway_role=role.id)
        ctx.bot.loop.create_task(ctx.thumbs())

        return f'Giveaway role set to {role.mention}. Users with this role will be able to create giveaways.', REPLY

    @giveaway.command(name='create', aliases=('c', 'new', 'start', 's', '+'))
    async def giveaway_create(
        self,
        ctx: Context,
        duration: IntervalConverter,
        *,
        prize: str,
        flags: CreateGiveawayFlags,
    ) -> CommandResponse:
        """Start a giveaway.

        Arguments:
        - `duration`: The duration of the giveaway. Must be between 5 seconds and 30 days.
          This argument cannot have spaces as it could be confused with the prize argument.
          If you still prefer to specify a duration with spaces, surround it with quotes.
        - `prize`: The prize of the giveaway. Must be between 1 and 100 characters.

        Flags:
        - `--winners <amount>`: The number of winners for the giveaway. Defaults to `1`. Must be between `1` and `20`.
        - `--message <message>`: An additional message to send with the giveaway, for example, a description.
          If provided, must be between 1 and 1000 characters, with at most 10 newlines.
        - `--level <level>`: The level requirement for the giveaway. Defaults to `0`. Must be between `0` and `500`.
        - `--roles <role>...`: A space-separated list of roles that the giveaway is restricted to.
          Members without any of these roles will not be allowed to enter the giveaway. This is an "any" check,
          meaning that a member only needs to have at least one of the roles specified to enter.
          Leave this flag out to allow everyone to enter.

        Examples:
        - `{PREFIX}giveaway start 1d Discord Nitro`
        - `{PREFIX}giveaway start 5h30m 100 coins --message Thanks for being a part of our community!`
        - `{PREFIX}giveaway start 30s Flash giveaway --winners 2 --level 5`
        - `{PREFIX}giveaway start 5m Boosters only --roles @Booster`
        """
        record = await ctx.db.get_guild_record(ctx.guild.id)
        has_role = record.giveaway_role_id and ctx.author._roles.has(record.giveaway_role_id)

        if not ctx.author.guild_permissions.manage_guild and not has_role:
            if record.giveaway_role_id:
                return (
                    f'You must have the {ctx.guild.get_role(record.giveaway_role_id).mention} role or '
                    'the Manage Server permission to create giveaways.',
                    ERROR,
                )
            return 'You must have the Manage Server permission to create giveaways.', ERROR

        if not 5 <= duration.total_seconds() <= 86400 * 30:
            return 'The giveaway duration must be between 5 seconds and 30 days.', ERROR

        if not 1 <= flags.winners <= 20:
            return 'The number of winners must be between 1 and 20.', ERROR

        if not 0 <= flags.level <= 500:
            return 'The level requirement must be between 0 and 500.', ERROR

        if not 1 <= len(prize) <= 100:
            return 'The prize must be between 1 and 100 characters.', ERROR

        if message := flags.message:
            if not 1 <= len(message) <= 1000:
                return 'The message must be between 1 and 1000 characters.', ERROR
            if message.count('\n') > 10:
                return 'The message can have at most 10 newlines.', ERROR

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name=prize)
        embed.set_footer(text=pluralize(f'{flags.winners} winner(s)'))

        ends_at = ctx.now + duration
        embed.description = f'\u23f3 Giveaway ends {format_dt(ends_at, "R")}'
        if flags.message:
            embed.description += f'\n\U0001f4e3*{flags.message}*'

        embed.add_field(name='Hosted by', value=ctx.author.mention)
        if flags.level:
            embed.add_field(name='Level requirement', value=flags.level)
        if flags.roles:
            embed.add_field(
                name='You must have one of these roles:',
                value=cutoff('\n'.join(f'- {role.mention}' for role in flags.roles), 1024),
                inline=False,
            )

        # Register the giveaway into DB and cache
        async with ctx.db.acquire() as conn:
            query = """
                    INSERT INTO giveaways (
                        guild_id, channel_id, message_id, timer_id, level_requirement, roles_requirement, prize
                    )
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING *
                    """
            record = await conn.fetchrow(
                query,
                ctx.guild.id, ctx.channel.id, ctx.message.id, -1,
                flags.level, list(set(flags.roles or [])), prize,
            )
            giveaway = GiveawayRecord.from_record(record)
            # timer_id and giveaway_id are cyclicly dependent
            timer = await ctx.bot.timers.create(ends_at, 'giveaway_end', giveaway_id=giveaway.id)
            await conn.execute('UPDATE giveaways SET timer_id = $1 WHERE id = $2', timer.id, giveaway.id)
            giveaway.timer_id = timer.id
            # Add to cache
            self._giveaway_cache[giveaway.id] = giveaway

        self._views.append(view := GiveawayView(ctx.bot, giveaway))
        await ctx.maybe_delete(ctx.message)
        return '\U0001f389\U0001f389 **GIVEAWAY** \U0001f389\U0001f389', embed, view

    @Cog.listener()
    async def on_timer_giveaway_end(self, timer: Timer) -> None:
        giveaway_id = timer.metadata['giveaway_id']
        giveaway = self._giveaway_cache.get(giveaway_id)  # FIXME: at scale, this needs to be a potential DB query
        if not giveaway:
            return

        await self.end_giveaway(giveaway)

    @giveaway.command(name='end', aliases=('stop', 'cancel', 'delete', 'remove', '-', 'e'))
    async def giveaway_end(self, ctx: Context) -> CommandResponse:
        """Ends a giveaway. This should be invoked by replying to the giveaway embed message."""
        if not ctx.message.reference:
            return 'You must reply to the giveaway message to end it.', ERROR

        giveaway = discord.utils.get(
            self._giveaway_cache.values(),
            channel_id=ctx.channel.id,
            message_id=ctx.message.reference.message_id,
        )
        if not giveaway:
            return 'This message is not a giveaway, or it has already ended.', ERROR

        await self.end_giveaway(giveaway)
        await ctx.thumbs()
