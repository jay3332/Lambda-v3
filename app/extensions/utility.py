import random

import discord
from async_google_trans_new.constant import LANGUAGES
from jishaku.codeblocks import codeblock_converter

from app.core import Cog, Context, Flags, REPLY, command, flag, group, cooldown
from app.extensions.ai import TranslationLanguage
from app.util import UserView, cutoff
from app.util.pagination import LineBasedFormatter, Paginator
from app.util.tags import execute_python_tag, execute_tags
from app.util.types import CommandResponse
from config import Colors


class TestFlags(Flags):
    target: discord.Member = flag()
    args: list[str] = flag(aliases=('arg', 'arguments'))


class TranslateFlags(Flags):
    source: TranslationLanguage = flag(short='s', aliases=('src', 'from', 'source-lang', 'original'), default='auto')


class RevealPronunciationView(UserView):
    def __init__(self, ctx: Context, pronunciation: str) -> None:
        super().__init__(ctx.author)
        self.pronunciation = pronunciation

    @discord.ui.button(label='Reveal Pronunciation', style=discord.ButtonStyle.primary)
    async def reveal_pronunciation(self, interaction: discord.Interaction, _) -> None:
        await interaction.response.send_message(
            cutoff(self.pronunciation, 4096, exact=True),
            ephemeral=True,
        )


def _capitalize_language(language: str) -> str:
    if 'chinese' in language:
        return 'Chinese (Simplified)' if 'simplified' in language else 'Chinese (Traditional)'

    return language.title()


class Utility(Cog):
    """Useful utility commands. These can be used for debugging, testing, and more."""

    emoji = '\U0001f527'

    @group(name='test', alias='test-tag')
    @cooldown(1, 3)
    async def test(self, ctx: Context, *, content: str, flags: TestFlags) -> None:
        """Returns the text formatted using [Tag Formatting](https://docs.lambdabot.cf/master).

        Examples:
        - `{PREFIX}test Hello there, {user.mention}!`
        - `{PREFIX}test The second argument is {arg(2)} --args first second`
        - `{PREFIX}test The target is {target.mention} --target @User`

        Arguments:
        - `content`: The contents of the text to format.

        Flags:
        - `--target`: The target user of the command which will be present via `{target}`. Defaults to yourself.
        - `--args`: A list of the arguments to pass in, separated by spaces. Arguments with spaces should be enclosed with double quotes.
        """
        await execute_tags(
            bot=ctx.bot,
            message=ctx.message,
            channel=ctx.channel,
            content=content,
            target=flags.target,
            args=flags.args,
        )

    @test.command(name='python', alias='py')
    @cooldown(1, 3)
    async def test_python(self, ctx: Context, *, code: codeblock_converter, flags: TestFlags) -> None:
        """Runs the given [Python-based tag response code](https://docs.lambdabot.cf/master/advanced-tags).

        See the above hyperlink for more information on Python-based tags.

        Arguments:
        - `code`: The Python code to execute. This can be enclosed in a codeblock.

        Flags:
        - `--target`: The target user of the command which will be present via `{target}`. Defaults to yourself.
        - `--args`: A list of the arguments to pass in, separated by spaces. Arguments with spaces should be enclosed with double quotes.
        """
        await execute_python_tag(
            bot=ctx.bot,
            message=ctx.message,
            channel=ctx.channel,
            code=code.content,
            target=flags.target,
            args=flags.args,
        )

    @group(aliases=('tr', 'trans'))
    @cooldown(1, 3)
    async def translate(
        self,
        ctx: Context,
        destination: TranslationLanguage | None = 'en',
        *,
        text: str,
        flags: TranslateFlags,
    ) -> CommandResponse:
        """Translates the given text to the given destination language.

        Arguments:
        - `destination`: The destination language to translate to. Defaults to `en` (English).
        - `text`: The text to translate.

        Flags:
        - `--source <source>`: The source language to translate from. The language will be automatically determined by default.
        """
        translated, _, pronunciation = await ctx.bot.translator.translate(
            text, lang_tgt=destination, lang_src=flags.source, pronounce=True,
        )
        view = pronunciation and RevealPronunciationView(ctx, pronunciation)

        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        if isinstance(translated, list):
            feminine, masculine = translated

            embed.add_field(name='Feminine', value=cutoff(feminine, 1024, exact=True), inline=False)
            embed.add_field(name='Masculine', value=cutoff(masculine, 1024, exact=True), inline=False)
        else:
            embed.description = cutoff(translated, 4096, exact=True)

        _, source = flags.source if flags.source != 'auto' else await ctx.bot.translator.detect(text)
        destination = LANGUAGES[destination]

        embed.set_author(name=f'Translation: {ctx.author.name}', icon_url=ctx.author.display_avatar)
        embed.set_footer(text=f'{_capitalize_language(source)} \u279c {_capitalize_language(destination)}')

        return embed, view, REPLY

    @translate.command(name='languages', alias='langs')
    async def translate_languages(self, ctx: Context) -> CommandResponse:
        """Lists all supported languages."""
        embed = discord.Embed(color=Colors.primary, timestamp=ctx.now)
        embed.set_author(name='Supported Languages')

        return Paginator(ctx, LineBasedFormatter(
            embed,
            [
                f'`{k}` ({_capitalize_language(v)})' for k, v in LANGUAGES.items()
            ]
        )), REPLY
    
    WALKER_QUOTES = [
        "Just remember, Dick and Cock are right next to each other",
        "I think I hear my mother calling, *mother*!",
        "Oh honey that tickles!",
        "As my grandmother Eunice always said, 'Better late than never!' She lived to be 92...",
        "True dat, true dat",
        "I'll ask Mrs. Swiggins to get those copied for you!",
        "Mrs. Swiggins is running a little late today",
        "I pleeeedge to do my beEeEeest today...",
        "So true and words to live by",
        "Looks like somebody's reading my cue cards!",
        "Naughty naughty",
        "Study study, school's your buddy.",
        "Make sure you jot down today's homework, or H.W. in the Biz",
        "Slow down, there's enough learning for everybody",
        "The period is Period ___ of course, my favorite class and hopefully yours!",
        "Here's some team punches for you guys",
        "Make sure to update the football field...looks like ___ is in scoring position!",
        "Don't forget your Ti-83 calculator!",
        "I'll never forget the time Harry Hoshenpepper tried to take his Scrantron in green highlighter...",
        "Aaaaaand... pinwheel!",
        "You wouldn't want to forget your hello kitty lunchbox!",
        "Watch your fingers, watch your toes, watch where your TI-84 calculators go!",
        "We don't wany any crack in the table! Crack is bad!",
        "This is a family program!",
        "Last call for Period \\_\\_\\_, this is the last and final call for Period \\_\\_\\_...",
        "Period 7, just like heaven!",
        "Ok folks, time for period 9!",
        "[name], would you like a bullying form?",
        "That's where the saying ___ comes from!",
        "They called prostitutes back then soiled doves....",
        "Just a few more minutes until showtime!",
        "Autographs will be available after school",
        "Signed: to my dearest fan",
        "We're all big weiners!",
        "And remember, say no to crack!",
        "Only ___ days left to high school...can you believe it?",
        "Looks like the ___ are about to score a touchdown!",
        "bam chika bam bam",
        "It's always bad when a student can drive...",
        "You never wanna see 'DO-OVER' on your report card...",
        "Let me get the lights for you! claps",
        "Special thanks to my lighting department",
        "Its time for the Walker Pledge on this beautiful ___ morning"
    ]
    
    @command(aliases=('walkerquote', 'walker-quote'), hidden=True)
    async def walker(self, ctx: Context) -> CommandResponse:
        """Sends a random quote from Mr. Walker"""
        webhook = discord.utils.get(await ctx.channel.webhooks(), name='Mr. Walker')
        if not webhook:
            async with ctx.bot.session.get('https://cdn.discordapp.com/attachments/728341827471671429/984612085965160448/unknown.png') as img:
                img = await img.read()
            
            webhook = await ctx.channel.create_webhook(name='Mr. Walker', avatar=img)
            
        await webhook.send(random.choice(self.WALKER_QUOTES), wait=True)
