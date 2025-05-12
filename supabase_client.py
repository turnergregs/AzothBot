from supabase import create_client, Client
from functools import lru_cache
from azoth_commands.helpers import generate_image_filename, generate_local_filename
import os
import re

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Helpers
def handle_supabase_response(response, context="Supabase operation") -> tuple[bool, str | list]:
	# Case 1: response is success with .data attribute
	data = getattr(response, "data", None)
	if data is not None:
		return True, data

	# Case 2: response is dict-like success
	if isinstance(response, dict) and "data" in response:
		return True, response["data"]

	# Case 3: any form of error
	error = getattr(response, "error", None) or getattr(response, "message", None)
	if not error and isinstance(response, dict):
		error = response.get("error")

	msg = f"❌ {context} failed."
	if error:
		msg += f" {error}"
	return False, msg


# Enums and lookup tables

@lru_cache(maxsize=None)
def _get_display_map(table: str) -> list[str]:
	try:
		response = supabase.table(table).select("name").execute()
		data = getattr(response, "data", None)
		return [r["name"] for r in data] if data else []
	except Exception: return []

def get_card_element_choices() -> list[str]: return _get_display_map("card_elements")
def get_card_attribute_choices() -> list[str]: return _get_display_map("card_attributes")
def get_card_type_choices() -> list[str]: return _get_display_map("card_types")
def get_deck_type_choices() -> list[str]: return _get_display_map("deck_types")
def get_deck_content_type_choices() -> list[str]: return _get_display_map("deck_content_types")
def get_ritual_type_choices() -> list[str]: return _get_display_map("ritual_types")
def get_difficulty_choices() -> list[str]: return _get_display_map("ritual_difficulties")

def get_all_card_names() -> list[str]:
	try:
		response = supabase.table("cards").select("name").execute()
		data = getattr(response, "data", None)
		return sorted([r["name"] for r in data]) if data else []
	except Exception: return []

def get_all_deck_names() -> list[str]:
	try:
		response = supabase.table("decks").select("name").execute()
		data = getattr(response, "data", None)
		return sorted([r["name"] for r in data]) if data else []
	except Exception: return []

def get_all_ritual_names() -> list[str]:
	try:
		query = supabase.table("rituals").select(
			"*, bonus_side:ritual_sides!rituals_bonus_side_id_fkey(*), "
			"challenge_side:ritual_sides!rituals_challenge_side_id_fkey(*), "
			"event_side:ritual_sides!rituals_event_side_id_fkey(*)"
		)
		data = query.execute().data
		names = []

		for ritual in data:
			if ritual.get("type") == "ritual":
				name = ritual.get("challenge_side", {}).get("name")
			elif ritual.get("type") == "consumable":
				name = ritual.get("bonus_side", {}).get("name")
			elif ritual.get("type") == "event":
				name = ritual.get("event_side", {}).get("name")
			else:
				name = None

			if name:
				names.append(name)

		return names
	except Exception as e:
		print(f"Error fetching ritual names: {e}")
		return []

# Card CRUD

def get_card_by_name(name: str) -> tuple[bool, dict | str]:
	response = supabase.table("cards").select("*").ilike("name", name).limit(1).execute()
	success, data = handle_supabase_response(response, f"fetching card '{name}'")
	if not success:
		return False, data
	if not data:
		return False, f"❌ No card found with name `{name}`."
	return True, data[0]


def create_card(card_data: dict) -> tuple[bool, dict | str]:
	response = supabase.table("cards").insert(card_data).execute()
	success, data = handle_supabase_response(response, f"creating card '{card_data.get('name')}'")

	if not success or not data:
		return False, data
	
	return True, data[0]


def update_card_fields(card: dict, update_data: dict) -> tuple[bool, str]:
	# Build the diff before updating
	changes = []
	for key, new_val in update_data.items():
		old_val = card.get(key)

		if isinstance(new_val, list) and isinstance(old_val, list):
			if sorted(new_val) != sorted(old_val):
				changes.append(f"`{key}`: `{old_val}` → `{new_val}`")
		else:
			if old_val != new_val:
				changes.append(f"`{key}`: `{old_val}` → `{new_val}`")

	if not changes:
		return False, "⚠️ No actual changes detected (fields match existing values)."

	# Apply the update
	card_id = card["id"]
	response = supabase.table("cards").update(update_data).eq("id", card_id).execute()
	success, _ = handle_supabase_response(response, f"updating card ID {card_id}")
	if not success:
		return False, f"❌ Failed to update card ID {card_id}."

	return True, "\n".join(changes)


def delete_card_by_name(name: str) -> tuple[bool, str]:
	success, card = get_card_by_name(name)
	if not success:
		return False, card

	card_id = card["id"]

	# Check if the card is referenced in any decks
	response = supabase.table("deck_cards").select("deck_id").eq("card_id", card_id).execute()
	if response.data:
		# Get names of decks that use this card
		deck_ids = [row["deck_id"] for row in response.data]
		decks_response = supabase.table("decks").select("name").in_("id", deck_ids).execute()
		deck_names = [row["name"] for row in decks_response.data] if decks_response.data else ["unknown"]

		return False, f"❌ Cannot delete `{name}` — it’s used in deck(s): {', '.join(deck_names)}"

	# Safe to delete
	response = supabase.table("cards").delete().eq("id", card_id).execute()
	success, _ = handle_supabase_response(response, f"deleting card '{name}'")
	if success:
		return True, f"✅ Deleted card `{name}`"
	else:
		return False, f"❌ Failed to delete card `{name}`"


def upload_image(name: str, image_bytes: bytes, bucket: str) -> tuple[bool, str]:
	safe_name = re.sub(r'\W+', '_', name.lower()).strip('_')

	try:
		# List existing files to determine version
		response = supabase.storage.from_(bucket).list()
		if not isinstance(response, list):
			return False, f"Failed to list existing images: {response}"

		existing_versions = [
			int(match.group(1))
			for f in response
			if (match := re.match(rf"^{safe_name}_(\d+)\.png$", f.get("name", "")))
		]

		next_version = max(existing_versions, default=0) + 1
		file_name = generate_image_filename(name, next_version)

		upload_response = supabase.storage.from_(bucket).upload(
			file_name,
			image_bytes,
			{"content-type": "image/png", "x-upsert": "true"}
		)

		# Handle upload errors
		if hasattr(upload_response, "status_code") and upload_response.status_code >= 400:
			return False, f"Upload failed: {upload_response.text}"

		return True, file_name

	except Exception as e:
		return False, f"Exception during upload: {e}"


def download_image(image_name: str, bucket: str, download_dir: str = "assets/downloaded_images") -> tuple[bool, str]:
	"""
	Downloads the image from Supabase and saves it locally as {clean_name}.png.
	"""

	os.makedirs(download_dir, exist_ok=True)

	# Use regex to strip trailing _version from filename
	match = re.match(r"^(.*)_\d+\.png$", image_name)
	if match:
		base_name = match.group(1)
	else:
		# fallback to full name without extension
		base_name = os.path.splitext(image_name)[0]
	local_name = generate_local_filename(base_name)
	local_path = os.path.join(download_dir, local_name)

	try:
		data = supabase.storage.from_(bucket).download(image_name)
		with open(local_path, "wb") as f:
			f.write(data)
		return True, local_path

	except Exception as e:
		return False, f"Failed to download image: {e}"


# Deck CRUD

def get_deck_by_name(name: str) -> tuple[bool, dict | str]:
	response = supabase.table("decks").select("*").ilike("name", name).limit(1).execute()
	success, data = handle_supabase_response(response, f"fetching deck '{name}'")
	if not success or not data:
		return False, data
	return True, data[0]

def create_deck(deck_data: dict) -> tuple[bool, str]:
	try:
		response = supabase.table("decks").insert(deck_data).execute()
		success, data = handle_supabase_response(response, "creating deck")
		if not success:
			return False, data
		return True, f"✅ Created deck `{deck_data['name']}`"
	except Exception as e:
		return False, f"❌ Deck creation failed: {e}"

def update_deck_fields(deck_id: int, update_data: dict) -> tuple[bool, str]:
	response = supabase.table("decks").update(update_data).eq("id", deck_id).execute()
	success, result = handle_supabase_response(response, f"updating deck ID {deck_id}")
	if success:
		return True, f"✅ Updated deck: {', '.join(update_data.keys())}"
	else:
		return False, result

def delete_deck_by_name(name: str) -> tuple[bool, str]:
	from datetime import datetime

	success, deck = get_deck_by_name(name)
	if not success:
		return False, deck

	# Check if the deck has contents
	success, contents = get_deck_contents(deck)
	if not success:
		return False, f"Could not load contents for `{name}`: {contents}"

	if not contents:  # Deck is empty → hard delete
		response = supabase.table("decks").delete().eq("id", deck["id"]).execute()
		success, result = handle_supabase_response(response, f"deleting deck '{name}'")
		if success:
			return True, f"🗑️ Deleted empty deck `{name}`"
		else:
			return False, result

	else:  # Deck has contents → soft delete
		archived_time = datetime.utcnow().isoformat()
		response = supabase.table("decks").update({"archived_at": archived_time}).eq("id", deck["id"]).execute()
		success, result = handle_supabase_response(response, f"archiving deck '{name}'")
		if success:
			return True, f"🗂️ Deck `{name}` archived (has contents)"
		else:
			return False, result


def get_deck_contents(deck: dict, full: bool = False) -> tuple[bool, list[dict | str] | str]:
	"""
	Retrieves the contents of a deck.
	If full=False: returns a list of names (used by /get_deck).
	If full=True: returns full dicts with all attributes (used by render).
	"""
	content_type = deck["content_type"]
	if content_type == "cards":
		table = "deck_cards"
		id_field = "card_id"
		data_table = "cards"
	elif content_type == "rituals":
		table = "deck_contents"
		id_field = "ritual_id"
		data_table = "rituals"
	else:
		return False, f"Unsupported content type: {content_type}"

	# Fetch raw deck entry rows
	response = supabase.table(table).select(f"{id_field}, quantity" if content_type == "rituals" else f"{id_field}").eq("deck_id", deck["id"]).execute()
	success, rows = handle_supabase_response(response, f"fetching deck contents for {deck['name']}")
	if not success or not rows:
		return True, []

	# Collect all referenced IDs
	id_list = [r[id_field] for r in rows]
	if not id_list:
		return True, []

	# Fetch full content records
	response = supabase.table(data_table).select("*").in_("id", id_list).execute()
	success, data = handle_supabase_response(response, f"fetching {content_type} records")
	if not success:
		return False, data

	id_to_obj = {r["id"]: r for r in data}

	if full:
		if content_type == "cards":
			# Return one entry per row (no quantity)
			return True, [id_to_obj[r[id_field]] for r in rows if r[id_field] in id_to_obj]
		else:  # rituals with quantity
			final = []
			for row in rows:
				item = id_to_obj.get(row[id_field])
				if item:
					final.extend([item] * row["quantity"])
			return True, final
	else:
		# Return list of names only
		return True, sorted([id_to_obj[r[id_field]]["name"] for r in rows if r[id_field] in id_to_obj])


def add_to_deck(deck: dict, item_name: str, quantity: int) -> tuple[bool, str]:
	content_type = deck["content_type"]
	table, id_field, name_table = {
		"cards": ("deck_cards", "card_id", "cards"),
		"rituals": ("deck_contents", "ritual_id", "rituals")
	}.get(content_type, (None, None, None))

	if not table:
		return False, f"Unsupported content_type: {content_type}"

	# Lookup card/ritual by name
	response = supabase.table(name_table).select("id").ilike("name", item_name).limit(1).execute()
	success, data = handle_supabase_response(response, f"finding {content_type} '{item_name}'")
	if not success or not data:
		return False, f"{content_type[:-1].capitalize()} '{item_name}' not found."

	item_id = data[0]["id"]

	if content_type == "cards":
		# Insert one row per copy
		rows = [{"deck_id": deck["id"], id_field: item_id} for _ in range(quantity)]
		supabase.table(table).insert(rows).execute()
	else:
		# Add/update quantity (same as before)
		check = supabase.table(table).select("quantity").eq("deck_id", deck["id"]).eq(id_field, item_id).execute()
		exists = check.data[0]["quantity"] if check.data else 0
		new_quantity = exists + quantity
		if exists:
			supabase.table(table).update({"quantity": new_quantity}).eq("deck_id", deck["id"]).eq(id_field, item_id).execute()
		else:
			supabase.table(table).insert({"deck_id": deck["id"], id_field: item_id, "quantity": quantity}).execute()

	return True, f"✅ Added {quantity}x `{item_name}` to `{deck['name']}`"

def remove_from_deck(deck: dict, item_name: str, quantity: int) -> tuple[bool, str]:
	content_type = deck["content_type"]
	table, id_field, name_table = {
		"cards": ("deck_cards", "card_id", "cards"),
		"rituals": ("deck_contents", "ritual_id", "rituals")
	}.get(content_type, (None, None, None))

	if not table:
		return False, f"Unsupported content_type: {content_type}"

	# Lookup item ID
	response = supabase.table(name_table).select("id").ilike("name", item_name).limit(1).execute()
	success, data = handle_supabase_response(response, f"finding {content_type} '{item_name}'")
	if not success or not data:
		return False, f"{content_type[:-1].capitalize()} '{item_name}' not found."

	item_id = data[0]["id"]

	if content_type == "cards":
		# Fetch all matching rows
		rows = supabase.table(table).select("id").eq("deck_id", deck["id"]).eq(id_field, item_id).execute()
		matching_ids = [r["id"] for r in rows.data]

		if not matching_ids:
			return False, f"`{item_name}` is not in `{deck['name']}`."

		to_delete = matching_ids[:quantity]
		for row_id in to_delete:
			supabase.table(table).delete().eq("id", row_id).execute()

		return True, f"🗑️ Removed {len(to_delete)}x `{item_name}` from `{deck['name']}`"

	else:
		# Ritual decks still use quantity model
		check = supabase.table(table).select("quantity").eq("deck_id", deck["id"]).eq(id_field, item_id).execute()
		if not check.data:
			return False, f"`{item_name}` is not in `{deck['name']}`."

		existing = check.data[0]["quantity"]
		new_quantity = existing - quantity

		if new_quantity > 0:
			supabase.table(table).update({"quantity": new_quantity}).eq("deck_id", deck["id"]).eq(id_field, item_id).execute()
		else:
			supabase.table(table).delete().eq("deck_id", deck["id"]).eq(id_field, item_id).execute()

		return True, f"🗑️ Removed {min(quantity, existing)}x `{item_name}` from `{deck['name']}`"


# Ritual CRUD

def get_ritual_by_name(name: str) -> tuple[bool, str | dict]:
	try:
		# Try to find the ritual by checking each side table for a matching name
		side_resp = supabase.table("ritual_sides").select("*").ilike("name", name).execute()
		if not side_resp.data:
			return False, f"No ritual found with side name '{name}'"

		ritual_side = side_resp.data[0]
		side_id = ritual_side["id"]

		bonus = supabase.table("rituals").select("*").eq("bonus_side_id", side_id).execute()
		challenge = supabase.table("rituals").select("*").eq("challenge_side_id", side_id).execute()
		event = supabase.table("rituals").select("*").eq("event_side_id", side_id).execute()
		data = (bonus.data or []) + (challenge.data or []) + (event.data or [])

		if len(data) == 0:
			return False, f"No ritual found with side ID {side_id}"
		ritual = data[0]

		# Inject side data into ritual object
		if ritual.get("bonus_side_id") == side_id:
			ritual["bonus_side"] = ritual_side
		elif ritual.get("challenge_side_id") == side_id:
			ritual["challenge_side"] = ritual_side
		elif ritual.get("event_side_id") == side_id:
			ritual["event_side"] = ritual_side

		# Fetch and inject other sides if applicable
		if ritual.get("bonus_side_id") and "bonus_side" not in ritual:
			bonus = supabase.table("ritual_sides").select("*").eq("id", ritual["bonus_side_id"]).single().execute()
			if bonus.data:
				ritual["bonus_side"] = bonus.data

		if ritual.get("challenge_side_id") and "challenge_side" not in ritual:
			chal = supabase.table("ritual_sides").select("*").eq("id", ritual["challenge_side_id"]).single().execute()
			if chal.data:
				ritual["challenge_side"] = chal.data

		if ritual.get("event_side_id") and "event_side" not in ritual:
			ev = supabase.table("ritual_sides").select("*").eq("id", ritual["event_side_id"]).single().execute()
			if ev.data:
				ritual["event_side"] = ev.data

		return True, ritual

	except Exception as e:
		return False, f"Error fetching ritual by name: {e}"


def create_ritual(ritual_data: dict) -> tuple[bool, dict | str]:
	response = supabase.table("rituals").insert(ritual_data).execute()
	success, data = handle_supabase_response(response, f"creating ritual")

	if not success or not data:
		return False, data
	
	return True, data[0]


def create_ritual_side(ritual_side_data: dict) -> tuple[bool, dict | str]:
	response = supabase.table("ritual_sides").insert(ritual_side_data).execute()
	success, data = handle_supabase_response(response, f"creating ritual_side '{ritual_side_data.get('name')}'")

	if not success or not data:
		return False, data
	
	return True, data[0]


def update_ritual_fields(ritual_id: str, update_data: dict) -> tuple[bool, str]:
	try:
		result = supabase.table("rituals").update(update_data).eq("id", ritual_id).execute()
		return True, "✅ Ritual updated." if result.data else "⚠️ No changes made."
	except Exception as e:
		return False, f"❌ Failed to update ritual: {e}"


def update_ritual_side_fields(side_id: str, update_data: dict) -> tuple[bool, str]:
	try:
		result = supabase.table("ritual_sides").update(update_data).eq("id", side_id).execute()
		return True, "✅ Ritual side updated." if result.data else "⚠️ No changes made to side."
	except Exception as e:
		return False, f"❌ Failed to update ritual side: {e}"


def delete_ritual_by_name(name: str) -> tuple[bool, str]:
	success, ritual = get_ritual_by_name(name)
	if not success:
		return False, ritual

	ritual_id = ritual["id"]

	# Step 1: Check if used in any decks
	response = supabase.table("deck_rituals").select("deck_id").eq("ritual_id", ritual_id).execute()
	if response.data:
		deck_ids = [row["deck_id"] for row in response.data]
		decks_response = supabase.table("decks").select("name").in_("id", deck_ids).execute()
		deck_names = [row["name"] for row in decks_response.data] if decks_response.data else ["unknown"]
		return False, f"❌ Cannot delete `{name}` — used in deck(s): {', '.join(deck_names)}"

	# Step 2: Delete the ritual itself
	response = supabase.table("rituals").delete().eq("id", ritual_id).execute()
	success, _ = handle_supabase_response(response, f"deleting ritual '{name}'")
	if not success:
		return False, f"❌ Failed to delete ritual `{name}`"

	# Step 3: Delete its associated ritual_sides
	side_ids = []
	for side_key in ["bonus_side", "challenge_side", "event_side"]:
		if side_key in ritual and isinstance(ritual[side_key], dict):
			side_id = ritual[side_key].get("id")
			if side_id:
				side_ids.append(side_id)

	for side_id in side_ids:
		try:
			supabase.table("ritual_sides").delete().eq("id", side_id).execute()
		except Exception as e:
			print(f"⚠️ Failed to delete ritual_side {side_id}: {e}")

	return True, f"✅ Deleted ritual `{name}`"


