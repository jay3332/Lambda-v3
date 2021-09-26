from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from typing import NamedTuple, TYPE_CHECKING

if TYPE_CHECKING:
    from bot.types import ImageSize

__all__ = (
    'rounded_mask',
)


def mask_to_circle(image: Image.Image, *, quality: int = 3) -> Image.Image:
    """Masks an image into a circle. A higher quality will result in a smoother circle."""
    width, height = size = image.size
    big = width * quality, height * quality

    with Image.new('L', big, 0) as mask:
        ImageDraw.Draw(mask).ellipse((0, 0) + big, fill=255)
        mask = mask.resize(size, Image.ANTIALIAS)
        mask = ImageChops.darker(mask, image.split()[-1])
        image.putalpha(mask)
        return image


def rounded_mask(size: ImageSize, radius: int, *, alpha: int = 255, quality: int = 5) -> Image.Image:
    """Create a rounded rectangle mask with the given size and border radius."""
    radius *= quality
    image = Image.new('RGBA', (size[0] * quality, size[1] * quality), (0, 0, 0, 0))

    with Image.new('RGBA', (radius, radius), (0, 0, 0, 0)) as corner:
        draw = ImageDraw.Draw(corner)

        draw.pieslice((0, 0, radius * 2, radius * 2), 180, 270, fill=(50, 50, 50, alpha + 55))
        mx, my = (size[0] * quality, size[1] * quality)

        image.paste(corner, (0, 0), corner)
        image.paste(corner.rotate(90), (0, my - radius), corner.rotate(90))
        image.paste(corner.rotate(180), (mx - radius, my - radius), corner.rotate(180))
        image.paste(corner.rotate(270), (mx - radius, 0), corner.rotate(270))

    draw = ImageDraw.Draw(image)
    draw.rectangle(((radius, 0), (mx - radius, my)), fill=(50, 50, 50, alpha))
    draw.rectangle(((0, radius), (mx, my - radius)), fill=(50, 50, 50, alpha))

    return image.resize(size, Image.ANTIALIAS)


def alpha_paste(background: Image.Image, foreground: Image.Image, box: ImageSize, mask: Image.Image) -> Image.Image:
    """Paste an image with alpha on top of another image additively, rather than overwriting the alpha."""
    background = background.convert('RGBA')
    foreground = foreground.convert('RGBA')

    with Image.new('RGBA', background.size) as overlay:
        overlay.paste(foreground, box, mask)
        return Image.alpha_composite(background, overlay)


class FontManager:
    """Manages fonts by opening then storing them in memory."""

    def __init__(self) -> None:
        self._fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    def get(self, path: str, size: int) -> ImageFont.FreeTypeFont:
        key = path, size
        try:
            return self._fonts[key]
        except KeyError:
            pass

        with open(path, 'rb') as fp:
            self._fonts[key] = font = ImageFont.truetype(fp, size=size)
            return font

    def clear(self) -> None:
        self._fonts.clear()

    def __del__(self) -> None:
        self.clear()
