from nextcord.ext import commands

# Import command modules
from .decks import add_deck_commands
from .cards import add_card_commands
from .content import add_content_commands
from .aspects import add_aspect_commands
from .rites import add_rite_commands
from .search import add_search_commands
from .cache import add_cache_commands
from .stats import add_stats_commands
from .misc import add_misc_commands
from .daily_update import add_daily_update_commands


class AzothCommands(commands.Cog):
	def __init__(self, bot):
		self.bot = bot

# Attach commands to the Cog.
#
# `heroes.py` is DELIBERATELY not attached (2026-08-26). Its commands are
# retired, not broken -- do not "fix" this by adding it back the way you would
# for a module that was left out by accident. Hero cards also never got ported
# to the new renderer, so /render_hero would draw the wrong frame.
add_deck_commands(AzothCommands)
add_card_commands(AzothCommands)
add_content_commands(AzothCommands)
add_aspect_commands(AzothCommands)
add_rite_commands(AzothCommands)
add_search_commands(AzothCommands)
add_cache_commands(AzothCommands)
add_stats_commands(AzothCommands)
add_misc_commands(AzothCommands)
add_daily_update_commands(AzothCommands)
