import logging
from util import get_nested_or
from webhook_logging import WebhookLogHandler, WebhookLogRecordFormatter

from discord import AllowedMentions
from pyhocon import ConfigFactory
from pyparsing import ParseResults

from bot import Bot


bot: Bot = None
config: ParseResults = None

def setup_logging(**config):
	log_handlers = [logging.StreamHandler()]
	webhook_formatter = WebhookLogRecordFormatter(
		get_nested_or(config, 'webhook_format', 'title', default='%(levelname)s'),
		get_nested_or(config, 'webhook_format', 'description', default='%(message)s')
	)

	if (logging_file_path := config.get('file', None)) is not None:
		log_file_handler = logging.FileHandler(logging_file_path)
		log_file_handler.setLevel(logging.INFO)
		if 'error_file' in config:
			log_file_handler.addFilter(lambda r: r.levelno < logging.ERROR)

		log_handlers.append(log_file_handler)

	if (logging_webhook_url := config.get('webhook', None)) is not None:
		log_webhook_handler = WebhookLogHandler(url=logging_webhook_url)
		log_webhook_handler.setFormatter(webhook_formatter)
		if 'error_webhook' in config:
			log_webhook_handler.addFilter(lambda r: r.levelno < logging.ERROR)

		log_handlers.append(log_webhook_handler)

	if (logging_errorfile_path := config.get('error_file', None)) is not None:
		log_errorfile_handler = logging.FileHandler(logging_errorfile_path)
		log_errorfile_handler.setLevel(logging.ERROR)

		log_handlers.append(log_errorfile_handler)

	if (logging_errorwebhook_url := config.get('error_webhook', None)) is not None:
		log_errorwebhook_handler = WebhookLogHandler(url=logging_errorwebhook_url)
		log_errorwebhook_handler.setFormatter(webhook_formatter)
		log_errorwebhook_handler.setLevel(logging.ERROR)

		log_handlers.append(log_errorwebhook_handler)

	discord_logger = logging.getLogger('discord')
	discord_logger.setLevel(logging.ERROR)

	logging.basicConfig(
		format=config.get('format', '%(asctime)s %(levelname)s %(name)s: %(message)s'),
		datefmt='%Y-%m-%d %H:%M:%S',
		handlers=log_handlers,
		level=logging.INFO
	)

if __name__ == '__main__':
	import sys

	config: ParseResults = ConfigFactory.parse_file('bot.conf')

	setup_logging(**config.get('logging'))

	logging.info('Starting...')

	bot_config = config.get_config('bot')
	if not bot_config:
		logging.fatal('Missing bot section in config.')
		sys.exit(1)

	token = bot_config.get('token', None)
	if not token:
		logging.fatal('Missing bot.token in config.')
		sys.exit(1)

	bot = Bot(config,
		command_prefix=bot_config.get('command_prefix', ('mindnight ', 'Mindnight ', 'mindnight', 'Mindnight', 'mn ', 'mn', 'Mn ', 'Mn')),
		case_insensitive=True,
		allowed_mentions=AllowedMentions(everyone=False, roles=False)
	)

	bot.load_extension('ext.meta')
	bot.load_extension('ext.game')

	bot.run(token)
