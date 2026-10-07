"""`/bulk_add_to_deck`: put many existing items into existing decks at once.

`/add_to_deck` takes one item per call, which is fine for a fix and tedious for a
content drop -- a pack of fifteen new cards and aspects is fifteen commands, each
picked from autocomplete. This takes one JSON file instead:

    {"decks": [
        {"name": "Testing Fates", "contents": [
            {"content_type": "aspect", "name": "Pittance"},
            {"content_type": "aspect", "name": "Stipend"}]},
        {"name": "Base Draft Deck", "contents": [
            {"name": "Gouge"},
            {"content_type": "card", "content_id": 447}]}
    ]}

An entry is the same shape `/bulk_insert` accepts under a new deck's `contents`
(game repo, skills/content-creation/references/EXPORT_FORMAT.md § decks[]):
`content_type` is card (default), aspect or rite; the item is named by
`content_id` or by `name`; one entry per copy. So a file can be written by hand
or cut out of an insert payload.

ALL OR NOTHING

Every deck and every item is resolved BEFORE anything is written, and every
problem is reported at once rather than the first one. The write is then a single
INSERT of every `deck_contents` row -- one PostgREST request, so one transaction.
There is no half-added state to clean up.

NAMES ARE EXACT

Unlike `/add_to_deck`'s typed-name fallback, which takes the first match across
all content types, an entry's type is part of the lookup -- a card and an aspect
may share a name. A name matching two rows of the same type is refused rather
than guessed.
"""
from __future__ import annotations

import supabase_helpers

TYPES = ("card", "aspect", "rite")


class DeckBulkError(Exception):
    """The payload was rejected. Nothing was written."""

    def __init__(self, lines: list[str]):
        self.lines = lines
        super().__init__("\n".join(lines))


def parse(payload) -> list[dict]:
    """The payload's decks and entries, shape-checked. Raises DeckBulkError.

    Returns `[{"name": deck_name, "contents": [{"content_type", "name", "content_id"}]}]`
    with `content_type` filled in and exactly one of `name` / `content_id` set.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("decks"), list):
        raise DeckBulkError(['JSON must be an object with a "decks" list.'])
    if not payload["decks"]:
        raise DeckBulkError(['"decks" is empty — nothing to add.'])

    errors: list[str] = []
    decks: list[dict] = []
    for i, deck in enumerate(payload["decks"]):
        where = f"decks[{i}]"
        if not isinstance(deck, dict) or not isinstance(deck.get("name"), str) or not deck["name"].strip():
            errors.append(f"{where}: needs a deck `name`.")
            continue
        contents = deck.get("contents")
        if not isinstance(contents, list) or not contents:
            errors.append(f"{where} ({deck['name']}): needs a non-empty `contents` list.")
            continue

        entries = []
        for j, entry in enumerate(contents):
            at = f"{where}.contents[{j}]"
            if not isinstance(entry, dict):
                errors.append(f"{at}: must be an object.")
                continue
            content_type = str(entry.get("content_type") or "card").lower()
            if content_type == "event":
                content_type = "rite"
            if content_type not in TYPES:
                errors.append(f"{at}: content_type must be one of {', '.join(TYPES)} (got `{content_type}`).")
                continue
            has_id = entry.get("content_id") is not None
            has_name = isinstance(entry.get("name"), str) and entry["name"].strip() != ""
            if has_id == has_name:
                errors.append(f"{at}: name the item by exactly one of `name` or `content_id`.")
                continue
            entries.append({
                "content_type": content_type,
                "name": entry["name"].strip() if has_name else None,
                "content_id": entry.get("content_id") if has_id else None,
            })
        decks.append({"name": deck["name"].strip(), "contents": entries})

    if errors:
        raise DeckBulkError(errors)
    return decks


def resolve(decks: list[dict]) -> list[dict]:
    """Every deck and item looked up. Raises DeckBulkError listing every miss.

    Returns `[{"deck": deck_row, "items": [(db_content_type, item_row)]}]`.
    """
    errors: list[str] = []
    resolved: list[dict] = []
    # The same item is often named once per deck; look each up once.
    cache: dict = {}

    for deck in decks:
        rows = supabase_helpers.fetch_all("decks", filters={"name": deck["name"]})
        if not rows:
            errors.append(f"No deck named `{deck['name']}`.")
        elif len(rows) > 1:
            errors.append(f"{len(rows)} decks are named `{deck['name']}`; rename one first.")

        items = []
        for entry in deck["contents"]:
            db_type = supabase_helpers._db_content_type(entry["content_type"])
            key = (db_type, entry["name"], entry["content_id"])
            if key not in cache:
                if entry["content_id"] is not None:
                    filters = {"id": entry["content_id"]}
                    label = f"{entry['content_type']} #{entry['content_id']}"
                else:
                    filters = {supabase_helpers.name_column_for(db_type): entry["name"]}
                    label = f"{entry['content_type']} `{entry['name']}`"
                matches = supabase_helpers.fetch_all(f"{db_type}s", filters=filters)
                if not matches:
                    cache[key] = f"No {label}."
                elif len(matches) > 1:
                    ids = ", ".join(str(m.get("id")) for m in matches)
                    cache[key] = f"{label} matches {len(matches)} rows (ids {ids}); use `content_id`."
                else:
                    cache[key] = matches[0]
            found = cache[key]
            if isinstance(found, str):
                errors.append(f"{deck['name']}: {found}")
            else:
                items.append((db_type, found))

        if rows and len(rows) == 1:
            resolved.append({"deck": rows[0], "items": items})

    if errors:
        # Deduplicated in order: one missing item named for two decks is still
        # worth reporting per deck, but not twice for the same deck.
        raise DeckBulkError(list(dict.fromkeys(errors)))
    return resolved


def join_rows(resolved: list[dict]) -> list[dict]:
    """The `deck_contents` rows to insert, one per copy."""
    return [
        {"deck_id": entry["deck"]["id"], "content_type": db_type, "content_id": item["id"]}
        for entry in resolved
        for db_type, item in entry["items"]
    ]


def apply(payload) -> list[dict]:
    """Parse, resolve, then write every row in one INSERT. Returns `resolve`'s plan.

    Raises DeckBulkError when anything is wrong; in that case nothing was written.
    """
    plan = resolve(parse(payload))
    rows = join_rows(plan)
    try:
        supabase_helpers.create_record("deck_contents", rows)
    except supabase_helpers.SupabaseQueryError as e:
        raise DeckBulkError([f"Insert failed, nothing was added: {e}"]) from e
    for entry in plan:
        supabase_helpers.update_record("decks", entry["deck"]["id"], {})
    return plan


def report_lines(plan: list[dict]) -> dict[str, list[str]]:
    """Per deck name, one line per item added (copies folded into `xN`)."""
    out: dict[str, list[str]] = {}
    for entry in plan:
        counts: dict = {}
        for db_type, item in entry["items"]:
            key = (db_type, item.get("id"), item.get("name"))
            counts[key] = counts.get(key, 0) + 1
        lines = []
        for (db_type, item_id, name), n in counts.items():
            copies = f" x{n}" if n > 1 else ""
            lines.append(f"{name} ({db_type} #{item_id}){copies}")
        out[entry["deck"]["name"]] = lines
    return out
