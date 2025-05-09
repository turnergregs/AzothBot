import os
from dotenv import load_dotenv
import nextcord
import json
from nextcord.ext import commands
from nextcord import Interaction, SlashOption
from utils.interaction_helpers import safe_interaction

load_dotenv()
DEV_GUILD_ID = int(os.getenv("DEV_GUILD_ID"))
BOT_PLAYER_ID = int(os.getenv("BOT_PLAYER_ID"))

def autocomplete_from_choices(field: str, input: str) -> list[str]:
	from supabase_client import get_card_element_choices, get_card_attribute_choices, get_card_type_choices, get_deck_type_choices, get_deck_content_type_choices
	lookup = {
		"card_element": get_card_element_choices,
		"card_attributes": get_card_attribute_choices,
		"card_type": get_card_type_choices,
		"deck_type": get_deck_type_choices,
		"deck_content_type": get_deck_content_type_choices
	}

	choices_func = lookup.get(field)
	if not choices_func:
		return []

	all_choices = choices_func()
	return [v for v in all_choices if input in v][:25]


class AzothCommands(commands.Cog):
	def __init__(self, bot):
		self.bot = bot

	# Card CRUD commands
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
		from azoth_logic.image_generator import generate_card_image
		from azoth_logic.card_renderer import CardRenderer
		from supabase_client import create_card, upload_card_image, update_card_fields, download_card_image

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
		image_success, image_path_or_error = generate_card_image(card_data)
		if not image_success:
			return f"✅ Created `{name}`, but image generation failed:\n{image_path_or_error}"
		image_path = image_path_or_error

		# Upload image to Supabase
		with open(image_path, "rb") as f:
			image_bytes = f.read()
		upload_success, file_path_or_error = upload_card_image(name, image_bytes)
		if not upload_success:
			return f"✅ Created `{name}`, but failed to upload image:\n{file_path_or_error}"

		# Update card with image path
		update_card_fields(created_card, {"image": file_path_or_error})
		created_card["image"] = file_path_or_error

		# Step 5: Download uploaded image from Supabase
		image_download_success, image_local_path = download_card_image(file_path_or_error)
		if not image_download_success:
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

		# Clean up local files
		for path in [image_path, image_local_path, final_path]:
			try:
				os.remove(path)
			except Exception as e:
				print(f"⚠️ Cleanup failed: {path} — {e}")

		return None  # already sent a response


	@nextcord.slash_command(name="update_card", description="Update fields on an existing card.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to update card.", require_authorized=True)
	async def update_card_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Name of the card to update", autocomplete=True),
		new_name: str = SlashOption(description="New card name"),
		type: str = SlashOption(description="New type", required=False, autocomplete=True),
		valence: int = SlashOption(description="New valence", required=False),
		element: str = SlashOption(description="New element", required=False, autocomplete=True),
		text: str = SlashOption(description="New rules text", required=False),
		attributes: str = SlashOption(description="New attributes (comma-separated)", required=False),
	):
		from supabase_client import get_card_by_name, update_card_fields
		success, card = get_card_by_name(name)
		if not success:
			return card  # this is the error message

		update_data = {}
		if new_name: update_data["name"] = new_name
		if type: update_data["type"] = type
		if valence is not None: update_data["valence"] = valence
		if element: update_data["element"] = element
		if text: update_data["text"] = text
		if attributes is not None:
			update_data["attributes"] = [a.strip() for a in attributes.split(",")]

		success, result = update_card_fields(card, update_data)
		if success:
			return f"✅ Updated `{name}`:\n{result}"
		else:
			return result


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
		from supabase_client import get_card_by_name, download_card_image
		from azoth_logic.card_renderer import CardRenderer

		success, card = get_card_by_name(name)
		if not success:
			return card

		# Download the art from Supabase
		image_success, image_path_or_error = download_card_image(card["image"])
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

		try:
			os.remove(final_path)
		except Exception as e:
			print(f"⚠️ Could not delete rendered card image: {e}")


	@nextcord.slash_command(name="regenerate_card_image", description="Regenerate the art image for a card.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=10, error_message="❌ Failed to regenerate card art.", require_authorized=True)
	async def regenerate_card_image_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Card name", autocomplete=True),
	):
		from supabase_client import get_card_by_name, upload_card_image, update_card_fields, download_card_image
		from azoth_logic.image_generator import generate_card_image
		from azoth_logic.card_renderer import CardRenderer

		success, card = get_card_by_name(name)
		if not success:
			return card

		# Generate card art image
		image_success, image_path_or_error = generate_card_image(card)
		if not image_success:
			return f"✅ Created `{name}`, but image generation failed:\n{image_path_or_error}"
		image_path = image_path_or_error

		# Upload image to Supabase
		with open(image_path, "rb") as f:
			image_bytes = f.read()
		upload_success, file_path_or_error = upload_card_image(name, image_bytes)
		if not upload_success:
			return f"✅ Created `{name}`, but failed to upload image:\n{file_path_or_error}"

		# Update card with image path
		update_card_fields(card, {"image": file_path_or_error})
		card["image"] = file_path_or_error

		# Step 5: Download uploaded image from Supabase
		image_download_success, image_local_path = download_card_image(file_path_or_error)
		if not image_download_success:
			return f"✅ Created `{name}`, image uploaded, but could not retrieve it:\n{image_local_path}"

		# Render full card
		output_dir = "assets/rendered_cards"
		renderer = CardRenderer()
		renderer.render_card(card, output_dir=output_dir)

		final_name = name.lower().replace(" ", "_") + ".png"
		final_path = os.path.join(output_dir, final_name)

		if not os.path.exists(final_path):
			return f"✅ Created `{name}`, but final render failed."

		# Send final card to Discord
		await interaction.followup.send(
			content=f"✅ Created `{name}` successfully!",
			file=nextcord.File(final_path)
		)

		# Clean up local files
		for path in [image_path, image_local_path, final_path]:
			try:
				os.remove(path)
			except Exception as e:
				print(f"⚠️ Cleanup failed: {path} — {e}")

		return None  # already sent a response


	# Deck CRUD commands

	@nextcord.slash_command(name="create_deck", description="Create a new deck.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to create deck.", require_authorized=True)
	async def create_deck_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Deck name"),
		type: str = SlashOption(description="Deck type", autocomplete=True),
		content_type: str = SlashOption(description="Content type", autocomplete=True),
	):
		from supabase_client import create_deck

		deck_data = {
			"name": name,
			"type": type,
			"content_type": content_type,
			"created_by": BOT_PLAYER_ID,
		}
		success, result = create_deck(deck_data)
		if success:
			return f"✅ Created `{name}`:\n{result}"
		else:
			return result


	@nextcord.slash_command(name="update_deck", description="Update deck type or archive status.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to update deck.", require_authorized=True)
	async def update_deck_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Deck name to update", autocomplete=True),
		new_name: str = SlashOption(description="New deck name"),
		type: str = SlashOption(description="New deck type", required=False, autocomplete=True),
		archived: bool = SlashOption(description="Archive this deck?", required=False)
	):
		from supabase_client import get_deck_by_name, update_deck_fields
		from datetime import datetime

		success, deck = get_deck_by_name(name)
		if not success:
			return deck

		update_data = {}
		if new_name: update_data["name"] = new_name
		if type: update_data["type"] = type
		if archived is not None: update_data["archived_at"] = datetime.utcnow().isoformat() if archived else None

		if not update_data:
			return "⚠️ No changes specified."

		return update_deck_fields(deck["id"], update_data)


	@nextcord.slash_command(name="delete_deck", description="Delete a deck. Hard delete if empty, soft delete if in use.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to delete deck.", require_authorized=True)
	async def delete_deck_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Name of the deck to delete", autocomplete=True),
	):
		from supabase_client import delete_deck_by_name

		success, result = delete_deck_by_name(name)
		return result


	@nextcord.slash_command(name="get_deck", description="Get a deck’s details and contents.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to get deck.")
	async def get_deck_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Deck name", autocomplete=True),
	):
		from supabase_client import get_deck_by_name, get_deck_contents
		import json

		# Load deck metadata
		success, deck = get_deck_by_name(name)
		if not success:
			return deck

		# Load contents (cards or rituals)
		success, contents = get_deck_contents(deck)
		deck["contents"] = contents if success else f"(error loading contents: {contents})"

		# Return JSON-formatted block
		deck_json = json.dumps(deck, indent=2)
		return f"```json\n{deck_json}\n```"


	@nextcord.slash_command(name="render_deck", description="Render the full contents of a deck.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=30, error_message="❌ Failed to render deck.")
	async def render_deck_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Deck to render", autocomplete=True),
	):
		from supabase_client import get_deck_by_name, get_deck_contents, download_card_image
		from azoth_logic.card_renderer import CardRenderer
		import io, os, uuid

		success, deck = get_deck_by_name(name)
		if not success:
			return deck

		success, cards = get_deck_contents(deck, full=True)
		if not success:
			return cards
		if not cards:
			return f"⚠️ Deck `{name}` is empty."

		for card in cards:
			success, result = download_card_image(card['image'])
			if not success:
				return f"⚠️ Failed to fetch art for `{card['name']}`: {result}"

		renderer = CardRenderer()

		filename = f"deck_render_{uuid.uuid4().hex}.png"
		output_path = f"assets/rendered_cards/{filename}"

		renderer.create_card_grid(cards, output_path)

		with open(output_path, "rb") as f:
			image_bytes = f.read()

		os.remove(output_path)  # ✅ cleanup

		file = nextcord.File(io.BytesIO(image_bytes), filename="deck.png")
		await interaction.followup.send(f"🖼️ Full deck: `{name}`", file=file)


	@nextcord.slash_command(name="render_hand", description="Render a sample hand from a deck.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=30, error_message="❌ Failed to render hand.")
	async def render_hand_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Deck name", autocomplete=True),
		hand_size: int = SlashOption(description="Number of cards to draw (default 6)", default=6)
	):
		from supabase_client import get_deck_by_name, get_deck_contents, download_card_image
		from azoth_logic.card_renderer import CardRenderer
		import io, os, uuid, random

		success, deck = get_deck_by_name(name)
		if not success:
			return deck

		success, cards = get_deck_contents(deck, full=True)
		if not success:
			return cards
		if not cards:
			return f"⚠️ Deck `{name}` is empty."

		for card in cards:
			success, result = download_card_image(card['image'])
			if not success:
				return f"⚠️ Failed to fetch art for `{card['name']}`: {result}"

		renderer = CardRenderer()
		filename = f"hand_render_{uuid.uuid4().hex}.png"
		output_path = f"assets/rendered_cards/{filename}"
		renderer.create_sample_hand(cards, output_path, hand_size)

		with open(output_path, "rb") as f:
			image_bytes = f.read()

		os.remove(output_path)  # ✅ cleanup

		file = nextcord.File(io.BytesIO(image_bytes), filename="hand.png")
		await interaction.followup.send(f"✋ Hand from `{name}`", file=file)


	@nextcord.slash_command(name="add_to_deck", description="Add a card or ritual to a deck.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to add to deck.", require_authorized=True)
	async def add_to_deck_cmd(
		self,
		interaction: Interaction,
		deck: str = SlashOption(description="Deck name", autocomplete=True),
		item: str = SlashOption(description="Card or Ritual", autocomplete=True),
		quantity: int = SlashOption(description="How many to add", default=1)
	):
		from supabase_client import get_deck_by_name, add_to_deck

		success, deck_data = get_deck_by_name(deck)
		if not success:
			return deck_data

		return add_to_deck(deck_data, item, quantity)


	@nextcord.slash_command(name="remove_from_deck", description="Remove a card or ritual from a deck.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to remove from deck.", require_authorized=True)
	async def remove_from_deck_cmd(
		self,
		interaction: Interaction,
		deck: str = SlashOption(description="Deck name", autocomplete=True),
		item: str = SlashOption(description="Card or Ritual", autocomplete=True),
		quantity: int = SlashOption(description="How many to remove", default=1)
	):
		from supabase_client import get_deck_by_name, remove_from_deck

		success, deck_data = get_deck_by_name(deck)
		if not success:
			return deck_data

		return remove_from_deck(deck_data, item, quantity)


	# Autocomplete functions

	# Card
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
	@regenerate_card_image_cmd.on_autocomplete("name")
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

	# Deck
	@create_deck_cmd.on_autocomplete("type")
	@update_deck_cmd.on_autocomplete("type")
	async def autocomplete_deck_type(self, interaction: Interaction, input: str):
		suggestions = autocomplete_from_choices("deck_type", input)
		await interaction.response.send_autocomplete(suggestions)

	@create_deck_cmd.on_autocomplete("content_type")
	async def autocomplete_deck_content_type(self, interaction: Interaction, input: str):
		suggestions = autocomplete_from_choices("deck_content_type", input)
		await interaction.response.send_autocomplete(suggestions)

	@update_deck_cmd.on_autocomplete("name")
	@get_deck_cmd.on_autocomplete("name")
	@delete_deck_cmd.on_autocomplete("name")
	@add_to_deck_cmd.on_autocomplete("deck")
	@remove_from_deck_cmd.on_autocomplete("deck")
	@render_deck_cmd.on_autocomplete("name")
	@render_hand_cmd.on_autocomplete("name")
	async def autocomplete_deck_name(self, interaction: Interaction, input: str):
		from supabase_client import get_all_deck_names
		matches = [d for d in get_all_deck_names() if input.lower() in d.lower()]
		await interaction.response.send_autocomplete(matches[:25])

	@add_to_deck_cmd.on_autocomplete("item")
	@remove_from_deck_cmd.on_autocomplete("item")
	async def autocomplete_item_name(self, interaction: Interaction, input: str):
		from supabase_client import get_deck_by_name, get_all_card_names, get_all_ritual_names

		deck_name = interaction.data["options"][0]["value"]
		success, deck = get_deck_by_name(deck_name)
		if not success:
			await interaction.response.send_autocomplete([])
			return

		if deck["content_type"] == "cards":
			source = get_all_card_names
		elif deck["content_type"] == "rituals":
			source = get_all_ritual_names
		else:
			await interaction.response.send_autocomplete([])
			return

		matches = [c for c in source() if input.lower() in c.lower()]
		await interaction.response.send_autocomplete(matches[:25])
