from supabase import create_client, Client
from dotenv import load_dotenv
from functools import lru_cache
import os

load_dotenv()

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

def get_element_choices() -> list[str]: return _get_display_map("elements")
def get_attribute_choices() -> list[str]: return _get_display_map("attributes")
def get_type_choices() -> list[str]: return _get_display_map("types")

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
	return handle_supabase_response(response, f"creating card '{card_data.get('name')}'")

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
	response = supabase.table("cards").delete().eq("id", card["id"]).execute()
	success, result = handle_supabase_response(response, f"deleting card '{name}'")
	if success: 
		return True, f"✅ Deleted {name}"
	else:
		return False, result

def upload_card_image(card_name: str, image_bytes: bytes) -> tuple[bool, str]:
	file_name = f"{card_name}.png"
	bucket = "cards"
	response = supabase.storage.from_(bucket).upload(
		file_name,
		image_bytes,
		{"content-type": "image/png", "upsert": True}
	)
	success, data = handle_supabase_response(response, f"uploading image for '{card_name}'")
	if not success:
		return False, data
	return True, f"{bucket}/{file_name}"

# Deck CRUD

def get_deck_by_name(name: str) -> tuple[bool, dict | str]:
	response = supabase.table("decks").select("*").ilike("name", name).limit(1).execute()
	success, data = handle_supabase_response(response, f"fetching deck '{name}'")
	if not success or not data:
		return False, data
	return True, data[0]

def create_deck(deck_data: dict) -> tuple[bool, dict | str]:
	response = supabase.table("decks").insert(deck_data).execute()
	return handle_supabase_response(response, f"creating deck '{deck_data.get('name')}'")

def delete_deck_by_name(name: str) -> tuple[bool, str]:
	success, deck = get_deck_by_name(name)
	if not success:
		return False, deck
	response = supabase.table("decks").delete().eq("id", deck["id"]).execute()
	return handle_supabase_response(response, f"deleting deck '{name}'")

def update_deck_fields(deck_id: int, update_data: dict) -> tuple[bool, str]:
	response = supabase.table("decks").update(update_data).eq("id", deck_id).execute()
	success, data = handle_supabase_response(response, f"updating deck ID {deck_id}")
	if not success or not data:
		return False, data
	return True, ", ".join(update_data.keys())
