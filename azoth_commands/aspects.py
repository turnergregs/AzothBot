import json
import nextcord
from nextcord import SlashOption, Interaction
from azoth_commands.helpers import safe_interaction, record_to_json
from azoth_commands.autocomplete import autocomplete_from_table
from constants import DEV_GUILD_ID, BOT_PLAYER_ID, ASSET_BUCKET_NAMES
from supabase_helpers import fetch_all, update_record
from azoth_logic import content_index

TABLE_NAME = "aspects"
MODEL_NAME = "aspect"

# Aspects take an EXISTING image name in this bucket rather than generating art
# (`create_aspect`'s `image` parameter), so nothing here uploads. `/render` draws
# aspects via fate_render.
bucket = ASSET_BUCKET_NAMES[MODEL_NAME]

def add_aspect_commands(cls):

	@nextcord.slash_command(name="create_aspect", description="Create a new aspect.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=15, error_message="❌ Failed to create aspect.", require_authorized=True)
	async def create_aspect_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Aspect name"),
		text: str = SlashOption(description="Aspect rules text"),
		attunement: float = SlashOption(description="Attunement"),
		image: str = SlashOption(description="Name of existing image to use in aspectimages", required=False),
		deck: str = SlashOption(description="Optional deck to add this aspect to", required=False, autocomplete=True),
		quantity: int = SlashOption(description="Number of copies to add to deck", required=False, default=1),
	):
		from supabase_helpers import create_record, add_to_deck

		create_data = {
			"name": name,
			"text": text,
			"attunement": attunement,
			"created_by": BOT_PLAYER_ID,
			"actions": [],
			"triggers": [],
			"properties": [],
		}
		if image: create_data["image"] = image

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
		# upload_success, file_path = generate_and_upload_image(created_record, bucket)
		# if not upload_success:
		# 	return f"✅ Created `{name}`, but failed to upload image:\n{file_path}"

		# Update Supabase record with image path
		# update_result = update_record(TABLE_NAME, created_record["id"], {"image": file_path})
		# if update_result:
		# 	created_record["image"] = file_path

		# Download image for local rendering
		# download_success, image_local_path = download_image(file_path, bucket, download_dir)
		# if not download_success:
		# 	return f"✅ Created `{name}`, but failed to retrieve image:\n{image_local_path}"

		# Render and send
		# created_record["fate_type"] = MODEL_NAME
		# render_path = renderer.render_fate(created_record, output_dir=render_dir)
		# await interaction.followup.send(
		# 	content=f"✅ Created `{name}` successfully!",
		# 	file=nextcord.File(render_path)
		# )

		return f"✅ Created `{name}`:\n```json\n{json.dumps(created_record, indent=2)}\n```"


	@nextcord.slash_command(name="update_aspect", description="Update fields on an existing aspect.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=10, error_message="❌ Failed to update aspect.", require_authorized=True)
	async def update_aspect_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Name of the aspect to update", autocomplete=True),
		new_name: str = SlashOption(description="New aspect name", required=False),
		text: str = SlashOption(description="New rules text", required=False),
		attunement: float = SlashOption(description="New attunement", required=False),
		image: str = SlashOption(description="New image name in aspectimages", required=False),
		# regenerate_image: bool = SlashOption(description="Regenerate the image?", required=False, default=False),
	):

		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		record = matches[0]
		update_data = {}

		if new_name: update_data["name"] = new_name
		if text: update_data["text"] = text
		if attunement: update_data["attunement"] = attunement
		if image: update_data["image"] = image

		# Apply update fields for rendering
		record = record | update_data

		# Optional image regeneration
		# if regenerate_image:
		# 	upload_success, file_path = generate_and_upload_image(record, bucket)
		# 	if not upload_success:
		# 		return f"✅ Updated `{name}`, but failed to upload image: `{file_path}`"
		# 	update_data["image"] = file_path

		# Save updates to database
		result = update_record(TABLE_NAME, record["id"], update_data)
		if not result:
			return f"❌ Failed to update {MODEL_NAME} `{name}`."

		# A rename changes what /get, /render and /search autocomplete on.
		if new_name:
			content_index.invalidate()

		return f"✅ Updated `{name}`:\n```json\n{record_to_json(result[0])}\n```"


	# ------------------------------------------------------------------
	# REMOVED 2026-08-27: /delete_aspect.
	#
	# Commented out rather than deleted. `aspects` has no `archived_at` column, so this was a real
	# DELETE with no undo -- and the game's `prune_content_dirs()` treats a
	# missing row as the deletion signal, so it also tore the item out of
	# the offline snapshot (game repo, docs/CONTENT_LOADING.md).
	#
	# All four /delete_* commands went at once: they were never part of the
	# working routine, and an accidental invocation is unrecoverable for
	# content. Content is retired by pulling it from the draft decks, not by
	# deleting the row.
	#
	# The body must stay commented, not merely unattached:
	# tests/test_command_registration.py fails a command that a module
	# defines but never assigns onto the cog.
	# ------------------------------------------------------------------

	# @nextcord.slash_command(name="delete_aspect", description="Delete an aspect.", guild_ids=[DEV_GUILD_ID])
	# @safe_interaction(timeout=5, error_message="❌ Failed to delete aspect.", require_authorized=True)
	# async def delete_aspect_cmd(self, interaction: Interaction, name: str):
		# from supabase_helpers import delete_record

		# matches = fetch_all(TABLE_NAME, filters={"name": name})
		# if len(matches) == 0:
			# return f"❌ No {MODEL_NAME} found with name `{name}`."

		# record = matches[0]
		# success = delete_record(TABLE_NAME, record["id"])
		# content_index.invalidate()
		# if not success:
			# return f"❌ Failed to delete {MODEL_NAME} `{name}`."

		# return f"🗑️ Deleted {MODEL_NAME} `{name}`."


	# Autocomplete Helpers

	@update_aspect_cmd.on_autocomplete("name")
	# @delete_aspect_cmd.on_autocomplete("name")
	# Live aspects only -- see the note in cards.py.
	async def autocomplete_aspect_name(self, interaction: Interaction, input: str):
		matches = content_index.names("aspect", input)
		await interaction.response.send_autocomplete(matches[:25])


	@create_aspect_cmd.on_autocomplete("deck")
	async def autocomplete_fate_decks(self, interaction: Interaction, input: str):
		from azoth_commands.autocomplete import autocomplete_from_table

		# Every unarchived deck. This used to filter on `decks.content_type`,
		# which was dropped 2026-08-27 -- `deck_contents` carries the type per
		# ROW, so a deck can hold anything and there is no deck-level type to
		# filter on any more.
		suggestions = autocomplete_from_table(
			table_name="decks",
			input=input,
			column="name",
			filters={"archived_at": None}
		)

		await interaction.response.send_autocomplete(suggestions[:25])


	cls.create_aspect_cmd = create_aspect_cmd
	cls.update_aspect_cmd = update_aspect_cmd
	# cls.delete_aspect_cmd = delete_aspect_cmd
