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
			if isinstance(value, tuple):
				op, v = value

                if op == ">=":
                    query = query.gte(key, v)
                elif op == ">":
                    query = query.gt(key, v)
                elif op == "<=":
                    query = query.lte(key, v)
                elif op == "<=":
                    query = query.lt(key, v)
                elif op == "!=":
                    query = query.neq(key, v)
                else:
                    raise ValueError(f"Unsupported filter operator: {op}")
			if isinstance(value, list):
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


def add_to_deck(deck: dict, item_name: str, quantity: int = 1) -> tuple[bool, str]:
	deck_id = deck.get("id")
	if not deck_id:
		return False, "Deck missing ID."

	# Check all supported content tables
	for content_type in ["card", "aspect", "event", "ritual", "consumable"]:
		name_column = "challenge_name" if content_type == "ritual" else "name"
		table_name = f"{content_type}s"

		records = fetch_all(table_name, filters={name_column: item_name})
		if not records:
			continue

		content_id = records[0]["id"]

		for _ in range(quantity):
			create_record("deck_contents", {
				"deck_id": deck_id,
				"content_id": content_id,
				"content_type": content_type
			})

		return True, f"✅ Added {quantity}x **{item_name}** to deck **{deck['name']}**."

	return False, f"❌ No matching item found named '{item_name}'."


def remove_from_deck(deck: dict, item_name: str, quantity: int = 1) -> tuple[bool, str]:
	deck_id = deck.get("id")
	if not deck_id:
		return False, "Deck missing ID."

	for content_type in ["card", "aspect", "event", "ritual", "consumable"]:
		name_column = "challenge_name" if content_type == "ritual" else "name"
		table_name = f"{content_type}s"

		records = fetch_all(table_name, filters={name_column: item_name})
		if not records:
			continue

		content_id = records[0]["id"]

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

	return False, f"❌ No matching item found named '{item_name}'."
