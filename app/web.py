from __future__ import annotations

import urllib.parse
from functools import wraps
from typing import Callable, ParamSpec, TYPE_CHECKING, TypeVar

from discord.http import Route
from quart import Quart, Response, make_response, request

from config import client_secret

if TYPE_CHECKING:
    from app.core import Bot
    from app.util.types import JsonObject

    class _Quart(Quart):
        bot: Bot

    Quart = _Quart

    P = ParamSpec('P')
    R = TypeVar('R')

__all__ = ('app',)

app = Quart(__name__)


def handle_cors(func: Callable[P, R]) -> Callable[P, R | Response]:
    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R | Response:
        if request.method == 'OPTIONS':
            response = make_response()
            response.headers['Access-Control-Allow-Origin'] = '*'
            response.headers['Access-Control-Allow-Headers'] = '*'
            response.headers['Access-Control-Allow-Methods'] = '*'
            return response

        return await func(*args, **kwargs)

    return wrapper


@app.get('/')
async def index() -> JsonObject:
    return {
        'message': 'Hello, world!'
    }


@app.route('/exchange-oauth', methods=['POST', 'OPTIONS'])
@handle_cors
async def exchange_oauth() -> JsonObject | tuple[JsonObject, int]:
    try:
        code = request.args['code']
    except KeyError:
        return {
            'error': 'Missing code'
        }, 400

    data = {
        'client_id': app.bot.user.id,
        'client_secret': client_secret,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': 'https://lambdabot.cf',
    }
    headers = {
        'Content-Type': 'application/x-www-form-urlencoded'
    }

    route = Route.BASE + '/oauth2/token'
    async with app.bot.session.post(route, data=urllib.parse.urlencode(data), headers=headers) as resp:
        if resp.status != 200:
            text = await resp.text('utf-8')
            return {
                'error': f'HTTP {resp.status}: {resp.reason} ({text})'
            }, 400

        return await resp.json()


@app.after_request
def after_request(response: Response) -> Response:
    headers = response.headers
    headers['Access-Control-Allow-Origin'] = '*'
    headers['Access-Control-Allow-Headers'] = '*'

    return response
