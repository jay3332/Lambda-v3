import re

import discord

from app.core import BAD_ARGUMENT, Cog, Context, REPLY, group
from app.util.common import pluralize
from app.util.types import CommandResponse
from config import Colors


class Settings(Cog):
    """Configuration settings for the bot."""

    emoji = '\U00002699'
    MENTION_REGEX: re.Pattern[str] = re.compile(r'<@!?\d+>')

    @group(aliases=('pf', 'prefixes', 'pref'))
    async def prefix(self, ctx: Context) -> CommandResponse:
        """View your server's prefixes."""
        record = await self.bot.db.get_guild_record(ctx.guild.id)
        prefixes = record.prefixes
        if not prefixes:
            return (
                f'No prefixes set for this server. Add one with `{ctx.clean_prefix}prefix add <prefix>`.\n'
                '*I will always respond to mentions.*'
            ), REPLY

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.description = '\n'.join(map(discord.utils.escape_mentions, prefixes))

        embed.set_author(name=f'Prefixes for {ctx.guild.name}', icon_url=ctx.guild.icon.url)
        embed.set_footer(text=pluralize(f'{len(prefixes)} prefix(es)'))

        message = '*I will always respond to mentions.*'
        return message, embed, REPLY

    @prefix.command('add', aliases=('create', '+', 'append', 'new', 'update'), user_permissions=('manage_guild',))
    async def prefix_add(self, ctx: Context, *prefixes: str) -> CommandResponse:
        """Add a prefix to your server's prefixes.

        You can separate prefixes by space to add multiple prefixes at once.
        You cannot have over 25 prefixes at once.

        Examples:
        - {PREFIX}prefix add !
        - {PREFIX}prefix add "lambda "
        - {PREFIX}prefix add ! ? "lambda "

        Arguments:
        - `prefixes`: A list of prefixes to add, separated by space. If you want a space in your prefix surround it with quotes.
        """
        if not prefixes:
            return 'Please specify prefixes to add.', BAD_ARGUMENT

        record = await self.bot.db.get_guild_record(ctx.guild.id)
        if len(record.prefixes) + len(prefixes) > 25:
            return 'You cannot have more than 25 prefixes at once.', BAD_ARGUMENT

        if any(self.MENTION_REGEX.search(prefix) for prefix in prefixes):
            return 'You cannot have mentions in your prefixes.', BAD_ARGUMENT

        record.prefixes.extend(prefixes)
        await record.update(prefixes=list(set(record.prefixes)))

        if len(prefixes) == 1:
            return f'Added {prefixes[0]!r} as a prefix.', REPLY

        return f'Added {len(prefixes)} prefixes.', REPLY

    @prefix.command('remove', aliases=('delete', '-', 'del', 'rm'), user_permissions=('manage_guild',))
    async def prefix_remove(self, ctx: Context, *prefixes: str) -> CommandResponse:
        """Remove a prefix from your server's prefixes.

        You can separate prefixes by space to remove multiple prefixes at once.

        Examples:
        - {PREFIX}prefix remove !
        - {PREFIX}prefix remove "lambda "
        - {PREFIX}prefix remove ! ? "lambda "

        Arguments:
        - `prefixes`: A list of prefixes to remove, separated by space. If there is a space in a prefix surround it with quotes.
        """
        if not prefixes:
            return 'Please specify prefixes to remove.', BAD_ARGUMENT

        record = await self.bot.db.get_guild_record(ctx.guild.id)
        updated = [prefix for prefix in record.prefixes if prefix not in prefixes]

        if len(updated) == len(record.prefixes):
            return 'No prefixes were removed. (None of your prefixes were valid)', REPLY

        diff = len(record.prefixes) - len(updated)
        await record.update(prefixes=updated)

        if len(prefixes) == 1:
            return f'Removed prefix {prefixes[0]!r}.', REPLY

        return f'Removed {diff} prefixes.', REPLY

    @prefix.command('clear', alias='wipe', user_permissions=('manage_guild',))
    async def prefix_clear(self, ctx: Context) -> CommandResponse:
        """Clear all of your server's prefixes."""
        record = await self.bot.db.get_guild_record(ctx.guild.id)
        if not record.prefixes:
            return 'No prefixes to clear.', REPLY

        if not await ctx.confirm(
            'Are you sure you want to clear all of your prefixes?\n'
            f'If so, you *must* prefix all commands with my mention ({ctx.bot.user.mention}) in order to use commands.',
            reference=ctx.message,
            delete_after=True,
        ):
            return 'Cancelled.', REPLY

        before = len(record.prefixes)
        await record.update(prefixes=[])

        return pluralize(f'Removed {before} prefix(es).'), REPLY

    @prefix.command('overwrite', aliases=('set', 'override'), user_permissions=('manage_guild',))
    async def prefix_overwrite(self, ctx: Context, *prefixes: str) -> CommandResponse:
        """Removes your server's previous prefixes and replaces them with the specified ones.

        You can separate prefixes by space to set multiple prefixes at once.

        Examples:
        - {PREFIX}prefix overwrite !
        - {PREFIX}prefix overwrite "lambda "
        - {PREFIX}prefix overwrite ! ? "lambda "

        Arguments:
        - `prefixes`: A list of prefixes to set, separated by space. If there is a space in a prefix surround it with quotes.
        """
        if not prefixes:
            return 'Please specify prefixes to set.', BAD_ARGUMENT

        if len(prefixes) > 25:
            return 'You cannot have more than 25 prefixes at once.', BAD_ARGUMENT

        if any(self.MENTION_REGEX.search(prefix) for prefix in prefixes):
            return 'You cannot have mentions in your prefixes.', BAD_ARGUMENT

        record = await self.bot.db.get_guild_record(ctx.guild.id)
        await record.update(prefixes=prefixes)

        if len(prefixes) == 1:
            return f'Set {prefixes[0]!r} as the only prefix.', REPLY

        return f'Set {len(prefixes)} prefixes.', REPLY
