import os
import json
import nextcord
from nextcord.ext import commands
from nextcord import SlashOption, Interaction
from azoth_commands.helpers import safe_interaction, generate_and_upload_image, record_to_json, to_snake_case
from azoth_commands.autocomplete import autocomplete_from_table
from constants import DEV_GUILD_ID, BOT_PLAYER_ID, ASSET_RENDER_PATHS, ASSET_BUCKET_NAMES, ASSET_DOWNLOAD_PATHS
from supabase_helpers import fetch_all, update_record
from supabase_storage import download_image

from azoth_logic.ritual_renderer import RitualRenderer
renderer = RitualRenderer()

TABLE_NAME = "events"
MODEL_NAME = "event"

bucket = ASSET_BUCKET_NAMES[MODEL_NAME]
render_dir = ASSET_RENDER_PATHS[MODEL_NAME]
download_dir = ASSET_DOWNLOAD_PATHS[MODEL_NAME]

def add_event_commands(cls):

	@nextcord.slash_command(name="create_event", description="Create a new event.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=15, error_message="❌ Failed to create event.", require_authorized=True)
	async def create_event_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Event name"),
		text: str = SlashOption(description="Event rules text"),
		foresight: int = SlashOption(description="Fate Foresight"),
		deck: str = SlashOption(description="Optional deck to add this event to", required=False, autocomplete=True),
		quantity: int = SlashOption(description="Number of copies to add to deck", required=False, default=1),
	):
		from supabase_helpers import create_record, add_to_deck

		create_data = {
			"name": name,
			"text": text,
			"foresight": foresight,
			"created_by": BOT_PLAYER_ID,
			"actions": [],
			"triggers": [],
			"properties": [],
		}

		created = create_record(TABLE_NAME, create_data)
		if not created:
			return f"❌ Failed to create {MODEL_NAME}."

		created_record = created[0]

		# Optionally add to deck
		if deck:
			matches = fetch_all("decks", filters={"name": deck})
			if len(matches) == 0:
				return f"✅ Created `{name}`, but could not find deck named `{deck}`."

			deck = matches[0]

			success, result = add_to_deck(deck, name, quantity)
			if not success:
				return f"✅ Created `{name}`, but could not add to deck named `{deck}`:\n{result}."

		# Generate and upload image
		upload_success, file_path = generate_and_upload_image(created_record, bucket)
		if not upload_success:
			return f"✅ Created `{name}`, but failed to upload image:\n{file_path}"

		# Update Supabase record with image path
		update_result = update_record(TABLE_NAME, created_record["id"], {"image": file_path})
		if update_result:
			created_record["image"] = file_path

		# Download image for local rendering
		download_success, image_local_path = download_image(file_path, bucket, download_dir)
		if not download_success:
			return f"✅ Created `{name}`, but failed to retrieve image:\n{image_local_path}"

		# Render and send
		created_record["fate_type"] = MODEL_NAME
		render_path = renderer.render_fate(created_record, output_dir=render_dir)
		await interaction.followup.send(
			content=f"✅ Created `{name}` successfully!",
			file=nextcord.File(render_path)
		)

		return None


	@nextcord.slash_command(name="update_event", description="Update fields on an existing event.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=10, error_message="❌ Failed to update event.", require_authorized=True)
	async def update_event_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Name of the event to update", autocomplete=True),
		new_name: str = SlashOption(description="New event name", required=False),
		text: str = SlashOption(description="New rules text", required=False),
		foresight: int = SlashOption(description="New foresight", required=False),
		regenerate_image: bool = SlashOption(description="Regenerate the image?", required=False, default=False),
	):

		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		record = matches[0]
		update_data = {}

		if new_name: update_data["name"] = new_name
		if text: update_data["text"] = text
		if foresight: update_data["foresight"] = foresight

		# Apply update fields for rendering
		record = record | update_data

		# Optional image regeneration
		if regenerate_image:
			upload_success, file_path = generate_and_upload_image(record, bucket)
			if not upload_success:
				return f"✅ Updated `{name}`, but failed to upload image: `{file_path}`"
			update_data["image"] = file_path

		# Save updates to database
		result = update_record(TABLE_NAME, record["id"], update_data)
		if not result:
			return f"❌ Failed to update {MODEL_NAME} `{name}`."

		final_name = new_name if new_name else name
		snake_name = to_snake_case(final_name)
		render_path = f"{render_dir}/{snake_name}.png"

		# Delete the cached rendered image if it exists
		if os.path.exists(render_path):
			try:
				render_path.unlink()
				print(f"Deleted cached render: {render_path}")
			except Exception as e:
				print(f"Warning: Could not delete cached render for {final_name}: {e}")

		# Optional re-download + render
		if regenerate_image:
			download_success, local_path = download_image(file_path, bucket, download_dir)
			if download_success:
				record["fate_type"] = MODEL_NAME
				render_path = renderer.render_fate(record, output_dir=render_dir)
				await interaction.followup.send(
					content=f"✅ Updated `{name}` and regenerated image!",
					file=nextcord.File(render_path)
				)
				return None

		return f"✅ Updated `{name}`:\n```json\n{record_to_json(result[0])}\n```"


	@nextcord.slash_command(name="get_event", description="Get event details.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to get event.")
	async def get_event_cmd(self, interaction: Interaction, name: str):
		
		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		record = matches[0]
		return f"```json\n{record_to_json(record)}\n```"


	@nextcord.slash_command(name="delete_event", description="Delete an event.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to delete event.", require_authorized=True)
	async def delete_event_cmd(self, interaction: Interaction, name: str):
		from supabase_helpers import delete_record

		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ No {MODEL_NAME} found with name `{name}`."

		record = matches[0]
		success = delete_record(TABLE_NAME, record["id"])
		if not success:
			return f"❌ Failed to delete {MODEL_NAME} `{name}`."

		return f"🗑️ Deleted {MODEL_NAME} `{name}`."


	@nextcord.slash_command(name="render_event", description="Render an event and return the image.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=10, error_message="❌ Failed to render event.")
	async def render_event_cmd(self, interaction: Interaction, name: str = SlashOption(description="Event name", autocomplete=True)):
		
		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		record = matches[0]

		# Download the art from Supabase
		image_success, image_result = download_image(record["image"], bucket, download_dir)
		if not image_success:
			return f"⚠️ Could not load image for `{name}`:\n{image_result}"

		record["fate_type"] = MODEL_NAME
		render_path = renderer.render_fate(record)
		await interaction.followup.send(file=nextcord.File(render_path))


	# Autocomplete Helpers

	@update_event_cmd.on_autocomplete("name")
	@delete_event_cmd.on_autocomplete("name")
	@get_event_cmd.on_autocomplete("name")
	@render_event_cmd.on_autocomplete("name")
	async def autocomplete_event_name(self, interaction: Interaction, input: str):
		from azoth_commands.autocomplete import autocomplete_from_table
		matches = autocomplete_from_table(TABLE_NAME, input)
		await interaction.response.send_autocomplete(matches[:25])


	@create_event_cmd.on_autocomplete("deck")
	async def autocomplete_fate_decks(self, interaction: Interaction, input: str):
		from azoth_commands.autocomplete import autocomplete_from_table

		suggestions = autocomplete_from_table(
			table_name="decks",
			input=input,
			column="name",
			filters={"content_type": "fates"}
		)

		await interaction.response.send_autocomplete(suggestions[:25])


	cls.create_event_cmd = create_event_cmd
	cls.update_event_cmd = update_event_cmd
	cls.get_event_cmd 	= get_event_cmd
	cls.delete_event_cmd = delete_event_cmd
	cls.render_event_cmd = render_event_cmd
