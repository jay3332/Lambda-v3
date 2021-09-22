from typing import Tuple

__all__ = (
    'Snowflake',
    'StrSnowflake',
    'ImageSize',
    'RGBColor',
    'RGBAColor',
    'AnyColor',
)

Snowflake = int
StrSnowflake = str

ImageSize = Tuple[int, int]

RGBColor = Tuple[int, int, int]
RGBAColor = Tuple[int, int, int, int]
AnyColor = RGBColor | RGBAColor | str
