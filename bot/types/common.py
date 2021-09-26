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

ImageSize = tuple[int, int]

RGBColor = tuple[int, int, int]
RGBAColor = tuple[int, int, int, int]
AnyColor = RGBColor | RGBAColor | str
