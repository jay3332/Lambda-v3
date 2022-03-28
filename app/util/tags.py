from __future__ import annotations

import inspect
import re
from typing import (
    Any,
    Callable,
    ClassVar,
    Concatenate,
    Generic,
    Iterator,
    NamedTuple,
    ParamSpec,
    Type,
    TypeAlias,
    TypeVar,
    TYPE_CHECKING,
)

from discord.utils import maybe_coroutine

if TYPE_CHECKING:
    Environment: TypeAlias = Any
    EnvironmentT = TypeVar('EnvironmentT', bound=Environment)

P = ParamSpec('P')
T = TypeVar('T')
TransformerT = TypeVar('TransformerT', bound='Transformer')


def transform(*names: str) -> Callable[[Callable[Concatenate[Transformer, Environment, P], T]], TransformerCallback[P, T]]:
    """Creates a transformer callback from a function"""

    def decorator(func: Callable[Concatenate[Transformer, Environment, P], T]) -> TransformerCallback[P, T]:
        nonlocal names

        if not names:
            names = [func.__name__]

        return TransformerCallback(func, *names)  # type: ignore

    return decorator


class TransformerCallback(Generic[P, T]):
    """Stores the callback for when the tag is transformed."""

    def __init__(
        self,
        func: Callable[Concatenate[Transformer, EnvironmentT, P], T],
        *names: str,
        parent: TransformerCallback | None = None,
    ) -> None:
        self.callback: Callable[Concatenate[Transformer, EnvironmentT, P], T] = func
        self.transformer: Transformer | None = None
        self.names: tuple[str, ...] = names

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

    def transform(self, *names: str) -> Callable[
        [Callable[Concatenate[Transformer, Environment, P], T]],
        TransformerCallback[P, T],
    ]:
        """Creates a transformer callback from a function"""
        def decorator(func: Callable[Concatenate[Transformer, Environment, P], T]) -> TransformerCallback[P, T]:
            nonlocal names

            if not names:
                names = [func.__name__]

            self.children.append(result := TransformerCallback(func, *names, parent=self))
            return result  # type: ignore

        return decorator

    def walk_children(self) -> Iterator[TransformerCallback]:
        for child in self.children:
            yield child
            yield from child.walk_children()

    def __call__(self, env: EnvironmentT, *args: P.args, **kwargs: P.kwargs) -> T:
        if self.transformer is None:
            raise RuntimeError(
                'The transformer callback was not registered with a transformer.'
            )

        return self.callback(self.transformer, env, *args, **kwargs)


class Transformer:
    """Interface to transform tags into text."""

    if TYPE_CHECKING:
        transformers: list[TransformerCallback]

    def __new__(cls: Type[Transformer]) -> Transformer:
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

    def walk_transformers(self) -> Iterator[TransformerCallback]:
        for transformer in self.transformers:
            yield transformer
            yield from transformer.walk_children()

    def get_transformer_callback(self, name: str) -> TransformerCallback | None:
        """Returns the transformer callback for the given name."""
        name = name.casefold()

        for transformer in self.walk_transformers():
            if name in transformer.names:
                return transformer

        return None


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


class Node(NamedTuple):
    start: int
    end: int

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
        modifier_count = 0

        for i, char in enumerate(text):
            if i > 0 and text[i - 1] == '\\':
                continue

            if char == '(':
                modifier_count += 1

            elif char == ')':
                modifier_count -= 1

            if char == '{' and modifier_count <= 0:
                buffers.append(i)

            elif char == '}' and modifier_count <= 0:
                start = buffers.pop()
                yield Node(start, i + 1)

    @classmethod
    async def parse(cls, text: str, *, env: EnvironmentT, transformers: TransformerRegistry) -> str:
        """Parses the given text and returns the transformed text."""
        for node in cls.walk_nodes(text):
            text = text[node.start:node.end].strip('{}')
            tag, _, args = text.partition(':')

            if args:
                args = [
                    arg.replace('\\;', ';')
                    for arg in cls.ARGUMENT_REGEX.split(args)
                ]
            else:
                args = []

            tag, _, modifier = tag.partition('(')
            modifier = modifier.removesuffix(')')
            callback = transformers.get_transformer_callback(tag.strip())
            repl = await maybe_coroutine(callback, env, modifier, *args)


