import os
import json
import nextcord
from nextcord.ext import commands
from nextcord import SlashOption, Interaction

from azoth_commands.helpers import safe_interaction
from azoth_commands.autocomplete import autocomplete_from_choices
from constants import DEV_GUILD_ID, BOT_PLAYER_ID, CARD_IMAGE_BUCKET

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
		deck: str = SlashOption(description="Optional deck to add this card to", required=False, autocomplete=True),
		quantity: int = SlashOption(description="Number of copies to add to deck", required=False, default=1),
	):
		from azoth_logic.card_renderer import CardRenderer
		from supabase_client import create_card, update_card_fields, download_image

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

		# Create card
		success, created_card = create_card(card_data)
		if not success:
			return created_card  # Error message

		if deck:
			from supabase_client import get_deck_by_name, add_to_deck

			deck_success, deck_data = get_deck_by_name(deck)
			if not deck_success:
				return f"✅ Created `{name}` but could not add to deck:\n{deck_data}"

			if deck_data["content_type"] != "cards":
				return f"✅ Created `{name}` but `{deck}` is not a card deck."

			add_success, add_msg = add_to_deck(deck_data, name, quantity)
			if not add_success:
				return f"✅ Created `{name}` but failed to add to `{deck}`:\n{add_msg}"

		# Generate card art image
		upload_success, file_path = generate_and_upload_card_image(card)
		if not upload_success:
			return f"✅ Created `{name}`, but failed to upload image:\n{file_path_or_error}"

		# Update card with image path
		update_card_fields(created_card, {"image": file_path_or_error})
		created_card["image"] = file_path_or_error

		# Step 5: Download uploaded image from Supabase
		download_success, image_local_path = download_image(file_path_or_error, CARD_IMAGE_BUCKET)
		if not download_success:
			return f"✅ Created `{name}`, image uploaded, but could not retrieve it:\n{image_local_path}"

		# Render full card
		output_dir = "assets/rendered_cards"
		renderer = CardRenderer()
		renderer.render_card(created_card, output_dir=output_dir)

		final_name = name.lower().replace(" ", "_") + ".png"
		final_path = os.path.join(output_dir, final_name)

		if not os.path.exists(final_path):
			return f"✅ Created `{name}`, but final render failed."

		# Send final card to Discord
		await interaction.followup.send(
			content=f"✅ Created `{name}` successfully!",
			file=nextcord.File(final_path)
		)

		return None  # already sent a response


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
		attributes: str = SlashOption(description="New attributes (comma-separated)", required=False),
		regenerate_image: bool = SlashOption(description="Regenerate the image?", required=False, default=False),
	):
		from supabase_client import get_card_by_name, update_card_fields, download_image
		from azoth_logic.card_renderer import CardRenderer

		success, card = get_card_by_name(name)
		if not success:
			return card

		update_data = {}
		if new_name: update_data["name"] = new_name
		if type: update_data["type"] = type
		if valence is not None: update_data["valence"] = valence
		if element: update_data["element"] = element
		if text: update_data["text"] = text
		if attributes is not None:
			update_data["attributes"] = [a.strip() for a in attributes.split(",")]

		card = card | update_data

		if regenerate_image:
			upload_success, file_path = generate_and_upload_card_image(card)
			if not upload_success:
				return f"✅ Updated `{name}`, but failed to upload image: `{file_path}`"

			# Update image field
			update_data["image"] = file_path

		# Update database fields
		success, result = update_card_fields(card, update_data)
		if not success:
			return result

		if regenerate_image:
			download_success, local_path = download_image(file_path, CARD_IMAGE_BUCKET)
			if download_success:
				renderer = CardRenderer()
				renderer.render_card(card, output_dir="assets/rendered_cards")
				final_path = os.path.join("assets", "rendered_cards", card["name"].lower().replace(" ", "_") + ".png")

				await interaction.followup.send(
					content=f"✅ Updated `{name}` and regenerated image!",
					file=nextcord.File(final_path)
				)

				return None

		return f"✅ Updated `{name}`:\n{result}"


	@nextcord.slash_command(name="get_card", description="Get card details.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to get card.")
	async def get_card_cmd(self, interaction: Interaction, name: str):
		from supabase_client import get_card_by_name

		success, card = get_card_by_name(name)
		if not success:
			return card  # this is the error message string

		card_json = json.dumps(card, indent=2)
		return f"```json\n{card_json}\n```"


	@nextcord.slash_command(name="delete_card", description="Delete a card.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to delete card.", require_authorized=True)
	async def delete_card_cmd(self, interaction: Interaction, name: str):
		from supabase_client import delete_card_by_name
		success, response = delete_card_by_name(name)
		return response


	@nextcord.slash_command(name="render_card", description="Render a card and return the image.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=10, error_message="❌ Failed to render card.")
	async def render_card_cmd(self, interaction: Interaction, name: str = SlashOption(description="Card name", autocomplete=True)):
		from supabase_client import get_card_by_name, download_image
		from azoth_logic.card_renderer import CardRenderer

		success, card = get_card_by_name(name)
		if not success:
			return card

		# Download the art from Supabase
		image_success, image_path_or_error = download_image(card["image"], CARD_IMAGE_BUCKET)
		if not image_success:
			return f"⚠️ Could not load image for `{name}`:\n{image_path_or_error}"

		# Render the full card using the image
		output_dir = "assets/rendered_cards"
		renderer = CardRenderer()
		renderer.render_card(card, output_dir=output_dir)

		# Send the rendered file
		filename = name.lower().replace(" ", "_") + ".png"
		final_path = os.path.join(output_dir, filename)

		if not os.path.exists(final_path):
			return f"❌ Render failed — output not found."

		await interaction.followup.send(file=nextcord.File(final_path))

	# Card Helpers

	def generate_and_upload_card_image(card_data: dict) -> tuple[bool, str | bytes]:
		from azoth_logic.image_generator import generate_card_image
		from supabase_client import upload_image

		success, image_path_or_error = generate_card_image(card_data)
		if not success:
			return False, image_path_or_error

		with open(image_path_or_error, "rb") as f:
			image_bytes = f.read()

		return upload_image(card_data["name"], image_bytes, CARD_IMAGE_BUCKET)


	# Autocomplete Helpers

	@create_card_cmd.on_autocomplete("element")
	@update_card_cmd.on_autocomplete("element")
	async def autocomplete_element(self, interaction: Interaction, input: str):
		suggestions = autocomplete_from_choices("card_element", input)
		await interaction.response.send_autocomplete(suggestions)


	@create_card_cmd.on_autocomplete("type")
	@update_card_cmd.on_autocomplete("type")
	async def autocomplete_type(self, interaction: Interaction, input: str):
		suggestions = autocomplete_from_choices("card_type", input)
		await interaction.response.send_autocomplete(suggestions)


	@create_card_cmd.on_autocomplete("attributes")
	@update_card_cmd.on_autocomplete("attributes")
	async def autocomplete_attributes(self, interaction: Interaction, input: str):
		# Split into parts based on commas
		parts = [p.strip() for p in input.split(",")]
		existing = parts[:-1]
		current = parts[-1]

		matches = autocomplete_from_choices("card_attributes", current)

		prefix = ", ".join(existing) + ", " if existing else ""
		suggestions = [prefix + match for match in matches][:25]

		await interaction.response.send_autocomplete(suggestions)


	@update_card_cmd.on_autocomplete("name")
	@delete_card_cmd.on_autocomplete("name")
	@get_card_cmd.on_autocomplete("name")
	@render_card_cmd.on_autocomplete("name")
	async def autocomplete_card_name(self, interaction: Interaction, input: str):
		from supabase_client import get_all_card_names
		matches = [n for n in get_all_card_names() if input.lower() in n.lower()]
		await interaction.response.send_autocomplete(matches[:25])


	@create_card_cmd.on_autocomplete("deck")
	async def autocomplete_card_decks(self, interaction: Interaction, input: str):
		from supabase_client import get_all_deck_names, get_deck_by_name

		# Optionally filter out non-card decks
		all_names = get_all_deck_names()
		card_decks = []
		for name in all_names:
			success, deck = get_deck_by_name(name)
			if success and deck["content_type"] == "cards":
				card_decks.append(name)

		matches = [d for d in card_decks if input.lower() in d.lower()]
		await interaction.response.send_autocomplete(matches[:25])


	cls.create_card_cmd = create_card_cmd
	cls.update_card_cmd = update_card_cmd
	cls.get_card_cmd 	= get_card_cmd
	cls.delete_card_cmd = delete_card_cmd
	cls.render_card_cmd = render_card_cmd
