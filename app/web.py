from __future__ import annotations

from typing import TYPE_CHECKING

from discord.http import Route
from quart import Quart, request

from config import client_secret

if TYPE_CHECKING:
    from app.core import Bot
    from app.util.types import JsonObject

    class _Quart(Quart):
        bot: Bot

    Quart = _Quart

__all__ = ('app',)

app = Quart(__name__)


@app.get('/')
async def index() -> JsonObject:
    return {
        'message': 'Hello, world!'
    }


@app.post('/exchange-oauth')
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
    async with app.bot.session.post(route, json=data, headers=headers) as resp:
        if resp.status != 200:
            return {
                'error': f'HTTP {resp.status}: {resp.reason}'
            }, 400

        return await resp.json()
