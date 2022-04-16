from __future__ import annotations

import json
import platform
import subprocess
from collections import defaultdict
from tempfile import NamedTemporaryFile
from textwrap import dedent
from typing import ClassVar

import discord
from discord.app_commands import Choice, autocomplete, describe
from jishaku.codeblocks import codeblock_converter

from app.core import BAD_ARGUMENT, Cog, Context, EDIT, REPLY, command, cooldown, group
from app.core.helpers import user_max_concurrency
from app.features.docs import DocumentationManager, DocumentationSource
from app.util import AnsiColor, AnsiStringBuilder
from app.util.common import executor_function
from app.util.types import CommandResponse, JsonObject
from config import Emojis


class Developer(Cog):
    """Commands that are useful for developers."""

    emoji = '\U0001f6e0'

    def cog_load(self) -> None:
        self.docs: DocumentationManager = DocumentationManager(self.bot)

    @group(aliases={'doc-search', 'rtfd'})
    async def rtfm(self, ctx: Context, source: DocumentationSource | None = None, *, query: str = None) -> CommandResponse:
        """Search documentation nodes given a query.

        Arguments:
        - `source`: The name of the documentation to use. Defaults to discord.py
        - `query`: Your search query.
        """
        source = source or DocumentationManager.SOURCES['discord.py']
        if not query:
            return source.url, REPLY

        return await self.docs.execute_rtfm(ctx, source=source, query=query), REPLY

    @rtfm.command()
    async def sources(self, _ctx: Context) -> CommandResponse:
        """View a list of available documentation sources."""
        return '`' + '` `'.join(source.key for source in self.docs.SOURCES.values()) + '`', REPLY

    async def docs_source_autocomplete(self, _interaction: discord.Interaction, current: str) -> list[Choice]:
        current = current.casefold()

        return [
            Choice(name=source.name, value=source.key)
            for source in self.docs.SOURCES.values()
            if source.key.startswith(current) or source.name.casefold().startswith(current)
        ]

    @command(aliases={'doc', 'documentation'}, hybrid=True)
    @describe(source='The name of the documentation to use. Defaults to discord.py', node='The documentation node.')
    @autocomplete(source=docs_source_autocomplete)  # type: ignore
    async def docs(self, ctx: Context, source: DocumentationSource | None = None, *, node: str) -> CommandResponse | None:
        """View rich documentation for a specific node.

        The name must be exact, or else `rtfm` is invoked instead.

        Arguments:
        - `source`: The name of the documentation to use. Defaults to discord.py
        - `node`: The documentation node.
        """
        source = source or DocumentationManager.SOURCES['discord.py']
        result = await self.docs.execute_doc(ctx, source=source, name=node)
        if result is BAD_ARGUMENT:  # artifact of old code. ERROR should be used for future reference instead of BAD_ARGUMENT
            await ctx.invoke(self.rtfm, source=source, query=node)
            return

        return *result, REPLY

    @executor_function
    def run_pyright(self, code: str) -> JsonObject:
        with NamedTemporaryFile('r+', dir='_pyrightfiles', suffix='.py') as fp:
            fp.write(code)
            fp.seek(0)

            response = subprocess.run([
                # this is not very portable
                'venv/bin/pyright' if platform.system() == 'Linux' else 'pyright',
                '--outputjson',
                fp.name,
            ], capture_output=True)

            return json.loads(response.stdout)

    SEVERITY_COLOR_MAPPING: ClassVar[dict[str, AnsiColor]] = {
        'error': AnsiColor.red,
        'warning': AnsiColor.yellow,
        'information': AnsiColor.blue,
    }

    SEVERITY_SYMBOL_MAPPING: ClassVar[dict[str, str]] = {
        'error': '-',
        'warning': '?',
        'information': '+',
    }

    @command(aliases={'type-check', 'typecheck'})
    @cooldown(1, 10)
    @user_max_concurrency(1)
    async def pyright(self, ctx: Context, *, code: codeblock_converter) -> CommandResponse:
        """Runs the Pyright static type checker on the given code.

        Arguments:
        - `code`: The code to type-check.
        """
        # messy code, made this in a rush
        yield f'{Emojis.loading} Running Pyright...', REPLY

        code: str = dedent(code.content).strip()
        result = await self.run_pyright(code)  # type: ignore

        lines = code.splitlines()
        diagnostics = result['generalDiagnostics']

        builder = AnsiStringBuilder()
        raw_output_builder = AnsiStringBuilder()

        lookup = defaultdict(list)

        for diagnostic in diagnostics:
            bounds = diagnostic['range']
            lookup[bounds['start']['line']].append(diagnostic)

            start_location = f'{bounds["start"]["line"] + 1}:{bounds["start"]["character"]}'
            end_location = f'{bounds["end"]["line"] + 1}:{bounds["end"]["character"]}'

            color = self.SEVERITY_COLOR_MAPPING[severity := diagnostic['severity']]
            raw_output_builder.append(
                f'{severity}(line {start_location} to {end_location}): {diagnostic["message"]}',
                color=color,
            )
            raw_output_builder.newline()

        for i, line in enumerate(lines):
            if i in lookup:
                builder.append(line, color=AnsiColor.white)

                for j, diagnostic in enumerate(lookup[i]):
                    splitter = '  # ' if not j else '; '
                    color = self.SEVERITY_COLOR_MAPPING[diagnostic['severity']]

                    builder.append(splitter, color=AnsiColor.gray)
                    message = diagnostic['message'].splitlines()[0]
                    builder.append(message, color=color, bold=True)

                builder.newline()
                continue

            builder.append(line)
            builder.newline()

        raw_output_builder.ensure_codeblock(fallback='diff')

        if not raw_output_builder.raw_length:
            raw_output_builder.append('No errors found!', color=AnsiColor.green)

        out = builder.strip().ensure_codeblock(fallback='py').dynamic(ctx)
        if len(builder) + len(raw_output_builder) > 1990:
            yield raw_output_builder, REPLY
        else:
            out += '\n' + raw_output_builder.dynamic(ctx)

        yield out, EDIT
