# noinspection PyPep8Naming
from datetime import datetime as _INTERNAL_dt, timezone as _INTERNAL_dt_tz
# noinspection PyPep8Naming
import time as _INTERNAL_time
# noinspection PyPep8Naming
import json as _INTERNAL_json
# noinspection PyPep8Naming
from sys import exit as _INTERNAL_exit


def _INTERNAL_restrict_sleep(*_args, **_kwargs):
    raise RuntimeError('time.sleep will not work as everything is executed atomically.')


_INTERNAL_time.sleep = _INTERNAL_restrict_sleep
del _INTERNAL_restrict_sleep

_INTERNAL_FMTARG = lambda _: 0


# noinspection PyPep8Naming
class _INTERNAL_BaseDiscordModel:
    id: int

    @property
    def created_at(self):
        return _INTERNAL_dt.fromtimestamp(((self.id >> 22) + 1_420_070_400_000) / 1000, tz=_INTERNAL_dt_tz.utc)


# noinspection PyPep8Naming
class model:
    # noinspection PyShadowingBuiltins
    class Asset:
        def __init__(self, base, animated=False):
            self.base = base
            self.animated = animated
            self.format = 'gif' if animated else 'png'
            self.size = None

        def __str__(self): return self.url

        @property
        def url(self):
            size = f'?size={self.size}' * (self.size is not None)
            return f'https://cdn.discordapp.com/{self.base}.{self.format}{size}'

        def replace(self, *, format=None, size=None):
            new = model.Asset(self.base, self.animated)
            new.format = format or self.format
            new.size = size or self.size
            return new

        def with_format(self, format): return self.replace(format=format)

        def with_size(self, size): return self.replace(size=size)

    class Guild(_INTERNAL_BaseDiscordModel):
        def __init__(self, id, name, icon_hash, owner, member_count):
            self.id = id
            self.name = name
            self.icon = icon_hash and model.Asset(f'icons/{id}/{icon_hash}', animated=icon_hash.startswith('a_'))
            self.owner = owner
            self.member_count = member_count

        def __repr__(self):
            return f'<Guild id={self.id} name={self.name!r} owner={self.owner!r}>'

    class Channel(_INTERNAL_BaseDiscordModel):
        def __init__(self, id, name, topic, slowmode, position, nsfw):
            self.id = id
            self.name = name
            self.topic = topic
            self.slowmode = slowmode
            self.position = position
            self.nsfw = nsfw

        def __repr__(self):
            return f'<Channel id={self.id} name={self.name!r} nsfw={self.nsfw}>'

        def mention(self): return f'<#{self.id}>'

    # noinspection PyShadowingNames
    class User(_INTERNAL_BaseDiscordModel):
        _display_avatar_hash: str

        def __init__(self, id, name, discriminator, avatar_hash, nick,
                     guild, disp_avatar_hash, joined_at, position, color):
            self.id = id
            self.name = name
            self.discriminator = discriminator
            self.avatar = model.Asset(
                f'avatars/{id}/{avatar_hash}', avatar_hash.startswith('a_')
            ) if not avatar_hash.isdigit() else model.Asset(
                f'embed/avatars/{avatar_hash}', False,
            )
            self.nick = nick
            self.joined_at = joined_at
            self.color = color
            self.position = position
            self._display_avatar_hash = disp_avatar_hash
            guild and disp_avatar_hash and self._register_guild(guild)

        @property
        def mention(self): return f'<@{self.id}>'

        @property
        def colour(self): return self.color

        @property
        def tag(self): return f'{self.name}#{self.discriminator}'

        __str__ = tag.fget  # type: ignore

        def __repr__(self):
            return f'<User id={self.id} name={self.name!r} discriminator={self.discriminator!r}>'

        def _register_guild(self, guild):
            self.display_avatar = model.Asset(
                f'guilds/{guild.id}/users/{id}/avatars/{self._display_avatar_hash}.png',
                animated=self._display_avatar_hash.startswith('a_'),
            ) if self._display_avatar_hash and guild else self.avatar


def _INTERNAL_TRANSFORM_DT(dt):
    return eval(dt, {'datetime': __import__('datetime')})  # don't kill me for this


EmptyEmbed = 'SINGLETON'


class EmbedProxy:
    def __init__(self, layer):
        self.__dict__.update(layer)

    def __len__(self) -> int:
        return len(self.__dict__)

    def __repr__(self) -> str:
        inner = ', '.join((f'{k}={v!r}' for k, v in self.__dict__.items() if not k.startswith('_')))
        return f'EmbedProxy({inner})'

    def __getattr__(self, attr: str):
        return EmptyEmbed


class Button:
    _PRIMARY = 1
    _SECONDARY = 2
    _SUCCESS = 3
    _DANGER = 4
    _LINK = 5

    def __init__(self, label, style=1, response=None, url=None):
        self.label = label
        self.style = style
        self.response = response
        self.url = url

    def __repr__(self):
        if self.style == self._LINK:
            return f'<Button label={self.label!r} url={self.url!r}>'

        return f'<Button label={self.label!r} style={self.style}>'

    def to_dict(self):
        return vars(self)

    @classmethod
    def link(cls, label, url):
        return cls(label, cls._LINK, url=url)

    @classmethod
    def primary(cls, label, response):
        return cls(label, cls._PRIMARY, response=response)

    @classmethod
    def secondary(cls, label, response):
        return cls(label, cls._SECONDARY, response=response)

    @classmethod
    def success(cls, label, response):
        return cls(label, cls._SUCCESS, response=response)

    @classmethod
    def danger(cls, label, response):
        return cls(label, cls._DANGER, response=response)

    url = link
    blurple = primary
    grey = gray = secondary
    green = success
    red = danger


class Embed:
    Empty = EmptyEmbed

    def __init__(
        self,
        *,
        colour=EmptyEmbed,
        color=EmptyEmbed,
        title=EmptyEmbed,
        type='rich',
        url=EmptyEmbed,
        description=EmptyEmbed,
        timestamp=EmptyEmbed,
    ):
        self.colour = colour if colour is not EmptyEmbed else color
        self.title = title
        self.type = type
        self.url = url
        self.description = description

        if self.title is not EmptyEmbed:
            self.title = str(self.title)

        if self.description is not EmptyEmbed:
            self.description = str(self.description)

        if self.url is not EmptyEmbed:
            self.url = str(self.url)

        if timestamp is not EmptyEmbed:
            self.timestamp = timestamp

    @classmethod
    def from_dict(cls, data):
        self = cls.__new__(cls)

        self.title = data.get('title', EmptyEmbed)
        self.type = data.get('type', EmptyEmbed)
        self.description = data.get('description', EmptyEmbed)
        self.url = data.get('url', EmptyEmbed)

        if self.title is not EmptyEmbed:
            self.title = str(self.title)

        if self.description is not EmptyEmbed:
            self.description = str(self.description)

        if self.url is not EmptyEmbed:
            self.url = str(self.url)

        # try to fill in the more rich fields

        try:
            self._colour = data['color']
        except KeyError:
            pass

        try:
            self._timestamp = _INTERNAL_dt.fromisoformat(data['timestamp'])
        except KeyError:
            pass

        for attr in ('thumbnail', 'video', 'provider', 'author', 'fields', 'image', 'footer'):
            try:
                value = data[attr]
            except KeyError:
                continue
            else:
                setattr(self, '_' + attr, value)

        return self

    def copy(self):
        return self.__class__.from_dict(self.to_dict())

    def __len__(self):
        total = len(self.title) + len(self.description)
        for field in getattr(self, '_fields', []):
            total += len(field['name']) + len(field['value'])

        try:
            footer_text = self._footer['text']
        except (AttributeError, KeyError):
            pass
        else:
            total += len(footer_text)

        try:
            author = self._author
        except AttributeError:
            pass
        else:
            total += len(author['name'])

        return total

    def __bool__(self):
        return any(
            (
                self.title,
                self.url,
                self.description,
                self.colour,
                self.fields,
                self.timestamp,
                self.author,
                self.thumbnail,
                self.footer,
                self.image,
                self.provider,
                self.video,
            )
        )

    @property
    def colour(self):
        return getattr(self, '_colour', EmptyEmbed)

    @colour.setter
    def colour(self, value):
        if value is EmptyEmbed:
            value = 0

        if isinstance(value, int):
            self._colour = value
        else:
            raise TypeError(f'Expected int or Embed.Empty but received {value.__class__.__name__} instead.')

    color = colour

    @property
    def timestamp(self):
        return getattr(self, '_timestamp', EmptyEmbed)

    @timestamp.setter
    def timestamp(self, value):
        if isinstance(value, _INTERNAL_dt):
            if value.tzinfo is None:
                value = value.astimezone()
            self._timestamp = value
        elif value is EmptyEmbed:
            self._timestamp = value
        else:
            raise TypeError(f"Expected datetime.datetime or Embed.Empty received {value.__class__.__name__} instead")

    @property
    def footer(self):
        return EmbedProxy(getattr(self, '_footer', {}))  # type: ignore

    def set_footer(self, *, text=EmptyEmbed, icon_url=EmptyEmbed):
        self._footer = {}
        if text is not EmptyEmbed:
            self._footer['text'] = str(text)

        if icon_url is not EmptyEmbed:
            self._footer['icon_url'] = str(icon_url)

        return self

    def remove_footer(self):
        try:
            del self._footer
        except AttributeError:
            pass

        return self

    @property
    def image(self):
        return EmbedProxy(getattr(self, '_image', {}))  # type: ignore

    def set_image(self, *, url):
        if url is EmptyEmbed:
            try:
                del self._image
            except AttributeError:
                pass
        else:
            self._image = {'url': str(url)}

        return self

    @property
    def thumbnail(self):
        return EmbedProxy(getattr(self, '_thumbnail', {}))  # type: ignore

    def set_thumbnail(self, *, url):
        if url is EmptyEmbed:
            try:
                del self._thumbnail
            except AttributeError:
                pass
        else:
            self._thumbnail = {
                'url': str(url),
            }

        return self

    @property
    def video(self):
        return EmbedProxy(getattr(self, '_video', {}))

    @property
    def provider(self):
        return EmbedProxy(getattr(self, '_provider', {}))

    @property
    def author(self):
        return EmbedProxy(getattr(self, '_author', {}))

    def set_author(self, *, name=EmptyEmbed, icon_url=EmptyEmbed, url=EmptyEmbed):
        self._author = {
            'name': str(name),
        }

        if url is not EmptyEmbed:
            self._author['url'] = str(url)

        if icon_url is not EmptyEmbed:
            self._author['icon_url'] = str(icon_url)

        return self

    def remove_author(self):
        try:
            del self._author
        except AttributeError:
            pass

        return self

    @property
    def fields(self):
        return [EmbedProxy(d) for d in getattr(self, '_fields', [])]

    def add_field(self, *, name, value, inline: bool = True):
        field = {
            'inline': inline,
            'name': str(name),
            'value': str(value),
        }

        try:
            self._fields.append(field)  # type: ignore
        except AttributeError:
            self._fields = [field]

        return self

    def insert_field_at(self, index: int, *, name, value, inline: bool = True):
        field = {
            'inline': inline,
            'name': str(name),
            'value': str(value),
        }

        try:
            self._fields.insert(index, field)
        except AttributeError:
            self._fields = [field]

        return self

    def clear_fields(self):
        try:
            self._fields.clear()
        except AttributeError:
            self._fields = []

    def remove_field(self, index: int):
        try:
            del self._fields[index]
        except (AttributeError, IndexError):
            pass

    def set_field_at(self, index: int, *, name, value, inline: bool = True):
        try:
            field = self._fields[index]
        except (TypeError, IndexError, AttributeError):
            raise IndexError('field index out of range')

        field['name'] = str(name)
        field['value'] = str(value)
        field['inline'] = inline
        return self

    def to_dict(self):
        attrs = ('_timestamp', '_colour', '_footer', '_image', '_thumbnail', '_video', '_provider', '_author', '_fields')

        result = {
            key[1:]: getattr(self, key)
            for key in attrs
            if key[0] == '_' and hasattr(self, key)
        }

        try:
            colour = result.pop('colour')
        except KeyError:
            pass
        else:
            if colour:
                result['color'] = colour.value

        try:
            timestamp = result.pop('timestamp')
        except KeyError:
            pass
        else:
            if timestamp:
                if timestamp.tzinfo:
                    result['timestamp'] = timestamp.astimezone(tz=_INTERNAL_dt_tz.utc).isoformat()
                else:
                    result['timestamp'] = timestamp.replace(tzinfo=_INTERNAL_dt_tz.utc).isoformat()

        if self.type:
            result['type'] = self.type

        if self.description:
            result['description'] = self.description

        if self.url:
            result['url'] = self.url

        if self.title:
            result['title'] = self.title

        return result
    

# noinspection PyPep8Naming
class engine:
    class Error(Exception):
        def __init__(self, message) -> None:
            print(message)
            _INTERNAL_exit(2468)

    args: list[str] = _INTERNAL_FMTARG('args!r')  # type: ignore

    guild = model.Guild(
        id=_INTERNAL_FMTARG('guild.id'),
        name=_INTERNAL_FMTARG('guild.name!r'),
        icon_hash=_INTERNAL_FMTARG('guild_icon!r'),  # type: ignore
        owner=model.User(
            id=_INTERNAL_FMTARG('guild.owner_id'),
            name=_INTERNAL_FMTARG('guild.owner.name!r'),
            discriminator=_INTERNAL_FMTARG('guild.owner.discriminator!r'),
            avatar_hash=_INTERNAL_FMTARG('guild.owner.avatar.key!r'),  # type: ignore
            nick=_INTERNAL_FMTARG('guild.owner.nick!r'),
            guild=None,  # type: ignore
            disp_avatar_hash=_INTERNAL_FMTARG('guild.owner.display_avatar.key!r'),
            joined_at=_INTERNAL_TRANSFORM_DT("_INTERNAL_FMTARG('guild.owner.joined_at!r')"),
            color=_INTERNAL_FMTARG('guild.owner.color.value'),
            position=_INTERNAL_FMTARG('guild.owner.top_role.position'),
        ),
        member_count=_INTERNAL_FMTARG('guild.member_count'),
    )

    server = guild

    user = model.User(
        id=_INTERNAL_FMTARG('user.id'),
        name=_INTERNAL_FMTARG('user.name!r'),
        discriminator=_INTERNAL_FMTARG('user.discriminator!r'),
        avatar_hash=_INTERNAL_FMTARG('user.avatar.key!r'),  # type: ignore
        nick=_INTERNAL_FMTARG('user.nick!r'),
        guild=guild,
        disp_avatar_hash=_INTERNAL_FMTARG('user.display_avatar.key!r'),
        joined_at=_INTERNAL_TRANSFORM_DT("_INTERNAL_FMTARG('user.joined_at!r')"),
        color=_INTERNAL_FMTARG('user.color.value'),
        position=_INTERNAL_FMTARG('user.top_role.position'),
    )

    member = user

    target = model.User(
        id=_INTERNAL_FMTARG('target.id'),
        name=_INTERNAL_FMTARG('target.name!r'),
        discriminator=_INTERNAL_FMTARG('target.discriminator!r'),
        avatar_hash=_INTERNAL_FMTARG('target.avatar.key!r'),  # type: ignore
        nick=_INTERNAL_FMTARG('target.nick!r'),
        guild=guild,
        disp_avatar_hash=_INTERNAL_FMTARG('target.display_avatar.key!r'),
        joined_at=_INTERNAL_TRANSFORM_DT("_INTERNAL_FMTARG('target.joined_at!r')"),
        color=_INTERNAL_FMTARG('target.color.value'),
        position=_INTERNAL_FMTARG('target.top_role.position'),
    )

    channel = model.Channel(
        id=_INTERNAL_FMTARG('channel.id'),
        name=_INTERNAL_FMTARG('channel.name!r'),
        topic=_INTERNAL_FMTARG('channel.topic!r'),
        slowmode=_INTERNAL_FMTARG('channel.slowmode_delay'),
        position=_INTERNAL_FMTARG('channel.position'),
        nsfw=_INTERNAL_FMTARG('channel.nsfw'),
    )

    @classmethod
    def expect_arg_count(cls, count: int) -> None:
        if len(cls.args) < count:
            raise TypeError(f'Expected {count} arguments, got {len(cls.args)}')

    @classmethod
    def arg(cls, idx: int, /) -> str:
        return cls.args[idx]

    # noinspection PyShadowingNames
    @staticmethod
    def respond(content=None, *, embed=None, embeds=None, button=None, buttons=None, reply=True):
        embeds = [embed] if embed is not None else embeds
        buttons = [button] if button is not None else buttons

        payload = {
            'op': 'respond',
            'd': {
                'content': None if content is None else str(content),
                'embeds': embeds and [embed.to_dict() if isinstance(embed, Embed) else embed for embed in embeds],
                'buttons': buttons and [button.to_dict() if isinstance(button, Button) else button for button in buttons][:25],
                'reply': reply,
            },
        }
        print(f'\x0e\x00:\x01{_INTERNAL_json.dumps(payload)}\x01\x02')  # random characters, who knows?

    @staticmethod
    def exit(msg=''):
        raise Error(msg)


engine.guild.owner._register_guild(engine.guild)
user = member = engine.user
target = engine.target
guild = server = engine.guild
channel = engine.channel
respond = reply = engine.respond
args = engine.args
arg = engine.arg
Error = engine.Error
# noinspection PyShadowingBuiltins
exit = error = engine.exit

del _INTERNAL_TRANSFORM_DT
del _INTERNAL_FMTARG

# BEGIN CODE ------------------------------------------------------------------
