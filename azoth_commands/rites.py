import asyncio
import io
import nextcord
from nextcord import SlashOption, Interaction
from azoth_commands.helpers import safe_interaction, generate_and_upload_image, record_to_json, to_snake_case
from azoth_commands.autocomplete import autocomplete_from_table
from constants import DEV_GUILD_ID, BOT_PLAYER_ID, ASSET_BUCKET_NAMES
from supabase_helpers import fetch_all, update_record
from azoth_logic import content_index, fate_render

# "Rite" is the current name for what the database still calls an "event".
#
# THE BOUNDARY: everything user-facing and every new identifier says `rite`;
# anything that names a database table, a `content_type` value, or a Storage
# bucket stays `event`, because renaming those means a migration. DB_KEY marks
# each place the old name is load-bearing.
#
# When the tables are eventually renamed, DB_KEY and TABLE_NAME are the two
# constants to change.
TABLE_NAME = "events"       # DB_KEY
DB_KEY = "event"            # DB_KEY: content_type, bucket and asset-path key
MODEL_NAME = "rite"         # what users see

# A rite's `image` feeds the DRAFT THUMBNAIL, not the card face -- event_card.tscn
# ships its Image node hidden. So this bucket is written to on create/update and
# never read back by the renderer. Renders stream from memory; no directories.
bucket = ASSET_BUCKET_NAMES[DB_KEY]

def add_rite_commands(cls):

	@nextcord.slash_command(name="create_rite", description="Create a new rite.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=15, error_message="❌ Failed to create event.", require_authorized=True)
	async def create_rite_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Rite name"),
		text: str = SlashOption(description="Rite rules text"),
		foresight: int = SlashOption(description="Fate Foresight"),
		deck: str = SlashOption(description="Optional deck to add this rite to", required=False, autocomplete=True),
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
		# A new item must be autocompletable now, not after the index TTL.
		content_index.invalidate()
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

		# Rendering runs PIL/numpy over the background mask -- blocking work, so it
		# goes off the event loop.
		try:
			data, ext = await asyncio.to_thread(fate_render.render, created_record, "rite")
		except Exception as e:
			return f"✅ Created `{name}`, but could not render it: {e}"

		await interaction.followup.send(
			content=f"✅ Created `{name}` successfully!",
			file=nextcord.File(io.BytesIO(data), filename=f"{to_snake_case(name)}.{ext}"))

		return None


	@nextcord.slash_command(name="update_rite", description="Update fields on an existing rite.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=10, error_message="❌ Failed to update event.", require_authorized=True)
	async def update_rite_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Name of the rite to update", autocomplete=True),
		new_name: str = SlashOption(description="New rite name", required=False),
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

		# A rename changes what /get, /render and /search autocomplete on, and it
		# also changes which BACKGROUND the rite draws -- event_card.gd picks the
		# material by display name (fate_layout.RITE_BACKGROUND_BY_NAME).
		if new_name:
			content_index.invalidate()

		if regenerate_image:
			try:
				data, ext = await asyncio.to_thread(fate_render.render, record, "rite")
			except Exception as e:
				return f"✅ Updated `{name}`, but could not render it: {e}"

			# The regenerated art is the DRAFT THUMBNAIL; the card face below is
			# the rite's background pattern and does not include it.
			await interaction.followup.send(
				content=f"✅ Updated `{name}` and regenerated its draft art!",
				file=nextcord.File(io.BytesIO(data),
								   filename=f"{to_snake_case(new_name or name)}.{ext}"))
			return None

		return f"✅ Updated `{name}`:\n```json\n{record_to_json(result[0])}\n```"


	@nextcord.slash_command(name="delete_rite", description="Delete a rite.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to delete event.", require_authorized=True)
	async def delete_rite_cmd(self, interaction: Interaction, name: str):
		from supabase_helpers import delete_record

		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ No {MODEL_NAME} found with name `{name}`."

		record = matches[0]
		success = delete_record(TABLE_NAME, record["id"])
		content_index.invalidate()
		if not success:
			return f"❌ Failed to delete {MODEL_NAME} `{name}`."

		return f"🗑️ Deleted {MODEL_NAME} `{name}`."


	# Autocomplete Helpers

	@update_rite_cmd.on_autocomplete("name")
	@delete_rite_cmd.on_autocomplete("name")
	async def autocomplete_rite_name(self, interaction: Interaction, input: str):
		from azoth_commands.autocomplete import autocomplete_from_table
		matches = autocomplete_from_table(TABLE_NAME, input)
		await interaction.response.send_autocomplete(matches[:25])


	@create_rite_cmd.on_autocomplete("deck")
	async def autocomplete_fate_decks(self, interaction: Interaction, input: str):
		from azoth_commands.autocomplete import autocomplete_from_table

		suggestions = autocomplete_from_table(
			table_name="decks",
			input=input,
			column="name",
			filters={"content_type": "fates"}
		)

		await interaction.response.send_autocomplete(suggestions[:25])


	cls.create_rite_cmd = create_rite_cmd
	cls.update_rite_cmd = update_rite_cmd
	cls.delete_rite_cmd = delete_rite_cmd
