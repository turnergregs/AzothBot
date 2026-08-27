# azothbot/supabase_helpers.py
from supabase_client import supabase, SUPABASE_ROLE


class SupabaseError(RuntimeError):
	"""Base class for Supabase access failures raised by this module."""


class SupabaseQueryError(SupabaseError):
	"""A query reached Supabase and failed."""


class SupabaseUnreadableError(SupabaseError):
	"""The current API key provably cannot read this table.

	Raised BEFORE the query, because the failure is otherwise invisible:
	PostgREST answers an RLS-denied SELECT with HTTP 200 and an empty array,
	which is indistinguishable from an empty table.
	"""


# Tables the anon key cannot SELECT. Verified against pg_policies and
# pg_class.relrowsecurity on 2026-08-26; see docs/DB_SCHEMA.md § RLS posture.
#
# Two different causes, same symptom (a silent empty result):
#
#   INSERT-only policy      the game writes these, nobody reads them back
#   RLS on with NO policy   deny-all; not empty tables, invisible ones
#
# Keep in sync with docs/DB_SCHEMA.md. A table added here that is actually
# readable only costs a confusing error; one omitted costs silent wrong answers.
ANON_INSERT_ONLY = frozenset({
	"turns", "turn_nodes", "levelups", "reports",
})

# Retired content types. Kept in the database on purpose -- they still hold data
# worth referencing -- but nothing reads them at runtime, and RLS is deny-all, so
# an anon read of either looks exactly like an empty table.
ANON_NO_POLICY = frozenset({
	"rituals", "consumables",
})

# The six taxonomy tables that used to live here -- `card_attributes`,
# `card_elements`, `card_types`, `deck_types`, `deck_content_types`,
# `deck_usage_types` -- were DROPPED 2026-08-27. Their vocabularies moved into
# `azoth_logic/taxonomy.py`, next to the game constants they mirror.
#
# They are deliberately absent rather than kept "just in case", for the same
# reason `fate_types` is: a missing table already fails loudly with PGRST205,
# and listing it here would report a permissions problem for what is really a
# gone table.

ANON_UNREADABLE = ANON_INSERT_ONLY | ANON_NO_POLICY


def _assert_readable(table_name: str):
	"""Fail loudly when the loaded key cannot read this table.

	Only the service-role key can read everything. 'unknown' is treated as
	not-proven and gets the same guard, so a key format we don't recognise
	fails visibly rather than silently returning nothing.
	"""
	if SUPABASE_ROLE == "service_role":
		return
	if table_name not in ANON_UNREADABLE:
		return

	reason = (
		"it is INSERT-only for anon (the game writes it; nothing reads it back)"
		if table_name in ANON_INSERT_ONLY
		else "RLS is enabled on it with no SELECT policy (deny-all)"
	)
	raise SupabaseUnreadableError(
		f"Cannot read `{table_name}` with a `{SUPABASE_ROLE}` key: {reason}. "
		f"PostgREST would return an empty result with HTTP 200, which looks "
		f"exactly like an empty table. Use the service-role key. "
		f"See docs/DB_SCHEMA.md \u00a7 Which key you are holding."
	)


def fetch_all(table_name: str, columns: list[str] = None, filters: dict = None, sort: list[str] = None, limit: int = None) -> list[dict]:
	"""Fetch records from a Supabase table.

	- columns: column names to select (defaults to '*')
	- filters: field -> value. None becomes `is null`, a list becomes `in`,
	  anything else becomes `eq`
	- sort: e.g. ["-created_at", "name"]; a leading '-' means descending.
	  Multiple columns apply left to right (this was broken until 2026-08-27 --
	  only the first took effect)
	- limit: pushed to PostgREST. WITHOUT it, PostgREST caps the response at
	  1000 rows, so slicing the result in Python silently reads a truncated
	  page of a larger table. Pass a limit whenever you only need the top N.

	Returns [] ONLY when the query genuinely matched no rows. Every failure
	raises: this function used to swallow exceptions and return [], which made
	a missing table, an RLS denial and an empty result indistinguishable to
	callers -- all of which render as "not found" at the call sites.

	Raises:
		SupabaseUnreadableError: the loaded key cannot read this table
		SupabaseQueryError: the query failed
	"""
	_assert_readable(table_name)

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
		# ONE order call, comma-joined -- not one per column.
		#
		# postgrest-py's .order() does params.add("order", ...), so calling it
		# twice sends `order=a&order=b` and PostgREST honours only the first.
		# Every column after the first was silently dropped: `sort=["usage_type",
		# "name"]` grouped correctly and then ordered arbitrarily WITHIN each
		# group, which looks like a sort that works until you read it closely.
		#
		# PostgREST wants `order=a.asc,b.desc`. The direction suffix is explicit
		# on every column because it has to be for the ones after the first.
		spec = ",".join(
			f"{column[1:]}.desc" if column.startswith("-") else f"{column}.asc"
			for column in sort)
		query = query.order(spec)

	if limit is not None:
		query = query.limit(limit)

	try:
		response = query.execute()
	except Exception as e:
		raise SupabaseQueryError(f"select on `{table_name}` failed: {e}") from e

	return response.data or []


def create_record(table_name: str, data: dict):
	"""Insert a record. Raises SupabaseQueryError on failure."""
	try:
		response = supabase.table(table_name).insert(data).execute()
	except Exception as e:
		raise SupabaseQueryError(f"insert into `{table_name}` failed: {e}") from e
	return response.data


def update_record(table_name: str, record_id, data: dict):
	"""Update a record by id, stamping `updated_at`.

	Returns the updated rows (a list). An empty list means no row matched
	`record_id` -- distinct from a failure, which raises.
	"""
	from datetime import datetime, timezone

	try:
		data["updated_at"] = datetime.now(timezone.utc).isoformat()
		response = supabase.table(table_name).update(data).eq("id", record_id).execute()
	except Exception as e:
		raise SupabaseQueryError(f"update on `{table_name}` id={record_id} failed: {e}") from e
	return response.data


def delete_record(table_name: str, record_id):
	"""Hard-delete a record by id. Raises SupabaseQueryError on failure."""
	try:
		response = supabase.table(table_name).delete().eq("id", record_id).execute()
	except Exception as e:
		raise SupabaseQueryError(f"delete on `{table_name}` id={record_id} failed: {e}") from e
	return response.data


def soft_delete_record(table_name: str, record_id):
	"""Archive a record by setting `archived_at`.

	Returns the updated rows, matching update_record/delete_record.

	Previously this did `response.data` on update_record's return value -- which
	is already a list -- so it raised AttributeError on EVERY call, swallowed it,
	and returned None. /delete_deck and /delete_hero therefore always reported
	"Failed to delete" even when the archive succeeded.
	"""
	from datetime import datetime, timezone

	return update_record(
		table_name, record_id,
		{"archived_at": datetime.now(timezone.utc).isoformat()},
	)


def get_display_name(obj, type=None):
	"""The display name of a content record.

	Every content type uses `name`. Rituals used `challenge_name` and were the
	one exception; that table is dead as of 2026-08-26. `type` is kept so the
	many call sites don't all need editing, and is ignored.
	"""
	return obj.get("name")


import re

# Content types that participate in decks. Order is the legacy first-match
# priority used only for raw (manually-typed) names.
# `ritual` and `consumable` were removed 2026-08-26 -- both concepts are retired.
DECK_CONTENT_TYPES = ["card", "aspect", "event"]
_ITEM_REF_RE = re.compile(r"^(card|aspect|event):(\d+)$")


def name_column_for(content_type: str) -> str:
	"""The column holding a content type's display name.

	Uniformly `name` since the ritual table was retired (2026-08-26). Kept as a
	function so a future exception has one place to live.
	"""
	return "name"


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
