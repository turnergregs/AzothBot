# azothbot/supabase_helpers.py
from supabase_client import supabase


"""
	Fetch records from a Supabase table.
	- columns: list of column names to select (defaults to '*')
	- filters: dict of field → value pairs to filter by
"""
def fetch_all(table_name: str, columns: list[str] = None, filters: dict = None) -> list[dict]:
	selector = ",".join(columns) if columns else "*"
	query = supabase.table(table_name).select(selector)

	if filters:
		for key, value in filters.items():
			if isinstance(value, list):
				query = query.in_(key, value)
			else:
				query = query.eq(key, value)

	try:
		response = query.execute()
		return response.data or []
	except Exception as e:
		print(f"Supabase fetch_all error: {e}")
		return []

"""Create a new record."""
def create_record(table_name, data):
	try:
		response = supabase.table(table_name).insert(data).execute()
		return response.data
	except Exception as e:
		print(f"Supabase create_record error: {e}")
		return None

"""Update a record by ID."""
def update_record(table_name, record_id, data):
	from datetime import datetime, timezone

	try:
		data["updated_at"] = datetime.now(timezone.utc).isoformat()
		response = supabase.table(table_name).update(data).eq("id", record_id).execute()
		return response.data
	except Exception as e:
		print(f"Supabase update_record error: {e}")
		return None

"""Delete a record by ID."""
def delete_record(table_name, record_id):
	try:
		response = supabase.table(table_name).delete().eq("id", record_id).execute()
		return response.data
	except Exception as e:
		print(f"Supabase delete_record error: {e}")
		return None

"""Sofr delete a record by ID."""
def soft_delete_record(table_name, record_id):
	from datetime import datetime
	try:
		response = update_record(table_name, record_id, {"archived_at": datetime.utcnow().isoformat()})
		return response.data
	except Exception as e:
		return None

""" Handler for obj types with special cases for names """
def get_display_name(obj, type):
	if type == "ritual":
		return obj.get("challenge_name")
	else:
		return obj.get("name")


def get_deck_contents(deck: dict, full: bool = False) -> tuple[bool, list[dict | str] | str]:

	content_type = deck.get("content_type")
	deck_id = deck.get("id")

	if content_type == "cards":
		# Fetch join rows
		join_rows = fetch_all("deck_cards", columns=["card_id"], filters={"deck_id": deck_id})
		if not join_rows:
			return True, []

		card_ids = [row["card_id"] for row in join_rows]

		# Fetch all relevant cards
		card_data = fetch_all("cards", filters={"id": card_ids})
		if not card_data:
			return False, "Failed to fetch card data."

		id_to_obj = {card["id"]: card for card in card_data}

		# Step 3: Build result
		if full:
			result = []
			for cid in card_ids:
				card = id_to_obj.get(cid)
				if card:
					card_with_type = card.copy()
					card_with_type["item_type"] = "card"
					result.append(card_with_type)
		else:
			result = [get_display_name(id_to_obj[cid], "cards") for cid in card_ids if cid in id_to_obj]

		return True, result

	elif content_type == "fates":
		# Fetch join rows
		join_rows = fetch_all("deck_fates", columns=["fate_id", "fate_type"], filters={"deck_id": deck_id})
		if not join_rows:
			return True, []

		# Group fate IDs by fate_type
		fate_groups = {"ritual": [], "event": [], "consumable": []}
		for row in join_rows:
			fate_type = row["fate_type"]
			fate_id = row["fate_id"]
			if fate_type in fate_groups:
				fate_groups[fate_type].append(fate_id)

		# Fetch each fate type
		id_to_obj = {}
		for fate_type, ids in fate_groups.items():
			if not ids:
				continue

			records = fetch_all(fate_type + "s", filters={"id": ids})

			if not records:
				return False, f"Failed to fetch {fate_type} data."
			id_to_obj.update({r["id"]: r for r in records})

		# Build result list in original join order
		if full:
			result = []
			for row in join_rows:
				fate = id_to_obj.get(row["fate_id"])
				if fate:
					fate_with_type = fate.copy()
					fate_with_type["item_type"] = row["fate_type"]
					result.append(fate_with_type)
		else:
			result = [
				get_display_name(id_to_obj[row["fate_id"]], row["fate_type"])
				for row in join_rows
				if row["fate_id"] in id_to_obj
			]

		return True, result

	else:
		return False, f"Unsupported content type: {content_type}"


def add_to_deck(deck: dict, item_name: str, quantity: int = 1) -> tuple[bool, str]:

	content_type = deck.get("content_type")
	deck_id = deck.get("id")

	if content_type == "cards":
		# Look up card by name (assume card names are unique)
		cards = fetch_all("cards", filters={"name": item_name})
		if not cards:
			return False, f"❌ No card found named '{item_name}'."
		card = cards[0]  # Take the first match
		card_id = card["id"]

		# Add to deck_cards N times
		for _ in range(quantity):
			create_record("deck_cards", {"deck_id": deck_id, "card_id": card_id})

		return True, f"✅ Added {quantity}x **{item_name}** to deck **{deck['name']}**."

	elif content_type == "fates":
		# Check all fate tables for the item
		for fate_type in ["ritual", "event", "consumable"]:
			records = fetch_all(fate_type + "s", filters={"title": item_name})
			if records:
				fate = records[0]
				fate_id = fate["id"]

				# Add to deck_fates N times
				for _ in range(quantity):
					create_record("deck_fates", {
						"deck_id": deck_id,
						"fate_id": fate_id,
						"fate_type": fate_type
					})

				return True, f"✅ Added {quantity}x **{item_name}** to deck **{deck['name']}**."

		return False, f"❌ No fate found named '{item_name}'."

	else:
		return False, f"❌ Unsupported deck type: {content_type}"


def remove_from_deck(deck: dict, item_name: str, quantity: int = 1) -> tuple[bool, str]:

	content_type = deck.get("content_type")
	deck_id = deck.get("id")

	if content_type == "cards":
		# Find card by name
		cards = fetch_all("cards", filters={"name": item_name})
		if not cards:
			return False, f"❌ No card found named '{item_name}'."
		card_id = cards[0]["id"]

		# Find matching join records
		join_rows = fetch_all("deck_cards", filters={"deck_id": deck_id, "card_id": card_id})
		if not join_rows:
			return False, f"❌ No copies of '{item_name}' found in this deck."

		# Delete up to N rows
		to_delete = join_rows[:quantity]
		for row in to_delete:
			delete_record("deck_cards", row["id"])

		return True, f"🗑️ Removed {len(to_delete)}x **{item_name}** from **{deck['name']}**."

	elif content_type == "fates":
		# Search across all fate types
		for fate_type in ["ritual", "event", "consumable"]:
			fates = fetch_all(fate_type + "s", filters={"title": item_name})
			if not fates:
				continue

			fate_id = fates[0]["id"]

			# Find matching join records
			join_rows = fetch_all("deck_fates", filters={
				"deck_id": deck_id,
				"fate_id": fate_id,
				"fate_type": fate_type
			})
			if not join_rows:
				return False, f"❌ No copies of '{item_name}' found in this deck."

			to_delete = join_rows[:quantity]
			for row in to_delete:
				delete_record("deck_fates", row["id"])

			return True, f"🗑️ Removed {len(to_delete)}x **{item_name}** from **{deck['name']}**."

		return False, f"❌ No fate found named '{item_name}'."

	else:
		return False, f"❌ Unsupported deck type: {content_type}"
