from __future__ import annotations

import urllib.parse
from functools import wraps
from typing import Callable, ParamSpec, TYPE_CHECKING, TypeVar

from discord.http import Route
from quart import Quart, Response, jsonify, make_response, request

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


async def _run_discord_request(method: str, route: str) -> JsonObject | tuple[JsonObject, int]:
    try:
        token = request.args['token']
    except KeyError:
        return {
           'error': 'Missing access token'
        }, 400

    token_type = request.args.get('tt', 'Bearer')

    route = Route.BASE + route
    headers = {
        'Authorization': f'{token_type} {token}'
    }

    async with app.bot.session.request(method, route, headers=headers) as resp:
        if resp.status != 200:
            text = await resp.text('utf-8')
            return {
               'error': f'HTTP {resp.status}: {resp.reason} ({text})'
           }, 400

        return await resp.json()


@app.route('/discord/user', methods=['GET', 'OPTIONS'])
@handle_cors
async def get_discord_user() -> JsonObject | tuple[JsonObject, int]:
    return await _run_discord_request('GET', '/users/@me')


@app.route('/discord/guilds', methods=['GET', 'OPTIONS'])
@handle_cors
async def get_discord_guilds() -> Response | JsonObject | tuple[JsonObject, int]:
    res = await _run_discord_request('GET', '/users/@me/guilds')
    if isinstance(res, dict):
        return res

    try:
        user_id = int(request.args['user_id'])
    except (KeyError, ValueError):
        return {
            'error': 'Missing user ID'
        }, 400

    for guild in res:
        native = app.bot.get_guild(int(guild['id']))
        if native is None:
            status = 0  # Unavailable because I'm not in the guild
        else:
            permissions = native.get_member(user_id).guild_permissions
            if permissions.administrator or permissions.manage_guild:
                status = 2  # Available
            else:
                status = 1  # Unavailable due to lack of permissions
        guild['status'] = status

    return jsonify(res)


@app.after_request
def after_request(response: Response) -> Response:
    headers = response.headers
    headers['Access-Control-Allow-Origin'] = '*'
    headers['Access-Control-Allow-Headers'] = '*'

    return response
