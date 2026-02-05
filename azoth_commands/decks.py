import os
import json
import nextcord
from nextcord.ext import commands
from nextcord import SlashOption, Interaction
from azoth_commands.helpers import safe_interaction, generate_and_upload_image
from azoth_commands.autocomplete import autocomplete_from_table
from constants import DEV_GUILD_ID, BOT_PLAYER_ID, ASSET_RENDER_PATHS, ASSET_BUCKET_NAMES, ASSET_DOWNLOAD_PATHS
from supabase_helpers import fetch_all, update_record, get_deck_contents
from supabase_storage import download_image

from azoth_logic.card_renderer import CardRenderer
from azoth_logic.ritual_renderer import RitualRenderer

bucket = ASSET_BUCKET_NAMES["card"]
render_dir = ASSET_RENDER_PATHS["card"]

TABLE_NAME = "decks"
MODEL_NAME = "deck"

def add_deck_commands(cls):

	@nextcord.slash_command(name="create_deck", description="Create a new deck.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to create deck.", require_authorized=True)
	async def create_deck_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Deck name"),
		description: str = SlashOption(description="Deck Description"),
		type: str = SlashOption(description="Deck type", autocomplete=True),
		content_type: str = SlashOption(description="Content type", autocomplete=True),
		usage_type: str = SlashOption(description="Usage type", autocomplete=True)
	):
		from supabase_helpers import create_record

		create_data = {
			"name": name,
			"description": description,
			"type": type,
			"content_type": content_type,
			"usage_type": usage_type,
			"created_by": BOT_PLAYER_ID,
		}

		created = create_record(TABLE_NAME, create_data)
		if not created:
			return f"❌ Failed to create {MODEL_NAME}."

		created_record = created[0]

		return f"✅ Created `{name}`:\n```json\n{json.dumps(created_record, indent=2)}\n```"


	@nextcord.slash_command(name="update_deck", description="Update deck type or archive status.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to update deck.", require_authorized=True)
	async def update_deck_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Deck name to update", autocomplete=True),
		new_name: str = SlashOption(description="New deck name", required=False),
		description: str = SlashOption(description="New deck description", required=False),
		type: str = SlashOption(description="New deck type", required=False, autocomplete=True),
		usage_type: str = SlashOption(description="New usage type", required=False, autocomplete=True),
		archived: bool = SlashOption(description="Archive this deck?", required=False)
	):

		from datetime import datetime

		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		record = matches[0]
		update_data = {}
		if new_name: update_data["name"] = new_name
		if description: update_data["description"] = description
		if type: update_data["type"] = type
		if usage_type: update_data["usage_type"] = usage_type
		if archived is not None: update_data["archived_at"] = datetime.utcnow().isoformat() if archived else None

		record = record | update_data

		result = update_record(TABLE_NAME, record["id"], update_data)
		if not result:
			return f"❌ Failed to update {MODEL_NAME} `{name}`."

		return f"✅ Updated `{name}`:\n```json\n{json.dumps(result[0], indent=2)}\n```"


	@nextcord.slash_command(name="delete_deck", description="Delete a deck. Hard delete if empty, soft delete if in use.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to delete deck.", require_authorized=True)
	async def delete_deck_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Name of the deck to delete", autocomplete=True),
	):
		from supabase_helpers import soft_delete_record

		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ No {MODEL_NAME} found with name `{name}`."

		record = matches[0]
		success = soft_delete_record(TABLE_NAME, record["id"])
		if not success:
			return f"❌ Failed to delete {MODEL_NAME} `{name}`."

		return f"🗑️ Deleted {MODEL_NAME} `{name}`."


	@nextcord.slash_command(name="get_deck", description="Get a deck’s details and contents.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to get deck.")
	async def get_deck_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Deck name", autocomplete=True),
	):
		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		record = matches[0]

		success, contents = get_deck_contents(record)
		record["contents"] = contents if success else f"(error loading contents: {contents})"

		record_json = json.dumps(record, indent=2)
		return f"```json\n{record_json}\n```"


	@nextcord.slash_command(name="render_deck", description="Render the full contents of a deck.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=60, error_message="❌ Failed to render deck.")
	async def render_deck_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Deck to render", autocomplete=True),
	):
		import io, uuid

		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		deck = matches[0]

		success, content_result = download_content_images(deck)
		if not success:
			return content_result
		if len(content_result) == 0:
			return f"⚠️ Deck `{name}` is empty."

		render_dir = ASSET_RENDER_PATHS["deck"]
		filename = f"deck_render_{uuid.uuid4().hex}.png"
		output_path = os.path.join(render_dir, filename)

		renderer = CardRenderer()
		# TODO support for RitualRenderer
		renderer.create_card_grid(content_result, output_path)

		with open(output_path, "rb") as f:
			image_bytes = f.read()

		os.remove(output_path)

		file = nextcord.File(io.BytesIO(image_bytes), filename="deck.png")
		await interaction.followup.send(f"🖼️ Full deck: `{name}`", file=file)


	@nextcord.slash_command(name="render_hand", description="Render a sample hand from a deck.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=60, error_message="❌ Failed to render hand.")
	async def render_hand_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Deck name", autocomplete=True),
		hand_size: int = SlashOption(description="Number of cards to draw (default 6)", default=6)
	):
		import io, uuid

		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		deck = matches[0]

		success, content_result = download_content_images(deck)
		if not success:
			return content_result
		if len(content_result) == 0:
			return f"⚠️ Deck `{name}` is empty."

		render_dir = ASSET_RENDER_PATHS["deck"]
		filename = f"deck_render_{uuid.uuid4().hex}.png"
		output_path = os.path.join(render_dir, filename)

		renderer = CardRenderer()
		# TODO support for RitualRenderer
		renderer.create_sample_hand(content_result, output_path, hand_size)

		with open(output_path, "rb") as f:
			image_bytes = f.read()

		os.remove(output_path)  # ✅ cleanup

		file = nextcord.File(io.BytesIO(image_bytes), filename="hand.png")
		await interaction.followup.send(f"✋ Hand from `{name}`", file=file)


	@nextcord.slash_command(name="add_to_deck", description="Add a card or fate to a deck.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to add to deck.", require_authorized=True)
	async def add_to_deck_cmd(
		self,
		interaction: Interaction,
		deck_name: str = SlashOption(description="Deck name", autocomplete=True),
		item_name: str = SlashOption(description="Card or Fate", autocomplete=True),
		quantity: int = SlashOption(description="How many to add (Default 1)", default=1)
	):
		from supabase_helpers import add_to_deck

		matches = fetch_all(TABLE_NAME, filters={"name": deck_name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{deck_name}`."

		deck = matches[0]

		success, result = add_to_deck(deck, item_name, quantity)
		if success:
			update_record(TABLE_NAME, deck["id"], {"updated_at": "now()"})
		return result


	@nextcord.slash_command(name="remove_from_deck", description="Remove a card or ritual from a deck.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to remove from deck.", require_authorized=True)
	async def remove_from_deck_cmd(
		self,
		interaction: Interaction,
		deck_name: str = SlashOption(description="Deck name", autocomplete=True),
		item_name: str = SlashOption(description="Card or Ritual", autocomplete=True),
		quantity: int = SlashOption(description="How many to remove", default=1)
	):
		from supabase_helpers import remove_from_deck

		matches = fetch_all(TABLE_NAME, filters={"name": deck_name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{deck_name}`."

		deck = matches[0]

		success, result = remove_from_deck(deck, item_name, quantity)
		if success:
			update_record(TABLE_NAME, deck["id"], {"updated_at": "now()"})
		return result

	# Deck Helpers

	def download_content_images(deck: dict):
		success, contents = get_deck_contents(deck, full=True)
		if not success:
			return False, contents
		if not contents:
			return True, []

		for item in contents:
			item_type = item["item_type"]
			download_dir = ASSET_DOWNLOAD_PATHS[item_type]
			bucket = ASSET_BUCKET_NAMES[item_type]
			if item_type == "ritual":
				image_success, image_result = download_image(item["challenge_image"], bucket, download_dir)
				if not image_success:
					return False, f"⚠️ Could not load image for `{item['challenge_name']}`:\n{image_result}"
				image_success, image_result = download_image(item["reward_image"], bucket, download_dir)
				if not image_success:
					return False, f"⚠️ Could not load image for `{item['reward_name']}`:\n{image_result}"
			else:
				image_success, image_result = download_image(item["image"], bucket, download_dir)
				if not image_success:
					return False, f"⚠️ Could not load image for `{item['name']}`:\n{image_result}"
		return True, contents


	@nextcord.slash_command(name="postpone", description="Move all of the copies of the item from live draft decks to Removed decks.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to postpone item.", require_authorized=True)
	async def postpone_cmd(
		self,
		interaction: Interaction,
		item_name: str = SlashOption(description="Item to postpone", autocomplete=True),
	):
		from supabase_helpers import remove_from_deck, add_to_deck

		# 1️⃣ Find all active base draft decks
		decks = fetch_all(
			TABLE_NAME,
			filters={
				"archived_at": None,
				"type": "base",
				"usage_type": "draft",
			},
		)

		if not decks:
			return "❌ No active base draft decks found."

		total_removed = 0
		item_content_type = None
		source_decks = []

		# 2️⃣ Remove ALL copies from ALL matching decks
		for deck in decks:
			success, contents = get_deck_contents(deck, full=True)
			if not success or not contents:
				continue

			# Count how many copies are in this deck
			matching_items = []
			for item in contents:
				if item.get("name") == item_name:
					matching_items.append(item)
					item_content_type = item["item_type"]

			if not matching_items:
				continue

			quantity = len(matching_items)
			success, result = remove_from_deck(deck, item_name, quantity)
			if not success:
				return f"❌ Failed to remove `{item_name}` from `{deck['name']}`:\n{result}"

			update_record(TABLE_NAME, deck["id"], {"updated_at": "now()"})
			total_removed += quantity
			source_decks.append(deck["name"])

		if total_removed == 0:
			return f"❌ `{item_name}` was not found in any active draft deck."

		# 3️⃣ Decide destination deck
		if item_content_type == "aspect":
			target_deck_id = 27  # Removed Aspect Cards
		else:
			target_deck_id = 26  # Removed Draft Cards

		target_deck_matches = fetch_all(TABLE_NAME, filters={"id": target_deck_id})
		if not target_deck_matches:
			return "❌ Target deck not found."

		target_deck = target_deck_matches[0]

		# 4️⃣ Add all removed copies to destination deck
		success, result = add_to_deck(target_deck, item_name, total_removed)
		if not success:
			return (
				f"❌ Removed {total_removed} copies, but failed to add to "
				f"`{target_deck['name']}`:\n{result}"
			)

		update_record(TABLE_NAME, target_deck["id"], {"updated_at": "now()"})

		return (
			f"⏸️ Postponed `{item_name}` ×{total_removed}\n"
			f"• Removed from: {', '.join(source_decks)}\n"
			f"• Added to `{target_deck['name']}`"
		)

	@nextcord.slash_command(
		name="stage",
		description="Move all copies of an item from live draft decks to Staging (or add it if missing).",
		guild_ids=[DEV_GUILD_ID]
	)
	@safe_interaction(timeout=5, error_message="❌ Failed to stage item.", require_authorized=True)
	async def stage_cmd(
		self,
		interaction: Interaction,
		item_name: str = SlashOption(description="Item to stage", autocomplete=True),
	):
		from supabase_helpers import remove_from_deck, add_to_deck

		STAGING_DECK_ID = 21

		# 1️⃣ Find all active base draft decks
		decks = fetch_all(
			TABLE_NAME,
			filters={
				"archived_at": None,
				"type": "base",
				"usage_type": "draft",
			},
		)

		if not decks:
			return "❌ No active base draft decks found."

		total_removed = 0
		source_decks = []

		# 2️⃣ Remove ALL copies from ALL matching decks (if present)
		for deck in decks:
			success, contents = get_deck_contents(deck, full=True)
			if not success or not contents:
				continue

			matching = [item for item in contents if item.get("name") == item_name]
			if not matching:
				continue

			quantity = len(matching)
			success, result = remove_from_deck(deck, item_name, quantity)
			if not success:
				return f"❌ Failed to remove `{item_name}` from `{deck['name']}`:\n{result}"

			update_record(TABLE_NAME, deck["id"], {"updated_at": "now()"})
			total_removed += quantity
			source_decks.append(deck["name"])

		# 3️⃣ Load staging deck
		target_matches = fetch_all(TABLE_NAME, filters={"id": STAGING_DECK_ID})
		if not target_matches:
			return "❌ Staging deck not found."

		staging_deck = target_matches[0]

		# 4️⃣ Decide how many to add
		add_quantity = total_removed if total_removed > 0 else 1

		success, result = add_to_deck(staging_deck, item_name, add_quantity)
		if not success:
			return (
				f"❌ Failed to add `{item_name}` ×{add_quantity} "
				f"to `{staging_deck['name']}`:\n{result}"
			)

		update_record(TABLE_NAME, staging_deck["id"], {"updated_at": "now()"})

		# 5️⃣ Response
		if total_removed > 0:
			return (
				f"⏸️ Staged `{item_name}` ×{total_removed}\n"
				f"• Removed from: {', '.join(source_decks)}\n"
				f"• Added to `{staging_deck['name']}`"
			)
		else:
			return (
				f"⏸️ Staged `{item_name}`\n"
				f"• Item was not present in live draft decks\n"
				f"• Added 1 copy to `{staging_deck['name']}`"
			)


	@nextcord.slash_command(name="merge_staging", description="Move all staged items back into live draft decks.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=10, error_message="❌ Failed to merge items.", require_authorized=True)
	async def merge_staging_cmd(
		self,
		interaction: Interaction,
	):
		from supabase_helpers import remove_from_deck, add_to_deck

		STAGING_DECK_ID = 21

		# Destination decks
		ASPECT_DECK_ID = 22
		COMBO_CARD_DECK_ID = 20
		DEFAULT_CARD_DECK_ID = 3

		# 1️⃣ Load staging deck
		staging_matches = fetch_all(TABLE_NAME, filters={"id": STAGING_DECK_ID})
		if not staging_matches:
			return "❌ Staging deck not found."

		staging_deck = staging_matches[0]

		success, contents = get_deck_contents(staging_deck, full=True)
		if not success:
			return f"❌ Failed to load staging deck contents:\n{contents}"

		if not contents:
			return "ℹ️ Staging deck is empty."

		# 2️⃣ Bucket items by destination
		move_plan = {
			ASPECT_DECK_ID: {},
			COMBO_CARD_DECK_ID: {},
			DEFAULT_CARD_DECK_ID: {},
		}

		for item in contents:
			name = item.get("name")
			if not name:
				continue

			if item["item_type"] == "aspect":
				target_deck_id = ASPECT_DECK_ID

			elif (
				item["item_type"] == "card"
				and item.get("valence") is None
				and item.get("element") is None
			):
				target_deck_id = NULL_CARD_DECK_ID

			else:
				target_deck_id = DEFAULT_CARD_DECK_ID

			move_plan[target_deck_id][name] = move_plan[target_deck_id].get(name, 0) + 1

		# 3️⃣ Remove EVERYTHING from staging
		for name, qty in {
			name: sum(
				bucket.get(name, 0)
				for bucket in move_plan.values()
			)
			for name in {
				item["name"] for item in contents if item.get("name")
			}
		}.items():
			success, result = remove_from_deck(staging_deck, name, qty)
			if not success:
				return f"❌ Failed to remove `{name}` ×{qty} from staging:\n{result}"

		update_record(TABLE_NAME, staging_deck["id"], {"updated_at": "now()"})

		# 4️⃣ Add items to destination decks
		moved_summary = []

		for deck_id, items in move_plan.items():
			if not items:
				continue

			matches = fetch_all(TABLE_NAME, filters={"id": deck_id})
			if not matches:
				return f"❌ Destination deck {deck_id} not found."

			deck = matches[0]

			for name, qty in items.items():
				success, result = add_to_deck(deck, name, qty)
				if not success:
					return (
						f"❌ Failed to add `{name}` ×{qty} to "
						f"`{deck['name']}`:\n{result}"
					)
				moved_summary.append(f"{name} ×{qty} → {deck['name']}")

			update_record(TABLE_NAME, deck["id"], {"updated_at": "now()"})

		# 5️⃣ Done
		return (
			"🎭 Unstaged all items:\n"
			+ "\n".join(f"• {line}" for line in moved_summary)
		)


	# Autocomplete Helpers

	@create_deck_cmd.on_autocomplete("type")
	@update_deck_cmd.on_autocomplete("type")
	async def autocomplete_type(self, interaction: Interaction, input: str):
		suggestions = autocomplete_from_table("deck_types", input)
		await interaction.response.send_autocomplete(suggestions)


	@create_deck_cmd.on_autocomplete("content_type")
	async def autocomplete_deck_content_type(self, interaction: Interaction, input: str):
		suggestions = autocomplete_from_table("deck_content_types", input)
		await interaction.response.send_autocomplete(suggestions)


	@create_deck_cmd.on_autocomplete("usage_type")
	@update_deck_cmd.on_autocomplete("usage_type")
	async def autocomplete_type(self, interaction: Interaction, input: str):
		suggestions = autocomplete_from_table("deck_usage_types", input)
		await interaction.response.send_autocomplete(suggestions)


	@update_deck_cmd.on_autocomplete("name")
	@get_deck_cmd.on_autocomplete("name")
	@delete_deck_cmd.on_autocomplete("name")
	@add_to_deck_cmd.on_autocomplete("deck_name")
	@remove_from_deck_cmd.on_autocomplete("deck_name")
	@render_deck_cmd.on_autocomplete("name")
	@render_hand_cmd.on_autocomplete("name")
	async def autocomplete_deck_name(self, interaction: Interaction, input: str):
		command = interaction.data.get("name")
		if command == "render_hand" or command == "render_deck":
			# TODO support for non-card decks
			matches = autocomplete_from_table(TABLE_NAME, input, "name", {"content_type": "cards"})
		else:
			matches = autocomplete_from_table(TABLE_NAME, input)
		await interaction.response.send_autocomplete(matches[:25])


	@add_to_deck_cmd.on_autocomplete("item_name")
	@remove_from_deck_cmd.on_autocomplete("item_name")
	@stage_cmd.on_autocomplete("item_name")
	async def autocomplete_item_name(self, interaction: Interaction, input: str):

	    deck_name = interaction.data["options"][0]["value"]
	    matches = fetch_all(TABLE_NAME, filters={"name": deck_name})
	    if len(matches) == 0:
	        await interaction.response.send_autocomplete([])
	        return

	    deck = matches[0]
	    matches = []
	    command = interaction.data.get("name")

	    if command == "add_to_deck":
	        if deck["content_type"] == "cards":
	            records = fetch_all("cards", columns=["name"])
	            matches = [r["name"] for r in records if input.lower() in r["name"].lower()]
	        elif deck["content_type"] == "fates":
	            for table in ["rituals", "events", "consumables", "aspects"]:
	                name_column = "challenge_name" if table == "rituals" else "name"
	                records = fetch_all(table, columns=[name_column])
	                matches += [r[name_column] for r in records if input.lower() in r[name_column].lower()]
	    else:  # remove_from_deck
	        success, items = get_deck_contents(deck, full=False)
	        if not success or not items:
	            await interaction.response.send_autocomplete([])
	            return
	        matches = [name for name in items if input.lower() in name.lower()]

	    # 🔑 Sort matches alphabetically (case-insensitive) before slicing
	    matches = sorted(matches, key=lambda s: s.lower())

	    await interaction.response.send_autocomplete(matches[:25])


	@postpone_cmd.on_autocomplete("item_name")
	async def autocomplete_postpone_item(self, interaction: Interaction, input: str):
		decks = fetch_all(
			TABLE_NAME,
			filters={
				"archived_at": None,
				"type": "base",
				"usage_type": "draft",
			},
		)

		if not decks:
			await interaction.response.send_autocomplete([])
			return

		item_names = set()

		for deck in decks:
			success, contents = get_deck_contents(deck, full=True)
			if not success or not contents:
				continue

			for item in contents:
				if item.get("name"):
					item_names.add(name)

		# Filter + sort
		matches = [
			name for name in item_names
			if input.lower() in name.lower()
		]
		matches = sorted(matches, key=lambda s: s.lower())

		await interaction.response.send_autocomplete(matches[:25])


	cls.create_deck_cmd = create_deck_cmd
	cls.update_deck_cmd = update_deck_cmd
	cls.delete_deck_cmd = delete_deck_cmd
	cls.get_deck_cmd	= get_deck_cmd
	cls.render_deck_cmd = render_deck_cmd
	cls.render_hand_cmd = render_hand_cmd
	cls.add_to_deck_cmd = add_to_deck_cmd
	cls.remove_from_deck_cmd = remove_from_deck_cmd
	cls.postpone_cmd = postpone_cmd
	cls.stage_cmd = stage_cmd
	cls.merge_staging_cmd = merge_staging_cmd

