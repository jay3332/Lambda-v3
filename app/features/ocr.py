from __future__ import annotations

import asyncio
import math
from io import BytesIO
from typing import Any, ClassVar, Coroutine, Iterator, Literal, TYPE_CHECKING, TypeAlias

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from app.util.common import executor_function, proportionally_scale

if TYPE_CHECKING:
    from pilmoji.core import FontT

    from app.core import Bot
    from app.util.types import JsonObject

    Walker: TypeAlias = Iterator[tuple[tuple[int, int, int, int], int, str]]
    AsyncWalker: TypeAlias = list[tuple[tuple[int, int, int, int], int, str]]


class OCRRenderer:
    """Renders an OCR payload from Microsoft Computer Vision"""

    FONT: ClassVar[FontT] = ImageFont.truetype('./assets/fonts/arial-unicode-ms.ttf', size=1)

    def __init__(self, bot: Bot, data: JsonObject) -> None:
        self.bot: Bot = bot
        self.data: dict[str, Any] = data

    def _walk_bounds(self) -> Walker:
        for region in self.data['regions']:
            for line in region['lines']:
                for word in line['words']:
                    x, y, w, h = word['boundingBox'].split(',')
                    yield (int(x), int(y), int(w), h := int(h)), h, word['text']

    def _walk_line_bounds(self) -> Walker:  # Less exact word coordinates, but is required for better translations
        for region in self.data['regions']:
            for line in region['lines']:
                x, y, w, h = line['boundingBox'].split(',')
                yield (int(x), int(y), int(w), h := int(h)), h, ' '.join(word['text'] for word in line['words'])

    def _walk_region_bounds(self) -> Walker:
        for region in self.data['regions']:
            x, y, w, h = region['boundingBox'].split(',')
            lines = region['lines']
            yield (
                (int(x), int(y), int(w), int(h)),
                int(lines[0]['boundingBox'].split(',')[-1]) if lines else 0,
                '\n'.join(' '.join(word['text'] for word in line['words']) for line in lines),
            )

    @staticmethod
    async def _gather_coro(bounds, line_height, coro) -> tuple[tuple[int, int, int, int], int, str]:
        return bounds, line_height, await coro

    @staticmethod
    async def _unsurround_coro(subject: Coroutine[Any, Any, str | list[str]]) -> str:
        subject = await subject

        if isinstance(subject, list):
            return subject[0]

        return subject

    async def resolve_translated_bounds(
        self,
        dest: str,
        *,
        source: str = 'auto',
        walker: Walker | None = None,
    ) -> AsyncWalker:
        # noinspection PyTypeChecker
        return await asyncio.gather(
            *(
                self._gather_coro(
                    bounds,
                    line_height,
                    self._unsurround_coro(self.bot.translator.translate(text, dest, lang_src=source)),
                )
                for bounds, line_height, text in walker or self._walk_line_bounds()
            )
        )

    @executor_function
    def _render(self, image: bytes, *, accuracy: Literal['word', 'line', 'region'] = 'line', walker: Walker | None = None) -> BytesIO:
        with Image.open(BytesIO(image)) as img:
            img = img.convert('RGBA')
            if img.width < 50 or img.height < 50:
                img = img.resize((proportionally_scale(img.size, min_dimension=50, max_dimension=2048)))

            img = ImageOps.exif_transpose(img)

            match self.data['orientation'].lower():
                case 'down':
                    img = img.transpose(Image.ROTATE_180)
                    undo = Image.ROTATE_180
                case 'left':
                    img = img.transpose(Image.ROTATE_270)
                    undo = Image.ROTATE_90
                case 'right':
                    img = img.transpose(Image.ROTATE_90)
                    undo = Image.ROTATE_270
                case _:
                    undo = None

            sample = img.rotate(angle := self.data['textAngle'] * (-180 / math.pi))

            with Image.new('RGBA', img.size) as overlay:
                walker = walker or {
                    'word': self._walk_bounds,
                    'line': self._walk_line_bounds,
                    'region': self._walk_region_bounds,
                }[accuracy]()

                for (x, y, w, h), line_height, text in walker:
                    variant = self.FONT.font_variant(size=line_height)
                    size = variant.getsize_multiline(text, stroke_width=(stroke := line_height // 8))

                    part = sample.crop(
                        (x, y, x + w, y + max(h, size[1])),
                    ).filter(
                        ImageFilter.GaussianBlur(8)
                    )
                    preview = part.resize((1, 1)).convert('L')
                    color, border = ('white', 'black') if preview.getpixel((0, 0)) <= 128 else ('black', 'white')

                    with Image.new('RGBA', size) as text_image:
                        ImageDraw.Draw(text_image).text(
                            (stroke, -2),
                            text,
                            fill=color,
                            font=variant,
                            stroke_width=stroke,
                            stroke_fill=border,
                        )

                        if text_image.width > w:
                            text_image = text_image.resize((w, text_image.height))

                        try:
                            ox = (part.width - text_image.width) // 2
                        except ZeroDivisionError:
                            ox = 0

                        part.paste(text_image, (ox, 0), text_image)

                    overlay.paste(part, (x, y), part)

                overlay = overlay.rotate(-angle)
                img.paste(overlay, (0, 0), overlay)

            if undo is not None:
                img = img.transpose(undo)

            buffer = BytesIO()
            img.save(buffer, 'png')
            buffer.seek(0)

            return buffer

    async def render_translated(self, image: bytes, dest: str, *, source: str = 'auto') -> BytesIO:
        flattened = await self.resolve_translated_bounds(dest, source=source)
        return await self._render(image, walker=flattened)
