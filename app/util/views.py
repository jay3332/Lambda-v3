from __future__ import annotations

from typing import Awaitable, Callable, TypeAlias, TYPE_CHECKING

import discord

if TYPE_CHECKING:
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
