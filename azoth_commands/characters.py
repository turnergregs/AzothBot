import os
import json
import nextcord
from nextcord.ext import commands
from nextcord import SlashOption, Interaction
from azoth_commands.helpers import safe_interaction
from azoth_commands.autocomplete import autocomplete_from_table
from constants import DEV_GUILD_ID, BOT_PLAYER_ID
from supabase_helpers import fetch_all, update_record

TABLE_NAME = "characters"
MODEL_NAME = "character"


def add_character_commands(cls):

	@nextcord.slash_command(name="create_character", description="Create a new character.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=15, error_message="❌ Failed to create character.", require_authorized=True)
	async def create_character_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Character name"),
		text: str = SlashOption(description="Character rules text"),
		r: int = SlashOption(description="Red (0–255)", min_value=0, max_value=255),
		g: int = SlashOption(description="Green (0–255)", min_value=0, max_value=255),
		b: int = SlashOption(description="Blue (0–255)", min_value=0, max_value=255)
	):
		from supabase_helpers import create_record

		create_data = {
			"name": name,
			"text": text,
			"color": {"r": r, "g": g, "b": b},
			"created_by": BOT_PLAYER_ID,
			"actions": [],
			"triggers": [],
			"properties": [],
		}

		created = create_record(TABLE_NAME, create_data)
		if not created:
			return f"❌ Failed to create {MODEL_NAME}."

		created_record = created[0]

		return f"✅ Created `{name}`:\n```json\n{json.dumps(created_record, indent=2)}\n```"


	@nextcord.slash_command(name="update_character", description="Update fields on an existing character.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=10, error_message="❌ Failed to update character.", require_authorized=True)
	async def update_character_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Name of the character to update", autocomplete=True),
		new_name: str = SlashOption(description="New character name", required=False),
		text: str = SlashOption(description="New rules text", required=False),
		r: int = SlashOption(description="Red (0–255)", min_value=0, max_value=255, required=False),
		g: int = SlashOption(description="Green (0–255)", min_value=0, max_value=255, required=False),
		b: int = SlashOption(description="Blue (0–255)", min_value=0, max_value=255, required=False)
	):

		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		record = matches[0]
		update_data = {}

		if new_name: update_data["name"] = new_name
		if text: update_data["text"] = text
		if any(v is not None for v in (r, g, b)):
		    update_color = record.get("color", {})
		    if r is not None: update_color["r"] = r
		    if g is not None: update_color["g"] = g
		    if b is not None: update_color["b"] = b
		    update_data["color"] = update_color

		record = record | update_data

		result = update_record(TABLE_NAME, record["id"], update_data)
		if not result:
			return f"❌ Failed to update {MODEL_NAME} `{name}`."

		return f"✅ Updated `{name}`:\n```json\n{json.dumps(result[0], indent=2)}\n```"


	@nextcord.slash_command(name="get_character", description="Get character details.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to get character.")
	async def get_character_cmd(self, interaction: Interaction, name: str):
		
		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		record = matches[0]
		record_json = json.dumps(record, indent=2)
		return f"```json\n{record_json}\n```"


	@nextcord.slash_command(name="delete_character", description="Delete a character.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to delete character.", require_authorized=True)
	async def delete_character_cmd(self, interaction: Interaction, name: str):
		from supabase_helpers import soft_delete_record

		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ No {MODEL_NAME} found with name `{name}`."

		record = matches[0]
		success = soft_delete_record(TABLE_NAME, record["id"])
		if not success:
			return f"❌ Failed to delete {MODEL_NAME} `{name}`."

		return f"🗑️ Deleted {MODEL_NAME} `{name}`."


	# Autocomplete Helpers


	@update_character_cmd.on_autocomplete("name")
	@delete_character_cmd.on_autocomplete("name")
	@get_character_cmd.on_autocomplete("name")
	async def autocomplete_character_name(self, interaction: Interaction, input: str):
		from azoth_commands.autocomplete import autocomplete_from_table
		matches = autocomplete_from_table(TABLE_NAME, input)
		await interaction.response.send_autocomplete(matches[:25])


	cls.create_character_cmd = create_character_cmd
	cls.update_character_cmd = update_character_cmd
	cls.get_character_cmd 	 = get_character_cmd
	cls.delete_character_cmd = delete_character_cmd
