from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from typing import ClassVar, Final, TYPE_CHECKING, Type

import discord
from dateparser.search import search_dates
from discord.ext import commands
from discord.http import handle_message_parameters
from discord.utils import format_dt

from app.core import BAD_ARGUMENT, Cog, Context, ERROR, Flags, Param, REPLY, Timer, command, flag, group, store_true
from app.util.common import converter, humanize_duration, pluralize
from app.util.converters import IntervalConverter
from app.util.pagination import FieldBasedFormatter, Paginator
from app.util.timezone import TimeZone
from app.util.types import CommandResponse
from config import Colors

if TYPE_CHECKING:
    from asyncpg import Record

    ReminderId: Type[Timer]


@converter
async def ReminderId(ctx: Context, argument: str) -> Timer:
    try:
        argument = int(argument)
    except ValueError:
        raise commands.BadArgument(
            'Reminder ID must be a valid integer.'
            f'See `{ctx.clean_prefix}reminders` to see a list of all of your reminders and their IDs.'
        )

    timer = await ctx.bot.timers.get_timer(argument)
    if not timer or timer.event != 'reminder':
        raise commands.BadArgument('Reminder with the given ID does not exist.')

    if timer.metadata['author_id'] != ctx.author.id:
        raise commands.BadArgument('This is not your reminder.')

    return timer


class ReminderConverter(commands.Converter[tuple[datetime, str]]):
    """Extracts a date and reminder message from the given argument."""

    if TYPE_CHECKING:
        def __getitem__(self, item: int) -> str:
            ...

    DATEPARSER_SETTINGS: Final[ClassVar[dict[str, str]]] = {
        "TIMEZONE": "UTC",
        "TO_TIMEZONE": "UTC",
        "RETURN_AS_TIMEZONE_AWARE": True,
        "PREFER_DATES_FROM": "future",
        "PREFER_DAY_OF_MONTH": "first",
    }

    def __init__(self, *, tz: TimeZone = TimeZone.utc()) -> None:
        self.dateparser_settings: dict[str, str] = self.DATEPARSER_SETTINGS.copy()

        if tz.name != "UTC":
            self.dateparser_settings["TIMEZONE"] = tz.name

    @staticmethod
    def sanitize_message(message: str) -> str:
        """Removes extra text from the message such as "me in" or "me to"."""
        for word in ("in", "to", "at"):
            if message.lower().startswith(subject := f'me {word} '):
                message = message[len(subject):]

            elif message.lower().startswith(subject := word + ' '):
                message = message[len(subject):]

        if message.lower().startswith('me '):  # see comment below
            message = message[3:]

        if message.lower().endswith(" in"):  # .removesuffix() is case-sensitive
            message = message[:-3]

        return message

    async def convert(self, ctx: Context, argument: str) -> tuple[datetime, str, str]:
        """Convert the given argument to a tuple of date and reminder message."""
        try:
            delta, match = IntervalConverter._convert(argument)

            start, end = match.span()
            message = argument[:start].rstrip() + ' ' + argument[end:].lstrip()
            message = self.sanitize_message(message.strip()).strip()

            return ctx.now + delta, message, argument

        except commands.BadArgument:
            pass

        if dates := search_dates(argument, settings=self.dateparser_settings, languages=["en"]):
            message, dt = dates[0]
            idx = argument.find(message)
            if idx != -1:
                # avoid double spaces by not using .replace()
                message = argument[:idx].rstrip() + ' ' + argument[idx + len(message):].lstrip()

            return dt, self.sanitize_message(message.strip()).strip(), argument

        raise commands.BadArgument(
            'Could not find a date from that message. Try something such as "do the laundry in 1 hour"'
        )


class ReminderFlags(Flags):
    repeat: IntervalConverter = flag(short='r')
    timezone: TimeZone = flag(alias='tz', short='t')
    dm: bool = store_true(alias='pm', short='d')


class Reminders(Cog):
    """Commands related to Lambda's reminders system."""

    emoji = '\u23f0'

    REMINDER_SUCCESS_MESSAGES: Final[ClassVar[tuple[str, ...]]] = (
        'Okay',
        'Got it',
        'Alright',
        'Sure thing',
        'Ok',
        'Alright then',
        'Sure',
    )

    @group(aliases=('rm', 'remindme', 'remember', 'setreminder', 'reminder'))
    async def remind(self, ctx: Context, *, reminder: ReminderConverter, flags: ReminderFlags) -> CommandResponse:
        """Sets a reminder for something in the future.

        Note that reminders will silently fail. If I cannot message in the channel/DM, I will not be able to remind you.
        This should rarely happen, though.

        Examples:
        - {PREFIX}remind me to go for a walk in 15 minutes
        - {PREFIX}remind 2d birthday
        - {PREFIX}remind tomorrow at 5:00 PM go to the park --timezone EST

        Arguments:
        - `reminder`: The reminder message. A date or time must be able to be extracted from this message.

        Flags:
        - `--repeat`: The interval to repeat the reminder.
        - `--timezone`: The timezone to use for the reminder. Defaults to your timezone, your guild's timezone if yours is not set, or UTC if neither is set.
        - `--dm`: Whether to DM the reminder to you.
        """
        if flags.timezone and flags.timezone != TimeZone.utc():
            # noinspection PyShadowingNames
            converter = ReminderConverter(tz=flags.timezone)
            result = await converter.convert(ctx, reminder[-1])
            flags.timezone = TimeZone.utc()

            return await ctx.invoke(self.remind, reminder=result, flags=flags)

        dt, message, _ = reminder
        message = message or 'something'

        if len(message) > 1400:
            return BAD_ARGUMENT, 'Reminder message is too long.', Param('reminder')

        if dt <= ctx.now:
            return BAD_ARGUMENT, 'That time is in the past.', Param('reminder')

        if dt - ctx.now < timedelta(seconds=15):
            return (
                BAD_ARGUMENT,
                'That time is too close to now. Try something at least 15 seconds in the future.',
                Param('reminder'),
            )

        if dt - ctx.now > timedelta(days=7300):
            return BAD_ARGUMENT, 'Reminder time must be under 20 years in the future.', Param('reminder')

        if flags.repeat and not 1800 <= flags.repeat.total_seconds() <= 86400 * 7300:
            return BAD_ARGUMENT, 'Repeating interval must be between 30 minutes and 20 years.', Param('repeat', flag=True)

        if flags.dm:
            dm = await ctx.author.create_dm()
            destination = dm.id
        else:
            destination = ctx.channel.id

        timer = await ctx.bot.timers.create(
            dt,
            'reminder',
            message=message,
            author_id=ctx.author.id,
            destination_id=destination,
            jump_url=ctx.message.jump_url,
            repeat=flags.repeat and flags.repeat.total_seconds(),
            original_creation=int(ctx.message.created_at.timestamp()),
        )

        message = f'{random.choice(self.REMINDER_SUCCESS_MESSAGES)}, {format_dt(dt, "R")}: {message} (ID: {timer.id})'
        if repeat := flags.repeat:
            message += f'\n*Repeating every {humanize_duration(repeat.total_seconds())}*'

        if flags.dm:
            message += '\n*I will DM you your reminder.*'

        return message, REPLY

    @Cog.listener()
    async def on_reminder_timer_complete(self, timer: Timer) -> None:
        """Sends a reminder message when a reminder timer is complete."""
        metadata = timer.metadata

        message = metadata['message']
        author_id = metadata['author_id']
        destination_id = metadata['destination_id']
        original_creation = metadata['original_creation']

        view = discord.ui.View()
        view.add_item(discord.ui.Button(label='Jump to Message', url=metadata['jump_url']))

        parameters = handle_message_parameters(
            f'<@{author_id}>, here is your reminder from <t:{original_creation}:R>: {message}',
            view=view,
        )
        try:
            await self.bot.http.send_message(destination_id, params=parameters)
        except discord.HTTPException as exc:
            self.bot.log.warn(f'Failed to send reminder: {exc}')

        if repeat := metadata['repeat']:
            await self.bot.timers.create(repeat, 'reminder', **metadata)

    @remind.command(name='repeat', aliases=('rp', 'loop'))
    async def remind_repeat(self, ctx: Context, reminder: ReminderId, *, interval: IntervalConverter) -> CommandResponse:
        """Set the interval to repeat the reminder.

        Arguments:
        - `reminder`: The ID of the reminder to repeat. You can view `{PREFIX}remind list` to find the ID of your reminder.
        - `interval`: The interval at which to repeat this reminder at. Pass in something such as "2 hours".
        """
        interval = int(interval.total_seconds())

        if not 1800 <= interval <= 86400 * 7300:
            return BAD_ARGUMENT, 'Repeating interval must be between 30 minutes and 20 years.'

        query = """
                UPDATE
                    timers
                SET
                    metadata = jsonb_set(metadata, '{repeat}', $1::JSONB, true)
                WHERE
                    id = $2
                """
        await ctx.bot.db.execute(query, str(interval), reminder.id)

        return f'Now repeating reminder with ID {reminder.id} every {humanize_duration(interval)}.', REPLY

    @remind.command(name='list')
    async def remind_list(self, ctx: Context) -> CommandResponse:
        """View all of your pending reminders."""
        query = """
                SELECT
                    id,
                    expires,
                    metadata
                FROM
                    timers
                WHERE
                    event = 'reminder'
                AND
                    (metadata->'author_id')::BIGINT = $1
                ORDER BY
                    expires ASC
                """
        records: list[Record | Timer] = await ctx.bot.db.fetch(query, ctx.author.id)

        short_timers = []
        for timer in self.bot.timers.short_timers:
            if timer.event != 'reminder':
                continue

            if timer.metadata['author_id'] != ctx.author.id:
                continue

            short_timers.append(timer)

        short_timers.sort(key=lambda t: t.expires)
        records = short_timers + records

        if not records:
            return 'You have no pending reminders.'

        description = (
            'Run `{0}reminder delete <id>` to cancel a reminder.\n'
            'You can also run `{0}reminders clear` to clear all reminders.'
        ).format(ctx.clean_prefix)

        embed = discord.Embed(color=Colors.primary, description=description, timestamp=ctx.now)
        embed.set_author(name=f'{ctx.author.name}\'s Reminders', icon_url=ctx.author.avatar)
        embed.set_footer(text=pluralize(f'{len(records)} reminder(s) listed.'))

        fields = []

        for timer in records:
            expires, metadata, id = (
                (timer.expires, timer.metadata, timer.id)
                if isinstance(timer, Timer)
                else (timer['expires'], json.loads(timer['metadata']), timer['id'])
            )
            message = metadata['message'] + f' [[Jump!]]({metadata["jump_url"]})'

            if repeat := metadata['repeat']:
                message += f'\n*Repeating every {humanize_duration(repeat)}*'

            fields.append({
                'name': f'{format_dt(expires)} ({format_dt(expires, "R")}) - ID: {id}',
                'value': message,
                'inline': False,
            })

        return Paginator(ctx, FieldBasedFormatter(embed, fields)), REPLY

    @remind.command(name='delete', aliases=('-', 'remove', 'cancel', 'forget'), brief='Deletes a reminder.')
    async def remind_delete(self, ctx: Context, *, reminder: ReminderId) -> CommandResponse:
        """Cancels and deletes a reminder. You cannot restore reminders after they have been deleted.

        Arguments:
        - `reminder`: The ID of the reminder to cancel. You can view a reminder's ID by running `{PREFIX}reminder list`.
        """
        message = reminder.metadata['message']

        if not await ctx.confirm(
            f'Are you sure you want to cancel the reminder with ID {reminder.id} ({message})',
            reference=ctx.message,
            delete_after=True,
        ):
            return 'Alright.', REPLY

        await ctx.bot.timers.end_timer(reminder, dispatch=False, cascade=True)
        return f'Deleted reminder with ID {reminder.id}: {message}', REPLY

    @remind.command(name='clear', aliases=('wipe', 'deleteall', 'removeall', 'reset'))
    async def remind_clear(self, ctx: Context) -> CommandResponse:
        """Cancel all of your pending reminders."""
        if not await ctx.confirm(
            'Are you sure you want to cancel all of your reminders? You will not be able to recover them.',
            reference=ctx.message,
            delete_after=True,
        ):
            return 'Alright.', REPLY

        query = """
                DELETE FROM
                    timers
                WHERE
                    event = 'reminder'
                AND
                    (metadata->'author_id')::BIGINT = $1
                """

        response = await ctx.bot.db.execute(query, ctx.author.id)
        try:
            action, amount = response.split()
            amount = int(amount)
        except ValueError:
            return 'Oops, something unexpected happened while trying to process your request. Mind trying that again?', ERROR

        if action != 'DELETE':
            return f'Expected a DELETE action but got {action!r} instead.', ERROR

        for timer in list(ctx.bot.timers.short_timers):  # We flatten it into a list to avoid iteration-time size mutation
            if timer.event != 'reminder':
                continue

            if timer.metadata['author_id'] != ctx.author.id:
                continue

            del ctx.bot.timers._short_timers[timer.id]
            amount += 1

        if amount <= 0:
            return 'You have no reminders to cancel.', REPLY

        ctx.bot.timers.reset_task()
        return pluralize(f'Successfully cleared all of your reminders. (Deleted {amount} reminder(s))'), REPLY

    @command()
    async def reminders(self, ctx: Context) -> CommandResponse:
        """An alias for `remind list`. See `{PREFIX}help remind list` for help on this command."""
        return await ctx.invoke(self.remind_list)  # type: ignore
