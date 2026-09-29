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
	# 2026-09-28_engagement_spans.sql. Read through the two views below.
	"engagement_spans",
})

# Retired content type. Kept in the database on purpose -- it still holds data
# worth referencing -- but nothing reads it at runtime, and RLS is deny-all, so
# an anon read of it looks exactly like an empty table.
ANON_NO_POLICY = frozenset({
	"rituals",
})

# Views over the INSERT-only turn-grain tables. They are `security_invoker` and
# granted to service_role ONLY, so anon cannot read them either -- which is the
# point: a view without the invoker flag runs as its owner and hands out the very
# rows the INSERT-only policy withholds. See the game repo's
# db/migrations/2026-08-31_turn_view_rls.sql.
#
# Listed here for the same reason the tables are. `player_info_view` in
# particular fails ALL the way to a wrong answer: /stats player would reply
# "No stats found for X" on an anon key, which reads as a player who has never
# played rather than as a key that cannot see them.
SERVICE_ROLE_ONLY_VIEWS = frozenset({
	"turn_clearing_view", "player_info_view", "turn_scoreboard_view",
	# 2026-09-28_ritual_stats.sql. Reads `turns` through run_cleared().
	"player_run_view",
	# (player_act_view, the turn-habits and the engagement views were dropped
	# 2026-09-29, db/migrations/2026-09-29_drop_retired_stats_views.sql.)
	# 2026-09-28_boss_fight_view.sql. Reads `turns`.
	"boss_fight_view",
	# 2026-09-28_breakdown_view.sql. Reads `turns` and `turn_nodes`.
	"breakdown_view",
	# 2026-09-28_player_summary_view.sql. Reads `engagement_spans`.
	"player_summary_view",
	# 2026-09-29_draft_offer_views.sql. Built on player_cohort_view.
	"draft_offer_view", "draft_item_offer_view",
	# 2026-09-29_leaderboard_best_view.sql. Reads `turns` for the furthest act.
	"leaderboard_best_view",
	# 2026-09-29_item_split_views.sql, for /stats item. Read `turns` or sit on
	# player_cohort_view.
	"boss_split_view", "draft_item_split_view", "hero_split_view",
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

ANON_UNREADABLE = ANON_INSERT_ONLY | ANON_NO_POLICY | SERVICE_ROLE_ONLY_VIEWS


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

	if table_name in ANON_INSERT_ONLY:
		reason = "it is INSERT-only for anon (the game writes it; nothing reads it back)"
	elif table_name in SERVICE_ROLE_ONLY_VIEWS:
		reason = ("it is a security_invoker view over the INSERT-only turn tables, "
		          "granted to service_role only")
	else:
		reason = "RLS is enabled on it with no SELECT policy (deny-all)"
	raise SupabaseUnreadableError(
		f"Cannot read `{table_name}` with a `{SUPABASE_ROLE}` key: {reason}. "
		f"PostgREST would return an empty result with HTTP 200, which looks "
		f"exactly like an empty table. Use the service-role key. "
		f"See docs/DB_SCHEMA.md \u00a7 Which key you are holding."
	)


# PostgREST's `max-rows` on this project: no response carries more, whatever
# is asked for. fetch_all pages in steps of it, so it must never be set above
# the server's value, or every page comes back short and reads as the last.
PAGE_SIZE = 1000

# Columns that identify a row on their own, preferred as the paging tiebreak.
_KEY_COLUMNS = ("id", "uuid")


def _order_spec(sort: list[str]) -> list[str]:
	"""["-created_at", "name"] -> ["created_at.desc", "name.asc"]."""
	return [f"{c[1:]}.desc" if c.startswith("-") else f"{c}.asc" for c in sort]


def _tiebreak(rows: list[dict], sorted_on: set) -> list[str]:
	"""Order terms that make a sort TOTAL, from what one page shows.

	A key column when the rows have one. Otherwise every column whose values
	on this page are all scalar: a `json` column cannot be ordered at all, and
	rows equal on every scalar column are, for any caller reading them,
	the same row -- which of two copies a page returns does not change the
	result.
	"""
	columns = [c for c in (rows[0] if rows else {}) if c not in sorted_on]
	keys = [c for c in _KEY_COLUMNS if c in columns]
	if keys:
		return [f"{keys[0]}.asc"]
	return [f"{c}.asc" for c in columns
	        if not any(isinstance(r.get(c), (dict, list)) for r in rows)]


def fetch_all(table_name: str, columns: list[str] = None, filters: dict = None, sort: list[str] = None, limit: int = None) -> list[dict]:
	"""Fetch records from a Supabase table: every matching row, however many.

	- columns: column names to select (defaults to '*')
	- filters: field -> value. None becomes `is null`, a list becomes `in`,
	  anything else becomes `eq`
	- sort: e.g. ["-created_at", "name"]; a leading '-' means descending.
	  Multiple columns apply left to right (this was broken until 2026-08-27 --
	  only the first took effect)
	- limit: the most rows wanted, pushed to PostgREST. Pass it whenever you
	  only need the top N: a limit of PAGE_SIZE or less is one request.

	PAGED (2026-09-29). PostgREST answers at most PAGE_SIZE rows per request
	and says nothing when it stops there, so a read of a larger table used to
	come back silently truncated -- `draft_item_offer_view` was ~500 rows and
	growing. A result that fits one page is still ONE request. When the first
	page comes back full, the read starts again from row 0 under a TOTAL order
	(the sort asked for, then `_tiebreak`) and pages through it: offset paging
	over an order with ties can return a row twice and skip another, and the
	unordered first page cannot be continued for the same reason.

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
	order = _order_spec(sort or [])

	def build(order_terms):
		query = supabase.table(table_name).select(selector)
		if filters:
			for key, value in filters.items():
				if value is None:
					query = query.is_(key, "null")
				elif isinstance(value, list):
					query = query.in_(key, value)
				else:
					query = query.eq(key, value)
		if order_terms:
			# ONE order call, comma-joined -- not one per column.
			#
			# postgrest-py's .order() does params.add("order", ...), so calling
			# it twice sends `order=a&order=b` and PostgREST honours only the
			# first. Every column after the first was silently dropped:
			# `sort=["usage_type", "name"]` grouped correctly and then ordered
			# arbitrarily WITHIN each group, which looks like a sort that works
			# until you read it closely.
			#
			# PostgREST wants `order=a.asc,b.desc`. The direction suffix is
			# explicit on every column because it has to be for the ones after
			# the first.
			query = query.order(",".join(order_terms))
		return query

	def run(query) -> list:
		try:
			response = query.execute()
		except Exception as e:
			raise SupabaseQueryError(f"select on `{table_name}` failed: {e}") from e
		return response.data or []

	def page(query, start: int) -> list:
		# postgrest-py 0.10.7 (pinned through supabase==1.0.3) sends
		# `Range: start-(end-1)`: its `end` is EXCLUSIVE. Later versions made
		# it inclusive. The loop below advances by the rows that came back and
		# stops on a short page, so it pages correctly under either reading.
		return run(query.range(start, start + PAGE_SIZE))

	if limit is not None and limit <= PAGE_SIZE:
		return run(build(order).limit(limit))

	first = page(build(order), 0)
	if len(first) < PAGE_SIZE:
		return first

	total = order + _tiebreak(first, {t.rsplit(".", 1)[0] for t in order})
	rows: list = []
	while limit is None or len(rows) < limit:
		chunk = page(build(total), len(rows))
		rows += chunk
		if len(chunk) < PAGE_SIZE:
			break
	return rows[:limit] if limit is not None else rows


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
# `ritual` was removed 2026-08-26 -- the concept is retired.
DECK_CONTENT_TYPES = ["card", "aspect", "rite"]
# `event` is the pre-rename spelling of `rite`. Both parse, so a ref picked from
# an autocomplete before the migration still works after it.
_ITEM_REF_RE = re.compile(r"^(card|aspect|rite|event):(\d+)$")


def _db_content_type(content_type: str) -> str:
	"""A Rite type in the spelling the database stores now; see rite_schema."""
	from azoth_logic import rite_schema
	return rite_schema.db_content_type(content_type)


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
		table_name = f"{content_type}s"  # e.g. 'cards', 'aspects', 'rites'

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
	content_type = _db_content_type(content_type)
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
	content_type = _db_content_type(content_type)
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
	for content_type in map(_db_content_type, DECK_CONTENT_TYPES):
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
