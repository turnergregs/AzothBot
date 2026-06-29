# azothbot/supabase_helpers.py
from supabase_client import supabase


"""
	Fetch records from a Supabase table.
	- columns: list of column names to select (defaults to '*')
	- filters: dict of field → value pairs to filter by
"""
def fetch_all(table_name: str, columns: list[str] = None, filters: dict = None, sort: list[str] = None) -> list[dict]:
	selector = ",".join(columns) if columns else "*"
	query = supabase.table(table_name).select(selector)

	if filters:
		for key, value in filters.items():
			if value is None:
				query = query.is_(key, "null")
			elif isinstance(value, list):
				query = query.in_(key, value)
			else:
				query = query.eq(key, value)

	if sort:
		for s in sort:
			if s.startswith("-"):
				query = query.order(s[1:], desc=True)
			else:
				query = query.order(s)

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


import re

# Content types that participate in decks. Order is the legacy
# first-match priority used only for raw (manually-typed) names.
DECK_CONTENT_TYPES = ["card", "aspect", "event", "ritual", "consumable"]
_ITEM_REF_RE = re.compile(r"^(card|aspect|event|ritual|consumable):(\d+)$")


def name_column_for(content_type: str) -> str:
	"""The column holding a content type's display name."""
	return "challenge_name" if content_type == "ritual" else "name"


def encode_item_ref(content_type: str, item_id) -> str:
	"""Encode a content type + id into the value Discord sends back, e.g. 'card:447'."""
	return f"{content_type}:{item_id}"


def parse_item_ref(value: str):
	"""Parse an encoded ref like 'card:447'.

	Returns (content_type, id) on success, or (None, None) when the value is a
	raw name (user typed free text instead of picking an autocomplete choice).
	"""
	if not value:
		return None, None
	match = _ITEM_REF_RE.match(value.strip())
	if not match:
		return None, None
	return match.group(1), int(match.group(2))


def make_item_label(name: str, content_type: str, item_id) -> str:
	"""Human-readable autocomplete label, e.g. 'Diversity (Card #447)'."""
	return f"{name} ({content_type.capitalize()} #{item_id})"



def get_deck_contents(deck: dict, full: bool = False) -> tuple[bool, list[dict | str] | str]:
	deck_id = deck.get("id")
	if not deck_id:
		return False, "Deck is missing ID."

	join_rows = fetch_all("deck_contents", columns=["id", "content_id", "content_type"], filters={"deck_id": deck_id})
	if not join_rows:
		return True, []

	# Group by content_type
	grouped = {}
	for row in join_rows:
		grouped.setdefault(row["content_type"], []).append(row["content_id"])

	results = []

	for content_type, ids in grouped.items():
		table_name = f"{content_type}s"  # e.g. 'cards', 'aspects', 'events'
		sort_key = "challenge_name" if content_type == "ritual" else "name"

		records = fetch_all(table_name, filters={"id": ids}, sort=["name"])
		if not records:
			return False, f"Failed to fetch {content_type} data."

		id_to_obj = {r["id"]: r for r in records}
		sort_order = {r["id"]: i for i, r in enumerate(records)}

		matching_rows = [r for r in join_rows if r["content_type"] == content_type]
		sorted_rows = sorted(matching_rows, key=lambda r: sort_order.get(r["content_id"], float("inf")))

		if full:
			for row in sorted_rows:
				obj = id_to_obj.get(row["content_id"])
				if obj:
					obj_copy = obj.copy()
					obj_copy["item_type"] = content_type
					results.append(obj_copy)
		else:
			for row in sorted_rows:
				obj = id_to_obj.get(row["content_id"])
				if obj:
					results.append(get_display_name(obj, content_type))

	return True, results


def add_to_deck_by_ref(deck: dict, content_type: str, content_id, quantity: int = 1) -> tuple[bool, str]:
	"""Add an exact item (resolved by id) to a deck."""
	deck_id = deck.get("id")
	if not deck_id:
		return False, "Deck missing ID."

	table_name = f"{content_type}s"
	records = fetch_all(table_name, filters={"id": content_id})
	if not records:
		return False, f"❌ No {content_type} found with id {content_id}."

	item_name = get_display_name(records[0], content_type) or str(content_id)

	for _ in range(quantity):
		create_record("deck_contents", {
			"deck_id": deck_id,
			"content_id": content_id,
			"content_type": content_type
		})

	return True, f"✅ Added {quantity}x **{item_name}** to deck **{deck['name']}**."


def remove_from_deck_by_ref(deck: dict, content_type: str, content_id, quantity: int = 1) -> tuple[bool, str]:
	"""Remove an exact item (resolved by id) from a deck."""
	deck_id = deck.get("id")
	if not deck_id:
		return False, "Deck missing ID."

	table_name = f"{content_type}s"
	records = fetch_all(table_name, filters={"id": content_id})
	item_name = get_display_name(records[0], content_type) if records else str(content_id)

	join_rows = fetch_all("deck_contents", filters={
		"deck_id": deck_id,
		"content_id": content_id,
		"content_type": content_type
	})
	if not join_rows:
		return False, f"❌ No copies of '{item_name}' found in this deck."

	to_delete = join_rows[:quantity]
	for row in to_delete:
		delete_record("deck_contents", row["id"])

	return True, f"🗑️ Removed {len(to_delete)}x **{item_name}** from **{deck['name']}**."


def _resolve_name_to_ref(item_name: str):
	"""Legacy fallback for raw (non-encoded) names: first match by type priority.

	Returns (content_type, content_id) or (None, None) if nothing matches."""
	for content_type in DECK_CONTENT_TYPES:
		name_column = name_column_for(content_type)
		records = fetch_all(f"{content_type}s", filters={name_column: item_name})
		if records:
			return content_type, records[0]["id"]
	return None, None


def add_to_deck(deck: dict, item_name: str, quantity: int = 1) -> tuple[bool, str]:
	"""Add an item to a deck. item_name may be an encoded ref ('card:447') or a raw name."""
	content_type, content_id = parse_item_ref(item_name)
	if not content_type:
		content_type, content_id = _resolve_name_to_ref(item_name)
	if not content_type:
		return False, f"❌ No matching item found named '{item_name}'."
	return add_to_deck_by_ref(deck, content_type, content_id, quantity)


def remove_from_deck(deck: dict, item_name: str, quantity: int = 1) -> tuple[bool, str]:
	"""Remove an item from a deck. item_name may be an encoded ref ('card:447') or a raw name."""
	content_type, content_id = parse_item_ref(item_name)
	if not content_type:
		content_type, content_id = _resolve_name_to_ref(item_name)
	if not content_type:
		return False, f"❌ No matching item found named '{item_name}'."
	return remove_from_deck_by_ref(deck, content_type, content_id, quantity)
