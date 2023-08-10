from __future__ import annotations

import json
import platform
import subprocess
from collections import defaultdict
from io import StringIO
from tempfile import NamedTemporaryFile
from textwrap import dedent
from typing import Annotated, ClassVar

import discord
from discord.app_commands import Choice, describe
from discord.ext.commands import Range
from jishaku.codeblocks import codeblock_converter

from app.core import BAD_ARGUMENT, Cog, Context, EDIT, Flags, REPLY, command, cooldown, flag, group, store_true
from app.core.helpers import user_max_concurrency
from app.features.docs import DocumentationManager, DocumentationSource
from app.util import AnsiColor, AnsiStringBuilder
from app.util.common import executor_function, humanize_size
from app.util.types import CommandResponse, JsonObject
from app.util.views import LinkView
from config import Emojis


class PrettyPrintFlags(Flags):
    indent: Range[int, 0, 16] = flag(short='i', alias='indents', default=4)
    sort_keys: bool = store_true(short='s', alias='sort')
    ascii: bool = store_true(short='a', aliases=('ensure-ascii', 'escape', 'ascii-only'))
    paste: bool = store_true(short='p')


class Developer(Cog):
    """Commands that are useful for developers."""

    emoji = '\U0001f6e0'

    def cog_load(self) -> None:
        self.docs: DocumentationManager = DocumentationManager(self.bot)

    @group(aliases={'doc-search', 'rtfd'}, hybrid=True, fallback='search')
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

    @rtfm.command(hybrid=True)
    async def sources(self, _ctx: Context) -> CommandResponse:
        """View a list of available documentation sources."""
        return '`' + '` `'.join(source.key for source in self.docs.SOURCES.values()) + '`', REPLY

    @command(aliases={'doc', 'documentation'}, hybrid=True)
    @describe(source='The name of the documentation to use. Defaults to discord.py', node='The documentation node.')
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

    @rtfm.autocomplete('source')
    @docs.autocomplete('source')
    async def docs_source_autocomplete(self, _interaction: discord.Interaction, current: str) -> list[Choice]:
        current = current.casefold()

        return [
            Choice(name=source.name, value=source.key)
            for source in self.docs.SOURCES.values()
            if source.key.startswith(current) or source.name.casefold().startswith(current)
        ]

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

        if not raw_output_builder.raw_length:
            raw_output_builder.append('No errors found!', color=AnsiColor.green)

        raw_output_builder.ensure_codeblock(fallback='diff')

        out = builder.strip().ensure_codeblock(fallback='py').dynamic(ctx)
        if len(builder) + len(raw_output_builder) > 1990:
            yield raw_output_builder, REPLY
        else:
            out += '\n' + raw_output_builder.dynamic(ctx)

        yield out, EDIT

    PRETTY_PRINT_MAX_SIZE: ClassVar[int] = 1024 * 1024 * 8  # 8 MB

    @command('pretty-print', aliases={'pp', 'prettyprint', 'pprint', 'beautify'})
    async def pretty_print(
        self,
        ctx: Context,
        *,
        json_content: Annotated[tuple[str, str], codeblock_converter] = None,
        flags: PrettyPrintFlags,
    ) -> CommandResponse:
        """Pretty prints the given JSON content.

        Arguments:
        - `json_content`: The JSON to pretty-print. You can also pass in a JSON file instead.

        Flags:
        - `--indent <indent_level>`: The indent level to use. Defaults to ``4``.
        - `--sort-keys`: Whether to sort the keys of the JSON. Defaults to ``False``.
        - `--ascii`: Whether to escape non-ASCII characters. Defaults to ``False``.
        - `--paste`: Whether to return the output as a link to a paste-bin instead of a file if it is too long. Defaults to ``False``.
        """
        if not json_content:
            if attachments := ctx.message.attachments:
                attachment = attachments[0]
            elif ctx.message.reference and ctx.message.reference.resolved and ctx.message.reference.resolved.attachments:
                attachment = ctx.message.reference.resolved.attachments[0]
            else:
                return 'No JSON content or attachment given.', BAD_ARGUMENT

            filename = attachment.filename.casefold()

            if not any(filename.endswith(suffix) for suffix in ('.txt', '.json')):
                _, _, ext = filename.rpartition('.')
                ext = ext or 'unknown'

                return f'Expected a raw string, JSON, or TXT file, got {ext!r} instead.', BAD_ARGUMENT

            if attachment.size > self.PRETTY_PRINT_MAX_SIZE:
                return f'Attachment is too large. ({humanize_size(attachment.size)} > {humanize_size(self.PRETTY_PRINT_MAX_SIZE)})', BAD_ARGUMENT

            content = await attachment.read()
            try:
                content = content.decode('utf-8')
            except UnicodeDecodeError:
                return 'The attachment is not a valid UTF-8 file.', BAD_ARGUMENT
        else:
            _, content = json_content

        try:
            json_content = json.loads(content.strip())
        except json.JSONDecodeError:
            return 'The JSON content is not valid.', BAD_ARGUMENT

        res = json.dumps(json_content, indent=flags.indent, sort_keys=flags.sort_keys, ensure_ascii=flags.ascii)
        if len(res) > 1989:
            if flags.paste:
                paste = await ctx.bot.cdn.paste(res, extension='json')
                res = paste.paste_url

                return f'<{res}>', LinkView({'Go to Paste': res}), REPLY

            with StringIO(res) as fp:
                return discord.File(fp, filename=f'pretty_print_{ctx.author.id}.json'), REPLY  # type: ignore

        return f'```json\n{res}```', REPLY
