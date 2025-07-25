import os
import json
import nextcord
from nextcord.ext import commands
from nextcord import SlashOption, Interaction
from azoth_commands.helpers import safe_interaction, generate_and_upload_image, record_to_json
from azoth_commands.autocomplete import autocomplete_from_table
from constants import DEV_GUILD_ID, BOT_PLAYER_ID, ASSET_RENDER_PATHS, ASSET_BUCKET_NAMES, ASSET_DOWNLOAD_PATHS
from supabase_helpers import fetch_all, update_record
from supabase_storage import download_image

from azoth_logic.card_renderer import CardRenderer
renderer = CardRenderer()

TABLE_NAME = "heroes"
MODEL_NAME = "hero"

bucket = ASSET_BUCKET_NAMES[MODEL_NAME]
render_dir = ASSET_RENDER_PATHS[MODEL_NAME]
download_dir = ASSET_DOWNLOAD_PATHS[MODEL_NAME]

def add_hero_commands(cls):

	@nextcord.slash_command(name="create_hero", description="Create a new hero.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=15, error_message="❌ Failed to create hero.", require_authorized=True)
	async def create_hero_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Hero name"),
		text: str = SlashOption(description="Hero power text"),
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

		# # Generate and upload image
		# upload_success, file_path = generate_and_upload_image(created_record, bucket)
		# if not upload_success:
		# 	return f"✅ Created `{name}`, but failed to upload image:\n{file_path}"

		# # Update Supabase record with image path
		# update_result = update_record(TABLE_NAME, created_record["id"], {"image": file_path})
		# if update_result:
		# 	created_record["image"] = file_path

		# # Download image for local rendering
		# download_success, image_local_path = download_image(file_path, bucket, download_dir)
		# if not download_success:
		# 	return f"✅ Created `{name}`, but failed to retrieve image:\n{image_local_path}"

		# # Render and send
		# render_path = renderer.render_card(created_record, output_dir=render_dir)
		# await interaction.followup.send(
		# 	content=f"✅ Created `{name}` successfully!",
		# 	file=nextcord.File(render_path)
		# )

		# return None

		return f"✅ Created `{name}`:\n```json\n{json.dumps(created_record, indent=2)}\n```"


	@nextcord.slash_command(name="update_hero", description="Update fields on an existing hero.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=10, error_message="❌ Failed to update hero.", require_authorized=True)
	async def update_hero_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Name of the hero to update", autocomplete=True),
		new_name: str = SlashOption(description="New hero name", required=False),
		text: str = SlashOption(description="New rules text", required=False),
		r: int = SlashOption(description="Red (0–255)", min_value=0, max_value=255, required=False),
		g: int = SlashOption(description="Green (0–255)", min_value=0, max_value=255, required=False),
		b: int = SlashOption(description="Blue (0–255)", min_value=0, max_value=255, required=False),
		regenerate_image: bool = SlashOption(description="Regenerate the image?", required=False, default=False),
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

		# Apply update fields for rendering
		record = record | update_data

		# # Optional image regeneration
		# if regenerate_image:
		# 	upload_success, file_path = generate_and_upload_image(record, bucket)
		# 	if not upload_success:
		# 		return f"✅ Updated `{name}`, but failed to upload image: `{file_path}`"
		# 	update_data["image"] = file_path

		# Save updates to database
		result = update_record(TABLE_NAME, record["id"], update_data)
		if not result:
			return f"❌ Failed to update {MODEL_NAME} `{name}`."

		# # Optional re-download + render
		# if regenerate_image:
		# 	download_success, local_path = download_image(file_path, bucket, download_dir)
		# 	if download_success:
		# 		render_path = renderer.render_card(record, output_dir=render_dir)
		# 		await interaction.followup.send(
		# 			content=f"✅ Updated `{name}` and regenerated image!",
		# 			file=nextcord.File(render_path)
		# 		)
		# 		return None

		return f"✅ Updated `{name}`:\n```json\n{record_to_json(result[0])}\n```"


	@nextcord.slash_command(name="get_hero", description="Get hero details.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to get hero.")
	async def get_hero_cmd(self, interaction: Interaction, name: str):
		
		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		record = matches[0]
		return f"```json\n{record_to_json(record)}\n```"


	@nextcord.slash_command(name="delete_hero", description="Delete a hero.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to delete hero.", require_authorized=True)
	async def delete_hero_cmd(self, interaction: Interaction, name: str):
		from supabase_helpers import soft_delete_record

		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ No {MODEL_NAME} found with name `{name}`."

		record = matches[0]
		success = soft_delete_record(TABLE_NAME, record["id"])
		if not success:
			return f"❌ Failed to delete {MODEL_NAME} `{name}`."

		return f"🗑️ Deleted {MODEL_NAME} `{name}`."


	@nextcord.slash_command(name="render_hero", description="Render a hero and return the image.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=10, error_message="❌ Failed to render hero.")
	async def render_hero_cmd(self, interaction: Interaction, name: str):
		
		return "⏰ Not supported yet, check again soon!"

		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		record = matches[0]

		# Download the art from Supabase
		image_success, image_result = download_image(record["image"], bucket, download_dir)
		if not image_success:
			return f"⚠️ Could not load image for `{name}`:\n{image_result}"

		render_path = renderer.render_card(record)
		await interaction.followup.send(file=nextcord.File(render_path))


	# Autocomplete Helpers


	@update_hero_cmd.on_autocomplete("name")
	@delete_hero_cmd.on_autocomplete("name")
	@get_hero_cmd.on_autocomplete("name")
	@render_hero_cmd.on_autocomplete("name")
	async def autocomplete_hero_name(self, interaction: Interaction, input: str):
		from azoth_commands.autocomplete import autocomplete_from_table
		matches = autocomplete_from_table(TABLE_NAME, input)
		await interaction.response.send_autocomplete(matches[:25])


	cls.create_hero_cmd = create_hero_cmd
	cls.update_hero_cmd = update_hero_cmd
	cls.get_hero_cmd 	 = get_hero_cmd
	cls.delete_hero_cmd = delete_hero_cmd
	cls.render_hero_cmd = render_hero_cmd
