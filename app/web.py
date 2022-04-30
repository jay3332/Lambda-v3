from __future__ import annotations

import asyncio
import inspect
import os
import re
import urllib.parse
from collections import defaultdict
from functools import wraps
from typing import Callable, Final, ParamSpec, TYPE_CHECKING, TypeVar

from discord.http import Route
from quart import Quart, Request, Response, jsonify, make_response, request

from app.features.custom_commands import CustomCommand
from config import client_secret

if TYPE_CHECKING:
    from app.core import Bot, Command
    from app.features.leveling.core import LevelingManager
    from app.features.leveling.rank_card import RankCard
    from app.util.types import JsonObject

    class _Quart(Quart):
        bot: Bot
        authorized_guilds: defaultdict[str, set[int]]
        token_store: dict[int, str]

    Quart = _Quart

    P = ParamSpec('P')
    R = TypeVar('R')

__all__ = ('app',)

app = Quart(__name__)
app.authorized_guilds = defaultdict(set)
app.token_store = {}

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
            'error': 'Unauthorized',
        }, 401


@app.route('/auth/<int:user_id>', methods=['POST', 'OPTIONS'])
@handle_cors
async def authorize_user(user_id: int) -> JsonObject | tuple[JsonObject, int]:
    try:
        token = request.args['token']
    except KeyError:
        return {
            'error': 'Missing access token'
        }, 400

    token_type = request.args.get('tt', 'Bearer')

    async with app.bot.session.request('GET', Route.BASE + '/users/@me', headers={
        'Authorization': f'{token_type} {token}'
    }) as resp:
        if resp.status != 200:
            text = await resp.text('utf-8')
            return {
                'error': f'HTTP {resp.status}: {resp.reason} ({text})'
            }, 400

        user = await resp.json()
        if user['id'] != str(user_id):
            return {
                'error': 'ID does not match token',
                'force_reauth': True,
            }, 400

    token = os.urandom(16).hex()
    app.token_store[user_id] = token
    return {
        'token': token,
    }


async def _authenticate_user(user_id: int) -> tuple[JsonObject, int] | None:
    try:
        token = request.headers['Authorization']
    except KeyError:
        return {
            'error': 'Missing authorization header'
        }, 400

    try:
        if token != app.token_store[user_id]:
            return {
                'error': 'Invalid token',
                'force_reauth': True,
            }, 401

    except KeyError:
        return {
            'error': f'You are currently unauthorized, please make a POST request to /auth/{user_id}',
            'force_reauth': True,
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
    if len(record.prefixes) >= 25:
        return {
            'error': 'Prefix limit reached (Max: 25)'
        }, 400

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


DB_KEY_MAPPING: Final[dict[str, str]] = {
    'font': 'font',
    'primaryColor': 'primary_color',
    'secondaryColor': 'secondary_color',
    'tertiaryColor': 'tertiary_color',
    'backgroundUrl': 'background_url',
    'backgroundColor': 'background_color',
    'backgroundImageAlpha': 'background_alpha',
    'backgroundBlur': 'background_blur',
    'overlayColor': 'overlay_color',
    'overlayAlpha': 'overlay_alpha',
    'overlayBorderRadius': 'overlay_border_radius',
    'avatarBorderColor': 'avatar_border_color',
    'avatarBorderAlpha': 'avatar_border_alpha',
    'avatarBorderRadius': 'avatar_border_radius',
    'progressBarColor': 'progress_bar_color',
    'progressBarAlpha': 'progress_bar_alpha',
}


def _rank_card_to_json(record: RankCard) -> JsonObject:
    return {
        'font': record.font.value,
        'primaryColor': record.data['primary_color'],
        'secondaryColor': record.data['secondary_color'],
        'tertiaryColor': record.data['tertiary_color'],
        'backgroundUrl': record.background_url,
        'backgroundColor': record.data['background_color'],
        'backgroundImageAlpha': record.background_image_alpha / 255,
        'backgroundBlur': record.background_blur,
        'overlayColor': record.data['overlay_color'],
        'overlayAlpha': record.data['overlay_alpha'],
        'overlayBorderRadius': record.overlay_border_radius,
        'avatarBorderColor': record.data['avatar_border_color'],
        'avatarBorderAlpha': record.data['avatar_border_alpha'],
        'avatarBorderRadius': record.avatar_border_radius,
        'progressBarColor': record.data['progress_bar_color'],
        'progressBarAlpha': record.data['progress_bar_alpha'],
    }


@app.route('/rank-card/<int:user_id>', methods=['GET', 'PATCH', 'OPTIONS'])
@handle_cors
async def rank_card(user_id: int) -> JsonObject | tuple[JsonObject, int]:
    if err := await _authenticate_user(user_id):
        return err

    manager: LevelingManager = app.bot.get_cog('Leveling').manager  # type: ignore
    user = app.bot.get_user(user_id)
    if not user:
        return {
            'error': 'User not found'
        }, 404

    record = await manager.fetch_rank_card(user)
    if request.method == 'GET':
        # respond with camelCase because front-end takes it
        return _rank_card_to_json(record)

    json = await request.get_json()
    kwargs = {
        DB_KEY_MAPPING[k]: v for k, v in json.items()
    }

    await record.update(**kwargs)
    return {
        'success': True,
        'updated': _rank_card_to_json(record),
    }


ARGUMENT_REGEX: re.Pattern[str] = re.compile(r'- `([\w-]+)(?: [^`]+)?` ?: ?(.+)')


def _serialize_command(command: Command) -> JsonObject:
    convert = command.permission_spec.permission_as_str
    result = {
        'name': command.qualified_name,
        'aliases': list(command.aliases),
        'description': command.short_doc,
        'arguments': {},
        'flags': {},
        'signature': [],
        'cooldown': command.cooldown and {
            'rate': command.cooldown.rate,
            'per': command.cooldown.per,
            'type': command._buckets.type,
        },
        'permissions': {
            'user': [convert(p) for p in command.permission_spec.user],
            'bot': [convert(p) for p in command.permission_spec.bot],
        },
    }
    body = command.help
    try:
        body = body[body.rindex('Arguments:\n') + 11:]
    except ValueError:
        pass
    else:
        arguments = ARGUMENT_REGEX.findall(body)

        args = result['arguments']
        flags = result['flags']
        for name, description in arguments:
            try:
                param = command.param_info[name]
            except KeyError:
                continue

            entity = flags if param.is_flag() else args
            entity[name.removeprefix('--')] = description

    signature: list[JsonObject] = result['signature']
    for name, info in command.param_info.items():
        default = (
            None
            if info.default is inspect.Parameter.empty
            else None
            if info is None
            else repr(info.default)
        )
        signature.append({
            'name': name,
            'required': info.required,
            'default': default,
            'choices': info.choices and [str(c) for c in info.choices],
            'store_true': info.store_true,
        })

    return result


# very quick fix, code quality here doesn't really matter though
_cached_commands: JsonObject | None = None
_cached_commands_task: asyncio.Task | None = None


async def _clear_cached_commands() -> None:
    global _cached_commands
    await asyncio.sleep(120)
    _cached_commands = None


@app.route('/commands', methods=['GET', 'OPTIONS'])
@handle_cors
async def command_info() -> JsonObject:
    global _cached_commands, _cached_commands_task

    if _cached_commands is None:
        _cached_commands = {
            cog.qualified_name: [
                _serialize_command(command) for command in cog.walk_commands()
                if not isinstance(command, CustomCommand) and not command.hidden
            ]
            for cog in app.bot.cogs.values() if not getattr(cog, '__hidden__', True)
        }

        if _cached_commands_task is None or _cached_commands_task.done():
            if _cached_commands_task is not None:
                _cached_commands_task.cancel()

            _cached_commands_task = app.bot.loop.create_task(_clear_cached_commands())

    return _cached_commands


@app.after_request
def after_request(response: Response) -> Response:
    headers = response.headers
    headers['Access-Control-Allow-Origin'] = '*'
    headers['Access-Control-Allow-Headers'] = '*'

    return response
