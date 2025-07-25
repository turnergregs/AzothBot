import os
import json
import nextcord
from nextcord.ext import commands
from nextcord import SlashOption, Interaction
from azoth_commands.helpers import safe_interaction, generate_and_upload_image, ritual_to_json, to_snake_case
from azoth_commands.autocomplete import autocomplete_from_table
from constants import DEV_GUILD_ID, BOT_PLAYER_ID, ASSET_RENDER_PATHS, ASSET_BUCKET_NAMES, ASSET_DOWNLOAD_PATHS
from supabase_helpers import fetch_all, update_record
from supabase_storage import download_image

from azoth_logic.ritual_renderer import RitualRenderer
renderer = RitualRenderer()

TABLE_NAME = "rituals"
MODEL_NAME = "ritual"

bucket = ASSET_BUCKET_NAMES[MODEL_NAME]
render_dir = ASSET_RENDER_PATHS[MODEL_NAME]
download_dir = ASSET_DOWNLOAD_PATHS[MODEL_NAME]

def add_ritual_commands(cls):

	@nextcord.slash_command(name="create_ritual", description="Create a new ritual.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=15, error_message="❌ Failed to create ritual.", require_authorized=True)
	async def create_ritual_cmd(
		self,
		interaction: Interaction,
		challenge_name: str = SlashOption(description="Ritual Challenge name"),
		challenge_text: str = SlashOption(description="Ritual Challenge rules text"),
		difficulty: str = SlashOption(description="Ritual Challenge difficulty", autocomplete=True),
		reward_name: str = SlashOption(description="Ritual Reward name"),
		reward_text: str = SlashOption(description="Ritual Reward rules text"),
		foresight: int = SlashOption(description="Fate Foresight"),
		deck: str = SlashOption(description="Optional deck to add this ritual to", required=False, autocomplete=True),
		quantity: int = SlashOption(description="Number of copies to add to deck", required=False, default=1),
	):
		from supabase_helpers import create_record, add_to_deck

		create_data = {
			"challenge_name": challenge_name,
			"challenge_text": challenge_text,
			"challenge_difficulty": difficulty,
			"challenge_actions": [],
			"challenge_triggers": [],
			"challenge_properties": [],
			"reward_name": reward_name,
			"reward_text": reward_text,
			"reward_actions": [],
			"reward_triggers": [],
			"reward_properties": [],
			"foresight": foresight,
			"created_by": BOT_PLAYER_ID,
		}

		created = create_record(TABLE_NAME, create_data)
		if not created:
			return f"❌ Failed to create {MODEL_NAME}."

		created_record = created[0]

		# Optionally add to deck
		if deck:
			matches = fetch_all("decks", filters={"name": deck})
			if len(matches) == 0:
				return f"✅ Created `{challenge_name}`, but could not find deck named `{deck}`."

			deck = matches[0]

			success, result = add_to_deck(deck, challenge_name, quantity)
			if not success:
				return f"✅ Created `{challenge_name}`, but could not add to deck named `{deck}`:\n{result}."

		for side_key in ["challenge", "reward"]:

			upload_success, file_path = generate_and_upload_image(created_record, bucket, side_key)
			if not upload_success:
				return f"✅ Created `{challenge_name}`, but failed to upload image:\n{file_path}"

			# Update Supabase record with image path
			update_result = update_record(TABLE_NAME, created_record["id"], {f"{side_key}_image": file_path})
			if update_result:
				created_record[f"{side_key}_image"] = file_path

			# Download image for local rendering
			download_success, image_local_path = download_image(file_path, bucket, download_dir)
			if not download_success:
				return f"✅ Created `{challenge_name}`, but failed to retrieve image:\n{image_local_path}"

		# Render and send
		created_record["fate_type"] = MODEL_NAME
		render_path = renderer.render_ritual(created_record, output_dir=render_dir)
		await interaction.followup.send(
			content=f"✅ Created `{challenge_name}` successfully!",
			file=nextcord.File(render_path)
		)

		return None


	@nextcord.slash_command(name="update_ritual", description="Update fields on an existing ritual.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=10, error_message="❌ Failed to update ritual.", require_authorized=True)
	async def update_ritual_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Name of the ritual to update", autocomplete=True),
		new_challenge_name: str = SlashOption(description="New ritual challenge name", required=False),
		challenge_text: str = SlashOption(description="New Ritual Challenge rules text", required=False),
		difficulty: str = SlashOption(description="New Ritual Challenge difficulty", required=False, autocomplete=True),
		reward_name: str = SlashOption(description="New Ritual Reward name", required=False),
		reward_text: str = SlashOption(description="New Ritual Reward rules text", required=False),
		text: str = SlashOption(description="New rules text", required=False),
		foresight: int = SlashOption(description="New foresight", required=False),
		regenerate_image: bool = SlashOption(description="Regenerate the image?", required=False, default=False),
	):

		matches = fetch_all(TABLE_NAME, filters={"challenge_name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		record = matches[0]
		update_data = {}

		if new_challenge_name: update_data["challenge_name"] = new_challenge_name
		if challenge_text: update_data["challenge_text"] = challenge_text
		if difficulty: update_data["challenge_difficulty"] = difficulty
		if reward_name: update_data["reward_name"] = reward_name
		if reward_text: update_data["reward_text"] = reward_text
		if foresight: update_data["foresight"] = foresight

		# Apply update fields for rendering
		record = record | update_data

		# Optional image regeneration
		if regenerate_image:
			for side_key in ["challenge", "reward"]:
				upload_success, file_path = generate_and_upload_image(record, bucket, side_key)
				if not upload_success:
					return f"✅ Updated `{name}`, but failed to upload image: `{file_path}`"
				update_data[f"{side_key}_image"] = file_path

		# Save updates to database
		result = update_record(TABLE_NAME, record["id"], update_data)
		if not result:
			return f"❌ Failed to update {MODEL_NAME} `{name}`."

		final_name = new_challenge_name if new_challenge_name else name
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
				render_path = renderer.render_ritual(record, output_dir=render_dir)
				await interaction.followup.send(
					content=f"✅ Updated `{name}` and regenerated image!",
					file=nextcord.File(render_path)
				)
				return None

		return f"✅ Updated `{name}`:\n```json\n{record_to_json(result[0])}\n```"


	@nextcord.slash_command(name="get_ritual", description="Get ritual details.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to get ritual.")
	async def get_ritual_cmd(self, interaction: Interaction, name: str):
		
		matches = fetch_all(TABLE_NAME, filters={"challenge_name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		record = matches[0]
		return f"```json\n{ritual_to_json(record)}\n```"


	@nextcord.slash_command(name="delete_ritual", description="Delete a ritual.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to delete ritual.", require_authorized=True)
	async def delete_ritual_cmd(self, interaction: Interaction, name: str):
		from supabase_helpers import delete_record

		matches = fetch_all(TABLE_NAME, filters={"challenge_name": name})
		if len(matches) == 0:
			return f"❌ No {MODEL_NAME} found with name `{name}`."

		record = matches[0]
		success = delete_record(TABLE_NAME, record["id"])
		if not success:
			return f"❌ Failed to delete {MODEL_NAME} `{name}`."

		return f"🗑️ Deleted {MODEL_NAME} `{name}`."


	@nextcord.slash_command(name="render_ritual", description="Render a ritual and return the image.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=10, error_message="❌ Failed to render ritual.")
	async def render_ritual_cmd(self, interaction: Interaction, name: str):
		
		matches = fetch_all(TABLE_NAME, filters={"challenge_name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		record = matches[0]

		# Download the art from Supabase
		for side_key in ["challenge", "reward"]:
			image_success, image_result = download_image(record[f"{side_key}_image"], bucket, download_dir)
			if not image_success:
				return f"⚠️ Could not load image for `{name}`:\n{image_result}"

		record["fate_type"] = MODEL_NAME
		render_path = renderer.render_ritual(record)
		await interaction.followup.send(file=nextcord.File(render_path))


	# Autocomplete Helpers

	@create_ritual_cmd.on_autocomplete("difficulty")
	@update_ritual_cmd.on_autocomplete("difficulty")
	async def autocomplete_difficulty(self, interaction: Interaction, input: str):
		suggestions = autocomplete_from_table("ritual_difficulties", input)
		await interaction.response.send_autocomplete(suggestions)

	@update_ritual_cmd.on_autocomplete("name")
	@delete_ritual_cmd.on_autocomplete("name")
	@get_ritual_cmd.on_autocomplete("name")
	@render_ritual_cmd.on_autocomplete("name")
	async def autocomplete_ritual_name(self, interaction: Interaction, input: str):
		from azoth_commands.autocomplete import autocomplete_from_table
		matches = autocomplete_from_table(TABLE_NAME, input, "challenge_name")
		await interaction.response.send_autocomplete(matches[:25])


	@create_ritual_cmd.on_autocomplete("deck")
	async def autocomplete_fate_decks(self, interaction: Interaction, input: str):
		from azoth_commands.autocomplete import autocomplete_from_table

		suggestions = autocomplete_from_table(
			table_name="decks",
			input=input,
			column="name",
			filters={"content_type": "fates"}
		)

		await interaction.response.send_autocomplete(suggestions[:25])


	cls.create_ritual_cmd = create_ritual_cmd
	cls.update_ritual_cmd = update_ritual_cmd
	cls.get_ritual_cmd 	  = get_ritual_cmd
	cls.delete_ritual_cmd = delete_ritual_cmd
	cls.render_ritual_cmd = render_ritual_cmd
