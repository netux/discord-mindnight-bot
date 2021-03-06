import asyncio
import logging
from typing import Callable, Optional, Union

import discord
from discord.ext import commands
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

	@property
	def support_invite(self):
		return self.config.get('bot.support_invite', None)

	# @overwrite
	def __init__(self, config: ParseResults, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.config = config

		self.load_extension('ext.meta')
		self.load_extension('ext.game')

	# @overwrite
	async def close(self):
		try:
			logging.info('Ending running games...')

			reason = '**Bot shutting down**. Sorry for the inconvenience.'
			support_invite = self.support_invite
			if support_invite is not None:
				reason += f'\nJoin our support server to learn more about this incident: <{support_invite}>.'
			await self.get_cog('Game').end_all(reason=reason)
		except Exception as ex:
			logging.fatal('Cannot end running games', exc_info=ex)

		await super().close()

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

		try:
			if isinstance(ex, commands.MissingRequiredArgument):
				await ctx.send(str(ex))
				return
			elif isinstance(ex, commands.NotOwner):
				await ctx.message.add_reaction('⛔')
				return
			elif isinstance(ex, (commands.CommandNotFound, commands.NoPrivateMessage, commands.CommandOnCooldown)):
				return

			await ctx.message.add_reaction('🥴')
			logging.exception(ex, exc_info=ex)
		except discord.errors.DiscordException as ex2:
			ex2.__cause__ = ex
			logging.exception(ex2, exc_info=ex2)

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

	def get_first_prefix(self):
		if isinstance(self.command_prefix, str):
			return self.command_prefix
		elif isinstance(self.command_prefix, (tuple, list)):
			return self.command_prefix[0]
		else:
			raise Exception('cannot get a bot prefix without context')

class Context(ContextBase):
	bot: Bot
