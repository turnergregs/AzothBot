from supabase import create_client, Client
from dotenv import load_dotenv
from functools import lru_cache
import os

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Helpers
@lru_cache(maxsize=None)
def _get_display_map(table: str) -> list[tuple[str, str]]:
	response = supabase.table(table).select("name", "display_name").execute()
	if not response.data:
		return []
	return [(r["display_name"], r["name"]) for r in response.data]

def get_element_choices() -> list[tuple[str, str]]:
	return _get_display_map("elements")

def get_attribute_choices() -> list[tuple[str, str]]:
	return _get_display_map("attributes")

def get_type_choices() -> list[tuple[str, str]]:
	return _get_display_map("card_types")

def get_all_card_names() -> list[str]:
	response = supabase.table("cards").select("name").execute()
	return sorted([card["name"] for card in response.data]) if response.data else []

def get_all_deck_names() -> list[str]:
	response = supabase.table("decks").select("name").execute()
	return sorted([deck["name"] for deck in response.data]) if response.data else []

# Card CRUD
def get_card_by_name(name: str) -> dict | None:
	response = supabase.table("cards").select("*").ilike("name", name).limit(1).execute()
	return response.data[0] if response.data else None

def create_card(card_data: dict) -> dict | None:
	response = supabase.table("cards").insert(card_data).execute()
	return response.data[0] if response.data else None

def update_card_fields(card_id: int, update_data: dict) -> dict | None:
	response = supabase.table("cards").update(update_data).eq("id", card_id).execute()
	return response.data[0] if response.data else None

def delete_card_by_name(name: str) -> bool:
	card = get_card_by_name(name)
	if not card:
		return False
	supabase.table("cards").delete().eq("id", card["id"]).execute()
	return True

def upload_card_image(card_name: str, image_bytes: bytes) -> str:
	file_name = f"{card_name}.png"
	bucket = "cards"
	supabase.storage.from_(bucket).upload(file_name, image_bytes, {"content-type": "image/png", "upsert": True})
	return f"{bucket}/{file_name}"

# Deck CRUD
def get_deck_by_name(name: str) -> dict | None:
	response = supabase.table("decks").select("*").ilike("name", name).limit(1).execute()
	return response.data[0] if response.data else None

def create_deck(deck_data: dict) -> dict | None:
	response = supabase.table("decks").insert(deck_data).execute()
	return response.data[0] if response.data else None

def delete_deck_by_name(name: str) -> bool:
	deck = get_deck_by_name(name)
	if not deck:
		return False
	supabase.table("decks").delete().eq("id", deck["id"]).execute()
	return True

def update_deck_fields(deck_id: int, update_data: dict) -> dict | None:
	response = supabase.table("decks").update(update_data).eq("id", deck_id).execute()
	return response.data[0] if response.data else None

