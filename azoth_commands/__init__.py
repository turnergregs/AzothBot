from nextcord.ext import commands

# Import command modules
from .decks import add_deck_commands
from .cards import add_card_commands
from .aspects import add_aspect_commands
from .heroes import add_hero_commands
from .events import add_event_commands
from .misc import add_misc_commands


class AzothCommands(commands.Cog):
	def __init__(self, bot):
		self.bot = bot

# Attach commands to the Cog
add_deck_commands(AzothCommands)
add_card_commands(AzothCommands)
add_aspect_commands(AzothCommands)
add_hero_commands(AzothCommands)
add_event_commands(AzothCommands)
add_misc_commands(AzothCommands)
