import importlib
from asyncio import create_subprocess_exec
from asyncio.subprocess import PIPE
from typing import NamedTuple

import discord

from app.core import Bot, Cog, Context, REPLY, group
from app.util import AnsiColor, AnsiStringBuilder, UserView
from app.util.types import CommandResponse
from config import Colors


class GitPullOutput(NamedTuple):
    modified: list[str]
    summary: str


class GitPullView(UserView):
    def __init__(self, ctx: Context, output: GitPullOutput):
        super().__init__(ctx.author)
        self.ctx: Context = ctx
        self.bot: Bot = ctx.bot
        self.output: GitPullOutput = output

        if not self.output.modified:
            self.reload_modified.disabled = True

    @discord.ui.button(label='Reload Modified Files', style=discord.ButtonStyle.primary)
    async def reload_modified(self, interaction: discord.Interaction, _) -> None:
        response = AnsiStringBuilder()

        async with self.ctx.typing():
            for file in self.output.modified:
                if not file.endswith('.py'):
                    continue

                module = file.replace('/', '.').replace('.py', '')  # Super unreliable but it gets the job done

                if module.startswith('app.extensions'):
                    try:
                        if module in self.bot.extensions:
                            await self.bot.reload_extension(module)
                            color, extra = AnsiColor.green, 'reloaded'
                        else:
                            await self.bot.load_extension(module)
                            color, extra = AnsiColor.cyan, 'loaded'

                    except Exception as exc:
                        color, extra = AnsiColor.red, str(exc)

                    response.append(module + ' ', color=color, bold=True)
                    response.append(extra, color=AnsiColor.gray).newline()
                    continue

                try:
                    resolved = importlib.import_module(module)
                    importlib.reload(resolved)
                except Exception as exc:
                    response.append(module + ' ', color=AnsiColor.red, bold=True)
                    response.append(str(exc), color=AnsiColor.gray).newline()
                else:
                    response.append(module + ' ', color=AnsiColor.green, bold=True)
                    response.append('reloaded non-extension', color=AnsiColor.gray).newline()

        await interaction.response.send_message(response.ensure_codeblock().dynamic(self.ctx))

    @discord.ui.button(label='Restart Bot', style=discord.ButtonStyle.danger)
    async def restart_bot(self, interaction: discord.Interaction, _) -> None:
        await interaction.response.send_message('Restarting...')
        await self.bot.close()

    # TODO: Reload all


class Admin(Cog):
    """Restricted commands."""
    __hidden__ = True

    def cog_check(self, ctx: Context) -> bool:
        return ctx.author.id == self.bot.owner_id

    @group(aliases={'owner', 'dev', 'admin', 'adm'})
    async def developer(self, ctx: Context) -> None:
        """Developer-only commands."""
        await ctx.send_help(ctx.command)

    @developer.command('bypass', aliases={'bp', 'bypass-checks'})
    async def bypass(self, ctx: Context) -> CommandResponse:
        """Toggle bypassing checks."""
        ctx.bot.bypass_checks = new = not ctx.bot.bypass_checks

        if new:
            return 'You will now be able to bypass checks.', REPLY

        return 'You will no longer be able to bypass checks.', REPLY

    @developer.group(aliases={'g', 'github', 'remote'})
    async def git(self, ctx: Context) -> None:
        """Manages requests between the bot it's Git remote."""
        await ctx.send_help(ctx.command)

    @staticmethod
    def _parse_git_output(output: str) -> GitPullOutput:
        idx = output.rfind('Fast-forward')
        if idx == -1:
            return GitPullOutput([], 'No files changed.')

        *modified, summary = output[idx + 13:].splitlines()
        modified = [f.rsplit(' | ', maxsplit=1)[0].strip() for f in modified]

        return GitPullOutput(modified, summary.strip())

    @git.command(aliases={'update'})
    async def pull(self, ctx: Context) -> CommandResponse:
        """Updates the local repository with changes from the remote repository."""
        async with ctx.typing():
            proc = await create_subprocess_exec("git", "pull", stdout=PIPE, stderr=PIPE)
            raw = '-'

            stdout, stderr = await proc.communicate()
            try:
                stdout, stderr = stdout.decode(), stderr.decode()
            except UnicodeDecodeError:
                output = GitPullOutput([], 'Failed to decode output.')
            else:
                output = self._parse_git_output(stdout)
                raw = f'```ansi\n{stdout}\n\n{stderr}```'

                if len(raw) > 2000:
                    raw = 'Output too long to display.'

        if not output.modified:
            color = Colors.warning if output.summary.startswith('N') else Colors.error
        else:
            color = Colors.success

        embed = discord.Embed(color=color, description=raw, timestamp=ctx.now)
        embed.add_field(name='Summary', value=output.summary, inline=False)

        modified = '\n'.join(output.modified[:16])
        if len(output.modified) > 16:
            modified += f'\n*{len(output.modified) - 16} more...*'

        embed.add_field(name='Modified Files', value=modified if output.modified else 'None')

        return embed, GitPullView(ctx, output), REPLY
