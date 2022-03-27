from __future__ import annotations

import re
import urllib.parse
from collections import defaultdict
from functools import wraps
from typing import Callable, ParamSpec, TYPE_CHECKING, TypeVar

from discord.http import Route
from quart import Quart, Request, Response, jsonify, make_response, request

from config import client_secret

if TYPE_CHECKING:
    from app.core import Bot
    from app.util.types import JsonObject

    class _Quart(Quart):
        bot: Bot
        authorized_guilds: defaultdict[str, set[int]]

    Quart = _Quart

    P = ParamSpec('P')
    R = TypeVar('R')

__all__ = ('app',)

app = Quart(__name__)
app.authorized_guilds = defaultdict(set)

MENTION_REGEX: re.Pattern[str] = re.compile(r'<@!?\d+>')


class HTTPError(Exception):
    pass


def handle_cors(func: Callable[P, R]) -> Callable[P, R | Response]:
    @wraps(func)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R | Response:
        if request.method == 'OPTIONS':
            response = await make_response()
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

    redirect_uri = request.args.get('redirect_uri', 'https://lambdabot.cf')

    data = {
        'client_id': app.bot.user.id,
        'client_secret': client_secret,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri.rstrip('/'),
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


async def _run_discord_request(method: str, route: str, req: Request = None) -> JsonObject | tuple[JsonObject, int]:
    req = req or request
    try:
        token = req.args['token']
    except KeyError:
        return {
           'error': 'Missing access token'
        }, 400

    token_type = req.args.get('tt', 'Bearer')

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
        native = app.bot.get_guild(guild_id := int(guild['id']))
        if native is None:
            status = 0  # Unavailable because I'm not in the guild
        else:
            permissions = native.get_member(user_id).guild_permissions
            if permissions.administrator or permissions.manage_guild:
                status = 2  # Available
                app.authorized_guilds[request.args['token']].add(guild_id)
            else:
                status = 1  # Unavailable due to lack of permissions
        guild['status'] = status

    return jsonify(res)


async def _authorize_guilds(token: str, token_type: str, user_id: int) -> JsonObject:
    route = Route.BASE + '/users/@me/guilds'
    headers = {
        'Authorization': f'{token_type} {token}'
    }

    async with app.bot.session.get(route, headers=headers) as resp:
        if resp.status != 200:
            text = await resp.text('utf-8')
            raise HTTPError(f'HTTP {resp.status}: {resp.reason} ({text})')

        res = await resp.json()

    for guild in res:
        native = app.bot.get_guild(guild_id := int(guild['id']))
        if native is None:
            continue

        permissions = native.get_member(user_id).guild_permissions
        if permissions.administrator or permissions.manage_guild:
            app.authorized_guilds[token].add(guild_id)

    return res


async def _handle_authorization(guild_id: int) -> tuple[JsonObject, int] | None:
    try:
        token = request.args['token']
    except KeyError:
        return {
            'error': 'Missing access token'
        }, 400

    try:
        user_id = int(request.args['user_id'])
    except (KeyError, ValueError):
        return {
            'error': 'Missing user ID'
        }, 400

    token_type = request.args.get('tt', 'Bearer')

    if token not in app.authorized_guilds:
        await _authorize_guilds(token, token_type, user_id)

    if guild_id not in app.authorized_guilds[token]:
        return {
            'error': 'Unauthorized'
        }, 401


@app.route('/data/<int:guild_id>', methods=['GET', 'OPTIONS'])
@handle_cors
async def guild_data(guild_id: int) -> JsonObject | tuple[JsonObject, int]:
    if err := await _handle_authorization(guild_id):
        return err

    record = await app.bot.db.get_guild_record(guild_id)
    return {
        'prefixes': record.prefixes,
    }


@app.route('/prefixes/<int:guild_id>', methods=['PUT', 'OPTIONS'])
@handle_cors
async def add_prefix(guild_id: int) -> JsonObject | tuple[JsonObject, int]:
    if err := await _handle_authorization(guild_id):
        return err

    json = await request.get_json()
    try:
        prefix = json['prefix']
    except KeyError:
        return {
            'error': 'Missing prefix'
        }, 400

    if not isinstance(prefix, str):
        return {
            'error': 'Prefix must be a string',
        }, 400

    if MENTION_REGEX.search(prefix):
        return {
            'error': 'Prefix cannot contain a mention',
        }, 400

    if len(prefix) > 100:
        return {
            'error': 'Prefix must be 100 characters or less',
        }, 400

    record = await app.bot.db.get_guild_record(guild_id)
    if prefix in record.prefixes:
        return {
            'error': 'Prefix already exists',
        }, 400

    await record.append(prefixes=prefix)
    return {
        'success': True,
        'prefixes': record.prefixes,
    }


@app.route('/prefixes/<int:guild_id>', methods=['DELETE', 'OPTIONS'])
@handle_cors
async def remove_prefix(guild_id: int) -> JsonObject | tuple[JsonObject, int]:
    if err := await _handle_authorization(guild_id):
        return err

    json = await request.get_json()
    try:
        prefix = json['prefix']
    except KeyError:
        return {
            'error': 'Missing prefix to remove'
        }, 400

    record = await app.bot.db.get_guild_record(guild_id)
    if prefix not in record.prefixes:
        return {
            'error': 'Prefix does not exist'
        }, 400

    record.prefixes.remove(prefix)
    await record.update(prefixes=record.prefixes)
    return {
        'success': True,
        'prefixes': record.prefixes,
    }


@app.after_request
def after_request(response: Response) -> Response:
    headers = response.headers
    headers['Access-Control-Allow-Origin'] = '*'
    headers['Access-Control-Allow-Headers'] = '*'

    return response
