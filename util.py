import asyncio
from typing import Any, Iterable, Mapping, Optional, Tuple, Type, Union, List

import discord

def noop(*_, **__):
	pass

async def async_noop(*_, **__):
	pass

def get_nested(obj: Mapping[Any, Any], *path: List[str]) -> Any:
	for k in path:
		obj = obj[k]
	return obj

def get_nested_or(obj: Mapping[Any, Any], *path: List[str], default: Any) -> Any:
	try:
		return get_nested(obj, *path)
	except KeyError:
		return default

class MultipleExceptions(BaseException):
	exceptions: Tuple[Exception]

	def __init__(self, excs: Iterable[Exception]) -> None:
		self.exceptions = tuple(excs)

	def __iter__(self) -> Iterable[Exception]:
		return self.exceptions.__iter__()

	def __len__(self) -> int:
		return self.exceptions.__len__()

	@classmethod
	def maybe(cls, excs: Iterable[Exception]) -> Exception:
		excs = tuple(excs)
		if len(excs) == 1:
			return excs[0]
		elif len(excs) > 1:
			return cls(excs)
		else:
			return None

	@staticmethod
	def get_first(ex: Exception, typ: Type[Exception]) -> Optional[Exception]:
		if isinstance(ex, MultipleExceptions):
			for inner_ex in ex.exceptions:
				if isinstance(inner_ex, typ):
					return inner_ex
			return None
		else:
			return ex if isinstance(ex, typ) else None

async def wait(futures: Iterable[Union[asyncio.Task, asyncio.Future]], *, loop: Optional[asyncio.AbstractEventLoop] = None, timeout: Optional[float] = None, return_when: str = asyncio.ALL_COMPLETED, raise_on_exception: bool = True, cancel_pending: bool = True, exception_whitelist: Tuple[Exception] = (asyncio.CancelledError,)):
	futures = list(map(asyncio.ensure_future, futures))
	try:
		(done, pending) = await asyncio.wait(futures, loop=loop, timeout=timeout, return_when=return_when)
	except asyncio.CancelledError as ex:
		if cancel_pending:
			for task in futures:
				try:
					if not task.done():
						task.cancel()
				except asyncio.CancelledError:
					# 🤷‍♂️
					pass

		raise ex
	else:
		if cancel_pending:
			for task in pending:
				task.cancel()

		if raise_on_exception:
			def get_ex(task: asyncio.Task):
				try:
					return task.exception()
				except asyncio.CancelledError as ex:
					return ex
			excs = list(filter(lambda ex: ex is not None and not isinstance(ex, exception_whitelist), map(get_ex, done)))
			if len(excs) != 0:
				raise MultipleExceptions.maybe(excs)

		return (done, pending)

def cancel_task(task: Optional[Union[asyncio.Future, asyncio.Task]]):
	if task is not None and not task.done():
		task.cancel()

def make_base_embed(*args, title: Optional[str] = None, **kwargs):
	t = 'Mindnight'
	if title is not None:
		t += f' - {title}'
	return discord.Embed(*args, **kwargs, title=t)

def get_channel_link(channel: Union[discord.TextChannel, discord.VoiceChannel, discord.DMChannel, discord.GroupChannel]):
	guild_id = channel.guild.id if isinstance(channel, discord.abc.GuildChannel) else '@me'
	return f'https://discord.com/channels/{guild_id}/{channel.id}'

def emote_url(em: Union[str, discord.Emoji, discord.PartialEmoji, discord.Reaction]) -> str:
	if isinstance(em, discord.Reaction):
		em = em.emoji

	if isinstance(em, discord.Emoji) or (isinstance(em, discord.PartialEmoji) and em.is_custom_emoji()):
		return em.url
	else:
		unicode = em
		if not isinstance(em, str):
			unicode = em.name

		codepoints = list(ord(c) for c in unicode)
		if 0x200d in codepoints:
			codepoints = filter(lambda c: c != 0xfe0f, codepoints)
		codepoints = '-'.join(hex(i)[2:] for i in codepoints)
		return f'https://twemoji.maxcdn.com/v/13.0.1/72x72/{codepoints}.png'

def fmt_list(iter: Iterable):
	iter = list(iter)
	if iter is None or len(iter) == 0:
		return ''
	elif len(iter) == 1:
		return str(iter[0])
	else:
		return ', '.join(map(str, iter[:-1])) + ' and ' + str(iter[-1])

def fmt_plural(amount: int, term: str):
	return term + ('s' if abs(amount) != 1 else '')

def fmt_time(secs: float):
	hours = secs / 3600
	minutes = secs / 60 % 60
	seconds = secs % 60

	result = []
	if hours >= 1:
		result.append(f'{int(hours)} ' + fmt_plural(hours, 'hour'))
	if minutes >= 1:
		result.append(f'{int(minutes)} ' + fmt_plural(minutes, 'minute'))
	if seconds > 0 or len(result) == 0:
		pretty_seconds = f'{int(seconds)}' + ('' if int(seconds) == seconds else f'{seconds - int(seconds):.2f}'[1:])
		result.append(f'{pretty_seconds} ' + fmt_plural(seconds, 'second'))
	return ' '.join(result)

class PrettyRepr:
	def __repr__(self) -> str:
		props = ' '.join(f'{repr(k)}={repr(v)}' for (k, v) in self.__dict__.items() if k[0] != '_')
		return f'<{self.__class__.__qualname__} {props}>'
