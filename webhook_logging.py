import logging
from datetime import datetime
from logging import PercentStyle
from typing import Optional

import discord
from discord import embeds
from discord import colour

DEFAULT_TITLE_FORMAT = '%(levelname)s:%(name)s'
DEFAULT_DESCRIPTION_FORMAT = '%(message)s'

LEVEL_TO_COLOR = {
	logging.CRITICAL: discord.Color.dark_red(),
	logging.ERROR: discord.Color.red(),
	logging.WARNING: discord.Color.gold(),
	logging.INFO: discord.Color.light_grey(),
	logging.DEBUG: discord.Color.blue(),
	logging.NOTSET: discord.Embed.Empty
}

class WebhookLogRecordFormatter(logging.Formatter):
	def __init__(self, titlefmt: Optional[str] = None, descriptionfmt: Optional[str] = None, datefmt: Optional[str] = None, validate: Optional[bool] = True) -> None:
		self._titlestyle = PercentStyle(titlefmt or DEFAULT_TITLE_FORMAT)
		self._descriptionstyle = PercentStyle(descriptionfmt or DEFAULT_DESCRIPTION_FORMAT)
		if validate:
			self._titlestyle.validate()
			self._descriptionstyle.validate()

		self.datefmt = datefmt

	def formatMessage(self, record: logging.LogRecord):
		return self._descriptionstyle.format(record)

	def formatEmbedTitle(self, record: logging.LogRecord):
		return discord.utils.escape_markdown(self._titlestyle.format(record))

	def formatEmbedDescription(self, record: logging.LogRecord):
		s = self.formatMessage(record)
		if record.exc_text:
			if s[-1:] != "\n":
				s = s + "\n"
			s = s + record.exc_text
		if record.stack_info:
			if s[-1:] != "\n":
				s = s + "\n"
			s = s + self.formatStack(record.stack_info)
		return s

	def formatEmbedColor(self, record: logging.LogRecord):
		return LEVEL_TO_COLOR[record.levelno]

	def format(self, record: logging.LogRecord) -> str:
		record.message = record.getMessage()
		if self._titlestyle.usesTime() or self._descriptionstyle.usesTime():
			record.asctime = self.formatTime(record, self.datefmt)
		if record.exc_info:
			# Cache the traceback text to avoid converting it multiple times
			# (it's constant anyway)
			if not record.exc_text:
				record.exc_text = self.formatException(record.exc_info)

		description = self.formatEmbedDescription(record)
		if len(description) > 1993:
			add = '...'
			if description[-1:] != '\n':
				add = add + '\n'
				description = description[:-1]
			description = description[:1993] + add
		description = discord.utils.escape_markdown(description, as_needed=True)
		description = f'```\n{description}```'

		return dict(
			embeds=[
				discord.Embed(
					title=self.formatEmbedTitle(record),
					description=description,
					colour=self.formatEmbedColor(record),
					timestamp=datetime.fromtimestamp(record.created)
				)
			]
		)

class WebhookLogHandler(logging.Handler):
	webhook: discord.Webhook

	def __init__(self, level = logging.NOTSET, *, id: Optional[int] = None, token: Optional[str] = None, url: Optional[str] = None) -> None:
		super().__init__(level)

		use_url = url is not None
		use_partial = id is not None and token is not None
		adapter = discord.RequestsWebhookAdapter()

		self.formatter = WebhookLogRecordFormatter()

		if use_url and not use_partial:
			self.webhook = discord.Webhook.from_url(url, adapter=adapter)
		elif use_partial and not use_url:
			self.webhook = discord.Webhook.partial(id, token, adapter=adapter)
		else:
			raise TypeError('must provide only one of either: id and token, or url')

	def emit(self, record: logging.LogRecord) -> None:
		kwargs = self.format(record)
		if isinstance(kwargs, str):
			content = kwargs
			kwargs = dict()
		else:
			content = kwargs.pop('content', None)

		self.webhook.send(content, **kwargs)

