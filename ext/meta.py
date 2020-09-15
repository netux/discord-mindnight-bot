from typing import List
from discord.ext import commands

from bot import Bot, Context
from util import make_base_embed

class MetaCog(commands.Cog, name='Meta'):
	def __init__(self, bot: Bot):
		bot.help_command = None

	def _fmt_command(self, ctx: Context, cmd: commands.Command):
		return f'`{ctx.prefix}{cmd.qualified_name}` - {cmd.help or cmd.brief or cmd.description.splitlines()[0]}'

	def _fmt_commands_help(self, ctx: Context, cmds: List[commands.Command]):
		return '\n'.join(map(lambda cmd: self._fmt_command(ctx, cmd), filter(lambda cmd: not cmd.hidden, cmds)))

	@commands.group(
		name='help',
		description='Show help',
		invoke_without_command=True
	)
	async def mindnight__help(self, ctx: Context):
		embed = make_base_embed(title='Help')
		embed.add_field(
			name='**Subcommands**',
			value=self._fmt_commands_help(ctx, ctx.command.walk_commands()),
			inline=False
		)

		support_invite = ctx.bot.config.get('bot.support_invite', None)
		if support_invite is not None:
			embed.add_field(
				name='Support Server',
				value=support_invite,
				inline=False
			)
		await ctx.send(embed=embed)

	@mindnight__help.command(
		name='commands',
		aliases=['command', 'usage', 'cmds', 'cmd'],
		brief='Display available commands and what they do.'
	)
	async def mindnight__help__commands(self, ctx: Context):
		game_cog = ctx.bot.get_cog('Game')
		await ctx.send(embed=make_base_embed(
			title='Game Commands',
			description=self._fmt_commands_help(ctx, game_cog.walk_commands())
		))

	# TODO(netux): make glossary command (meta, protocol)

	@mindnight__help.command(
		name='howtoplay',
		aliases=['htp', '?'],
		brief='Information about how to play Mindnight.'
	)
	async def mindnight__help__howtoplay(self, ctx: Context):
		await ctx.send('https://youtu.be/MoSxtK-pPnQ')
		await ctx.send(
			embed=make_base_embed(
				title='How to Play',
				description='\n'.join((
					'**It is recommended that you watch the video above before joining a game, as it explains the basics.**',
					'',
					f'To join or create a game in the current channel, use `{ctx.prefix}join`.',
					f'After gathering 5 to 10 people, the creator of the game can run `{ctx.prefix}start` to start the game.'
				))
			)
		)

	@mindnight__help.command(
		name='info',
		aliases=['information', 'inspiration'],
		brief='Information about the bot and the inspiration behind it.'
	)
	async def mindnight__help__info(self, ctx: Context):
		await ctx.send(
			embed=make_base_embed(
				title='Information & Inspiration',
				description='\n'.join((
					'This bot is a recreation of the video game Mindnight as a Discord bot. This bot is also loosely based off Rastimal\'s UNO bot.',
					'',
					'The original game is free on Steam and worth checking out!'
				))
			).add_field(
				name='Source Code',
				value='https://github.com/netux/discord-mindnight-bot',
				inline=False
			)
		)
		await ctx.send('https://store.steampowered.com/app/667870/MINDNIGHT/')

def setup(bot: Bot):
	bot.add_cog(MetaCog(bot))
