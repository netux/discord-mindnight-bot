import logging

from pyhocon import ConfigFactory
from pyparsing import ParseResults

from bot import Bot


bot: Bot = None
config: ParseResults = None

if __name__ == '__main__':
	import sys

	config: ParseResults = ConfigFactory.parse_file('bot.conf')

	logging.basicConfig(format='%(asctime)s %(levelname)s %(name)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S', level=logging.INFO)

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
		case_insensitive=True
	)

	bot.load_extension('ext.meta')
	bot.load_extension('ext.game')

	bot.run(token)
