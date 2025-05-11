import os
import json
import nextcord
from nextcord.ext import commands
from nextcord import SlashOption, Interaction

from azoth_commands.helpers import safe_interaction
from azoth_commands.autocomplete import autocomplete_from_choices
from constants import DEV_GUILD_ID, BOT_PLAYER_ID, CARD_IMAGE_BUCKET, RITUAL_IMAGE_BUCKET

def add_deck_commands(cls):

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
		import io, uuid

		success, deck = get_deck_by_name(name)
		if not success:
			return deck

		success, cards = get_deck_contents(deck, full=True)
		if not success:
			return cards
		if not cards:
			return f"⚠️ Deck `{name}` is empty."

		for card in cards:
			success, result = download_card_image(card['image'], CARD_IMAGE_BUCKET)
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
		import io, uuid, random

		success, deck = get_deck_by_name(name)
		if not success:
			return deck

		success, cards = get_deck_contents(deck, full=True)
		if not success:
			return cards
		if not cards:
			return f"⚠️ Deck `{name}` is empty."

		for card in cards:
			success, result = download_card_image(card['image'], CARD_IMAGE_BUCKET)
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

	# Autocomplete Helpers

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


	cls.create_deck_cmd = create_deck_cmd
	cls.update_deck_cmd = update_deck_cmd
	cls.delete_deck_cmd = delete_deck_cmd
	cls.get_deck_cmd	= get_deck_cmd
	cls.render_deck_cmd = render_deck_cmd
	cls.render_hand_cmd = render_hand_cmd
	cls.add_to_deck_cmd = add_to_deck_cmd
	cls.remove_from_deck_cmd = remove_from_deck_cmd
