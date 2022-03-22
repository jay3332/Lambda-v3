from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import ClassVar, Final, TYPE_CHECKING

import discord.utils
from dateparser.search import search_dates
from discord.ext import commands
from discord.http import handle_message_parameters
from discord.utils import format_dt

from app.core import BAD_ARGUMENT, Cog, Context, Flags, Param, REPLY, Timer, flag, group, store_true
from app.util import humanize_duration
from app.util.converters import IntervalConverter
from app.util.pagination import FieldBasedFormatter, Paginator
from app.util.timezone import TimeZone
from app.util.types import CommandResponse
from config import Colors


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
            converter = ReminderConverter(tz=flags.timezone)
            result = await converter.convert(ctx, reminder[-1])
            flags.timezone = TimeZone.utc()

            return await ctx.invoke(self.remind, reminder=result, flags=flags)

        dt, message, _ = reminder
        message = message or 'something'

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

    @remind.command(name='list')
    async def remind_list(self, ctx: Context) -> CommandResponse:
        """View all of your pending reminders."""
        query = """
                SELECT
                    id,
                    expires,
                    metadata->'message' AS message,
                    metadata->'jump_url' AS jump_url,
                    metadata->'repeat' AS repeat
                FROM
                    timers
                WHERE
                    event = 'reminder'
                AND
                    (metadata->'author_id')::BIGINT = $1
                ORDER BY
                    expires ASC
                """
        records = await self.bot.db.fetch(query, ctx.author.id)
        if not records:
            return 'You have no reminders listed.'

        description = (
            'Run `{0}reminder delete <id>` to cancel a reminder.\n'
            'You can also run `{0}reminders clear` to clear all reminders.'
        ).format(ctx.clean_prefix)

        embed = discord.Embed(color=Colors.primary, description=description, timestamp=ctx.now)
        embed.set_author(name=f'{ctx.author.name}\'s Reminders', icon_url=ctx.author.avatar.url)
        embed.set_footer(text=f'{len(records)} reminders listed.')

        # maybe a comprehension was a bad idea
        fields = [
            {
                'name': f'{format_dt(record["expires"])} ({format_dt(record["expires"], "R")}) - ID: {record["id"]}',
                'value': (
                    record['message']
                    + f' [\\[Jump!\\]]({record["jump_url"]})'
                    + (
                        f'\n*Repeating every {humanize_duration(record["repeat"])}*'
                        if record['repeat'] and record['repeat'] != 'null'
                        else ''
                    )
                ),
                'inline': False,
            }
            for record in records
        ]

        return Paginator(ctx, FieldBasedFormatter(embed, fields)), REPLY
