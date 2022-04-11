import discord
from jishaku.codeblocks import codeblock_converter

from app.core import Cog, Context, Flags, flag, group, cooldown
from app.util.tags import execute_python_tag, execute_tags
from app.util.types import CommandResponse


class TestFlags(Flags):
    target: discord.Member = flag()
    args: list[str] = flag(aliases=('arg', 'arguments'))


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
