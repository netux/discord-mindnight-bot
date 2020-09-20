import asyncio
import logging
from collections import namedtuple
from dataclasses import dataclass
from enum import Enum
from random import choice, randint
from typing import Any, Callable, Coroutine, Dict, Iterable, List, Optional, Set, Tuple, Union

import discord
from discord.ext import commands

from bot import Bot, Context
from lazy_search import LazyMemberConverter
from util import MultipleExceptions, PrettyRepr, async_noop, cancel_task, emote_url, fmt_list, fmt_plural, fmt_time, get_channel_link, make_base_embed, wait

DEBUG = None

ACCEPT_SYNONYMS = ('accept', 'acc', 'yes', 'confirm', 'approve', 'secure')
REJECT_SYNONYMS = ('reject', 'rej', 'refuse', 'ref', 'no', 'deny', 'disapprove', 'hack')
UNSET_STR = '🔷'
UNKNOWN_STR = '?'
VOTED_ACCEPT_STR = 'Y'
VOTED_REJECT_STR = 'N'
AGENT_COLOR = discord.Color(0x78FB72)
HACKER_COLOR = discord.Color(0xDB1034)

logger = logging.getLogger('game')

class Emotes:
	agent = '🕵'
	hacker = '👨‍💻'
	tick = '✅'
	cross = '❎'
	secure_button = '✔'
	hack_button = '❌'
	confirm_button = '🆗'
	skip_phase_button = '⏩'
	random = '🎲'

# NOTE(netux): debug users for testing in the lonelyness
class FakeUser(discord.Object, PrettyRepr):
	name: str = 'FakeUser'
	nick: str = 'FakeUser'

	async def request_input(self):
		await async_noop()
		res = input(f'[from {self}]: ')
		await async_noop()
		return res

	async def send(self, content: Optional[str] = '', *args, **kwargs):
		await async_noop()
		print(f'[to {self}]: {content} ({args}, {kwargs})')
		msg = FakeMessage(randint(0, 100000000), content, self, **kwargs)
		await async_noop()
		return msg

	@property
	def mention(self) -> str:
		return f'<@{self.id}>'

	def __str__(self) -> str:
		return f'FakeUser#{self.id:0>4}'

class FakeMessage(discord.Object, PrettyRepr):
	author: FakeUser
	content: str

	def __init__(self, id: int, author: FakeUser, content: Optional[str] = None, **kwargs):
		super().__init__(id)
		self.author = author
		self._edit(content, **kwargs)

	def _edit(self, content: Optional[str] = None, **kwargs):
		self.content = content
		for k, v in kwargs.items():
			setattr(self, k, v)

	async def edit(self, content: Optional[str] = None, **kwargs):
		self._edit(content, **kwargs)

	async def add_reaction(self, *args, **kwargs):
		# noop
		pass


class GameState(Enum):
	from enum import auto

	LOBBY = auto()
	RUNNING = auto()
	ENDED = auto()

class GameError(Exception):
	pass

class RoundPhase(Enum):
	from enum import auto

	TALKING = auto()
	SELECT = auto()
	VOTING = auto()
	MISSION = auto()

class PlayerRole(Enum):
	from enum import auto

	AGENT = auto()
	HACKER = auto()

class GamePlayer(PrettyRepr):
	user: discord.User
	role: Optional[PlayerRole]

	def __init__(self, user: discord.User, role: Optional[PlayerRole] = None):
		self.user = user
		self.role = role

	async def send(self, *args, reactions: Dict = None, **kwargs):
		msg = await self.user.send(*args, **kwargs)
		if reactions is None:
			return msg
		if isinstance(self.user, (discord.User, discord.Member)):
			for em in reactions['emotes']:
				await msg.add_reaction(em)
			check = lambda r, u: u.id == self.user.id and r.message.id == msg.id and r.emoji in reactions['emotes']
			(r, _) = await reactions['bot'].wait_for('reaction_add', check=check)
			return (msg, r.emoji)
		elif DEBUG and isinstance(self.user, FakeUser):
			while True:
				inp = await self.user.request_input()
				for i, terms in enumerate(reactions['terms']):
					if inp.lower() in terms:
						return (msg, reactions['emotes'][i])

		return None

	def __str__(self) -> str:
		return self.user.__str__()

	def __eq__(self, o: object) -> bool:
		if isinstance(o, GamePlayer):
			o = o.user
		try:
			return self.user.id == o.id
		except AttributeError:
			return False

	def __hash__(self) -> int:
		return self.user.__hash__()

class TeamMember(PrettyRepr):
	player: GamePlayer
	has_hacked: bool

	def __init__(self, player: GamePlayer) -> None:
		self.player = player
		self.has_hacked = None

	def set_hacked(self, hacked: bool) -> None:
		self.has_hacked = hacked

	def __str__(self) -> str:
		return self.player.__str__()

	def __eq__(self, o: object) -> bool:
		if isinstance(o, TeamMember):
			o = o.player
		if not isinstance(o, GamePlayer):
			raise NotImplementedError(f'cannot compare {o.__class__.__name__} with {self.__class__.__name__}')
		return self.player.__eq__(o)

	def __hash__(self) -> int:
		return self.player.__hash__()

RoundVotes = namedtuple('RoundVotes', ('accepted', 'rejected'))

class MindnightRound(PrettyRepr):
	phase: RoundPhase = None
	proposer: GamePlayer = None
	team: Set[TeamMember] = None
	votes: RoundVotes = None

	_team_rejected: bool = None
	_voting_attempts: int = 0
	_node_hacked: bool = None
	_hacks_detected: int = 0

	@property
	def team_rejected(self) -> bool:
		return self._team_rejected

	@property
	def voting_attempts(self) -> int:
		return self._voting_attempts

	@property
	def node_hacked(self) -> bool:
		return self._node_hacked

	@property
	def hacks_detected(self) -> int:
		return self._hacks_detected

	def talking_phase(self):
		self.phase = RoundPhase.TALKING

	def select_phase(self, proposer: GamePlayer):
		self.phase = RoundPhase.SELECT
		self.proposer = proposer
		self.team = set()
		self._team_rejected = None

	def voting_phase(self):
		self.phase = RoundPhase.VOTING
		self.votes = RoundVotes(set(), set())

	def increase_voting_attempts(self):
		self._voting_attempts += 1

	def force_team_rejected(self, team_rejected: bool):
		self._team_rejected = team_rejected
		if self._team_rejected == True:
			self.increase_voting_attempts()
		return self._team_rejected

	def update_team_rejected(self):
		return self.force_team_rejected(len(self.votes.rejected) >= len(self.votes.accepted))

	def mission_phase(self):
		self.phase = RoundPhase.MISSION

	def force_node_hacked(self, node_hacked: bool):
		self._node_hacked = node_hacked
		return self._node_hacked

	def update_node_hacked(self, hacks_needed: int):
		hacks_detected = 0
		for member in self.team:
			if member.has_hacked is not None and member.has_hacked:
				hacks_detected += 1
		self._hacks_detected = hacks_detected
		return self.force_node_hacked(hacks_detected >= hacks_needed)

@dataclass
class GameInfo:
	node_pick: Tuple[int, int, int, int, int]
	hackers: int
	node_hacks: Tuple[int, int, int, int, int] = tuple(1 for _ in range(5))

	def get_pick_count_for_node(self, node_idx: int):
		return self.node_pick[node_idx]

	def get_hacks_count_for_node(self, node_idx: int):
		return self.node_hacks[node_idx]

INFO = {
	5: GameInfo((2, 3, 2, 3, 3), hackers=2),
	6: GameInfo((2, 3, 4, 3, 4), hackers=2),
	7: GameInfo((2, 3, 3, 4, 4), hackers=3, node_hacks=(1, 1, 1, 2, 1)),
	8: GameInfo((3, 4, 4, 5, 5), hackers=3, node_hacks=(1, 1, 1, 2, 1)),
	9: GameInfo((3, 4, 4, 5, 5), hackers=3, node_hacks=(1, 1, 1, 2, 1)),
	10: GameInfo((3, 4, 4, 5, 5), hackers=4, node_hacks=(1, 1, 1, 2, 1))
}
MIN_PLAYER_COUNT = min(INFO.keys())
MAX_PLAYER_COUNT = max(INFO.keys())
MAX_VOTING_ATTEMPTS = 5

class MindnightGame(PrettyRepr):
	channel: discord.TextChannel
	players: List[GamePlayer]
	nodes_hacked: List[bool]

	state: GameState = GameState.LOBBY
	info: GameInfo = None
	rounds: Tuple[Optional[MindnightRound], Optional[MindnightRound], Optional[MindnightRound], Optional[MindnightRound], Optional[MindnightRound]]
	round_idx: Optional[int] = None

	_bot: Bot = None
	_timer_task: asyncio.Task = None
	_phase_task: asyncio.Task = None
	_confirm_team_waiting_task: asyncio.Task = None
	_last_send_channel_message: discord.Message = None

	def __init__(self, bot: Bot, channel: discord.TextChannel):
		self.channel = channel
		self.players = []

		self.rounds = [None] * 5

		self._bot = bot

	@property
	def round(self) -> MindnightRound:
		return self.rounds[self.round_idx] if self.round_idx is not None else None

	def add_player(self, user: discord.User):
		if user.id in map(lambda p: p.user.id, self.players):
			raise GameError('player already in the game')

		if len(self.players) == MAX_PLAYER_COUNT:
			raise GameError('there are already enough players in the game')

		self.players.append(GamePlayer(user))

	def remove_player(self, user: discord.User):
		players2 = []
		for player in self.players:
			if player.user.id == user.id:
				continue
			players2.append(player)

		if len(self.players) == len(players2):
			raise GameError('player not in the game')

		self.players = players2

	def _handle_phase_task_done(self, fut: asyncio.Future):
		try:
			ex = fut.exception()
		except asyncio.CancelledError:
			pass
		else:
			if ex is not None:
				asyncio.create_task(self._abrupt_end(exc_info=ex))

	def _set_phase_task(self, coro: Coroutine):
		cancel_task(self._phase_task)
		self._phase_task = asyncio.create_task(coro)
		self._phase_task.add_done_callback(self._handle_phase_task_done)

	def _cancel_tasks(self):
		cancel_task(self._timer_task)
		cancel_task(self._phase_task)
		cancel_task(self._confirm_team_waiting_task)

	def _make_return_embed(self, *args, description: Optional[str] = None, **kwargs):
		if description is not None:
			description += '\n\n'
		else:
			description = ''
		description += f'**[return to the game]({get_channel_link(self.channel)})**'
		return make_base_embed(*args, **kwargs, description=description)

	def _typing(self):
		return discord.abc.Typing(self.channel)

	async def _send(self, *args, **kwargs):
		msg = await self.channel.send(*args, **kwargs)
		self._last_send_channel_message = msg
		return msg

	def _start_timer(self, *, time: int, callback: Callable[[int], Any], send_at: List[int]):
		async def make_coro():
			nonlocal time, callback, send_at
			seconds_left = time
			send_at = sorted(filter(lambda s: s <= time, send_at), reverse=True)
			i = 0
			while seconds_left > 0:
				s = send_at[i] if i < len(send_at) else 0

				delta = seconds_left - s
				await asyncio.sleep(delta)

				seconds_left = s if s != 0 else seconds_left - delta
				if seconds_left > 0:
					await discord.utils.maybe_coroutine(callback, seconds_left)

				i += 1

		self._timer_task = asyncio.create_task(make_coro())
		return self._timer_task

	async def _run_all_check_forbidden(self, tasks: Iterable[Union[asyncio.Task, asyncio.Future]]):
		done, _ = await wait(tasks, raise_on_exception=False, cancel_pending=False)
		def has_task_failed_with_forbidden(task: asyncio.Task):
			try:
				ex = task.exception()
				if ex is not None:
					if MultipleExceptions.get_first(ex, discord.Forbidden) is not None:
						return True
					else:
						raise ex
			except asyncio.CancelledError:
				pass

			return False

		return set((task for task in done if has_task_failed_with_forbidden(task)))

	async def start(self):
		if self.state != GameState.LOBBY:
			raise GameError('game must be in LOBBY state to start')

		if len(self.players) < MIN_PLAYER_COUNT:
			raise GameError(f'must have at least {MIN_PLAYER_COUNT} players to start the game')

		try:
			self.state = GameState.RUNNING
			self.info = INFO[len(self.players)]

			hackers = []
			for _ in range(self.hacker_count):
				while True:
					p = choice(self.players)
					if p.role is None:
						p.role = PlayerRole.HACKER
						hackers.append(p)
						break

			async def send_role(player: GamePlayer):
				if player.role is None:
					player.role = PlayerRole.AGENT
					await player.send(
						'You are Agent.',
						embed=self._make_return_embed(
							description=f'You are Agent.',
							color=AGENT_COLOR
						).set_thumbnail(url=emote_url(Emotes.agent))
					)
				elif player.role == PlayerRole.HACKER:
					await player.send('You are Hacker.',
						embed=self._make_return_embed(
							description=f'You are hacker.\nYour fellow hackers are {fmt_list(str(p2) for p2 in hackers if p2.user.id != player.user.id)}.',
							color=HACKER_COLOR
						).set_thumbnail(url=emote_url(Emotes.hacker))
					)

			def wrap_send_role(player: GamePlayer):
				fut = asyncio.ensure_future(send_role(player))
				fut.payload = player
				return fut

			async with self._typing():
				failed = await self._run_all_check_forbidden(map(wrap_send_role, self.players))
				if len(failed) > 0:
					await self._end_with_cant_dm((fut.payload for fut in failed))
					return

		except Exception as ex:
			await self._abrupt_end(exc_info=ex)
		else:
			self._set_phase_task(self.next_round())

	async def next_round(self):
		self.round_idx = 0 if self.round_idx is None else self.round_idx + 1
		self.rounds[self.round_idx] = MindnightRound()

		phase_func = self.select_phase if self.round_idx == 0 else self.talking_phase
		self._set_phase_task(phase_func())

	async def talking_phase(self):
		self.round.talking_phase()

		msg = await self._send(embed=make_state_embed(self))
		await msg.add_reaction(Emotes.skip_phase_button)

		player_ids = set(p.user.id for p in self.players if not isinstance(p.user, FakeUser))
		has_enough_votes = lambda r, _: r.message.id != msg.id or r.emoji != Emotes.skip_phase_button or r.count - 1 >= len(player_ids)

		async def wait_for_all_votes():
			while True:
				reaction, _ = await self._bot.wait_for('reaction_add', check=has_enough_votes)
				users = map(lambda u: u.id, await reaction.users().flatten())
				if player_ids.issubset(users):
					break
			await self._send(f'{Emotes.skip_phase_button} Talking phase skipped.')

		async with self._typing():
			await wait((
				wait_for_all_votes(),
				self._start_timer(
					time=30 + 15 * self.round_idx + 1,
					callback=lambda seconds_left: self._send(f'⌛ {fmt_time(seconds_left)} left.'),
					send_at=(30, 10)
				)
			), return_when=asyncio.FIRST_COMPLETED)

		self._set_phase_task(self.select_phase())

	async def select_phase(self, *, skip: bool = False):
		cancel_task(self._confirm_team_waiting_task)

		last_proposer = self.rounds[self.round_idx - 1].proposer if self.round_idx != 0 and self.round.voting_attempts == 0 and not skip else self.round.proposer
		proposer = self.round.proposer
		while True:
			if self.round_idx == 0 and self.round.voting_attempts == 0 and self.round.proposer is None:
				proposer = choice(self.players)
			else:
				proposer = self.players[(self.players.index(last_proposer) + 1) % len(self.players)]

			if proposer == last_proposer:
				continue

			break


		self.round.select_phase(proposer)

		TIME_SECONDS = 60
		await self._send(
			'\n'.join((
				f'**{self.round.proposer.user.mention} IS PROPOSING**.',
				f'Use `{self._bot.get_first_prefix()}pick <users...>` to select **{self.picks_for_current_node}** players to complete the mission.',
				f'Then use `{self._bot.get_first_prefix()}confirm` to confirm your selection and start the voting phase.',
				'',
				f'You all have {fmt_time(TIME_SECONDS)} to decide.'
			)),
			embed=make_base_embed()
				.add_field(
					name='Required team size',
					value=self.picks_for_current_node
				)
				.add_field(
					name='Time left',
					value=fmt_time(TIME_SECONDS)
				)
		)

		async with self._typing():
			await self._start_timer(
				time=TIME_SECONDS,
				callback=lambda seconds_left: self._send(f'⌛ {fmt_time(seconds_left)} left...'),
				send_at=(30, 10)
			)

		await self._send(f'{self.round.proposer.user} took too long to assemble a team.',
			embed=make_base_embed(
				title='Select Phase',
				description='**Picking a random team**'
			).set_thumbnail(url=emote_url(Emotes.random))
		)
		self.round.team.clear()
		while len(self.round.team) < self.picks_for_current_node:
			new_member = TeamMember(choice(self.players))
			self.round.team.add(new_member)
		await self.confirm_team()

	def _create_confirm_team_task(self, msg: discord.Message):
		async def make_coro():
			nonlocal msg
			check = lambda r, u: u.id == self.round.proposer.user.id and r.message.id == msg.id and r.emoji == Emotes.confirm_button
			await self._bot.wait_for('reaction_add', check=check)
			await self.confirm_team()
		self._confirm_team_waiting_task = asyncio.create_task(make_coro())
		return self._confirm_team_waiting_task

	async def set_team(self, members: Iterable[GamePlayer]):
		if self.round is None:
			return

		members = set(map(lambda p: TeamMember(p), members))
		if len(members) > self.picks_for_current_node:
			raise GameError(f'too many players given. Must pick exactly {self.picks_for_current_node} players.')

		diff_txt_arr = []
		added = members - self.round.team
		removed = set(m for m in self.round.team if m not in members)
		if len(added) > 0:
			diff_txt_arr.append('added ' +  ', '.join(str(m) for m in added if m not in removed))
		if len(removed) > 0:
			diff_txt_arr.append('removed ' +  ', '.join(str(m) for m in removed if m not in added))

		self.round.team = members

		msg = await self._send('Updated team:\n' + '\n'.join(diff_txt_arr),
			embed=make_state_embed(self)
		)
		cancel_task(self._confirm_team_waiting_task)
		if len(self.round.team) == self.picks_for_current_node:
			await msg.add_reaction(Emotes.confirm_button)
			self._create_confirm_team_task(msg)

	async def skip_team_composition(self):
		if self.state != GameState.RUNNING:
			raise GameError('game is not running')

		if self.round.phase != RoundPhase.SELECT:
			raise GameError('game is not on SELECT phase')

		await self._send(f'{self.round.proposer} has skipped being the proposer.')
		self._set_phase_task(self.select_phase(skip=True))

	async def confirm_team(self):
		if self.state != GameState.RUNNING:
			raise GameError('game is not running')

		if self.round.phase != RoundPhase.SELECT:
			raise GameError('game is not on SELECT phase')

		if len(self.round.team) < self.picks_for_current_node:
			raise GameError(f'team size is lower than needed. Need {self.picks_for_current_node}, got {len(self.round.team)}')

		self._cancel_tasks()
		self._set_phase_task(self.voting_phase())

	async def voting_phase(self):
		self.round.voting_phase()

		async def ask_for_vote(player: GamePlayer):
			try:
				(msg, resp_emote) = await player.send(
					embed=make_base_embed(
						title='Voting time!',
						description=f'React with 👍 to accept {self.round.proposer}\'s team, or 👎 to reject it.'
					).add_field(
						name='Team',
						value=fmt_list(self.round.team),
						inline=False
					),
					reactions=dict(emotes=['👍', '👎'], terms=[ACCEPT_SYNONYMS, REJECT_SYNONYMS], bot=self._bot)
				)
			except discord.Forbidden:
				await self._end_with_cant_dm([player])
			else:
				self.player_vote(player, resp_emote == '👍')
				await msg.edit(embed=self._make_return_embed())

		async with self._typing():
			await wait((
				wait(
					map(ask_for_vote, self.players),
					return_when=asyncio.FIRST_EXCEPTION
				),
				self._start_timer(
					time=60,
					callback=lambda seconds_left: self._send(f'⌛ {fmt_time(seconds_left)} left...', embed=make_state_embed(self)),
					send_at=(60, 30, 10)
				)
			), return_when=asyncio.FIRST_COMPLETED)

		self.round.update_team_rejected()

		if self.round.team_rejected:
			await self._send(
				f'**TEAM REJECTED**\n{self.round.voting_attempts}/{MAX_VOTING_ATTEMPTS} teams rejected.',
				embed=make_state_embed(self)
			)
			if self.round.voting_attempts >= MAX_VOTING_ATTEMPTS:
				await self.end(PlayerRole.HACKER,
					reason=f'Too many teams have been rejected ({MAX_VOTING_ATTEMPTS}).'
				)
				return
			else:
				self._set_phase_task(self.select_phase())
		else:
			await self._send('**TEAM ACCEPTED!**', embed=make_state_embed(self))
			self._set_phase_task(self.mission_phase())

	def has_voted(self, player: GamePlayer) -> bool:
		if self.round is None or self.round.votes is None:
			return False
		return player in self.round.votes.accepted or player in self.round.votes.rejected

	def player_vote(self, player: GamePlayer, accept: bool):
		if self.has_voted(player):
			raise GameError('already voted')

		if accept:
			self.round.votes.accepted.add(player)
		else:
			self.round.votes.rejected.add(player)

	async def mission_phase(self):
		self.round.mission_phase()

		async def ask_for_action(member: TeamMember):
			emotes = [Emotes.secure_button]
			terms = [ACCEPT_SYNONYMS]
			instructions = f'React with {Emotes.secure_button} to secure'
			if member.player.role == PlayerRole.HACKER:
				emotes.append(Emotes.hack_button)
				terms.append(REJECT_SYNONYMS)
				instructions += f' or {Emotes.hack_button} to hack'
			instructions += ' the node.'
			try:
				(msg, resp_emote) = await member.player.send(
					embed=make_base_embed(
						description=instructions
					).add_field(
						name='Team',
						value=fmt_list(self.round.team),
						inline=False
					),
					reactions=dict(emotes=emotes, terms=terms, bot=self._bot)
				)
			except discord.Forbidden:
				await self._end_with_cant_dm([member.player])
			else:
				member.set_hacked(resp_emote == Emotes.hack_button)
				await msg.edit(embed=self._make_return_embed())

		async with self._typing():
			timer_fut = asyncio.ensure_future(self._start_timer(
				time=15,
				callback=lambda seconds_left: self._send(f'⌛ {fmt_time(seconds_left)} left...', embed=make_state_embed(self)),
				send_at=(15,)
			))

			(done, _) = await wait((
				wait(
					map(ask_for_action, self.round.team),
					return_when=asyncio.FIRST_EXCEPTION
				),
				timer_fut
			), return_when=asyncio.FIRST_COMPLETED)

			if timer_fut in done:
				# TODO(netux): confirm that this is what happens in the real game
				for member in self.round.team:
					if member.has_hacked is None:
						member.set_hacked(False)

		self.round.update_node_hacked(self.hacks_for_current_node)
		await self._send(
			embed=make_base_embed(
				title='Mission Phase',
				description='\n\n'.join((
					f'**Node {self.round_idx + 1} hacked**.' if self.round.node_hacked else f'**Node {self.round_idx + 1} secured**',
					f'{self.round.hacks_detected} hacks detected between ' + fmt_list(self.round.team)
				)),
				color=HACKER_COLOR if self.round.node_hacked else AGENT_COLOR
			).set_thumbnail(
				url=emote_url(Emotes.hacker if self.round.node_hacked else Emotes.agent)
			)
		)

		nodes_hacked = list(map(lambda rnd: rnd.node_hacked if rnd is not None else None, self.rounds))
		if nodes_hacked.count(True) >= 3:
			await self.end(PlayerRole.HACKER, reason='3 nodes were hacked')
			return
		elif nodes_hacked.count(False) >= 3:
			await self.end(PlayerRole.AGENT, reason='3 nodes were secured')
			return

		async with self._typing():
			await asyncio.sleep(15)
		self._set_phase_task(self.next_round())

	async def end(self, winner: PlayerRole = None, *, reason: str = None):
		self.state = GameState.ENDED
		self._cancel_tasks()

		await self._send(
			'\n'.join(filter(lambda s: s is not None, (
				'Mindnight game finished, ' + ('nobody wins' if winner is None else (f'{Emotes.hacker} Hackers win' if winner == PlayerRole.HACKER else f'{Emotes.agent} Agents win')) + '.',
				reason
			))),
			embed=make_state_embed(self, color=discord.Embed.Empty if winner is None else (HACKER_COLOR if winner == PlayerRole.HACKER else AGENT_COLOR))
		)

	async def _end_with_cant_dm(self, players: List[GamePlayer]):
		return await self.end(reason='**' + ' '.join((
			f'I can\'t DM {fmt_list(players)}.',
			'Please make sure you haven\'t blocked me, and that you have enabled "Allow direct messages from server members." on your Privacy Settings for this server, then try again.'
		)) + '**')

	async def _abrupt_end(self, *, exc_info: Exception = None):
		if exc_info is not None:
			logger.exception(f'Abruptly ended game {self}', exc_info=exc_info)

		try:
			await self._send('Game ended due to unexpected error.')
		except Exception:
			pass

		try:
			await self.end()
		except Exception:
			# Force game state to end so that channel doesn't get stuck in crashed game running.
			self.state = GameState.ENDED

	@property
	def hacker_count(self):
		return self.info.hackers

	@property
	def picks_for_current_node(self):
		return self.info.get_pick_count_for_node(self.round_idx)

	@property
	def hacks_for_current_node(self):
		return self.info.get_hacks_count_for_node(self.round_idx)

def make_state_embed(game, *args, **kwargs):
	game: MindnightGame = game

	embed = make_base_embed(*args, **kwargs)
	if game.state == GameState.LOBBY:
		def fmt_player(idx: int, p: GamePlayer):
			ret = str(p)
			if idx == 0:
				ret += ' (host)'
			return ret
		embed.description = 'Waiting for host to start game'
		embed.add_field(
			name='Players',
			value='\n'.join(map(lambda t: fmt_player(*t), enumerate(game.players))),
			inline=False
		)
	if game.state == GameState.RUNNING:
		phase_name_txt = '???'
		fmt_player_with_proposer = lambda p: f'**{p}**' if p == game.round.proposer else str(p)
		fmt_player_with_team = lambda p: ('➜ ' if p in game.round.team else '') + fmt_player_with_proposer(p)
		if game.round.phase == RoundPhase.TALKING:
			phase_name_txt = 'Talking'
			embed.description = 'All players are discussing their reads.'
			embed.add_field(
				name='Players',
				value='\n'.join(map(str, game.players)),
				inline=False
			)
			embed.set_footer(text=f'React with {Emotes.skip_phase_button} to vote skip.')
		if game.round.phase == RoundPhase.SELECT:
			phase_name_txt = 'Select'
			embed.description = f'{game.round.proposer.user} is selecting players to complete the node.'
			embed.add_field(
				name='Players',
				value='\n'.join(map(fmt_player_with_team, game.players)),
				inline=False
			)
			embed.add_field(
				name='Team assembled',
				value=f'{len(game.round.team)}/{game.picks_for_current_node}',
				inline=False
			)
			embed.set_footer(text=f'Remember to run `{game._bot.get_first_prefix()}confirm` to begin the voting phase.')
		elif game.round.phase == RoundPhase.VOTING:
			phase_name_txt = 'Voting'
			embed.description = f'All players are voting on the team assembled by {game.round.proposer}.'
			if game.round.team_rejected is None:
				def fmt_player(p: GamePlayer):
					ret = fmt_player_with_team(p)
					if not game.has_voted(p):
						ret += ' (waiting)'
					return ret
				embed.add_field(
					name='Players',
					value='\n'.join(map(fmt_player, game.players)),
					inline=False
				)
				embed.set_footer(text='Check your DMs to vote.')
			else:
				fmt_vote_list = lambda l: ', '.join(map(str, l)) if len(l) > 0 else '<no one>'
				embed.add_field(
					name='Votes',
					value='\n'.join((
						f'{Emotes.tick} ' + fmt_vote_list(game.round.votes.accepted),
						f'{Emotes.cross} ' + fmt_vote_list(game.round.votes.rejected)
					)),
					inline=False
				)
		elif game.round.phase == RoundPhase.MISSION:
			phase_name_txt = 'Mission'
			embed.description = f'Waiting for team assembled to capture the node.'
			def fmt_team(member: TeamMember):
				ret = str(member)
				if member.has_hacked is None:
					ret += ' (waiting)'
				return ret
			embed.add_field(
				name='Team',
				value='\n'.join(map(fmt_team, game.round.team)),
				inline=False
			)

		embed.title += f' - {phase_name_txt} Phase'
		if game.round.voting_attempts > 0:
			embed.add_field(
				name='Teams rejected',
				value=f'{game.round.voting_attempts}/{MAX_VOTING_ATTEMPTS}',
				inline=True
			)
	elif game.state == GameState.ENDED:
		embed.description = 'Game has ended, thanks for playing!'
		hackers = list(str(p) for p in game.players if p.role == PlayerRole.HACKER)
		if len(hackers) > 0:
			embed.add_field(
				name='Hackers',
				value=', '.join(hackers),
				inline=False
			)

	return embed


class MindnightCog(commands.Cog, name='Game'):
	games: Dict[int, MindnightGame] # Channel ID -> Mindnight Game

	def __init__(self):
		self.games = dict()

	# @overwrite
	async def cog_check(self, ctx: Context):
		return commands.guild_only()(ctx)

	# @overwrite
	async def cog_command_error(self, ctx: Context, ex: Exception):
		if isinstance(ex, commands.CommandInvokeError):
			ex = ex.original

		if isinstance(ex, GameError):
			await ctx.reply(str(ex) + '.')
			ctx.stop_error_propagation()

	@commands.command(
		name='join',
		description='Join (or create) a Mindnight game in the channel',
		brief='Join/create game'
	)
	async def mindnight__join(self, ctx: Context):
		game: MindnightGame = self.games.get(ctx.channel.id, None)
		create_new_game = game is None or game.state == GameState.ENDED
		if create_new_game:
			game = MindnightGame(ctx.bot, ctx.channel)
			self.games[ctx.channel.id] = game

		game.add_player(ctx.author)
		await ctx.reply('Mindnight game ' + ('created' if create_new_game else 'joined') + '.',
			embed=make_base_embed(
				title='Game created',
				description=f'Use `{ctx.bot.get_first_prefix()}join` to join.'
			) if create_new_game else None
		)

		if DEBUG and (player_count := DEBUG.get('player_count', None)) is not None:
			debug_to_add = player_count - len(game.players)
			for _ in range(debug_to_add):
				game.add_player(FakeUser(randint(0, 10000)))
			if debug_to_add > 0:
				await ctx.reply(f'Added {debug_to_add} debug FakeUsers.')

	@commands.command(
		name='leave',
		description='Leave the game on this channel. If the game is already running, it forcefully ends.',
		brief='Leave game'
	)
	async def mindnight__leave(self, ctx: Context):
		game: MindnightGame = self.games.get(ctx.channel.id, None)
		if game is None or game.state == GameState.ENDED:
			await ctx.reply('no Mindnight game to leave.')
			return

		game.remove_player(ctx.author)
		await ctx.reply('removed from Mindnight game.')

		out_of_human_players = len(list(filter(lambda p: not isinstance(p.user, FakeUser), game.players))) == 0
		if game.state != GameState.LOBBY or out_of_human_players:
			await game.end(
				reason='All players left.' if out_of_human_players else 'A player left in the middle of the game.'
			)
			del self.games[ctx.channel.id]

	def get_game_maybe(self, ctx: Context) -> MindnightGame:
		game: MindnightGame = self.games.get(ctx.channel.id, None)
		if game is None:
			raise GameError(f'no Mindnight game on this channel. Use `{ctx.prefix}join` to create a game.')
		return game

	@commands.command(
		name='start',
		description='Start the game on this channel. Can only be used by the user that first joined the game.',
		brief='Start the game'
	)
	async def mindnight__start(self, ctx: Context):
		game = self.get_game_maybe(ctx)

		if game.players[0].user.id != ctx.author.id:
			await ctx.reply(f'only {game.players[0].user} can start the Mindnight game.')
			return

		if game.state == GameState.RUNNING:
			await ctx.reply('Mindnight game already started in this channel.')
			return

		await game.start()
		if game.state == GameState.ENDED:
			# game failed to start, ended abruptly.
			return

		player_mentions = ' '.join(map(lambda p: p.user.mention, game.players))
		hackers_txt = f'{game.info.hackers} ' + fmt_plural(game.info.hackers, 'hacker')
		await ctx.send(f'{player_mentions} Mindnight game started.\nThere are {hackers_txt} among you.')

	@commands.command(
		name='pick',
		aliases=['select', 'choose'],
		description='Pick people for a team. You must be the proposer to pick a team.',
		brief='Pick people for a team.',
		usage='[pick_user_1 pick_user_2 ...] or [-remove_user_1 -remove_user_2 ...]'
	)
	async def mindnight__pick(self, ctx: Context, *users: str):
		game = self.get_game_maybe(ctx)

		if game.state != GameState.RUNNING:
			await ctx.reply('Mindnight game is not in progress.')
			return

		if game.round.phase != RoundPhase.SELECT:
			await ctx.reply('not in select phase.')
			return

		if game.round.proposer.user.id != ctx.author.id:
			await ctx.reply('only the proposer can pick.')
			return

		new_team: List[TeamMember] = set(map(lambda m: m.player, game.round.team))
		for query in users:
			delete = False
			if query.startswith('-'):
				delete = True
				query = query[1:]
			elif query.startswith('+'):
				query = query[1:]

			user = LazyMemberConverter.find(map(lambda p: p.user, game.players), query)
			if user is None:
				await ctx.reply(f'couldn\'t find player "{query}"')
				return

			player = discord.utils.find(lambda p: p.user.id == user.id, game.players)

			if delete:
				try:
					new_team.remove(player)
				except KeyError:
					pass
			else:
				new_team.add(player)

		await game.set_team(new_team)

	@commands.command(
		name='pass',
		aliases=['skip'],
		description='If you are the proposer, passes proposer to the next player.',
		brief='Pass proposer to the next player'
	)
	async def mindnight__pass(self, ctx: Context):
		game = self.get_game_maybe(ctx)

		if game.state != GameState.RUNNING:
			await ctx.reply('Mindnight game is not in progress.')
			return

		if game.round.phase != RoundPhase.SELECT:
			await ctx.reply('not in select phase.')
			return

		if game.round.proposer.user.id != ctx.author.id:
			await ctx.reply('only the proposer can do this.')
			return

		await game.skip_team_composition()

	@commands.command(
		name='confirm',
		description='If you are proposer, confirms your team and starts Voting Phase.',
		brief='Confirmed proposed team'
	)
	async def mindnight__confirm(self, ctx: Context):
		game = self.get_game_maybe(ctx)

		if game.state != GameState.RUNNING:
			await ctx.reply('Mindnight game is not in progress.')
			return

		if game.round.phase != RoundPhase.SELECT:
			await ctx.reply('not in select phase.')
			return

		if game.round.proposer.user.id != ctx.author.id:
			await ctx.reply('only the proposer can do this.')
			return

		await game.confirm_team()

	@commands.command(
		name='state',
		aliases=['status', 'game', 'board'],
		description='\n'.join((
			'Show information about the state of the game in the channel.',
			'This information is also given periodically thoughout the match.'
		)),
		brief='Show information about the state of the game'
	)
	async def mindnight__state(self, ctx: Context):
		game = self.get_game_maybe(ctx)
		await ctx.send(embed=make_state_embed(game))

	@commands.command(
		name='votes',
		aliases=['vote'],
		description='Shows information on each player\'s votes on the team compositions of previous nodes.',
		brief='Show information on each player\'s votes'
	)
	async def mindnight__votes(self, ctx: Context):
		game = self.get_game_maybe(ctx)

		if game.state == GameState.LOBBY:
			await ctx.reply('Mindnight game is not in progress.')
			return

		longer_username_length = int(max(map(lambda p: len(str(p.user)), game.players)))
		def fmt_row(player: GamePlayer):
			username = str(player.user)
			def fmt_vote(round_idx: int, rnd: MindnightRound):
				if rnd is None or round_idx > game.round_idx or round_idx == game.round_idx and rnd.phase.value <= RoundPhase.VOTING.value:
					return UNKNOWN_STR
				else:
					if player in rnd.votes.accepted:
						return VOTED_ACCEPT_STR
					elif player in rnd.votes.rejected:
						return VOTED_REJECT_STR
					else:
						return UNKNOWN_STR
			return username + (' ' * (longer_username_length - len(username))) + ' ' + ' '.join(map(lambda t: fmt_vote(*t), enumerate(game.rounds)))

		await ctx.send(embed=make_base_embed(
			title='Votes',
			description='```' + '\n'.join((
				(' ' * longer_username_length) + ' ' + ' '.join(map(lambda i: str(i + 1), range(len(game.rounds)))),
				*map(fmt_row, game.players)
			)) + '```'
		))

	@commands.command(
		name='nodes',
		aliases=['node', 'objectives', 'objective', 'missions', 'mission', 'rounds', 'round'],
		description='Shows information about the nodes: the team, who proposed it, and whenever it was compromised.',
		brief='Show information about the nodes'
	)
	async def mindnight__nodes(self, ctx: Context):
		game = self.get_game_maybe(ctx)

		if game.state == GameState.LOBBY:
			await ctx.reply('Mindnight game is not in progress.')
			return

		def fmt_round(idx: int, rnd: MindnightRound):
			node_hacked = rnd.node_hacked if rnd is not None else None
			picks_for_node = game.info.get_pick_count_for_node(idx)
			hacks_for_node = game.info.get_hacks_count_for_node(idx)

			ret = UNSET_STR if node_hacked is None else str(Emotes.hacker if node_hacked else Emotes.agent)
			ret += f' **Node {idx + 1}**'
			ret += f' [{picks_for_node} ' + fmt_plural(picks_for_node, 'player') + '/'
			ret += f'{hacks_for_node} ' + fmt_plural(hacks_for_node, 'hack') + ']'
			if rnd is not None:
				if rnd.proposer is not None:
					ret += f'\n➜ Proposer: {rnd.proposer}'
				if rnd.phase.value > RoundPhase.SELECT.value:
					ret += '\n➜ Team: ' + ', '.join(map(str, rnd.team))
					if rnd.hacks_detected is not None and rnd.hacks_detected > 0:
						ret += f' ({rnd.hacks_detected} ' + fmt_plural(rnd.hacks_detected, 'hack') + ')'
			return ret

		await ctx.send(
			embed=make_base_embed(
				title='Nodes',
				description='\n'.join(map(lambda t: fmt_round(*t), enumerate(game.rounds)))
			)
		)

	@commands.command(
		name='debug',
		aliases=['d'],
		description='🤫',
		hidden=True
	)
	@commands.is_owner()
	async def mindnight__debug(self, ctx: Context, p_idx: Union[int, str], cmd: str, *args):
		if not DEBUG:
			return

		game = self.get_game_maybe(ctx)

		if isinstance(p_idx, str):
			if p_idx == 'p':
				user = game.round.proposer.user
			else:
				return
		else:
			user = game.players[p_idx].user

		fake_msg = FakeMessage(1, user, f'{ctx.prefix}{cmd}' + ((' ' + ' '.join(args)) if len(args) > 0 else ''))
		fake_msg.channel = ctx.channel
		fake_msg._state = ctx.message._state
		ctx2: Context = await ctx.bot.get_context(fake_msg)
		await ctx.bot.invoke(ctx2)

def setup(bot: Bot):
	def get_emotes():
		for config_name in filter(lambda k: not k.startswith('_'), Emotes.__dict__.keys()):
			id = bot.config.get('emotes.' + config_name, None)
			if id is None or not isinstance(id, int):
				continue

			emote = discord.utils.get(bot.emojis, id=id)
			if emote is not None:
				setattr(Emotes, config_name, emote)
			else:
				logger.warn(''.join((
					f'Emote {config_name} with ID {id} not found on internal cache.',
					' Make sure the bot belongs to the guild where the emote was added.'
				)))

	if not bot.is_ready():
		bot.add_temporary_listener('ready', get_emotes)
	else:
		get_emotes()

	if bot.config.get('debug.enabled', False):
		global DEBUG
		DEBUG = dict(bot.config.get('debug'))

	bot.add_cog(MindnightCog())
