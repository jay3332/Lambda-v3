from __future__ import annotations

import datetime
import inspect
import json
import random
import re
from typing import (
    Any,
    Callable,
    ClassVar,
    Concatenate,
    Final,
    Generic,
    Iterator,
    NamedTuple,
    ParamSpec,
    Protocol,
    Type,
    TypeVar,
    TYPE_CHECKING,
)

import discord
from discord.ext.commands import BadColourArgument, ColourConverter
from discord.http import handle_message_parameters
from discord.utils import maybe_coroutine, valid_icon_size

from app.util import cutoff
from app.util.common import ordinal, preinstantiate

if TYPE_CHECKING:
    from app.core import Bot

__all__ = (
    'transform',
    'Transformer',
    'TransformerRegistry',
    'TransformerCallback',
    'Environment',
    'Parser',
    'parse',
    'DiscordMetadata',
    'RandomTransformer',
    'MetaTransformer',
    'UserTransformer',
    'LevelingTransformer',
    'EmbedTransformer',
    'ConditionalTransformer',
    'ViewTransformer',
    'VariableTransformer',
    'create_transformer_registry',
    'execute_tags',
)

P = ParamSpec('P')
T = TypeVar('T')
EnvironmentT = TypeVar('EnvironmentT', bound='Environment')
TransformerT = TypeVar('TransformerT', bound='Transformer')


def transform(*names: str, split_args: bool = True, evaluate_modifier: bool = True) -> Callable[
    [Callable[Concatenate[Transformer, Environment, P], T]],
    TransformerCallback[EnvironmentT, P, T],
]:
    """Creates a transformer callback from a function"""

    def decorator(func: Callable[Concatenate[Transformer, Environment, P], T]) -> TransformerCallback[EnvironmentT, P, T]:
        nonlocal names

        if not names:
            names = [func.__name__]

        return TransformerCallback(func, *names, split_args=split_args, evaluate_modifier=evaluate_modifier)  # type: ignore

    return decorator


class DiscordMetadata(NamedTuple):
    message: discord.Message
    bot: Bot

    @property
    def user(self) -> discord.Member:
        return self.message.author

    @property
    def guild(self) -> discord.Guild:
        return self.message.guild

    @property
    def channel(self) -> discord.TextChannel:
        return self.message.channel


class Environment(Generic[T]):
    """Represents a tag parsing environment."""

    def __init__(self, *, message: discord.Message, bot: Bot) -> None:
        self.metadata: T = DiscordMetadata(message, bot)
        self.vars: dict[str, Any] = {}

        self.embed: discord.Embed | None = None
        self.view: discord.ui.View | None = None

    def get_embed(self) -> discord.Embed:
        if self.embed is None:
            self.embed = discord.Embed()

        return self.embed

    def get_view(self) -> discord.ui.View:
        if self.view is None:
            self.view = discord.ui.View()

        return self.view


class TransformerCallback(Generic[EnvironmentT, P, T]):
    """Stores the callback for when the tag is transformed."""

    def __init__(
        self,
        func: Callable[Concatenate[Transformer, EnvironmentT, P], T],
        *names: str,
        parent: TransformerCallback | None = None,
        split_args: bool = True,
        evaluate_modifier: bool = True,
    ) -> None:
        self.callback: Callable[Concatenate[Transformer, EnvironmentT, P], T] = func
        self.transformer: Transformer | None = None
        self.names: tuple[str, ...] = names
        self.split_args: bool = split_args
        self.evaluate_modifier: bool = evaluate_modifier

        self.parent: TransformerCallback | None = parent
        self.children: list[TransformerCallback] = []

    @property
    def name(self) -> str:
        return self.names[0]

    @property
    def parents(self) -> list[TransformerCallback]:
        """Returns a list of all parents of this tree.

        The most related parents are first in the list.
        E.g. if this is a.b.c this will return [b, a].
        """
        parents: list[TransformerCallback] = []
        parent: TransformerCallback | None = self.parent

        while parent is not None:
            parents.append(parent)
            parent = parent.parent

        return parents

    @property
    def qualified_name(self) -> str:
        parents = self.parents[::-1]
        parents.append(self)

        return '.'.join(parent.name for parent in parents)

    def transform(self, *names: str, split_args: bool = True, evaluate_modifier: bool = True) -> Callable[
        [Callable[Concatenate[Transformer, Environment, P], T]],
        TransformerCallback[EnvironmentT, P, T],
    ]:
        """Creates a transformer callback from a function"""
        def decorator(func: Callable[Concatenate[Transformer, Environment, P], T]) -> TransformerCallback[EnvironmentT, P, T]:
            nonlocal names

            if not names:
                names = [func.__name__]

            self.children.append(
                result := TransformerCallback(
                    func,
                    *names,
                    parent=self,
                    split_args=split_args,
                    evaluate_modifier=evaluate_modifier,
                ),
            )
            return result  # type: ignore

        return decorator

    def __call__(self, env: EnvironmentT, *args: P.args, **kwargs: P.kwargs) -> T:
        if self.transformer is None:
            raise RuntimeError(
                'The transformer callback was not registered with a transformer.'
            )

        return self.callback(self.transformer, env, *args, **kwargs)


class Transformer(Generic[EnvironmentT]):
    """Interface to transform tags into text."""

    if TYPE_CHECKING:
        transformers: list[TransformerCallback[EnvironmentT, Any, Any]]

    def __new__(cls: Type[Transformer[EnvironmentT]]) -> Transformer[EnvironmentT]:
        self = super().__new__(cls)
        self.transformers = []

        for name, member in inspect.getmembers(
            cls,
            predicate=lambda m: isinstance(m, TransformerCallback),
        ):
            member: TransformerCallback
            member.transformer = self
            self.transformers.append(member)

        return self

    def _get_transformer_callback(
        self,
        name: str,
        *,
        parent: TransformerCallback | None = None,
    ) -> TransformerCallback[EnvironmentT, Any, Any] | None:
        name = name.casefold()
        children = parent.children if parent else self.transformers

        for transformer in children:
            if name in transformer.names:
                return transformer

        return None

    def get_transformer_callback(self, name: str) -> TransformerCallback[EnvironmentT, Any, Any] | None:
        """Returns the transformer callback for the given name."""
        name = name.split('.')
        parent = None

        for tag in name:
            parent = self._get_transformer_callback(tag, parent=parent)
            if parent is None:
                return None

        return parent


class TransformerRegistry(Generic[TransformerT]):
    def __init__(self, *transformers: TransformerT) -> None:
        self.transformers: list[TransformerT] = list(transformers)

    def get_transformer_callback(self, name: str) -> TransformerCallback | None:
        """Returns the transformer callback for the given name."""
        name = name.casefold()

        for transformer in self.transformers:
            callback = transformer.get_transformer_callback(name)

            if callback is not None:
                return callback

        return None

    def add_transformer(self, transformer: TransformerT) -> None:
        self.transformers.append(transformer)

    def remove_transformer(self, transformer: TransformerT) -> None:
        self.transformers.remove(transformer)

    def copy(self) -> TransformerRegistry[TransformerT]:
        return TransformerRegistry(*self.transformers)


class Node:
    def __init__(self, start: int, end: int) -> None:
        self.start: int = start
        self.end: int = end

    def __iter__(self) -> Iterator[int]:
        yield self.start
        yield self.end

    def __len__(self) -> int:
        return self.end - self.start


class Parser:
    """Handles parsing of tags.

    Grammar: "{" name ( "." attr )* ["(" modifier ")"] [ ":" argument ( ";" argument )* ] "}"
    """

    ARGUMENT_REGEX: ClassVar[re.Pattern[str]] = re.compile(r'(?<!\\);')

    @staticmethod
    def walk_nodes(text: str) -> Iterator[Node]:
        buffers = []
        can_increase_modifier = False
        modifier_count = 0

        for i, char in enumerate(text):
            if i > 0 and text[i - 1] == '\\':
                continue

            if char == '(' and can_increase_modifier:
                modifier_count += 1

            elif char == ')' and can_increase_modifier and modifier_count > 0:
                modifier_count -= 1

            if modifier_count > 0:
                continue

            if char == '{':
                buffers.append(i)
                can_increase_modifier = True

            elif char == '}':
                start = buffers.pop()
                yield Node(start, i + 1)
                can_increase_modifier = False

            elif char == ':':
                can_increase_modifier = False

    @staticmethod
    def split_tag(text: str) -> tuple[str, str, str]:
        """Split a tag into its name, modifier, and arguments."""
        modifier_count = 0
        modifier_cumulative_count = 0

        tag = None
        tag_end = None
        modifier = None
        args = None

        for i, char in enumerate(text):
            if char == '(':
                modifier_count += 1
                modifier_cumulative_count += 1

                if modifier_cumulative_count == 1:  # First "(" encountered
                    tag_end = i
                    tag = text[:tag_end]

            elif char == ')' and modifier_count > 0:
                modifier_count -= 1

            if modifier_count > 0:
                continue

            end = len(text) - 1 == i
            if end or char == ':':
                modifier = (
                    ''
                    if tag_end is None
                    else text[tag_end + 1:i if end and char != ':' else i - 1]
                )
                try:
                    args = text[i + 1:]
                except IndexError:
                    args = ''

                break

        if tag is None:
            tag = text.split(':')[0]

        if modifier is None:
            modifier = ''

        if args is None:
            args = ''

        return tag, modifier, args

    @classmethod
    async def parse(cls, text: str, *, env: EnvironmentT, transformers: TransformerRegistry, silent: bool = False) -> str:
        """Parses the given text and returns the transformed text."""
        nodes = list(cls.walk_nodes(text))

        for i, node in enumerate(nodes, start=1):
            start, end = node
            entry = text[start:end].removeprefix('{').removesuffix('}')
            tag, modifier, args = cls.split_tag(entry)

            callback = transformers.get_transformer_callback(tag.strip())
            if callback is None:
                continue

            if callback.split_args:
                args = [
                    arg.replace('\\;', ';')
                    for arg in cls.ARGUMENT_REGEX.split(args)
                ] if args else []
            else:
                args = [args]

            if callback.evaluate_modifier:
                modifier = await cls.parse(modifier, env=env, transformers=transformers, silent=silent)

            try:
                repl = await maybe_coroutine(callback, env, modifier, *args)
            except Exception as exc:
                if silent:
                    continue

                repl = f'{{error: {exc}}}'
            repl = str(repl) if repl is not None else ''

            offset = len(repl) - len(node)
            text = text[:start] + repl + text[end:]

            if not offset:
                continue

            for child in nodes[i:]:
                if child.start > start:
                    child.start += offset

                if child.end > start:
                    child.end += offset

        return text


parse = Parser.parse


def _transform_bool(string: str) -> bool | None:
    """Transform a string into a boolean."""
    string = string.lower()
    if string in ('true', 'yes', '1'):
        return True

    if string in ('false', 'no', '0'):
        return False

    return None


_OPERATOR_LOOKUP: Final[dict[str, tuple[Type[T], Callable[[T, T], bool]]]] = {
    '#==': (float, float.__eq__),
    '#!=': (float, float.__ne__),
    '#<>': (float, float.__ne__),
    '==': (str, str.__eq__),
    '!=': (str, str.__ne__),
    '<>': (str, str.__ne__),
    '>=': (float, float.__ge__),
    '<=': (float, float.__le__),
    '>': (float, float.__gt__),
    '<': (float, float.__lt__),
}


def transform_conditional(condition: str) -> bool | None:
    """Transform a conditional like '5 > 3' into a boolean."""
    condition = condition.strip()
    transformed = _transform_bool(condition)
    if transformed is not None:
        return transformed

    for candidate, (converter, op) in _OPERATOR_LOOKUP.items():
        if candidate not in condition:
            continue

        left, _, right = condition.partition(candidate)
        try:
            left = converter(left.strip('( \t\n\r)'))
            right = converter(right.strip('( \t\n\r)'))
        except ValueError:
            return False

        return op(left, right)

    return None


@preinstantiate()
class ConditionalTransformer(Transformer[Any]):
    @transform('if', '?')
    async def transform_if(self, _, condition: str, *args) -> str:
        """Transform an if statement.

        Usage: {if(condition):content;else}
        """
        if not args:
            raise ValueError('expected at least one argument')

        if len(args) > 2:
            raise ValueError('expected at most two arguments')

        condition = transform_conditional(condition)

        if condition:
            return args[0]
        elif condition is False:
            return args[1] if len(args) > 1 else ''

        raise ValueError('invalid conditional')

    @transform('unless', '!', 'not')
    async def transform_unless(self, _, condition: str, *args: str) -> str:
        """Transform an unless statement."""
        if not args:
            raise ValueError('expected at least one argument')

        if len(args) > 2:
            raise ValueError('expected at most two arguments')

        condition = transform_conditional(condition)

        if condition is False:
            return args[0]
        elif condition:
            return args[1] if len(args) > 1 else ''

        raise ValueError('invalid conditional')

    @transform('exists', '??', split_args=False)
    async def transform_exists(self, _, modifier: str, arg: str) -> bool:
        """Return whether it exists"""
        return bool(modifier or arg)

    @transform('or', 'any', 'some')
    async def transform_or(self, _, _mod, *args: str) -> bool:
        """Return whether any of the arguments are true."""
        return any(map(transform_conditional, args))

    @transform('and', 'all')
    async def transform_and(self, _, _mod, *args: str) -> bool:
        """Return whether all arguments are true."""
        return all(map(transform_conditional, args))


@preinstantiate()
class MetaTransformer(Transformer[Any]):
    @transform('char-at', 'charAt', 'getchar', 'char', split_args=False)
    async def char_at(self, _, modifier: str, arg: str) -> str:
        if not modifier or not arg:
            raise ValueError('char-at requires a modifier (index) and an argument (string)')

        try:
            modifier = int(modifier)
        except ValueError:
            raise ValueError('index must be an integer')

        try:
            return arg[modifier - 1 if modifier > 0 else modifier]
        except IndexError:
            raise IndexError('character out of range')

    @transform('escape', split_args=False, evaluate_modifier=False)
    async def escape(self, _, modifier: str, arg: str) -> str:
        return (arg or modifier).replace(';', '\\;')

    @transform('length', 'len', 'size', split_args=False)
    async def length(self, _, modifier: str, arg: str) -> int:
        return len(arg or modifier)

    @transform('lowercase', 'lower', split_args=False)
    async def lowercase(self, _, modifier: str, arg: str) -> str:
        return (arg or modifier).lower()

    @transform('uppercase', 'upper')
    async def uppercase(self, _, modifier: str, arg: str) -> str:
        return (arg or modifier).upper()

    @transform('replace', 'repl', 'sub')
    async def replace(self, _, modifier: str, *args: str) -> str:
        if len(args) != 2:
            raise ValueError('replace requires exactly two arguments')

        from_, to = args
        return modifier.replace(from_, to)

    @transform('repeat', 'rep', split_args=False)
    async def repeat(self, _, modifier: str, arg: str) -> str:
        if not arg:
            raise ValueError('repeat requires an argument')

        try:
            modifier = int(modifier)
        except ValueError:
            raise ValueError('repeat requires an integer modifier')

        if modifier < 1:
            raise ValueError('repeat requires a positive integer')

        if modifier > 1000:
            raise ValueError('repeat modifier must be less than 1000')

        return arg * modifier

    @transform('strip', 'trim', 'truncate', split_args=False)
    async def strip(self, _, modifier: str, arg: str) -> str:
        """Usage: {strip(<>):<my-string>} -> my-string"""
        if not modifier:
            modifier = ' \n'

        return arg.strip(modifier)

    @transform('round', 'rnd', split_args=False)
    async def round(self, _, modifier: str, arg: str) -> int:
        try:
            num = float(arg or modifier)
        except ValueError:
            raise ValueError('round requires a number argument')

        return round(num)

    @transform('ordinal', 'ord', split_args=False)
    async def ordinal(self, _, modifier: str, arg: str) -> str:
        try:
            num = int(arg or modifier)
        except ValueError:
            raise ValueError('ordinal requires a number argument')

        return ordinal(num)

    @transform('cutoff')
    async def cutoff(self, _, modifier: str, arg: str) -> str:
        if not modifier or not arg:
            raise ValueError('cutoff requires a modifier (max length) and an argument (string)')

        if not arg:
            raise ValueError('cutoff requires a single argument')

        try:
            modifier = int(modifier)
        except ValueError:
            raise ValueError('max length must be an integer')

        if modifier < 1:
            raise ValueError('max length must be greater than 0')

        return cutoff(arg, modifier)

    @transform('comment', '//', split_args=False, evaluate_modifier=False)
    async def comment(self, *_) -> str:
        return ''

    # TODO: {math: 1 + 1}


@preinstantiate()
class RandomTransformer(Transformer[Any]):
    @transform('random', 'rng', '#', 'rand')
    def random(self, _, modifier: str, *args: str) -> int:
        """Return a random number between args(0) and args(1)."""
        if len(args) > 2:
            raise ValueError('random takes at most 2 arguments')

        if len(args) == 0:
            args = modifier,

        if len(args) == 1:
            args = '1', args[0]

        try:
            low, high = map(int, map(str.strip, args))
        except ValueError:
            raise ValueError('random takes only integers as arguments')

        if low > high:
            low, high = high, low

        if low == high:
            return low

        return random.randint(low, high)

    @transform('choice', 'choose', '##', 'randchoice', 'random-choice', 'pick')
    def choice(self, _, modifier: str, *args: str) -> str:
        """Return a random choice of the given arguments."""
        if not args:
            if modifier:
                raise ValueError('choice does not take a modifier')

            raise ValueError('choice takes at least 1 argument')

        return random.choice(args)


class _SupportsUser(Protocol):
    user: discord.Member
    guild: discord.Guild


@preinstantiate()
class UserTransformer(Transformer[Environment[_SupportsUser]]):
    @staticmethod
    def _get_user(env: Environment[_SupportsUser], modifier: str) -> discord.Member:
        if modifier:
            try:
                return env.metadata.guild.get_member(int(modifier))
            except (ValueError, AttributeError):
                raise ValueError('could not resolve modifier, try removing it instead.')

        return env.metadata.user

    @staticmethod
    def get_asset_details(args: tuple[str, ...]) -> tuple[str | None, int | None]:
        if not args:
            return None, None

        if len(args) == 1:
            args = (None, int(args[0])) if args[0].isdigit() else (args[0], 0)
        if len(args) != 2:
            raise ValueError('asset details must be in the form of <format> <size>')

        fmt, size = args

        if not valid_icon_size(size):
            raise ValueError('avatar size must be a power of 2 between 16 and 4096')

        return fmt.lower(), size

    @staticmethod
    def get_asset_url(asset: discord.Asset, fmt: str, size: int) -> str:
        if fmt:
            asset = asset.with_format(fmt)  # type: ignore
        if size:
            asset = asset.with_size(size)

        return asset.url

    @transform('user', 'member')
    def user(self, env: Environment[_SupportsUser], modifier: str, *_) -> discord.Member:
        """Return the author of the current message."""
        return self._get_user(env, modifier)

    @user.transform('name', 'username')
    def user_name(self, env: Environment[_SupportsUser], modifier: str, *_) -> str:
        """Return the name of the author of the current message."""
        return self._get_user(env, modifier).name

    @user.transform('discriminator', 'discrim')
    def user_discriminator(self, env: Environment[_SupportsUser], modifier: str, *_) -> str:
        """Return the discriminator of the author of the current message."""
        return self._get_user(env, modifier).discriminator

    @user.transform('nick', 'nickname', 'display-name', 'display')
    def user_nick(self, env: Environment[_SupportsUser], modifier: str, *_) -> str:
        """Return the nickname of the author of the current message."""
        return self._get_user(env, modifier).nick

    @user.transform('mention', 'ping')
    def user_mention(self, env: Environment[_SupportsUser], modifier: str, *_) -> str:
        """Return a mention of the author of the current message."""
        return self._get_user(env, modifier).mention

    @user.transform('tag')
    def user_tag(self, env: Environment[_SupportsUser], modifier: str, *_) -> discord.Member:
        """Return the tag of the author of the current message."""
        return self._get_user(env, modifier)

    @user.transform('avatar', 'avatar-url', 'pfp', 'icon')
    def user_avatar(self, env: Environment[_SupportsUser], modifier: str, *args: str) -> str:
        """Return the avatar URL of the author of the current message."""
        fmt, size = self.get_asset_details(args)
        avatar = self._get_user(env, modifier).avatar

        return self.get_asset_url(avatar, fmt, size)

    @user.transform('display-avatar', 'display-avatar-url', 'display-pfp', 'display-icon')
    def user_display_avatar(self, env: Environment[_SupportsUser], modifier: str, *args: str) -> str:
        """Return the [guild] avatar URL of the author of the current message."""
        fmt, size = self.get_asset_details(args)
        avatar = self._get_user(env, modifier).display_avatar

        return self.get_asset_url(avatar, fmt, size)

    @user.transform('id')
    def user_id(self, env: Environment[_SupportsUser], modifier: str, *_) -> int:
        """Return the ID of the author of the current message."""
        return self._get_user(env, modifier).id

    @user.transform('created', 'created-at', 'creation-date')
    def user_created(self, env: Environment[_SupportsUser], modifier: str, *_) -> datetime.datetime:
        """Return the creation date of the author of the current message."""
        return self._get_user(env, modifier).created_at

    @user.transform('joined', 'joined-at', 'join-date')
    def user_joined(self, env: Environment[_SupportsUser], modifier: str, *_) -> datetime.datetime:
        """Return the join date of the author of the current message."""
        return self._get_user(env, modifier).joined_at


@preinstantiate()
class LevelingTransformer(Transformer[Environment[DiscordMetadata]]):
    @staticmethod
    async def _get_record(env: Environment[DiscordMetadata]):
        cog = env.metadata.bot.get_cog('Leveling')
        if cog is None:
            raise RuntimeError('Leveling cog not found.')

        record = cog.manager.user_stats_for(env.metadata.user)  # type: ignore
        await record.fetch_if_necessary()

        return record

    @transform('level', 'lvl', 'lv')
    async def level(self, env: Environment[DiscordMetadata], *_) -> int:
        """Return the current level of the author of the current message."""
        record = await self._get_record(env)
        return record.level

    @transform('xp', 'exp')
    async def xp(self, env: Environment[DiscordMetadata], *_) -> int:
        """Return the current XP of the author of the current message."""
        record = await self._get_record(env)
        return record.xp


@preinstantiate()
class EmbedTransformer(Transformer[Environment[Any]]):
    @transform('embed', evaluate_modifier=False)
    def embed(self, env: Environment[Any], modifier: str, *_) -> None:
        """Return the embed of the current message."""
        if not modifier:
            raise ValueError('access a subtag or directly use JSON')

        env.embed = discord.Embed.from_dict(json.loads(modifier))

    @embed.transform('title', split_args=False)
    def embed_title(self, env: Environment[Any], _, arg: str) -> None:
        """Set the title of the embed of the current message."""
        if not arg:
            raise ValueError('no title specified')

        env.get_embed().title = arg

    @embed.transform('description', split_args=False)
    def embed_description(self, env: Environment[Any], _, arg: str) -> None:
        """Set the description of the embed of the current message."""
        if not arg:
            raise ValueError('no description specified')

        env.get_embed().description = arg

    @embed.transform('url', split_args=False)
    def embed_url(self, env: Environment[Any], _, arg: str) -> None:
        """Set the URL of the embed of the current message."""
        if not arg:
            raise ValueError('no url specified')

        env.get_embed().url = arg.strip('< >')

    @embed.transform('color', 'colour', split_args=False)
    async def embed_color(self, env: Environment[Any], _, arg: str) -> None:
        """Set the color of the embed of the current message."""
        if not arg:
            raise ValueError('no color specified')

        try:
            env.get_embed().colour = await ColourConverter().convert(None, arg)  # type: ignore
        except BadColourArgument:
            raise ValueError('invalid color specified')

    @embed.transform('author', 'name', split_args=False)
    async def embed_author(self, env: Environment[Any], _, arg: str) -> None:
        """Set the name of the author of the embed of the current message."""
        if not arg:
            raise ValueError('no author specified')

        icon = env.embed and env.embed.author.icon_url
        url = env.embed and env.embed.author.url
        empty = discord.Embed.Empty

        env.get_embed().set_author(name=arg, url=url or empty, icon_url=icon or empty)

    @embed_author.transform('icon', 'icon-url', split_args=False)
    async def embed_author_icon(self, env: Environment[Any], _, arg: str) -> None:
        """Set the icon of the author of the embed of the current message."""
        if not arg:
            raise ValueError('no author icon specified')

        name = env.embed and env.embed.author.name
        url = env.embed and env.embed.author.url
        empty = discord.Embed.Empty

        env.get_embed().set_author(
            name=name or '{error: Missing author name}',
            url=url or empty,
            icon_url=arg.strip('< >'),
        )

    @embed_author.transform('url', split_args=False)
    async def embed_author_url(self, env: Environment[Any], _, arg: str) -> None:
        """Set the url of the author of the embed of the current message."""
        if not arg:
            raise ValueError('no author url specified')

        name = env.embed and env.embed.author.name
        icon = env.embed and env.embed.author.icon_url
        empty = discord.Embed.Empty

        env.get_embed().set_author(
            name=name or '{error: Missing author name}',
            url=arg.strip('< >'),
            icon_url=icon or empty,
        )

    @embed.transform('thumbnail', 'thumb', split_args=False)
    async def embed_thumbnail(self, env: Environment[Any], _, arg: str) -> None:
        """Set the thumbnail of the embed of the current message."""
        if not arg:
            raise ValueError('no thumbnail specified')

        env.get_embed().set_thumbnail(url=arg.strip('< >'))

    @embed.transform('image', 'img', split_args=False)
    async def embed_image(self, env: Environment[Any], _, arg: str) -> None:
        """Set the image of the embed of the current message."""
        if not arg:
            raise ValueError('no image specified')

        env.get_embed().set_image(url=arg.strip('< >'))

    @embed.transform('footer', 'foot', split_args=False)
    async def embed_footer(self, env: Environment[Any], _, arg: str) -> None:
        """Set the footer of the embed of the current message."""
        if not arg:
            raise ValueError('no footer specified')

        icon_url = env.embed and env.embed.footer.icon_url or discord.Embed.Empty

        env.get_embed().set_footer(text=arg, icon_url=icon_url)

    @embed_footer.transform('icon', 'icon-url', split_args=False)
    async def embed_footer_icon(self, env: Environment[Any], _, arg: str) -> None:
        """Set the icon of the footer of the embed of the current message."""
        if not arg:
            raise ValueError('no footer icon specified')

        text = env.embed and env.embed.footer.text or '{error: No text specified}'

        env.get_embed().set_footer(text=text, icon_url=arg.strip('< >'))

    @embed.transform('timestamp', 'time', split_args=False)
    async def embed_timestamp(self, env: Environment[Any], _, arg: str) -> None:
        """Set the timestamp of the embed of the current message."""
        if not arg:
            env.get_embed().timestamp = discord.utils.utcnow()
            return

        env.get_embed().timestamp = datetime.datetime.fromtimestamp(float(arg), tz=datetime.timezone.utc)

    @embed.transform('field', 'add-field', 'create-field', split_args=False)
    async def embed_field(self, env: Environment[Any], modifier: str, arg: str) -> None:
        """Add a field to the embed of the current message."""
        if not modifier:
            raise ValueError('name of this field is required as a modifier')

        if not arg:
            raise ValueError('no value specified (first argument)')

        env.get_embed().add_field(name=modifier, value=arg, inline=False)

    @embed.transform('inline', 'inline-field', 'add-inline-field', 'create-inline-field', split_args=False)
    async def embed_field_inline(self, env: Environment[Any], modifier: str, arg: str) -> None:
        """Adds an inline field to this embed."""
        if not modifier:
            raise ValueError('name of this field is required as a modifier')

        if not arg:
            raise ValueError('no value specified (first argument)')

        env.get_embed().add_field(name=modifier, value=arg, inline=True)


@preinstantiate()
class ViewTransformer(Transformer[Any]):
    @transform('link', 'url', 'hyperlink', 'button-link', split_args=False)
    async def link(self, env: Environment[Any], modifier: str, arg: str) -> None:
        """Adds a button linking to the given URL."""
        if not modifier:
            raise ValueError('label of this link required as a modifier')

        if not arg:
            raise ValueError('no url specified (first argument)')

        env.get_view().add_item(discord.ui.Button(label=modifier, url=arg.strip('< >')))

    @transform('button', split_args=False)
    async def button(self, env: Environment[Any], modifier: str, arg: str) -> None:
        """Adds a button with responding the given text.

        Buttons will always time out in 30 seconds, respond ephemerally and have no underlying checks.
        """
        if not modifier:
            raise ValueError('label of this button required as a modifier')

        if not arg:
            raise ValueError('no response text specified (first argument)')

        button = discord.ui.Button(label=modifier, style=discord.ButtonStyle.primary)

        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.send_message(arg[:4000], ephemeral=True)

        button.callback = callback

        env.get_view().timeout = 30
        env.get_view().add_item(button)


class VariableTransformer(Transformer[Any]):
    @transform('declare', '=', 'var', 'variable', 'set', split_args=False)
    async def declare(self, env: Environment[Any], modifier: str, arg: str) -> None:
        """Sets a variable to the given value."""
        if not modifier:
            raise ValueError('name of this variable is required as a modifier')

        # TODO: support arguments e.g. {=(hello):Hello $1!} -> {hello:World} -> Hello World!

        name = modifier.casefold().strip()

        if self.get_transformer_callback(name) is not None:
            raise ValueError(f'variable/tag {name!r} already exists')

        @transform(name, evaluate_modifier=False)
        async def callback(_, c_env: Environment[Any], _modifier: str, *_c_args: str) -> None:
            return c_env.vars[name]

        self.transformers.append(callback)
        callback.transformer = self

        env.vars[name] = arg

    @transform('get', 'getvar', 'valueof')
    async def get(self, env: Environment[Any], modifier: str, *_: str) -> None:
        """Gets the value of the given variable."""
        if not modifier:
            raise ValueError('name of this variable is required as a modifier')

        name = modifier.casefold().strip()
        try:
            return env.vars[name]
        except KeyError:
            raise ValueError(f'variable {name!r} does not exist')


def create_transformer_registry(*extra: Transformer[Any]) -> TransformerRegistry[Any]:
    return TransformerRegistry(
        MetaTransformer,
        RandomTransformer,
        UserTransformer,
        LevelingTransformer,
        EmbedTransformer,
        ConditionalTransformer,
        ViewTransformer,
        VariableTransformer(),
        *extra,
    )


async def execute_tags(
    *,
    bot: Bot,
    message: discord.Message,
    channel: discord.abc.Messageable | int,
    content: str,
    env: Environment[Any] = None,
    transformers: TransformerRegistry[Any] = None,
    silent: bool = False,
    **send_kwargs: Any,
) -> None:
    """Executes all tags in the given content."""
    if transformers is None:
        transformers = create_transformer_registry()

    if not content:
        return

    env = env or Environment(message=message, bot=bot)

    result = await parse(content, env=env, transformers=transformers, silent=silent)
    kwargs = {
        'content': result,
        **send_kwargs,
    }

    if env.embed is not None:
        kwargs['embed'] = env.embed

    if env.view is not None:
        kwargs['view'] = env.view

    if isinstance(channel, int):
        params = handle_message_parameters(**kwargs)
        await bot.http.send_message(channel, params=params)

    await channel.send(**kwargs)
