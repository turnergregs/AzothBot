import os
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands
from nextcord import Interaction, SlashOption
from azoth_logic import (
	create_card,
	modify_card,
	get_card,
	delete_card,
	rename_card,
	render_card,
)
load_dotenv()
DEV_GUILD_ID = int(os.getenv("DEV_GUILD_ID"))

class AzothCommands(commands.Cog):
	def __init__(self, bot):
		self.bot = bot

	@nextcord.slash_command(name="create_card", description="Create a new card.", guild_ids=[DEV_GUILD_ID])
	async def create_card_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Card name"),
		valence: int = SlashOption(description="Card valence"),
		element: str = SlashOption(
			description="Card element",
			choices=[("Anima", "anima"), ("Blood", "blood"), ("Sol", "sol")]
		),
		type: str = SlashOption(description="Card type"),
		text: str = SlashOption(description="Card effect text")
	):
		response = await create_card(name, valence, element, type, text)
		await interaction.response.send_message(response)

	@nextcord.slash_command(name="modify_card", description="Modify a card field.", guild_ids=[DEV_GUILD_ID])
	async def modify_card_cmd(
		self,
		interaction: Interaction,
		name: str,
		field: str,
		value: str
	):
		response = await modify_card(name, field, value)
		await interaction.response.send_message(response)

	@nextcord.slash_command(name="get_card", description="Get card details.", guild_ids=[DEV_GUILD_ID])
	async def get_card_cmd(self, interaction: Interaction, name: str):
		response = await get_card(name)
		await interaction.response.send_message(response)

	@nextcord.slash_command(name="delete_card", description="Delete a card.", guild_ids=[DEV_GUILD_ID])
	async def delete_card_cmd(self, interaction: Interaction, name: str):
		response = await delete_card(name)
		await interaction.response.send_message(response)

	@nextcord.slash_command(name="rename_card", description="Rename a card.", guild_ids=[DEV_GUILD_ID])
	async def rename_card_cmd(
		self,
		interaction: Interaction,
		old_name: str,
		new_name: str
	):
		response = await rename_card(old_name, new_name)
		await interaction.response.send_message(response)

	@nextcord.slash_command(name="render_card", description="Render card image.", guild_ids=[DEV_GUILD_ID])
	async def render_card_cmd(self, interaction: Interaction, name: str):
		image_path = await render_card(name)
		if image_path:
			await interaction.response.send_message(file=nextcord.File(image_path))
		else:
			await interaction.response.send_message("❌ Could not render card.")
