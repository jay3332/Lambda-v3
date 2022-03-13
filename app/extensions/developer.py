from app.core import BAD_ARGUMENT, Cog, Context, REPLY, command
from app.features.docs import DocumentationManager, DocumentationSource
from app.util.types import CommandResponse


class Developer(Cog):
    """Commands that are useful for developers."""

    def __setup__(self) -> None:
        self.docs: DocumentationManager = DocumentationManager(self.bot)

    @command(aliases={'doc-search', 'rtfd'})
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

    @command(aliases={'doc', 'documentation'})
    async def docs(self, ctx: Context, source: DocumentationSource | None = None, *, node: str) -> CommandResponse | None:
        """View rich documentation for a specific node.

        The name must be exact, or else `rtfm` is invoked instead.

        Arguments:
        - `source`: The name of the documentation to use. Defaults to discord.py
        - `node`: The documentation node.
        """
        source = source or DocumentationManager.SOURCES['discord.py']
        result = await self.docs.execute_doc(ctx, source=source, name=node)
        if result is BAD_ARGUMENT:
            await ctx.invoke(self.rtfm, source=source, query=node)
            return

        return *result, REPLY