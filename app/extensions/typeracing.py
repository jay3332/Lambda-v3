# Ported from <https://github.com/jay3332/LambdaRewrite/blob/master/bot/cogs/misc/typerace.py>

from __future__ import annotations

import difflib
import json
import random
import re
import time
from io import BytesIO
from typing import Any

import aiohttp
import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from app.core import Bot, Cog, Context, command, cooldown
from app.util.common import executor_function
from app.util.pillow import wrap_text
from app.util.types import RGBColor
from config import Colors


class Typeracing(Cog):
    """Typeracing and it's related commands."""

    emoji = '\U0001f3c6'

    MINIMUM_WPM = 30
    IMAGE_TEXT_COLOR = (107, 203, 232)
    IMAGE_GLOW_COLOR = (0, 0, 0)
    IMAGE_TEXT_SIZE = 36
    IMAGE_TEXT_FONT = ImageFont.truetype(
        BytesIO(open('./assets/fonts/whitney.otf', 'rb').read()),
        size=IMAGE_TEXT_SIZE,
    )
    IMAGE_TEXT_PADDING = 12

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self.quotes: list[dict[str, Any]] = []
        self.bot.loop.create_task(self.load_quotes())

    async def load_quotes(self) -> None:
        async with self.bot.session.get('https://type.fit/api/quotes') as response:
            if not response.ok:
                return

            try:
                data = await response.json()
            except aiohttp.ContentTypeError:
                data = json.loads(await response.read())
            self.quotes = data

    @executor_function
    def render_image(self, text: str, *, color: RGBColor = None) -> BytesIO:
        """Renders the text image from the given text."""
        color = color or self.IMAGE_TEXT_COLOR
        text = '\n'.join(wrap_text(text, self.IMAGE_TEXT_FONT, 600 - self.IMAGE_TEXT_PADDING))
        base_size = (600, (text.count('\n') + 1) * (self.IMAGE_TEXT_SIZE + 7) + 23)

        with Image.new('RGBA', base_size) as main:
            draw = ImageDraw.Draw(main)
            draw.text((self.IMAGE_TEXT_PADDING, 10), text, color, font=self.IMAGE_TEXT_FONT)

            with Image.new('RGBA', base_size, (47, 49, 54)) as final:
                draw = ImageDraw.Draw(final)
                draw.text((self.IMAGE_TEXT_PADDING, 10), text, self.IMAGE_GLOW_COLOR, font=self.IMAGE_TEXT_FONT)
                final = final.filter(ImageFilter.GaussianBlur(radius=4))
                final.paste(main, (0, 0), main)

                buffer = BytesIO()
                final.save(buffer, 'png')
                buffer.seek(0)

                return buffer

    @command('typerace', aliases=('tr', 'type', 'typeracing'), hybrid=True)
    @commands.max_concurrency(1, commands.BucketType.channel)
    @cooldown(2, 10, commands.BucketType.channel)
    async def typerace(self, ctx: Context):
        """Starts a typerace in the current channel.

        The first 3 people to type the prompt with at least 95.5% accuracy will win.
        """
        if not self.quotes:
            return await ctx.send("I'm still caching all of the quotes, hold on...")

        quote = random.choice(self.quotes)

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_image(url='attachment://prompt.png')
        embed.set_author(name=f'{ctx.author.name}\'s Typerace', icon_url=ctx.author.avatar.url)
        embed.set_footer(text='- ' + (quote.get('author') or 'Unknown'))

        file = discord.File(await self.render_image(text := quote['text']), 'prompt.png')
        msg = await ctx.send(file=file, embed=embed)
        start_time = time.perf_counter()

        accuracy_cache = {}
        winner_text = []
        winners = []
        emojis = '\N{TROPHY}', '\N{SECOND PLACE MEDAL}', '\N{THIRD PLACE MEDAL}'
        word_count = len(text) / 5

        async def process_message(message: discord.Message) -> None:
            accuracy = accuracy_cache[message.id]

            winners.append(message.author)
            delay = time.perf_counter() - start_time
            wpm = 60 * accuracy * (word_count / delay)

            emoji = emojis[len(winners) - 1]
            ctx.bot.loop.create_task(message.add_reaction(emoji))

            winner_text.append(f'{emoji} {message.author.mention} in {delay:.1f}s: **{wpm:.2f} WPM** (Acc: {accuracy:.1%})')
            embed.description = '\n'.join(winner_text)
            await ctx.maybe_edit(msg, embed=embed)

        def check(message: discord.Message) -> bool:
            content = re.sub(' +', ' ', message.content)
            accuracy = difflib.SequenceMatcher(a=text, b=content).ratio()
            accuracy_cache[message.id] = accuracy

            return (
                message.channel == ctx.channel
                and not message.author.bot
                and message.author not in winners
                and accuracy >= 0.955
            )

        timeout = word_count * (60 / self.MINIMUM_WPM)
        await self.bot.gather_messages(check, limit=3, timeout=timeout, callback=process_message)

        final = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        final.set_author(name='Typerace finished!')
        final.description = (
            'This typerace has finished. '
            f'You can start another game by doing `{ctx.clean_prefix}typerace`.')

        if winner_text:
            final.add_field(name='Winners', value='\n'.join(winner_text))
        else:
            final.description += (
                f'\n\nThere are no winners since nobody hit the minimum WPM of **{self.MINIMUM_WPM:,}**.'
            )

        final.add_field(name='Prompt', value=text, inline=False)
        await ctx.send(embed=final)


class Prompts:
    _common_200 = [
        'the', 'of', 'to', 'and', 'a', 'in', 'is', 'it', 'you',
        'that', 'he', 'was', 'for', 'on', 'are', 'with', 'as',
        'his', 'they', 'be', 'at', 'one', 'have', 'this', 'from',
        'or', 'had', 'by', 'not', 'word', 'but', 'what', 'some',
        'we', 'can', 'out', 'other', 'were', 'all', 'there',
        'when', 'up', 'use', 'your', 'how', 'said', 'an', 'each',
        'she', 'which', 'do', 'their', 'time', 'if', 'will', 'way',
        'about', 'many', 'then', 'them', 'write', 'would', 'like',
        'so', 'these', 'her', 'long', 'make', 'thing', 'see', 'him',
        'two', 'has', 'look', 'more', 'day', 'could', 'go', 'come',
        'did', 'number', 'sound', 'no', 'most', 'people', 'my',
        'over', 'know', 'water', 'than', 'call', 'first', 'who',
        'may', 'down', 'side', 'been', 'now', 'find', 'any', 'new',
        'work', 'part', 'take', 'get', 'place', 'made', 'live',
        'where', 'after', 'back', 'little', 'only', 'round', 'man',
        'year', 'came', 'show', 'every', 'good', 'me', 'give', 'our',
        'under', 'name', 'very', 'through', 'just', 'form', 'sentence',
        'great', 'think', 'say', 'help', 'low', 'line', 'differ',
        'turn', 'cause', 'much', 'mean', 'before', 'move', 'right',
        'boy', 'old', 'too', 'same', 'tell', 'does', 'set', 'three',
        'want', 'air', 'well', 'also', 'play', 'small', 'end',
        'put', 'home', 'read', 'hand', 'port', 'large', 'spell',
        'add', 'even', 'land', 'here', 'must', 'big', 'high', 'such',
        'follow', 'act', 'why', 'ask', 'men', 'change', 'went', 'light',
        'kind', 'off', 'need', 'house', 'picture', 'try', 'us', 'again',
        'animal', 'point', 'mother', 'world', 'near', 'build', 'self',
        'earth', 'father', 'head'
    ]

    @classmethod
    def generate_common_200(cls, /, length: int = 50) -> str:
        return ' '.join(random.choices(cls._common_200, k=length))
