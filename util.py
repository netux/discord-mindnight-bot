import asyncio
from bot import Bot
from types import CoroutineType
from typing import Any, Iterable, Optional, Union, List, Tuple

import discord

def noop(*_, **__):
	pass

async def async_noop(*_, **__):
	pass

async def wait_in_order(*coros: Iterable[Union[CoroutineType, asyncio.Future]]):
	results = []
	for coro in coros:
		results.append(await coro)
	return results

async def wait_for_first(*coros: Iterable[CoroutineType]):
	futures: List[asyncio.Future] = list(map(lambda coro: asyncio.create_task(coro) if asyncio.iscoroutine(coro) else coro, coros))

	result = None
	result_coro = None

	async def wrap_coro(index: int, fut: asyncio.Future) -> Tuple[int, Any]:
		nonlocal result, result_coro

		try:
			this_result = await fut
		except asyncio.CancelledError:
			pass
		else:
			if result_coro is not None:
				return
			result = this_result
			result_coro = coros[index]

			for coro in futures:
				coro.cancel()

	await asyncio.gather(*map(lambda tup: wrap_coro(tup[0], tup[1]), enumerate(futures)))

	return (result_coro, result)

def make_base_embed(*args, title: Optional[str] = None, **kwargs):
	t = 'Mindnight'
	if title is not None:
		t += f' - {title}'
	return discord.Embed(*args, **kwargs, title=t)

def get_bot_prefix(bot: Bot):
	if isinstance(bot.command_prefix, str):
		return bot.command_prefix
	elif isinstance(bot.command_prefix, (tuple, list)):
		return bot.command_prefix[0]
	else:
		raise Exception('cannot get bot prefix')

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
