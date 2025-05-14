from nextcord.ext import commands

# Import command modules
from .characters import add_character_commands
from .decks import add_deck_commands
from .cards import add_card_commands
from .rituals import add_ritual_commands
from .events import add_event_commands
from .consumables import add_consumable_commands


class AzothCommands(commands.Cog):
	def __init__(self, bot):
		self.bot = bot

# Attach commands to the Cog
add_character_commands(AzothCommands)
add_deck_commands(AzothCommands)
add_card_commands(AzothCommands)
add_ritual_commands(AzothCommands)
add_event_commands(AzothCommands)
add_consumable_commands(AzothCommands)
