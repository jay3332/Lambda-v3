from PIL import ImageFont

__all__ = (
    'Font',
    'Color',
)


Font = ImageFont.ImageFont | ImageFont.FreeTypeFont | ImageFont.TransposedFont
Color = int | tuple[int, int, int] | tuple[int, int, int, int] | str
