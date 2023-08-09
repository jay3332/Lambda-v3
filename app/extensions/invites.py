from __future__ import annotations

import contextlib
from collections import defaultdict

import discord

from app.core import Bot, Cog, Context, command
from app.core.helpers import REPLY, cooldown, group
from app.util.common import sentinel
from app.util.types import CommandResponse, Snowflake

VANITY = sentinel('VANITY', repr='VANITY', hash=0)


class Invites(Cog, name='Invite Tracking'):
    """Manage and track how users join your server."""

    emoji = '\U0001f4eb'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self.invites: defaultdict[Snowflake, dict[str, discord.Invite]] = defaultdict(dict)
        self.invite_channels: dict[Snowflake, Snowflake] = {}
        self.__task = bot.loop.create_task(self._load_invites())

    async def _load_invites(self) -> None:
        await self.bot.wait_until_ready()
        await self.bot.db.wait()

        query = 'SELECT guild_id, invite_tracking_channel_id FROM guilds'
        records = await self.bot.db.fetch(query)

        for record in records:
            channel_id = record['invite_tracking_channel_id']
            if channel_id is None:
                continue

            guild = self.bot.get_guild(record['guild_id'])
            if guild is None:
                continue

            self.invite_channels[guild.id] = channel_id
            self.invites[guild.id] = await self._fetch_guild_invites(guild)

    @staticmethod
    async def _fetch_guild_invites(guild: discord.Guild) -> dict[str, discord.Invite]:
        try:
            mapping = {
                invite.code: invite for invite in await guild.invites()
            }
        except discord.HTTPException:
            mapping = {}

        if 'VANITY_URL' in guild.features:
            vanity = await guild.vanity_invite()
            mapping[VANITY] = mapping[vanity.code] = vanity

        return mapping

    @Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        self.invites[invite.guild.id][invite.code] = invite

    @Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        self.invites[invite.guild.id].pop(invite.code, None)

    @Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        invites = self.invites[channel.guild.id]
        for invite in list(invites.values()):
            if invite.channel == channel:
                del invites[invite.code]

    async def _dispatch_invite_message(self, member: discord.Member, invite: discord.Invite) -> None:
        channel_id = self.invite_channels.get(member.guild.id)
        if channel_id is None:
            return

        channel = self.bot.get_partial_messageable(channel_id)
        with contextlib.suppress(discord.HTTPException):
            inviter = invite.inviter or 'Unknown'
            # TODO: custom invite messages?
            await channel.send(f'**{member}** joined using invite **{invite.code}** (invite created by {inviter})')

    @Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id not in self.invite_channels:
            return

        invites = await self._fetch_guild_invites(member.guild)
        cached = self.invites[member.guild.id]

        # make sure both invites have the same keys
        invites = sorted(invites.values(), key=lambda invite: invite.code)
        old_sorted = sorted(cached.values(), key=lambda invite: invite.code)

        for old, new in zip(old_sorted, invites):
            if old.uses >= new.uses:
                continue

            # If this invite has more uses than the old one, it's the one that was used
            cached[new.code] = new
            self.bot.loop.create_task(self._dispatch_invite_message(member, new))
            break

    @group(
        name='invite-tracking', aliases=('invite-channel', 'inv-channel', 'itr'),
        user_permissions=('manage_guild',), bot_permissions=('manage_guild',),
        hybrid=True, fallback='help'
    )
    @cooldown(1, 15)
    async def invite_tracking(self, ctx: Context) -> None:
        """Commands regarding Lambda's invite tracking feature."""
        await ctx.send_help(ctx.command)

    @invite_tracking.command(name='set-channel', aliases={'set', 'sc', 'ch', 'chan', 'setchannel', 'channel'}, hybrid=True)
    @cooldown(1, 15)
    async def invite_tracking_set(self, ctx: Context, channel: discord.TextChannel) -> CommandResponse:
        """Set the channel that invite tracking messages will be sent to.

        This will implicitly enable invite tracking if it is not already enabled.
        """
        query = 'UPDATE guilds SET invite_tracking_channel_id = $1 WHERE guild_id = $2'
        await self.bot.db.execute(query, channel.id, ctx.guild.id)
        self.invite_channels[ctx.guild.id] = channel.id
        if not self.invites[ctx.guild.id]:
            self.invites[ctx.guild.id] = await self._fetch_guild_invites(ctx.guild)

        ctx.bot.loop.create_task(ctx.thumbs())
        return f'Invite tracking channel set to {channel.mention}', REPLY

    @invite_tracking.command(name='disable', aliases=('off', 'stop', 'dis', 'disable-tracking', 'disabletracking'), hybrid=True)
    @cooldown(1, 15)
    async def invite_tracking_disable(self, ctx: Context) -> CommandResponse:
        """Disable invite tracking for this server."""
        query = 'UPDATE guilds SET invite_tracking_channel_id = NULL WHERE guild_id = $1'
        await self.bot.db.execute(query, ctx.guild.id)
        self.invite_channels.pop(ctx.guild.id, None)

        ctx.bot.loop.create_task(ctx.thumbs())
        return 'Disabled invite tracking for this server.', REPLY
