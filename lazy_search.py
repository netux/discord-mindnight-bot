import re
import logging
from typing import List, Optional, Union

import discord
from discord.ext import commands


USER_MENTION_REGEX = re.compile(r'<@!?(?P<id>\d+)>')

class LazyMemberConverter(commands.Converter):
	@staticmethod
	def find(users: List[Union[discord.User, discord.Member]], query: str) -> Optional[Union[discord.User, discord.Member]]:
		query = query.lower()
		result = None
		try:
			uid = int(query)
			result = discord.utils.get(users, id=uid)
		except ValueError:
			if (mention_match := USER_MENTION_REGEX.match(query)) is not None and mention_match.end() == len(query):
				result = discord.utils.get(users, id=int(mention_match.group('id')))
			else:
				result_str = None
				result_match_type = None
				for user in users:
					match_str = None
					match_type = None

					if query in (match_str := user.name.lower()):
						match_type = 'name'
					elif user.nick is not None and query in (match_str := user.nick.lower()):
						match_type = 'nick'
					else:
						continue

					# prioritize:
					#  1. shorter matches over longer matches
					#  2. matches at the start over matches somewhere else
					#  3. username matches over nickname matches
					if result is None \
						or (match_type == result_match_type and len(match_str) < len(result_str)) \
						or (match_str.startswith(query) and not result_str.startswith(query)) \
						or (match_type == 'name' and result_match_type == 'nick'):
						result = user
						result_str = match_str
						result_match_type = match_type

					if query == match_str:
						break
		return result

	async def convert(self, ctx, arg):
		try:
			result = LazyMemberConverter.find(ctx.guild.members, arg)
		except Exception as ex:
			logging.exception(ex, exc_info=ex)
			result = None
		if result is None:
			raise commands.BadArgument(f'No user with ID or name "{arg}" was found')

		return result
