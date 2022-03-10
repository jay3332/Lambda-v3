from __future__ import annotations

from typing import Any, Awaitable, Callable, NamedTuple, TYPE_CHECKING

import discord
from discord.ext import commands
from discord.utils import maybe_coroutine

from app.util.types import AsyncCallable, TypedContext
from app.util.views import ConfirmationView

if TYPE_CHECKING:
    from datetime import datetime

    from app.core import Bot
    from app.database import Database
    from app.util.views import AnyUser


@discord.utils.copy_doc(commands.Cog)
class Cog(commands.Cog):
    __hidden__: bool = False

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot
        bot.loop.create_task(maybe_coroutine(self.__setup__))

    def __setup__(self) -> Awaitable[None] | None:
        pass

    @classmethod
    def simple_setup(cls, bot: Bot) -> None:
        bot.add_cog(cls(bot))


class PermissionSpec(NamedTuple):
    """A pair of sets of permissions which will be checked for before a command is run."""
    user: set[str]
    bot: set[str]

    @classmethod
    def new(cls) -> PermissionSpec:  # TODO: replace with typing.Self once linter supports it
        """Creates a new permission spec.

        Users default to requiring no permissions.
        Bots default to requiring Send Messages, Embed Links, and External Emojis permissions.
        """
        return cls(user=set(), bot={'send_messages', 'embed_links', 'use_external_emojis'})

    @staticmethod
    def permission_as_str(permission: str) -> str:
        """Takes the attribute name of a permission and turns it into a capitalized, readable one."""
        # weird and hacky solution but it works.
        return permission.replace('_', ' ').title().replace('Tts ', 'TTS ').replace('Guild', 'Server')


# We don't use @has_permissions/@bot_has_permissions as I may want to implement custom permission checks later on
def check_permissions(ctx: Context) -> bool:
    """Checks if the command's author has the required permissions."""
    permissions = ctx.command._permissions

    other = ctx.channel.permissions_for(ctx.author)
    missing = [perm for perm, value in other.items() if perm in permissions.user and not value]

    if missing and not other.administrator:
        raise commands.MissingPermissions(missing)

    other = ctx.channel.permissions_for(ctx.me)
    missing = [perm for perm, value in other.items() if perm in permissions.bot and not value]

    if missing and not other.administrator:
        raise commands.BotMissingPermissions(missing)

    return True


@discord.utils.copy_doc(commands.Command)
class Command(commands.Command):
    def __init__(self, func: Callable[..., Awaitable[Any]], **kwargs: Any) -> None:
        self._permissions: PermissionSpec = PermissionSpec.new()
        super().__init__(func, **kwargs)

        self.add_check(check_permissions)


@discord.utils.copy_doc(commands.Group)
class GroupCommand(commands.Group, Command):
    @discord.utils.copy_doc(commands.Group.command)
    def command(self, *args: Any, **kwargs: Any) -> Callable[[AsyncCallable[..., Any]], Command]:
        def decorator(func: Callable[..., Awaitable[Any]]) -> Command:
            from app.core.helpers import command

            kwargs.setdefault('parent', self)
            result = command(*args, **kwargs)(func)
            self.add_command(result)
            return result

        return decorator

    @discord.utils.copy_doc(commands.Group.group)
    def group(self, *args: Any, **kwargs: Any) -> Callable[[AsyncCallable[..., Any]], GroupCommand]:
        def decorator(func: Callable[..., Awaitable[Any]]) -> GroupCommand:
            from app.core.helpers import group as _group

            kwargs.setdefault('parent', self)
            result = _group(*args, **kwargs)(func)
            self.add_command(result)
            return result

        return decorator


@discord.utils.copy_doc(commands.Context)
class Context(TypedContext):
    bot: Bot
    command: Command | GroupCommand

    def __init__(self, **attrs) -> None:
        self._message: discord.Message | None = None
        super().__init__(**attrs)

    @property
    def db(self) -> Database:
        """The database instance for the current context."""
        return self.bot.db

    @property
    def now(self) -> datetime:
        """Returns when the message of this context was created at."""
        return self.message.created_at

    @staticmethod
    def utcnow() -> datetime:
        """A shortcut for :func:`discord.utils.utcnow`."""
        return discord.utils.utcnow()

    @property
    def clean_prefix(self) -> str:
        """This is preferred over the base implementation as I feel like regex, which was used in the base implementation, is simply unnecessary for this."""
        if self.prefix is None:
            return ''

        user = self.bot.user
        return self.prefix.replace(f'<@{user.id}>', f'@{user.name}').replace(f'<@!{user.id}>', f'@{user.name}')

    async def thumbs(self, message: discord.Message = None) -> None:
        """Reacts to the message with a thumbs-up emoji."""
        message = message or self.message
        try:
            await message.add_reaction('\U0001f44d')
        except discord.HTTPException:
            pass

    async def confirm(
        self,
        content: str = None,
        *,
        delete_after: bool = False,
        view: ConfirmationView = None,
        user: AnyUser = None,
        timeout: float = 60.,
        true: str = 'Yes',
        false: str = 'No',
        **kwargs,
    ) -> bool | None:
        """Sends a confirmation message and waits for a response."""
        user = user or self.author
        view = view or ConfirmationView(user=user, true=true, false=false, timeout=timeout)
        message = await self.send(content, view=view, **kwargs)

        await view.wait()
        if delete_after:
            await message.delete(delay=0)
        else:
            await message.edit(view=view)

        return view.value

    async def maybe_edit(self, message: discord.Message = None, content: Any = None, **kwargs: Any) -> discord.Message | None:
        """Edits the message silently."""
        try:
            await message.edit(content=content, **kwargs)
        except (AttributeError, discord.NotFound):
            if not message or message.channel == self.channel:
                return await self.send(content, **kwargs)

            return await message.channel.send(content, **kwargs)

    @staticmethod
    async def maybe_delete(message: discord.Message, *args: Any, **kwargs: Any) -> None:
        """Deletes the message silently if it exists."""
        try:
            await message.delete(*args, **kwargs)
        except (AttributeError, discord.NotFound, discord.Forbidden):
            pass

    @discord.utils.copy_doc(commands.Context.send)
    async def send(self, content: Any = None, **kwargs: Any) -> discord.Message:
        if kwargs.get('embed') and kwargs.get('embeds') is not None:
            kwargs['embeds'].append(kwargs['embed'])
            del kwargs['embed']

        if kwargs.get('file') and kwargs.get('files') is not None:
            kwargs['files'].append(kwargs['file'])
            del kwargs['file']

        if kwargs.pop('edit', False) and self._message:
            kwargs.pop('files', None)
            kwargs.pop('reference', None)

            await self.maybe_edit(self._message, content, **kwargs)
            return self._message

        self._message = result = await super().send(content, **kwargs)
        return result
