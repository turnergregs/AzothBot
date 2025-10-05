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

from azoth_logic.card_renderer import CardRenderer
renderer = CardRenderer()

TABLE_NAME = "cards"
MODEL_NAME = "card"

bucket = ASSET_BUCKET_NAMES[MODEL_NAME]
render_dir = ASSET_RENDER_PATHS[MODEL_NAME]
download_dir = ASSET_DOWNLOAD_PATHS[MODEL_NAME]

def add_card_commands(cls):

	@nextcord.slash_command(name="create_card", description="Create a new card.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=15, error_message="❌ Failed to create card.", require_authorized=True)
	async def create_card_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Card name"),
		type: str = SlashOption(description="Card type", autocomplete=True),
		valence: int = SlashOption(description="Card valence"),
		element: str = SlashOption(description="Element", autocomplete=True),
		text: str = SlashOption(description="Card rules text"),
		attributes: str = SlashOption(description="Attributes (comma-separated)", required=False),
		subtypes: str = SlashOption(description="Subtypes (comma-separated)", required=False),
		deck: str = SlashOption(description="Optional deck to add this card to", required=False, autocomplete=True),
		quantity: int = SlashOption(description="Number of copies to add to deck", required=False, default=1),
	):
		from supabase_helpers import create_record, add_to_deck

		attr_list = [a.strip() for a in attributes.split(",")] if attributes else []
		subtype_list = [s.strip() for s in subtypes.split(",")] if subtypes else []

		if valence < 0:
			valence = None

		create_data = {
			"name": name,
			"type": type,
			"subtypes": subtype_list,
			"valence": valence,
			"element": element,
			"text": text,
			"attributes": attr_list,
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
		render_path = renderer.render_card(created_record, output_dir=render_dir)
		await interaction.followup.send(
			content=f"✅ Created `{name}` successfully!",
			file=nextcord.File(render_path)
		)

		return None


	@nextcord.slash_command(name="update_card", description="Update fields on an existing card.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=10, error_message="❌ Failed to update card.", require_authorized=True)
	async def update_card_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Name of the card to update", autocomplete=True),
		new_name: str = SlashOption(description="New card name", required=False),
		type: str = SlashOption(description="New type", required=False, autocomplete=True),
		valence: int = SlashOption(description="New valence", required=False),
		element: str = SlashOption(description="New element", required=False, autocomplete=True),
		text: str = SlashOption(description="New rules text", required=False),
		subtypes: str = SlashOption(description="New subtypes (comma-separated)", required=False),
		attributes: str = SlashOption(description="New attributes (comma-separated)", required=False),
		regenerate_image: bool = SlashOption(description="Regenerate the image?", required=False, default=False),
	):

		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		record = matches[0]
		update_data = {}

		if new_name: update_data["name"] = new_name
		if type: update_data["type"] = type

		if valence is not None:
			if valence == -1:
				valence = None
			update_data["valence"] = valence
		if element: update_data["element"] = element
		if text: update_data["text"] = text
		if attributes is not None: update_data["attributes"] = [a.strip() for a in attributes.split(",")]
		if subtypes is not None: update_data["subtypes"] = [s.strip() for s in subtypes.split(",")]

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
				render_path = renderer.render_card(record, output_dir=render_dir)
				await interaction.followup.send(
					content=f"✅ Updated `{name}` and regenerated image!",
					file=nextcord.File(render_path)
				)
				return None

		return f"✅ Updated `{name}`:\n```json\n{record_to_json(result[0])}\n```"


	@nextcord.slash_command(name="get_card", description="Get card details.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to get card.")
	async def get_card_cmd(self, interaction: Interaction, name: str):
		
		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		record = matches[0]

		# Look up decks that use this card
		deck_contents = fetch_all("deck_contents", filters={"content_id": record["id"], "content_type": MODEL_NAME})
		deck_ids = [dc["deck_id"] for dc in deck_contents]

		usages = []
		if deck_ids:
			decks = fetch_all("decks", filters={"id": deck_ids})
			usages = [d["name"] for d in decks]

		record["usages"] = usages

		return f"```json\n{record_to_json(record)}\n```"


	@nextcord.slash_command(name="delete_card", description="Delete a card.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to delete card.", require_authorized=True)
	async def delete_card_cmd(self, interaction: Interaction, name: str):
		from supabase_helpers import delete_record

		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ No {MODEL_NAME} found with name `{name}`."

		record = matches[0]
		success = delete_record(TABLE_NAME, record["id"])
		if not success:
			return f"❌ Failed to delete {MODEL_NAME} `{name}`."

		return f"🗑️ Deleted {MODEL_NAME} `{name}`."


	@nextcord.slash_command(name="render_card", description="Render a card and return the image.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=10, error_message="❌ Failed to render card.")
	async def render_card_cmd(self, interaction: Interaction, name: str = SlashOption(description="Card name", autocomplete=True)):
		
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

	@create_card_cmd.on_autocomplete("element")
	@update_card_cmd.on_autocomplete("element")
	async def autocomplete_element(self, interaction: Interaction, input: str):
		suggestions = autocomplete_from_table("card_elements", input)
		await interaction.response.send_autocomplete(suggestions)


	@create_card_cmd.on_autocomplete("type")
	@update_card_cmd.on_autocomplete("type")
	async def autocomplete_type(self, interaction: Interaction, input: str):
		suggestions = autocomplete_from_table("card_types", input)
		await interaction.response.send_autocomplete(suggestions)


	@create_card_cmd.on_autocomplete("attributes")
	@update_card_cmd.on_autocomplete("attributes")
	async def autocomplete_attributes(self, interaction: Interaction, input: str):
		# Split into parts based on commas
		parts = [p.strip() for p in input.split(",")]
		existing = parts[:-1]
		current = parts[-1]

		matches = autocomplete_from_table("card_attributes", current)

		prefix = ", ".join(existing) + ", " if existing else ""
		suggestions = [prefix + match for match in matches][:25]

		await interaction.response.send_autocomplete(suggestions)


	@update_card_cmd.on_autocomplete("name")
	@delete_card_cmd.on_autocomplete("name")
	@get_card_cmd.on_autocomplete("name")
	@render_card_cmd.on_autocomplete("name")
	async def autocomplete_card_name(self, interaction: Interaction, input: str):
		from azoth_commands.autocomplete import autocomplete_from_table
		matches = autocomplete_from_table(TABLE_NAME, input)
		await interaction.response.send_autocomplete(matches[:25])


	@create_card_cmd.on_autocomplete("deck")
	async def autocomplete_card_decks(self, interaction: Interaction, input: str):
		from azoth_commands.autocomplete import autocomplete_from_table

		suggestions = autocomplete_from_table(
			table_name="decks",
			input=input,
			column="name",
			filters={"content_type": "cards"}
		)

		await interaction.response.send_autocomplete(suggestions[:25])


	cls.create_card_cmd = create_card_cmd
	cls.update_card_cmd = update_card_cmd
	cls.get_card_cmd 	= get_card_cmd
	cls.delete_card_cmd = delete_card_cmd
	cls.render_card_cmd = render_card_cmd
