from __future__ import annotations

from typing import Final, TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import ClientSession

BASE_URL: Final[str] = 'https://www2.deepl.com/jsonrpc'

LANGUAGES: Final[dict[str, str]] = {
    'auto': 'Auto',
    'DE': 'German',
    'EN': 'English',
    'FR': 'French',
    'ES': 'Spanish',
    'IT': 'Italian',
    'NL': 'Dutch',
    'PL': 'Polish',
    'BG': 'Bulgarian',
    'ZH': 'Chinese',
    'CS': 'Czech',
    'DA': 'Danish',
    'ET': 'Estonian',
    'FI': 'Finnish',
    'EL': 'Greek',
    'HU': 'Hungarian',
    'JA': 'Japanese',
    'LV': 'Latvian',
    'LT': 'Lithuanian',
    'PT': 'Portuguese',
    'RO': 'Romanian',
    'RU': 'Russian',
    'SK': 'Slovak',
    'SL': 'Slovenian',
    'SV': 'Swedish',
}

GOOGLE_REVERSE_MAPPING: Final[dict[str, str]] = {
    **{k.lower(): k for k in LANGUAGES},
    'zh-cn': 'ZH',
}

REVERSE_LANGUAGE_MAPPING: Final[dict[str, str]] = {v: k for k, v in LANGUAGES.items()}
JSONRPC_VERSION: Final[str] = '2.0'


class SplittingError(Exception):
    pass


async def split_sentences(session: ClientSession, text: str, lang: str = 'auto', *, json: bool = False) -> list[str]:
    if text is None:
        raise SplittingError('Text can\'t be be None.')
    if lang not in LANGUAGES:
        raise SplittingError(f'Language {lang} not available.')

    payload = {
        'jsonrpc': JSONRPC_VERSION,
        'method': 'LMT_split_into_sentences',
        'params': {
            'texts': [
                text,
            ],
            'lang': {
                'lang_user_selected': lang,
            },
        },
    }

    async with session.post(BASE_URL, json=payload) as response:
        response = await response.json()

        if 'result' not in response:
            raise SplittingError('DeepL call resulted in a unknown result.')

        splitted_texts = response['result']['splitted_texts']

        if not splitted_texts:
            raise SplittingError('Text could not be splitted.')

        if json:
            return response
        return splitted_texts[0]


class TranslationError(Exception):
    pass


async def translate(session: ClientSession, text: str, to_lang: str, from_lang: str = 'auto', *, json: bool = False) -> str:
    if text is None:
        raise TranslationError('Text can\'t be None.')

    if len(text) > 5000:
        raise TranslationError('Text too long (limited to 5000 characters).')

    if to_lang not in LANGUAGES:
        raise TranslationError(f'Language {to_lang} not available.')

    if from_lang is not None and from_lang not in LANGUAGES:
        raise TranslationError(f'Language {from_lang} not available.')

    payload = {
        'id': 45620001,
        'jsonrpc': JSONRPC_VERSION,
        'method': 'LMT_handle_jobs',
        'params': {
            'jobs': [
                {
                    'kind': 'default',
                    'raw_en_sentence': text,
                },
            ],
            'lang': {
                'preference': {'weight': {}, 'default': 'default'},
                'source_lang_user_selected': from_lang,
                'target_lang': to_lang,
            },
        },
    }

    async with session.post(BASE_URL, json=payload) as response:
        response = await response.json()

        if 'result' not in response:
            raise TranslationError(f'DeepL call resulted in a unknown result: {response}')

        translations = response['result']['translations']

        if (
            not translations
            or translations[0]['beams'] is None
            or translations[0]['beams'][0]['postprocessed_sentence'] is None
        ):
            raise TranslationError(f'No translations found: {response}')

        if json:
            return response
        return translations[0]['beams'][0]['postprocessed_sentence']
