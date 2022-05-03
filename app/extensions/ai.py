from __future__ import annotations

from io import BytesIO
from typing import Annotated, Any, ClassVar, TYPE_CHECKING

import aiohttp
import difflib
import discord
from async_google_trans_new.constant import LANGUAGES
from PIL import Image as PilImage

from app.core import BAD_ARGUMENT, ERROR, Cog, Context, Flags, Param, REPLY, cooldown, flag, group, store_true
from app.core.helpers import user_max_concurrency
from app.features.ocr import OCRRenderer
from app.util import UserView, converter
from app.util.common import executor_function, proportionally_scale
from app.util.image import BadArgument, ImageFinder
from config import Colors, computer_vision_key

if TYPE_CHECKING:
    from app.util.types import CommandResponse

_REVERSE_LANGUAGE_MAPPING = {v: k for k, v in LANGUAGES.items()}
_EXTRA_MAPPING = {
    'chinese': 'zh-cn',
    'cn': 'zh-cn',
    'tw': 'zh-tw',
}


@converter
async def Link(_ctx: Context, argument: str) -> str:
    argument = argument.strip('<>')

    if argument.startswith('http://'):
        raise BadArgument('`http://` links are not supported as they are insecure, please use `https://` instead.')

    if argument.startswith('https://') and '.' in argument:
        return argument

    raise BadArgument(f'`{argument[:1000]}` is not a valid link.')


@converter
async def TranslationLanguage(ctx: Context, argument: str) -> str:
    argument = argument.lower()

    if argument == 'auto':
        return argument

    if argument in _REVERSE_LANGUAGE_MAPPING:
        return _REVERSE_LANGUAGE_MAPPING[argument]

    if argument in _REVERSE_LANGUAGE_MAPPING.values():
        return argument

    try:
        return _EXTRA_MAPPING[argument]
    except KeyError:
        matches = difflib.get_close_matches(argument, LANGUAGES, cutoff=0.8)

        raise BadArgument(
            f'`{argument}` is not a valid language. '
            f'See `{ctx.clean_prefix}translate languages` for a list of valid languages.\n\n'
            + (
                f'Did you mean: {", ".join(f"`{m}` ({LANGUAGES[m]})" for m in matches)}'
            )
        )


class TranslateFlags(Flags):
    render: bool = store_true(short='r', aliases=('render-image', 'immediately-render', 'image', 'as-image'))
    source: TranslationLanguage = flag(short='s', aliases=('src', 'from', 'source-lang', 'original'), default='auto')


class TranslatedOcrView(UserView):
    def __init__(
        self,
        ctx: Context,
        *,
        raw_text: str,
        translated: str,
        data: dict[str, Any],
        image: bytes,
        source: str,
        dest: str,
    ) -> None:
        super().__init__(ctx.author)

        self.ctx: Context = ctx
        self.raw_text: str = raw_text
        self.translated: str = translated
        self.data: dict[str, Any] = data
        self.image: bytes = image
        self.source: str = source
        self.dest: str = dest

    @discord.ui.button(label='View Original Content')
    async def view_original(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        button.disabled = True

        if len(self.raw_text) <= 4096:
            embed = discord.Embed(color=Colors.primary, description=self.raw_text, timestamp=self.ctx.now)
            embed.set_author(name='Original OCR Results', icon_url=self.ctx.author.display_avatar)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.send_message(
            content='Original OCR Results:',
            file=discord.File(BytesIO(self.raw_text.encode()), filename=f'ocr_{self.ctx.author.id}.txt'),
            ephemeral=True,
        )
        await interaction.message.edit(view=self)

    @discord.ui.button(label='Render as Image', style=discord.ButtonStyle.primary)
    async def render_as_image(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        button.disabled = True
        await interaction.response.edit_message(view=self)

        async with self.ctx.typing():
            renderer = OCRRenderer(self.ctx.bot, self.data)
            fp = await renderer.render_translated(self.image, self.dest, source=self.source)

            if fp.getbuffer().nbytes < self.ctx.guild.filesize_limit - 1024:  # 1 KB of breathing room
                await interaction.followup.send(
                    file=discord.File(fp, filename=f'ocr_translation_{self.ctx.author.id}.png'),
                )

            entry = await self.ctx.bot.cdn.safe_upload(fp, extension='png', directory='ocr_uploads')
            await interaction.followup.send(entry.url)


class AI(Cog):
    """AI-related commands, such as optical character recognition."""

    emoji = '\U0001f9e0'

    OCR_FINDER: ClassVar[ImageFinder] = ImageFinder(max_size=1024 * 1024 * 10, max_width=3000, max_height=3000)  # 10 MB
    COMPUTER_VISION_ENDPOINT: ClassVar[str] = 'https://jay3332-ocr.cognitiveservices.azure.com'
    COMPUTER_VISION_VERSION: ClassVar[str] = 'v3.2'

    @classmethod
    def computer_vision_endpoint(cls, endpoint: str) -> str:
        return f'{cls.COMPUTER_VISION_ENDPOINT}/vision/{cls.COMPUTER_VISION_VERSION}/{endpoint}'

    @executor_function
    def _resize_image(self, image: bytes) -> bytes:
        with PilImage.open(BytesIO(image)) as img:
            if img.width >= 50 and img.height >= 50:
                return image

            img = img.resize((proportionally_scale(img.size, min_dimension=50, max_dimension=2048)))

            buffer = BytesIO()
            img.save(buffer, format='PNG')
            return buffer.getvalue()

    async def _request_ocr(self, image: bytes, **params: str) -> tuple[str, dict[str, Any]]:
        image: bytes = await self._resize_image(image)  # type: ignore

        async with self.bot.session.post(
            self.computer_vision_endpoint('ocr'),
            headers={
                'Content-Type': 'application/octet-stream',
                'Ocp-Apim-Subscription-Key': computer_vision_key,
            },
            data=image,
            params=params,
        ) as response:
            response.raise_for_status()
            data = await response.json()

            raw_text = '\n'.join(
                '\n'.join(
                    ' '.join(word['text'] for word in line['words'])
                    for line in region['lines']
                )
                for region in data['regions']
            )
            return raw_text, data

    @group('ocr', aliases=('read-image', 'image-to-text', 'itt'))
    @cooldown(1, 25)
    @user_max_concurrency(1)
    async def ocr(self, ctx: Context, *, image: str = None) -> CommandResponse:
        """Reads text/characters from an image and sends them in raw text form.

        OCR stands for Optical Chararacter Recognition.

        Arguments:
        - `image`: The image to read text from. Can be provided as an attachment or URL.
        """
        image = await self.OCR_FINDER.find(ctx, image, allow_gifs=False, fallback_to_user=False, run_conversions=False)
        if image is None:
            return 'Could not find an image. Please provide an image or direct link.', BAD_ARGUMENT, Param('image')

        async with ctx.typing():
            try:
                raw_text, _ = await self._request_ocr(image)
            except aiohttp.ClientResponseError as exc:
                return f'Error {exc.status}: {exc.message}', ERROR

            if len(raw_text) <= 4096:
                embed = discord.Embed(color=Colors.primary, description=raw_text, timestamp=ctx.now)
                embed.set_author(name='OCR Results', icon_url=ctx.author.display_avatar)
                return embed, REPLY

            return 'OCR Results:', discord.File(BytesIO(raw_text.encode()), filename=f'ocr_{ctx.author.id}.txt'), REPLY

    _GOOGLE_TO_BCP_MAPPING: ClassVar[dict[str, str]] = {
        'zh-cn': 'zh-Hans',
        'zh-tw': 'zh-Hant',
        'sr': 'sr-Cryl',
        'auto': 'unk',
        **{item: item for item in (
            'cs',
            'da',
            'nl',
            'en',
            'fi',
            'fr',
            'de',
            'el',
            'hu',
            'it',
            'ja',
            'ko',
            'nb',
            'pl',
            'pt',
            'ru',
            'es',
            'sv',
            'tr',
            'ar',
            'ro',
            'sk',
        )},
    }

    @ocr.command('translate', aliases=('tr', 't', 'translated'))
    @cooldown(1, 25)
    @user_max_concurrency(1)
    async def ocr_translate(
        self,
        ctx: Context,
        image: Link | None = None,  # type: ignore
        destination: Annotated[str, TranslationLanguage] = 'en',
        *,
        flags: TranslateFlags,
    ) -> CommandResponse:
        """Reads text/characters from an image and translates them into the given destination language.

        Arguments:
        - `image`: The image to read text from. Can be provided as an attachment or URL.
        - `destination`: The language to translate to. See `{PREFIX}translate languages` for a list of valid languages. Defaults to ``en`` (English).

        Flags:
        - `--source <source>`: The source language of the text in the image. By default, the language will automatically be detected.
        - `--render`: Whether to directly render translated content on top of the image. This will give a "Google Lens-like" result. This *will* take time.
        """
        image = await self.OCR_FINDER.find(ctx, image, allow_gifs=False, fallback_to_user=False, run_conversions=False)
        if image is None:
            return 'Could not find an image. Please provide an image or direct link.', BAD_ARGUMENT, Param('image')

        async with ctx.typing():
            try:
                raw_text, data = await self._request_ocr(
                    image,
                    language=self._GOOGLE_TO_BCP_MAPPING.get(flags.source, 'unk'),
                )
            except aiohttp.ClientResponseError as exc:
                return f'Error {exc.status}: {exc.message}', ERROR

            if not flags.render:
                translated = await ctx.bot.translator.translate(raw_text, destination, lang_src=flags.source)
                if isinstance(translated, list):
                    translated = translated[0]

                view = TranslatedOcrView(
                    ctx, raw_text=raw_text, translated=translated, data=data,
                    image=image, dest=destination, source=flags.source,
                )

                if len(translated) <= 4096:
                    embed = discord.Embed(color=Colors.primary, description=translated, timestamp=ctx.now)
                    embed.set_author(name='Translated Text', icon_url=ctx.author.display_avatar)

                    return embed, REPLY

                return (
                    'Translated Text:',
                    discord.File(BytesIO(translated.encode()), filename=f'ocr_translation_{ctx.author.id}.txt'),
                    view,
                    REPLY,
                )

            renderer = OCRRenderer(self.bot, data)
            fp = await renderer.render_translated(image, destination, source=flags.source)

            if fp.getbuffer().nbytes < ctx.guild.filesize_limit - 1024:  # 1 KB of breathing room
                return discord.File(fp, filename=f'ocr_translation_{ctx.author.id}.png'), REPLY

            entry = await ctx.bot.cdn.safe_upload(fp, extension='png', directory='ocr_uploads')
            return entry.url, REPLY
