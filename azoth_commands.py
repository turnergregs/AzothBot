import os
from dotenv import load_dotenv
import nextcord
from nextcord.ext import commands
from nextcord import Interaction, SlashOption
from supabase_client import (
	get_element_choices,
	get_attribute_choices,
	get_type_choices,
)
load_dotenv()
DEV_GUILD_ID = int(os.getenv("DEV_GUILD_ID"))
AUTHORIZED_USER_IDS = set(map(int, os.getenv("AUTHORIZED_USER_IDS", "").split(",")))
BOT_PLAYER_ID = int(os.getenv("BOT_PLAYER_ID"))

def autocomplete_from_choices(field: str, input: str) -> list[str]:
	lookup = {
		"element": get_element_choices,
		"attributes": get_attribute_choices,
		"type": get_type_choices,
	}
	choices_func = lookup.get(field)
	if not choices_func:
		return []
	choices = choices_func()
	return [label for label, value in choices if input.lower() in label.lower()]

class AzothCommands(commands.Cog):
	def __init__(self, bot):
		self.bot = bot

	# Card CRUD commands
	@nextcord.slash_command(name="create_card", description="Create a new card.", guild_ids=[DEV_GUILD_ID])
	async def create_card_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Card name"),
		type: str = SlashOption(description="Card type", autocomplete=True),
		valence: int = SlashOption(description="Card valence"),
		element: str = SlashOption(description="Element", autocomplete=True),
		text: str = SlashOption(description="Card rules text"),
		attributes: str = SlashOption(description="Attributes (comma-separated)", required=False),
		deck: str = SlashOption(description="Deck to assign this card to", required=False, autocomplete=True),
	):
		await interaction.response.defer()

		attr_list = [a.strip() for a in attributes.split(",")] if attributes else []

		card_data = {
			"name": name,
			"type": type,
			"valence": valence,
			"element": element,
			"text": text,
			"attributes": attr_list,
			"created_by": BOT_PLAYER_ID,
			"actions": [],
			"triggers": [],
			"properties": [],
		}
		if deck:
			card_data["deck"] = deck

		from azoth_logic import render_card_image
		from supabase_client import upload_card_image, create_card

		image_bytes = render_card_image(card_data)
		image_path = upload_card_image(name, image_bytes)
		card_data["image"] = image_path

		result = create_card(card_data)
		if result:
			await interaction.followup.send(f"✅ Card `{name}` created successfully.")
		else:
			await interaction.followup.send("❌ Failed to create card.")


	@nextcord.slash_command(name="update_card", description="Update fields on an existing card.", guild_ids=[DEV_GUILD_ID])
	async def update_card_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Name of the card to update", autocomplete=True),
		type: str = SlashOption(description="New type", required=False, autocomplete=True),
		valence: int = SlashOption(description="New valence", required=False),
		element: str = SlashOption(description="New element", required=False, autocomplete=True),
		text: str = SlashOption(description="New rules text", required=False),
		attributes: str = SlashOption(description="New attributes (comma-separated)", required=False),
	):
		await interaction.response.defer()

		from supabase_client import get_card_by_name, update_card_fields
		card = get_card_by_name(name)
		if not card:
			await interaction.followup.send("❌ Card not found.")
			return

		update_data = {}
		update_data["created_by"] = BOT_PLAYER_ID
		if type: update_data["type"] = type
		if valence is not None: update_data["valence"] = valence
		if element: update_data["element"] = element
		if text: update_data["text"] = text
		if attributes is not None:
			update_data["attributes"] = [a.strip() for a in attributes.split(",")]

		updated = update_card_fields(card["id"], update_data)
		if updated:
			await interaction.followup.send(f"✅ Updated `{name}`.")
		else:
			await interaction.followup.send("❌ Failed to update card.")


	@nextcord.slash_command(name="get_card", description="Get card details.", guild_ids=[DEV_GUILD_ID])
	async def get_card_cmd(self, interaction: Interaction, name: str):
		response = await get_card(name)
		await interaction.response.send_message(response)

	@nextcord.slash_command(name="delete_card", description="Delete a card.", guild_ids=[DEV_GUILD_ID])
	async def delete_card_cmd(self, interaction: Interaction, name: str):
		if interaction.user.id not in AUTHORIZED_USER_IDS:
			await interaction.response.send_message("❌ You’re not authorized to use this command.", ephemeral=True)
			return
		
		response = await delete_card(name)
		await interaction.response.send_message(response)

	@nextcord.slash_command(name="rename_card", description="Rename a card.", guild_ids=[DEV_GUILD_ID])
	async def rename_card_cmd(
		self,
		interaction: Interaction,
		old_name: str,
		new_name: str
	):
		if interaction.user.id not in AUTHORIZED_USER_IDS:
			await interaction.response.send_message("❌ You’re not authorized to use this command.", ephemeral=True)
			return
		
		response = await rename_card(old_name, new_name)
		await interaction.response.send_message(response)

	@nextcord.slash_command(name="render_card", description="Render card image.", guild_ids=[DEV_GUILD_ID])
	async def render_card_cmd(self, interaction: Interaction, name: str):
		image_path = await render_card(name)
		if image_path:
			await interaction.response.send_message(file=nextcord.File(image_path))
		else:
			await interaction.response.send_message("❌ Could not render card.")


	# Helpers
	@create_card_cmd.on_autocomplete("element")
	@update_card_cmd.on_autocomplete("element")
	async def autocomplete_element(self, interaction: Interaction, input: str):
		matches = autocomplete_from_choices("element", input)
		await interaction.response.send_autocomplete(matches[:25])

	@create_card_cmd.on_autocomplete("type")
	@update_card_cmd.on_autocomplete("type")
	async def autocomplete_type(self, interaction: Interaction, input: str):
		matches = autocomplete_from_choices("type", input)
		await interaction.response.send_autocomplete(matches[:25])

	@create_card_cmd.on_autocomplete("attributes")
	@update_card_cmd.on_autocomplete("attributes")
	async def autocomplete_attributes(self, interaction: Interaction, input: str):
		parts = [p.strip() for p in input.split(",")]
		last_part = parts[-1]
		matches = autocomplete_from_choices("attributes", last_part)
		# Prefix existing parts before final suggestion
		prefix = ", ".join(parts[:-1]) + ", " if len(parts) > 1 else ""
		suggestions = [prefix + match for match in matches]
		await interaction.response.send_autocomplete(suggestions[:25])
