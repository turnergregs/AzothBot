import asyncio
import io
import nextcord
from nextcord import SlashOption, Interaction
from azoth_commands.helpers import safe_interaction, record_to_json, to_snake_case
from azoth_commands.autocomplete import autocomplete_from_table
from constants import DEV_GUILD_ID, BOT_PLAYER_ID
from supabase_helpers import fetch_all, update_record
from azoth_logic import content_index, fate_render, rite_schema

MODEL_NAME = "rite"         # what users see

# The table was renamed `events` -> `rites` with the game's 0.9.5
# (db/migrations/2026-09-14_rename_events_to_rites.sql in the game repo). It is
# read from rite_schema at CALL time, never bound here: the bot can start before
# that migration is applied and keep running after it.
#
# Rites carry no art of their own. The card face is a background pattern picked
# by name and coloured from `image_data`, so nothing here generates or uploads an
# image. Renders stream from memory; no directories.

def add_rite_commands(cls):

	@nextcord.slash_command(name="create_rite", description="Create a new rite.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=15, error_message="❌ Failed to create rite.", require_authorized=True)
	async def create_rite_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Rite name"),
		text: str = SlashOption(description="Rite rules text"),
		deck: str = SlashOption(description="Optional deck to add this rite to", required=False, autocomplete=True),
		quantity: int = SlashOption(description="Number of copies to add to deck", required=False, default=1),
	):
		from supabase_helpers import create_record, add_to_deck

		create_data = {
			"name": name,
			"text": text,
			"created_by": BOT_PLAYER_ID,
			"actions": [],
			"triggers": [],
			"properties": [],
		}

		created = create_record(rite_schema.current().table, create_data)
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
	@safe_interaction(timeout=10, error_message="❌ Failed to update rite.", require_authorized=True)
	async def update_rite_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Name of the rite to update", autocomplete=True),
		new_name: str = SlashOption(description="New rite name", required=False),
		text: str = SlashOption(description="New rules text", required=False),
	):

		matches = fetch_all(rite_schema.current().table, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		record = matches[0]
		update_data = {}

		if new_name: update_data["name"] = new_name
		if text: update_data["text"] = text

		# Save updates to database
		result = update_record(rite_schema.current().table, record["id"], update_data)
		if not result:
			return f"❌ Failed to update {MODEL_NAME} `{name}`."

		# A rename changes what /get, /render and /search autocomplete on, and it
		# also changes which BACKGROUND the rite draws -- rite_card.gd picks the
		# material by display name (fate_layout.RITE_BACKGROUND_BY_NAME).
		if new_name:
			content_index.invalidate()

		return f"✅ Updated `{name}`:\n```json\n{record_to_json(result[0])}\n```"


	# ------------------------------------------------------------------
	# REMOVED 2026-08-27: /delete_rite.
	#
	# Commented out rather than deleted. `rites` has no `archived_at` column, so this was a real
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

	# @nextcord.slash_command(name="delete_rite", description="Delete a rite.", guild_ids=[DEV_GUILD_ID])
	# @safe_interaction(timeout=5, error_message="❌ Failed to delete rite.", require_authorized=True)
	# async def delete_rite_cmd(self, interaction: Interaction, name: str):
		# from supabase_helpers import delete_record

		# matches = fetch_all(rite_schema.current().table, filters={"name": name})
		# if len(matches) == 0:
			# return f"❌ No {MODEL_NAME} found with name `{name}`."

		# record = matches[0]
		# success = delete_record(rite_schema.current().table, record["id"])
		# content_index.invalidate()
		# if not success:
			# return f"❌ Failed to delete {MODEL_NAME} `{name}`."

		# return f"🗑️ Deleted {MODEL_NAME} `{name}`."


	# Autocomplete Helpers

	@update_rite_cmd.on_autocomplete("name")
	# @delete_rite_cmd.on_autocomplete("name")
	# Live rites only -- see the note in cards.py.
	async def autocomplete_rite_name(self, interaction: Interaction, input: str):
		matches = content_index.names("rite", input)
		await interaction.response.send_autocomplete(matches[:25])


	@create_rite_cmd.on_autocomplete("deck")
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


	cls.create_rite_cmd = create_rite_cmd
	cls.update_rite_cmd = update_rite_cmd
	# cls.delete_rite_cmd = delete_rite_cmd
