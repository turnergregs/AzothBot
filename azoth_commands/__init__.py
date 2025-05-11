from nextcord.ext import commands

# Import command modules
from .cards import add_card_commands
from .decks import add_deck_commands
from .rituals import add_ritual_commands

class AzothCommands(commands.Cog):
	def __init__(self, bot):
		self.bot = bot

# Attach commands to the Cog
add_card_commands(AzothCommands)
add_deck_commands(AzothCommands)
add_ritual_commands(AzothCommands)
