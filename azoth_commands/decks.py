import asyncio
import json
import nextcord
from nextcord import SlashOption, Interaction
from azoth_commands.helpers import pack_fields_into_embeds, safe_interaction, missing_asset_hint
from azoth_commands.autocomplete import autocomplete_from_table
from azoth_logic import taxonomy
from constants import DEV_GUILD_ID, BOT_PLAYER_ID
from supabase_helpers import fetch_all, update_record, get_deck_contents

from azoth_logic import deck_render

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
		usage_type: str = SlashOption(description="Usage type", autocomplete=True)
	):
		from supabase_helpers import create_record

		create_data = {
			"name": name,
			"description": description,
			"type": type,
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


	# ------------------------------------------------------------------
	# REMOVED 2026-08-27: /delete_deck.
	#
	# Commented out rather than deleted. This one was the safe member of the set -- it soft-deletes via
	# `soft_delete_record`, setting `archived_at` -- but it went with the
	# others for consistency. `/update_deck` still archives a deck via its
	# `archived` parameter, so nothing is lost.
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

	# @nextcord.slash_command(name="delete_deck", description="Delete a deck. Hard delete if empty, soft delete if in use.", guild_ids=[DEV_GUILD_ID])
	# @safe_interaction(timeout=5, error_message="❌ Failed to delete deck.", require_authorized=True)
	# async def delete_deck_cmd(
		# self,
		# interaction: Interaction,
		# name: str = SlashOption(description="Name of the deck to delete", autocomplete=True),
	# ):
		# from supabase_helpers import soft_delete_record

		# matches = fetch_all(TABLE_NAME, filters={"name": name})
		# if len(matches) == 0:
			# return f"❌ No {MODEL_NAME} found with name `{name}`."

		# record = matches[0]
		# success = soft_delete_record(TABLE_NAME, record["id"])
		# if not success:
			# return f"❌ Failed to delete {MODEL_NAME} `{name}`."

		# return f"🗑️ Deleted {MODEL_NAME} `{name}`."


	@nextcord.slash_command(name="decks", description="List every unarchived deck.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=10, error_message="❌ Failed to list decks.")
	async def decks_cmd(self, interaction: Interaction):
		"""Every live deck, grouped the way the game groups them.

		Unarchived only, deliberately: 20 of the 28 rows are archived, and a list
		that is two-thirds dead content is not a list you can scan. `/show_deck`
		still opens an archived deck by name.

		The **id** is shown because it is the thing you cannot get anywhere else
		and the thing that keeps mattering -- `/stage` and `/merge_staging` are
		commented out precisely because they pinned ids that had since moved, and
		the Rites deck was invisible to `draft_deck_view` for its whole life
		without anyone being able to see the deck list to notice.
		"""
		decks = fetch_all(TABLE_NAME, filters={"archived_at": None},
		                  sort=["usage_type", "name"])
		if not decks:
			return "❌ No unarchived decks."

		# One read for every count, rather than one per deck.
		counts: dict = {}
		kinds: dict = {}
		for row in fetch_all("deck_contents", columns=["deck_id", "content_type"]):
			counts[row["deck_id"]] = counts.get(row["deck_id"], 0) + 1
			kinds.setdefault(row["deck_id"], set()).add(row["content_type"])

		LABEL = {"card": "cards", "aspect": "aspects", "event": "rites"}

		groups: dict = {}
		for deck in decks:
			total = counts.get(deck["id"], 0)
			held = ", ".join(sorted(LABEL.get(k, k) for k in kinds.get(deck["id"], ())))
			detail = f"{total} × {held}" if held else "empty"
			groups.setdefault(deck["usage_type"] or "(none)", []).append(
				f"`{deck['id']:>3}` **{deck['name']}** — {detail}")

		fields = [(f"{usage} ({len(lines)})", "\n".join(lines), False)
		          for usage, lines in sorted(groups.items())]

		total_items = sum(counts.get(d["id"], 0) for d in decks)
		embeds = pack_fields_into_embeds(
			fields,
			title=f"Decks — {len(decks)} live",
			colour=0x5865F2,
			footer=f"{total_items} items across them. Archived decks are not listed.")
		for embed in embeds:
			await interaction.followup.send(embed=embed)


	@nextcord.slash_command(name="show_deck", description="Show a deck’s details and contents.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to show deck.")
	async def show_deck_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Deck name", autocomplete=True),
	):
		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		MAX_LEN = 1900

		record = matches[0]

		success, contents = get_deck_contents(record)
		record["contents"] = contents if success else f"(error loading contents: {contents})"

		record_json = json.dumps(record, indent=2)

		if len(record_json) > MAX_LEN:
		    record_json = record_json[:MAX_LEN] + "\n... (truncated)"

		return f"```json\n{record_json}\n```"


	# 110 cards means 110 art downloads. They are parallelised (see
	# deck_render.fetch_art_many) and land in ~27s warm, but a cold process pays
	# DNS and TLS on top -- hence 120s rather than the 60s the old renderer used.
	@nextcord.slash_command(name="render_deck", description="Render the full contents of a deck.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=120, error_message="❌ Failed to render deck.")
	async def render_deck_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Deck to render", autocomplete=True),
	):
		import io

		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		deck = matches[0]

		success, content_result = download_content_images(deck)
		if not success:
			return content_result
		if len(content_result) == 0:
			return f"⚠️ Deck `{name}` is empty."

		# Cards only for now: aspects and events need the fate renderer.
		cards = [c for c in content_result if c.get("item_type") == "card"]
		if not cards:
			return f"⚠️ Deck `{name}` has no cards to render."

		# 110 art downloads plus PIL compositing -- seconds of blocking work, so it
		# runs off the event loop rather than starving the gateway heartbeat.
		try:
			image_bytes = await asyncio.to_thread(deck_render.render_grid, cards)
		except ValueError as e:
			return f"⚠️ {e}"
		except FileNotFoundError as e:
			return f"⚠️ Missing render asset: {e}\n{missing_asset_hint(e)}"

		skipped = len(content_result) - len(cards)
		note = f" ({skipped} non-card item(s) not rendered)" if skipped else ""
		file = nextcord.File(io.BytesIO(image_bytes), filename="deck.png")
		await interaction.followup.send(f"🖼️ Full deck: `{name}` — {len(cards)} cards{note}", file=file)


	@nextcord.slash_command(name="render_hand", description="Render a sample hand from a deck.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=60, error_message="❌ Failed to render hand.")
	async def render_hand_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Deck name", autocomplete=True),
		hand_size: int = SlashOption(description="Number of cards to draw (default 6)", default=6)
	):
		import io

		matches = fetch_all(TABLE_NAME, filters={"name": name})
		if len(matches) == 0:
			return f"❌ Could not find {MODEL_NAME} named `{name}`."

		deck = matches[0]

		success, content_result = download_content_images(deck)
		if not success:
			return content_result
		if len(content_result) == 0:
			return f"⚠️ Deck `{name}` is empty."

		cards = [c for c in content_result if c.get("item_type") == "card"]
		if not cards:
			return f"⚠️ Deck `{name}` has no cards to draw from."

		try:
			image_bytes = await asyncio.to_thread(deck_render.render_hand, cards, hand_size)
		except FileNotFoundError as e:
			return f"⚠️ Missing render asset: {e}\n{missing_asset_hint(e)}"

		file = nextcord.File(io.BytesIO(image_bytes), filename="hand.png")
		await interaction.followup.send(
			f"✋ Hand of {min(hand_size, len(cards))} from `{name}`", file=file)


	@nextcord.slash_command(name="add_to_deck", description="Add a card, aspect, or event to a deck.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to add to deck.", require_authorized=True)
	async def add_to_deck_cmd(
		self,
		interaction: Interaction,
		deck_name: str = SlashOption(description="Deck name", autocomplete=True),
		item_name: str = SlashOption(description="Card, Aspect, or Event", autocomplete=True),
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


	@nextcord.slash_command(name="remove_from_deck", description="Remove a card, aspect, or event from a deck.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to remove from deck.", require_authorized=True)
	async def remove_from_deck_cmd(
		self,
		interaction: Interaction,
		deck_name: str = SlashOption(description="Deck name", autocomplete=True),
		item_name: str = SlashOption(description="Card, Aspect, or Event", autocomplete=True),
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
		"""Deck contents, without pre-downloading art.

		The renderer fetches art itself, in parallel and deduplicated by
		filename (deck_render.fetch_art_many). This used to download every
		item's image to disk first -- serially, and then again inside the
		renderer, which roughly doubled the time to render a deck.

		The name is kept because several commands call it; it no longer
		downloads anything.
		"""
		return get_deck_contents(deck, full=True)


	# ------------------------------------------------------------------
	# HIDDEN 2026-08-27: /postpone, /stage and /merge_staging.
	#
	# Commented out rather than deleted -- the balance workflow they
	# implement is still wanted, but all three are unsafe as written:
	# /stage and /merge_staging hardcode deck ids 21/22/20/3, and in the
	# current database deck 21 (Staging) is ARCHIVED and deck 22 is named
	# "Testing Fates" despite the constant being ASPECT_DECK_ID. See
	# docs/COMMANDS.md § Deck curation.
	#
	# The bodies must stay commented, not merely unattached:
	# tests/test_command_registration.py fails a command that a module
	# defines but never assigns onto the cog. Commenting hides them from
	# its AST scan, which is the supported way to park a command.
	# ------------------------------------------------------------------

	# @nextcord.slash_command(name="postpone", description="Move all of the copies of the item from live draft decks to Removed decks.", guild_ids=[DEV_GUILD_ID])
	# @safe_interaction(timeout=5, error_message="❌ Failed to postpone item.", require_authorized=True)
	# async def postpone_cmd(
		# self,
		# interaction: Interaction,
		# item_name: str = SlashOption(description="Item to postpone", autocomplete=True),
	# ):
		# from supabase_helpers import remove_from_deck, add_to_deck, parse_item_ref, get_display_name

		# # Resolve the encoded item ref (falls back to the raw name for typed input)
		# ref_type, ref_id = parse_item_ref(item_name)
		# display_name = item_name
		# if ref_type:
			# recs = fetch_all(f"{ref_type}s", filters={"id": ref_id})
			# if recs:
				# display_name = get_display_name(recs[0], ref_type)

		# # 1️⃣ Find all active base draft decks
		# decks = fetch_all(
			# TABLE_NAME,
			# filters={
				# "archived_at": None,
				# "type": "base",
				# "usage_type": "draft",
			# },
		# )

		# if not decks:
			# return "❌ No active base draft decks found."

		# total_removed = 0
		# item_content_type = None
		# source_decks = []

		# # 2️⃣ Remove ALL copies from ALL matching decks
		# for deck in decks:
			# success, contents = get_deck_contents(deck, full=True)
			# if not success or not contents:
				# continue

			# # Count how many copies are in this deck
			# matching_items = []
			# for item in contents:
				# if ref_type:
					# is_match = item["id"] == ref_id and item["item_type"] == ref_type
				# else:
					# is_match = item.get("name") == item_name
				# if is_match:
					# matching_items.append(item)
					# item_content_type = item["item_type"]

			# if not matching_items:
				# continue

			# quantity = len(matching_items)
			# success, result = remove_from_deck(deck, item_name, quantity)
			# if not success:
				# return f"❌ Failed to remove `{display_name}` from `{deck['name']}`:\n{result}"

			# update_record(TABLE_NAME, deck["id"], {"updated_at": "now()"})
			# total_removed += quantity
			# source_decks.append(deck["name"])

		# if total_removed == 0:
			# return f"❌ `{display_name}` was not found in any active draft deck."

		# # 3️⃣ Decide destination deck
		# if item_content_type == "aspect":
			# target_deck_id = 27  # Removed Aspect Cards
		# else:
			# target_deck_id = 26  # Removed Draft Cards

		# target_deck_matches = fetch_all(TABLE_NAME, filters={"id": target_deck_id})
		# if not target_deck_matches:
			# return "❌ Target deck not found."

		# target_deck = target_deck_matches[0]

		# # 4️⃣ Add all removed copies to destination deck
		# success, result = add_to_deck(target_deck, item_name, total_removed)
		# if not success:
			# return (
				# f"❌ Removed {total_removed} copies, but failed to add to "
				# f"`{target_deck['name']}`:\n{result}"
			# )

		# update_record(TABLE_NAME, target_deck["id"], {"updated_at": "now()"})

		# return (
			# f"⏸️ Postponed `{display_name}` ×{total_removed}\n"
			# f"• Removed from: {', '.join(source_decks)}\n"
			# f"• Added to `{target_deck['name']}`"
		# )

	# @nextcord.slash_command(
		# name="stage",
		# description="Move all copies of an item from live draft decks to Staging (or add it if missing).",
		# guild_ids=[DEV_GUILD_ID]
	# )
	# @safe_interaction(timeout=5, error_message="❌ Failed to stage item.", require_authorized=True)
	# async def stage_cmd(
		# self,
		# interaction: Interaction,
		# item_name: str = SlashOption(description="Item to stage", autocomplete=True),
	# ):
		# from supabase_helpers import remove_from_deck, add_to_deck, parse_item_ref, get_display_name

		# STAGING_DECK_ID = 21

		# # Resolve the encoded item ref (falls back to the raw name for typed input)
		# ref_type, ref_id = parse_item_ref(item_name)
		# display_name = item_name
		# if ref_type:
			# recs = fetch_all(f"{ref_type}s", filters={"id": ref_id})
			# if recs:
				# display_name = get_display_name(recs[0], ref_type)

		# # 1️⃣ Find all active base draft decks
		# decks = fetch_all(
			# TABLE_NAME,
			# filters={
				# "archived_at": None,
				# "type": "base",
				# "usage_type": "draft",
			# },
		# )

		# if not decks:
			# return "❌ No active base draft decks found."

		# total_removed = 0
		# source_decks = []

		# # 2️⃣ Remove ALL copies from ALL matching decks (if present)
		# for deck in decks:
			# success, contents = get_deck_contents(deck, full=True)
			# if not success or not contents:
				# continue

			# if ref_type:
				# matching = [it for it in contents if it["id"] == ref_id and it["item_type"] == ref_type]
			# else:
				# matching = [it for it in contents if it.get("name") == item_name]
			# if not matching:
				# continue

			# quantity = len(matching)
			# success, result = remove_from_deck(deck, item_name, quantity)
			# if not success:
				# return f"❌ Failed to remove `{display_name}` from `{deck['name']}`:\n{result}"

			# update_record(TABLE_NAME, deck["id"], {"updated_at": "now()"})
			# total_removed += quantity
			# source_decks.append(deck["name"])

		# # 3️⃣ Load staging deck
		# target_matches = fetch_all(TABLE_NAME, filters={"id": STAGING_DECK_ID})
		# if not target_matches:
			# return "❌ Staging deck not found."

		# staging_deck = target_matches[0]

		# # 4️⃣ Decide how many to add
		# add_quantity = total_removed if total_removed > 0 else 1

		# success, result = add_to_deck(staging_deck, item_name, add_quantity)
		# if not success:
			# return (
				# f"❌ Failed to add `{display_name}` ×{add_quantity} "
				# f"to `{staging_deck['name']}`:\n{result}"
			# )

		# update_record(TABLE_NAME, staging_deck["id"], {"updated_at": "now()"})

		# # 5️⃣ Response
		# if total_removed > 0:
			# return (
				# f"⏸️ Staged `{display_name}` ×{total_removed}\n"
				# f"• Removed from: {', '.join(source_decks)}\n"
				# f"• Added to `{staging_deck['name']}`"
			# )
		# else:
			# return (
				# f"⏸️ Staged `{display_name}`\n"
				# f"• Item was not present in live draft decks\n"
				# f"• Added 1 copy to `{staging_deck['name']}`"
			# )


	# @nextcord.slash_command(name="merge_staging", description="Move all staged items back into live draft decks.", guild_ids=[DEV_GUILD_ID])
	# @safe_interaction(timeout=10, error_message="❌ Failed to merge items.", require_authorized=True)
	# async def merge_staging_cmd(
		# self,
		# interaction: Interaction,
	# ):
		# from supabase_helpers import remove_from_deck_by_ref, add_to_deck_by_ref, get_display_name

		# STAGING_DECK_ID = 21

		# # Destination decks
		# ASPECT_DECK_ID = 22
		# COMBO_CARD_DECK_ID = 20  # cards with null valence and null element are combo cards
		# DEFAULT_CARD_DECK_ID = 3

		# # 1️⃣ Load staging deck
		# staging_matches = fetch_all(TABLE_NAME, filters={"id": STAGING_DECK_ID})
		# if not staging_matches:
			# return "❌ Staging deck not found."

		# staging_deck = staging_matches[0]

		# success, contents = get_deck_contents(staging_deck, full=True)
		# if not success:
			# return f"❌ Failed to load staging deck contents:\n{contents}"

		# if not contents:
			# return "ℹ️ Staging deck is empty."

		# # 2️⃣ Bucket items by destination deck, keyed by (content_type, content_id)
		# move_plan = {
			# ASPECT_DECK_ID: {},
			# COMBO_CARD_DECK_ID: {},
			# DEFAULT_CARD_DECK_ID: {},
		# }
		# display_names = {}

		# for item in contents:
			# content_type = item["item_type"]
			# content_id = item["id"]
			# key = (content_type, content_id)
			# display_names[key] = get_display_name(item, content_type)

			# if content_type == "aspect":
				# target_deck_id = ASPECT_DECK_ID
			# elif (
				# content_type == "card"
				# and item.get("valence") is None
				# and item.get("element") is None
			# ):
				# target_deck_id = COMBO_CARD_DECK_ID
			# else:
				# target_deck_id = DEFAULT_CARD_DECK_ID

			# move_plan[target_deck_id][key] = move_plan[target_deck_id].get(key, 0) + 1

		# # 3️⃣ Remove EVERYTHING from staging
		# for bucket in move_plan.values():
			# for (content_type, content_id), qty in bucket.items():
				# success, result = remove_from_deck_by_ref(staging_deck, content_type, content_id, qty)
				# if not success:
					# name = display_names.get((content_type, content_id), content_id)
					# return f"❌ Failed to remove `{name}` ×{qty} from staging:\n{result}"

		# update_record(TABLE_NAME, staging_deck["id"], {"updated_at": "now()"})

		# # 4️⃣ Add items to destination decks
		# moved_summary = []

		# for deck_id, items in move_plan.items():
			# if not items:
				# continue

			# matches = fetch_all(TABLE_NAME, filters={"id": deck_id})
			# if not matches:
				# return f"❌ Destination deck {deck_id} not found."

			# deck = matches[0]

			# for (content_type, content_id), qty in items.items():
				# success, result = add_to_deck_by_ref(deck, content_type, content_id, qty)
				# name = display_names.get((content_type, content_id), content_id)
				# if not success:
					# return (
						# f"❌ Failed to add `{name}` ×{qty} to "
						# f"`{deck['name']}`:\n{result}"
					# )
				# moved_summary.append(f"{name} ×{qty} → {deck['name']}")

			# update_record(TABLE_NAME, deck["id"], {"updated_at": "now()"})

		# # 5️⃣ Done
		# return (
			# "🎭 Unstaged all items:\n"
			# + "\n".join(f"• {line}" for line in moved_summary)
		# )


	# Autocomplete Helpers

	@create_deck_cmd.on_autocomplete("type")
	@update_deck_cmd.on_autocomplete("type")
	async def autocomplete_deck_type(self, interaction: Interaction, input: str):
		suggestions = taxonomy.suggest("deck_types", input)
		await interaction.response.send_autocomplete(suggestions)


	# Was also called `autocomplete_type`, shadowing the one above at module
	# level. Both decorators still registered, so it worked -- but the second
	# definition silently replaced the first name, which is a trap.
	@create_deck_cmd.on_autocomplete("usage_type")
	@update_deck_cmd.on_autocomplete("usage_type")
	async def autocomplete_deck_usage_type(self, interaction: Interaction, input: str):
		suggestions = taxonomy.suggest("deck_usage_types", input)
		await interaction.response.send_autocomplete(suggestions)


	@update_deck_cmd.on_autocomplete("name")
	@show_deck_cmd.on_autocomplete("name")
	# @delete_deck_cmd.on_autocomplete("name")
	@add_to_deck_cmd.on_autocomplete("deck_name")
	@remove_from_deck_cmd.on_autocomplete("deck_name")
	@render_deck_cmd.on_autocomplete("name")
	@render_hand_cmd.on_autocomplete("name")
	async def autocomplete_deck_name(self, interaction: Interaction, input: str):
		# Every unarchived deck, for every command that takes one.
		#
		# /render_hand and /render_deck used to be narrowed to `content_type =
		# 'cards'` because the deck renderer draws cards only. That column was
		# dropped 2026-08-27, and the narrowing was already redundant: both
		# commands filter their contents to cards and say so in the reply
		# ("3 non-card item(s) not rendered", or "has no cards to render").
		# Hiding the deck from the picker was the worse of the two behaviours --
		# it made a real deck look missing.
		matches = autocomplete_from_table(TABLE_NAME, input, "name", {"archived_at": None})
		await interaction.response.send_autocomplete(matches[:25])


	@remove_from_deck_cmd.on_autocomplete("item_name")
	async def autocomplete_remove_item_name(self, interaction: Interaction, input: str):
		from supabase_helpers import encode_item_ref, make_item_label, get_display_name

		deck_name = interaction.data["options"][0]["value"]
		matches = fetch_all(TABLE_NAME, filters={"name": deck_name})
		if len(matches) == 0:
			await interaction.response.send_autocomplete([])
			return

		deck = matches[0]
		success, items = get_deck_contents(deck, full=True)
		if not success or not items:
			await interaction.response.send_autocomplete([])
			return

		input_lower = input.lower()
		choices = {}
		for item in items:
			content_type = item["item_type"]
			name = get_display_name(item, content_type)
			if name and input_lower in name.lower():
				label = make_item_label(name, content_type, item["id"])
				choices[label] = encode_item_ref(content_type, item["id"])

		sorted_items = sorted(choices.items(), key=lambda kv: kv[0].lower())[:25]
		await interaction.response.send_autocomplete(dict(sorted_items))


	@add_to_deck_cmd.on_autocomplete("item_name")
	# @stage_cmd.on_autocomplete("item_name")
	async def autocomplete_item_name(self, interaction: Interaction, input: str):
		"""EVERY row, live or not -- deliberately not filtered.

		`/show`, `/render`, `/rules`, `/search` and the `/update_*` pickers hide
		content that sits in no unarchived deck (see `content_index` § Liveness).
		This one must not: adding a card to a deck is precisely what makes it
		live again, and filtering here would make retired content unrecoverable
		through the bot -- with no `/delete_*` commands left to undo it either.
		This picker IS the way back.
		"""
		from supabase_helpers import encode_item_ref, make_item_label

		input_lower = input.lower()
		choices = {}

		tables = [("cards", "card"), ("aspects", "aspect"), ("events", "event")]
		for table, content_type in tables:
			records = fetch_all(table, columns=["id", "name"])
			for r in records:
				name = r.get("name")
				if name and input_lower in name.lower():
					label = make_item_label(name, content_type, r["id"])
					choices[label] = encode_item_ref(content_type, r["id"])

		# Sort by label (case-insensitive) and cap at Discord's 25-choice limit
		sorted_items = sorted(choices.items(), key=lambda kv: kv[0].lower())[:25]
		await interaction.response.send_autocomplete(dict(sorted_items))


	# @postpone_cmd.on_autocomplete("item_name")
	# async def autocomplete_postpone_item(self, interaction: Interaction, input: str):
		# from supabase_helpers import encode_item_ref, make_item_label, get_display_name
		# decks = fetch_all(
			# TABLE_NAME,
			# filters={
				# "archived_at": None,
				# "type": "base",
				# "usage_type": "draft",
			# },
		# )

		# if not decks:
			# await interaction.response.send_autocomplete([])
			# return

		# input_lower = input.lower()
		# choices = {}
		# for deck in decks:
			# success, contents = get_deck_contents(deck, full=True)
			# if not success or not contents:
				# continue
			# for item in contents:
				# content_type = item["item_type"]
				# name = get_display_name(item, content_type)
				# if name and input_lower in name.lower():
					# label = make_item_label(name, content_type, item["id"])
					# choices[label] = encode_item_ref(content_type, item["id"])

		# sorted_items = sorted(choices.items(), key=lambda kv: kv[0].lower())[:25]
		# await interaction.response.send_autocomplete(dict(sorted_items))


	cls.decks_cmd = decks_cmd
	cls.create_deck_cmd = create_deck_cmd
	cls.update_deck_cmd = update_deck_cmd
	# cls.delete_deck_cmd = delete_deck_cmd
	cls.show_deck_cmd	= show_deck_cmd
	cls.render_deck_cmd = render_deck_cmd
	cls.render_hand_cmd = render_hand_cmd
	cls.add_to_deck_cmd = add_to_deck_cmd
	cls.remove_from_deck_cmd = remove_from_deck_cmd
	# cls.postpone_cmd = postpone_cmd
	# cls.stage_cmd = stage_cmd
	# cls.merge_staging_cmd = merge_staging_cmd

