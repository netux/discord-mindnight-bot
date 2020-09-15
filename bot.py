import asyncio
import logging
from typing import Callable, Optional, Union

import discord
from discord.ext import commands, tasks
from pyparsing import ParseResults

class ContextBase(commands.Context):
	channel: discord.TextChannel
	guild: Optional[discord.Guild]
	author: Union[discord.User, discord.Member]

	_error_was_handled: bool = False

	@property
	def error_was_handled(self) -> bool:
		return self._error_was_handled

	def stop_error_propagation(self):
		if not self.command_failed:
			return
		self._error_was_handled = True

	async def reply(self, content: Optional[str], **kwargs):
		if self.channel.type != discord.ChannelType.private:
			content = f'{self.author.mention} {content}'
		return await self.send(content, **kwargs)

class Bot(commands.Bot):
	config: ParseResults

	# @overwrite
	def __init__(self, config: ParseResults, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.config = config

	# @overwrite
	async def on_ready(self):
		logging.info(f'Logged in as {self.user}')

		self.dispatch('after_ready')

	# @overwrite
	def get_context(self, message: discord.Message):
		return super().get_context(message, cls=Context)

	# @overwrite
	async def on_command_error(self, ctx: ContextBase, ex: BaseException):
		if ctx.error_was_handled:
			return

		if isinstance(ex, commands.MissingRequiredArgument):
			await ctx.send(str(ex))
			return
		elif isinstance(ex, commands.NotOwner):
			await ctx.message.add_reaction('⛔')
			return
		elif isinstance(ex, (commands.CommandNotFound, commands.NoPrivateMessage)):
			return

		logging.exception(ex, exc_info=ex)
		await ctx.message.add_reaction('🥴')

	def add_temporary_listener(self, event: str, callback: Callable[..., None], *, check: Optional[Callable[..., bool]] = None, timeout: Optional[float] = None):
		if check is None:
			def _check(*args):
				return True
			check = _check
		ev_name = event if event.startswith('on_') else ('on_' + event)

		remove_listener = lambda: self.remove_listener(handle_callback, name=ev_name)

		timer = None
		async def handle_callback(*args):
			if check(*args):
				if timer is not None:
					timer.cancel()
				remove_listener()
				await discord.utils.maybe_coroutine(callback, *args)

		if timeout is not None:
			async def handle_timeout():
				await asyncio.sleep(timeout)
				if not asyncio.current_task().cancelled():
					remove_listener()

			timer = asyncio.get_event_loop().create_task(handle_timeout())

		self.add_listener(handle_callback, name=ev_name)

class Context(ContextBase):
	bot: Bot
