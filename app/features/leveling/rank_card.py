from __future__ import annotations

import asyncio
import functools
from collections import OrderedDict
from enum import Enum
from io import BytesIO
from typing import Any, ClassVar, Final, TYPE_CHECKING, Type, overload

from PIL import Image, ImageDraw, ImageFilter
from aiohttp import ClientTimeout
from pilmoji import Pilmoji
from pilmoji.source import MicrosoftEmojiSource

from app.util.common import executor_function
from app.util.pillow import FallbackFont, alpha_paste, rounded_mask

if TYPE_CHECKING:
    from discord import Member, User

    from app.core import Bot
    from app.features.leveling.core import LevelingRecord
    from app.util.types import RankCard as RankCardPayload, RGBColor, RGBAColor

__all__ = (
    'RankCard',
)

FONT_MAPPING: Final[tuple[str, ...]] = (
    'rubik.ttf',
    'arial-unicode-ms.ttf',
    'menlo.ttf',
    'whitney.otf',
    'karla.ttf',
    'poppins.ttf',
    'inter.ttf',
)


class Font(Enum):
    """Represents a rank card's font."""
    RUBIK = 0
    ARIAL_UNICODE = 1
    MENLO = 2
    WHITNEY = 3
    KARLA = 4
    POPPINS = 5
    INTER = 6

    def __str__(self) -> str:
        return self.name.replace('_', ' ').title()


class BaseRankCard:
    """Represents a user's rank card information."""

    def __init__(self, *, data: RankCardPayload, user: Member | User, bot: Bot) -> None:
        self._bot: Bot = bot
        self.user: Member | User = user
        self._from_data(data)

    @staticmethod
    def _to_rgb(color: int) -> RGBColor:
        return (color >> 16) & 0xff, (color >> 8) & 0xff, color & 0xff

    # noinspection PyNestedDecorators
    @overload
    @classmethod
    def manual(
        cls: Type[BaseRankCard],
        *,
        user: Member | User,
        bot: Bot,
        font: Font = ...,
        primary_color: RGBColor = ...,  # these should actually be ints
        secondary_color: RGBColor = ...,
        tertiary_color: RGBColor = ...,
        background_url: str | None = ...,
        background_color: RGBColor = ...,
        background_image_alpha: float = ...,
        background_blur: int = ...,
        overlay_color: RGBColor = ...,
        overlay_alpha: float = ...,
        overlay_border_radius: int = ...,
        avatar_border_color: RGBColor = ...,
        avatar_border_alpha: float = ...,
        avatar_border_radius: int = ...,
        progress_bar_color: RGBColor = ...,
        progress_bar_alpha: float = ...,
    ) -> BaseRankCard:
        ...

    @classmethod
    def manual(cls: Type[BaseRankCard], *, user: Member | User, bot: Bot, **kwargs: Any) -> BaseRankCard:
        return cls(data={**kwargs, 'user_id': user.id}, user=user, bot=bot)  # type: ignore

    def _from_data(self, data: RankCardPayload) -> None:
        self.data: RankCardPayload = data
        self.user_id: int = data['user_id']
        self.font: Font = Font(data['font'])

        _ = self._to_rgb
        self.primary_color: RGBColor = _(data['primary_color'])
        self.secondary_color: RGBColor = _(data['secondary_color'])
        self.tertiary_color: RGBColor = _(data['tertiary_color'])

        self.background_url: str | None = data['background_url']
        self.background_color: RGBColor = _(data['background_color'])
        self.background_image_alpha: int = int(data['background_alpha'] * 255)
        self.background_blur: int = data['background_blur']

        overlay_color = _(data['overlay_color'])
        self.overlay_color: RGBAColor = *overlay_color, int(data['overlay_alpha'] * 255)  # type: ignore
        self.overlay_border_radius: int = data['overlay_border_radius']

        avatar_border_color = _(data['avatar_border_color'])
        self.avatar_border_color: RGBAColor = *avatar_border_color, int(data['avatar_border_alpha'] * 255)  # type: ignore
        self.avatar_border_radius: int = data['avatar_border_radius']

        progress_bar_color = _(data['progress_bar_color'])
        self.progress_bar_color: RGBAColor = *progress_bar_color, int(data['progress_bar_alpha'] * 255)  # type: ignore

    async def update(self, **kwargs: Any) -> None:
        if not len(kwargs):
            return

        kwargs = OrderedDict(kwargs)
        chunk = ', '.join(f'{key} = ${i}' for i, key in enumerate(kwargs, start=2))

        data = await self._bot.db.fetchrow(
            f'UPDATE rank_cards SET {chunk} WHERE user_id = $1 RETURNING rank_cards.*;',
            self.user_id, *kwargs.values()
        )
        self._from_data(data)  # type: ignore


class RankCard(BaseRankCard):
    """Represents a rank card with rendering methods."""

    LB_COUNT: Final[ClassVar[int]] = 5
    LB_PADDING: Final[ClassVar[int]] = 12
    LB_WIDTH: Final[ClassVar[int]] = 520
    LB_SECTION_HEIGHT: Final[ClassVar[int]] = 96
    LB_STRIDE: Final[ClassVar[int]] = LB_SECTION_HEIGHT + LB_PADDING
    LB_HEIGHT: Final[ClassVar[int]] = LB_PADDING + LB_STRIDE * LB_COUNT
    LB_INDENT: Final[ClassVar[int]] = 64
    LB_AVATAR_SIZE: Final[ClassVar[int]] = int(LB_SECTION_HEIGHT * 0.7)
    LB_OFFSET: Final[ClassVar[float]] = (LB_SECTION_HEIGHT - LB_AVATAR_SIZE) / 2
    LB_TEXT_LEFT_PADDING: Final[ClassVar[int]] = 18
    LB_TEXT_OFFSET = LB_TEXT_LEFT_PADDING + LB_AVATAR_SIZE + LB_INDENT + round(LB_OFFSET)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self._prepared_background: Image.Image | None = None
        self._prepared_leaderboard_background: Image.Image | None = None

    async def _fetch_background_bytes(self) -> BytesIO | None:
        if not self.background_url:
            return

        async with self._bot.session.get(
            self.background_url,
            timeout=ClientTimeout(total=5),
        ) as response:
            if response.status >= 400:
                return

            return BytesIO(await response.read())

    async def fetch_background_bytes(self) -> BytesIO | None:
        try:
            return await self._fetch_background_bytes()
        except asyncio.TimeoutError:
            return None

    def _prepare_background(self, stream: BytesIO | None = None, size: tuple[int, int] = (1390, 600)) -> Image.Image:
        base = Image.new('RGBA', size, self.background_color)
        width, height = size
        aspect_ratio = height / width  # inversed

        if stream is None:
            return base

        with Image.open(stream).convert('RGBA') as im:
            if im.height / im.width >= aspect_ratio:
                target = int(im.width * aspect_ratio)
                y_offset = int((im.height - target) / 2)
                im = im.crop((0, y_offset, im.width, im.height - y_offset))
            else:
                target = int(im.height * (1 / aspect_ratio))
                x_offset = int((im.width - target) / 2)
                im = im.crop((x_offset, 0, im.width - x_offset, im.height))

            im = im.resize(size)

            if self.background_blur > 0:
                im = im.filter(ImageFilter.GaussianBlur(radius=self.background_blur))

            if self.background_image_alpha < 253:
                im.putalpha(self.background_image_alpha)

            base = alpha_paste(base, im, (0, 0), im)

        return base

    prepare_background = executor_function(_prepare_background)

    @executor_function
    def _render(
        self,
        background: Image.Image,
        avatar_bytes: bytes,
        *,
        user: Member | None = None,
        rank: int | None = None,
        level: int,
        xp: int,
        max_xp: int,
    ) -> BytesIO:
        user = user or self.user

        with Image.new('RGBA', (1250, 460)) as image:
            with Image.new('RGBA', (1250, 460), self.overlay_color) as overlay:
                with rounded_mask(overlay.size, self.overlay_border_radius) as mask:
                    image = alpha_paste(image, overlay, (0, 0), mask)

            with Image.new('RGBA', (316, 316), self.avatar_border_color) as border:
                with rounded_mask(border.size, self.avatar_border_radius + 14) as mask:
                    image = alpha_paste(image, border, (34, 25), mask)

            with Image.open(BytesIO(avatar_bytes)) as avatar:
                avatar = avatar.convert('RGBA').resize((278, 278))
                with rounded_mask(avatar.size, self.avatar_border_radius) as mask:
                    image = alpha_paste(image, avatar, (53, 44), mask)

            with Image.new('RGBA', (718, 74), self.progress_bar_color) as bar_bg:
                with rounded_mask(bar_bg.size, bar_bg.height // 2, quality=3) as mask:
                    image = alpha_paste(image, bar_bg, (393, 272), mask)

                try:
                    ratio = xp / max_xp
                except ZeroDivisionError:
                    ratio = 0

                width = bar_bg.width - 24
                projected_width = int(width * ratio)

                if projected_width > 0:
                    with Image.new('RGBA', (width, bar_bg.height - 24)) as bar:
                        with (
                            Image.new('RGBA', (projected_width, bar.height), self.tertiary_color) as actual,
                            rounded_mask(bar.size, bar.height // 2, quality=3) as mask,
                        ):
                            mask = mask.crop((0, 0, projected_width, mask.height))
                            bar.paste(actual, (0, 0), mask)

                        image.paste(bar, (405, 284), bar)

            with Pilmoji(
                image,
                source=MicrosoftEmojiSource,
                render_discord_emoji=False,
                emoji_position_offset=(0, 7),
            ) as pilmoji:
                fonts = self._bot.fonts
                get_font = functools.partial(fonts.get, './assets/fonts/' + FONT_MAPPING[self.font.value])

                # Level
                font = get_font(size=53)
                text = f'Level {level}'
                width, _ = pilmoji.getsize(text, font)
                offset = int(width / 2)

                pilmoji.text((original := 192 - offset, 359), 'Level ', self.secondary_color, font)
                offset, _ = pilmoji.getsize('Level ', font)
                pilmoji.text((offset + original, 359), str(level), self.primary_color, font)

                # Username
                username = user.name
                font = FallbackFont(
                    get_font(size=55),
                    lambda: fonts.get('./assets/fonts/' + FONT_MAPPING[Font.ARIAL_UNICODE.value], size=55),
                    fallback_offset=(0, -5)
                )
                with font.session(pilmoji.draw) as font:
                    pilmoji.text((406, 189), username, self.primary_color, font)  # type: ignore
                    width, _ = pilmoji.getsize(username, font)  # type: ignore

                # Discriminator
                if user.discriminator != '0':
                    font = get_font(size=50)
                    text = '#' + user.discriminator
                    pilmoji.text((409 + width, 194), text, (*self.secondary_color[:3], 190), font)  # type: ignore

                # Rank
                font = get_font(size=60)
                text = f' #{rank:,}' if rank else ' Unranked'
                width, _ = pilmoji.getsize(text, font)

                pilmoji.text((1156 - width, 45), text, self.primary_color, font)

                font = get_font(size=45)
                offset, _ = pilmoji.getsize('RANK', font)
                pilmoji.text((1156 - width - offset, 60), 'RANK', self.secondary_color, font)

                # XP
                font = get_font(size=40)
                text = f'{xp:,} XP'
                pilmoji.text((406, 361), text, self.primary_color, font)
                offset, _ = pilmoji.getsize(text, font)

                # Max XP
                font = get_font(size=36)
                text = f' / {max_xp:,}'
                pilmoji.text((406 + offset, 365), text, self.secondary_color, font)

            with background:
                buffer = BytesIO()
                background.paste(image, (70, 70), image)
                background.save(buffer, 'png')
                buffer.seek(0)

                return buffer

    async def render(
        self,
        *,
        rank: int,
        level: int,
        xp: int,
        max_xp: int,
        user: Member | None = None,
    ) -> BytesIO:
        if self._prepared_background is None:
            background_bytes = await self.fetch_background_bytes()
            self._prepared_background = await self.prepare_background(background_bytes)  # type: ignore

        user = user or self.user
        avatar = user.avatar or user.default_avatar
        avatar = await avatar.with_format('png').with_size(512).read()

        return await self._render(
            self._prepared_background.copy(),
            avatar,
            user=user,
            rank=rank,
            level=level,
            xp=xp,
            max_xp=max_xp,
        )

    @executor_function
    def prepare_leaderboard_background(self, stream: BytesIO | None = None) -> Image.Image:
        image = self._prepare_background(stream, size=(self.LB_WIDTH, self.LB_HEIGHT))

        for y in range(self.LB_COUNT):
            y = self.LB_PADDING + y * self.LB_STRIDE
            with Image.new(
                'RGBA',
                (self.LB_WIDTH - self.LB_PADDING - self.LB_INDENT, self.LB_SECTION_HEIGHT),
                self.overlay_color,
            ) as overlay:
                border_radius = self.overlay_border_radius
                border_radius = border_radius and border_radius // 2
                with rounded_mask(overlay.size, border_radius) as mask:
                    image = alpha_paste(image, overlay, (self.LB_INDENT, y), mask)

        return image

    @executor_function
    def _render_leaderboard(
        self,
        background: Image.Image,
        *,
        records: list[LevelingRecord],
        avatars: list[bytes],
        offset: int,
    ) -> BytesIO:
        avatar_downscale = self.LB_AVATAR_SIZE / 278
        # Store all fonts
        get_font = functools.partial(self._bot.fonts.get, './assets/fonts/' + FONT_MAPPING[self.font.value])
        username_font = get_font(size=28)
        rank_fonts = None, get_font(size=42), get_font(size=40), get_font(size=38)
        level_text_font, level_font = get_font(size=24), get_font(size=30)
        xp_font = get_font(size=22)
        level_offset, _ = level_text_font.getsize('LEVEL ')

        for i, (record, avatar_bytes) in enumerate(zip(records, avatars)):
            rank = str(i + offset + 1)
            top = (self.LB_SECTION_HEIGHT + self.LB_PADDING) * i + self.LB_PADDING

            avatar_top = round(top + self.LB_OFFSET)
            with Image.open(BytesIO(avatar_bytes)) as avatar:
                avatar = avatar.convert('RGBA').resize((self.LB_AVATAR_SIZE, self.LB_AVATAR_SIZE))
                with rounded_mask(avatar.size, int(self.avatar_border_radius * avatar_downscale)) as mask:
                    background = alpha_paste(
                        background,
                        avatar,
                        (round(self.LB_OFFSET) + self.LB_INDENT, avatar_top),
                        mask,
                    )

            draw = ImageDraw.Draw(background)
            # Rank text
            draw.text(
                (self.LB_INDENT // 2, top + self.LB_SECTION_HEIGHT // 2),
                rank,
                fill=self.primary_color,
                font=rank_fonts[len(rank)],
                anchor='mm',
            )
            # Username
            draw.text(
                (self.LB_TEXT_OFFSET, avatar_top),
                str(record.user),
                fill=(*self.primary_color, 235),
                font=username_font,
            )
            # "LEVEL"
            draw.text((self.LB_TEXT_OFFSET, avatar_top + 42), 'LEVEL', fill=self.secondary_color, font=level_text_font)
            base_offset = self.LB_TEXT_OFFSET + level_offset
            # Level number
            draw.text((base_offset, avatar_top + 36), str(record.level), fill=self.primary_color, font=level_font)
            base_offset += level_font.getsize(str(record.level) + '  ')[0]
            # XP
            draw.text(
                (base_offset, avatar_top + 44),
                f'{record.xp:,} XP',
                fill=(*self.secondary_color, 225),
                font=xp_font,
            )

        # If there are less than LB_COUNT records, crop the image
        if len(records) < self.LB_COUNT:
            lacking = self.LB_COUNT - len(records)
            background = background.crop((0, 0, self.LB_WIDTH, self.LB_HEIGHT - self.LB_STRIDE * lacking))

        with background:
            buffer = BytesIO()
            background.save(buffer, 'png')
            buffer.seek(0)
            return buffer

    async def render_leaderboard(self, *, records: list[LevelingRecord], rank_offset: int) -> BytesIO:
        if self._prepared_leaderboard_background is None:
            background_bytes = await self.fetch_background_bytes()
            self._prepared_leaderboard_background = await self.prepare_leaderboard_background(background_bytes)

        avatars = await asyncio.gather(
            *(record.user.avatar.with_format('png').with_size(512).read() for record in records)
        )
        return await self._render_leaderboard(
            self._prepared_leaderboard_background.copy(),
            records=records,
            avatars=avatars,
            offset=rank_offset,
        )

    async def update(self, **kwargs: Any) -> None:
        if 'background_url' in kwargs:
            self.invalidate()
        await super().update(**kwargs)

    def invalidate(self) -> None:
        self._prepared_background = None
        self._prepared_leaderboard_background = None
