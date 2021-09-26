from __future__ import annotations

from discord import Interaction as _Interaction

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot import Lambda

__all__ = (
    'Interaction',
)


class Interaction(_Interaction):
    client: Lambda
