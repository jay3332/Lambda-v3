from __future__ import annotations

from typing import Any, Awaitable, Callable, TypeAlias, TYPE_CHECKING

import discord

from app.util.structures import TemporaryAttribute

if TYPE_CHECKING:
    from app.core import Context
    from app.core.models import HybridCommand, HybridGroupCommand
    from app.util.types import AsyncCallable, TypedInteraction

AnyUser: TypeAlias = discord.User | discord.Member


class UserView(discord.ui.View):
    def __init__(self, user: AnyUser, *, timeout: float = None) -> None:
        self.user: AnyUser = user
        super().__init__(timeout=timeout)

    async def interaction_check(self, interaction: TypedInteraction) -> bool:
        if interaction.user != self.user:
            message = f'This component view is owned by {self.user.mention}, therefore you cannot use it.'
            await interaction.response.send_message(content=message, ephemeral=True)
            return False

        return True


class ConfirmationView(UserView):
    def __init__(
        self, *, user: AnyUser, true: str = 'Confirm', false: str = 'Cancel',
        timeout: float = None, defer: bool = True, hook: AsyncCallable[[TypedInteraction], None] = None,
    ) -> None:
        self.value: bool | None = None
        self._defer: bool = defer
        self._hook = hook
        super().__init__(user, timeout=timeout)

        self._true_button = discord.ui.Button(style=discord.ButtonStyle.success, label=true)
        self._false_button = discord.ui.Button(style=discord.ButtonStyle.danger, label=false)

        self._true_button.callback = self._make_callback(True)
        self._false_button.callback = self._make_callback(False)

        self.interaction: discord.Interaction | None = None

        self.add_item(self._true_button)
        self.add_item(self._false_button)

    def _make_callback(self, toggle: bool) -> Callable[[TypedInteraction], Awaitable[None]]:
        async def callback(interaction: TypedInteraction) -> None:
            self.value = toggle
            self.interaction = interaction

            self._true_button.disabled = True
            self._false_button.disabled = True

            if toggle:
                self._false_button.style = discord.ButtonStyle.secondary
            else:
                self._true_button.style = discord.ButtonStyle.secondary

            self.stop()
            if toggle and self._hook is not None:
                await self._hook(interaction)
            elif self._defer:
                await interaction.response.defer()

        return callback


class LinkView(discord.ui.View):
    def __init__(self, mapping: dict[str, str]) -> None:
        super().__init__()

        for key, value in mapping.items():
            button = discord.ui.Button(label=key, url=value)
            self.add_item(button)


async def _dummy_parse_arguments(_ctx: Context) -> None:
    pass


async def _get_context(command: HybridCommand | HybridGroupCommand, interaction: discord.Interaction) -> Context:
    interaction._cs_command = command
    interaction.message = None
    return await interaction.client.get_context(interaction)


async def _invoke_command(
    command: HybridCommand | HybridGroupCommand,
    source: discord.Interaction | Context,
    *,
    args: Any,
    kwargs: Any,
) -> None:
    ctx = await _get_context(command, source) if isinstance(source, discord.Interaction) else source
    ctx.args = [ctx.cog, ctx, *args]
    ctx.kwargs = kwargs

    with TemporaryAttribute(command, '_parse_arguments', _dummy_parse_arguments):
        await ctx.bot.invoke(ctx)


class StaticCommandButton(discord.ui.Button):
    def __init__(
        self,
        *,
        command: HybridCommand | HybridGroupCommand,
        command_args: list[Any] = None,
        command_kwargs: dict[str, Any] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.command = command
        self.command_args = command_args or []
        self.command_kwargs = command_kwargs or {}

    async def callback(self, interaction: discord.Interaction) -> None:
        await _invoke_command(self.command, interaction, args=self.command_args, kwargs=self.command_kwargs)


class CommandInvocableModal(discord.ui.Modal):
    def __init__(self, command: HybridCommand | HybridGroupCommand = None, **kwargs) -> None:
        super().__init__(timeout=300, **kwargs)
        self.command = command

    async def get_context(self, interaction: TypedInteraction) -> Context:
        return await _get_context(self.command, interaction)

    # source is _underscored to avoid conflict with commands that have a source parameter
    async def invoke(self, _source: TypedInteraction | Context, /, *args: Any, **kwargs: Any) -> None:
        await _invoke_command(self.command, _source, args=args, kwargs=kwargs)


GetModal = (
    Callable[['TypedInteraction'], Awaitable[discord.ui.Modal] | discord.ui.Modal]
    | discord.ui.Modal
)


class ModalButton(discord.ui.Button):
    def __init__(self, modal: GetModal, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.modal = modal

    async def callback(self, interaction: TypedInteraction) -> Any:
        modal = self.modal
        if callable(modal):
            modal = await discord.utils.maybe_coroutine(modal, interaction)

        await interaction.response.send_modal(modal)
