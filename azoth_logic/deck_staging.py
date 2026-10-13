"""`/merge_staging` and `/stage`: content in and out of the Staging deck.

`/merge_staging` moves everything in the Staging deck into the decks that deal
it. `/stage` is the other direction, one item at a time (see STAGING ONE ITEM).

Cards, aspects and rites for the next release are uploaded early into the deck
whose usage type is `staging` (the game's `DeckManager.STAGING_USAGE_TYPE`).
Every build downloads that deck and none deals from it, so the rows can exist
before the build that needs them ships. At the release they go into live decks,
and from then on every build deals them. This is that step.

NO DECK IDS

The command this replaces pinned four ids (21 Staging, 22, 20, 3) and was hidden
2026-08-27 when they stopped meaning what its constants said. Nothing is pinned
here. The Staging deck is the unarchived base deck with usage type `staging`. A
staged item goes to the unarchived base deck, of the usage type that deals its
kind (`POOL`), that ALREADY HOLDS THE MOST of that kind: aspects join the deck
the aspects are in, combo cards (no valence and no element) the deck the combo
cards are in, every other card the deck the other cards are in, rites the rites
deck. Renaming, renumbering or replacing a deck needs no change here.

ALL OR NOTHING BEFORE THE WRITE

A kind with no deck holding any of it, or with two decks tied, is not guessed:
every such problem is reported at once and nothing moves.

ROWS MOVE, THEY ARE NOT COPIED

The old command deleted each row from Staging and then inserted a new one, one
request per copy, so a failure part-way lost content. Here a destination is one
UPDATE of `deck_contents.deck_id`: a row is in exactly one deck at every moment.
The destinations are still separate requests (PostgREST has no transaction
across them), so a failure between two leaves the later ones in Staging, and
running the command again moves the rest.

STAGING ONE ITEM

`/stage` pulls every copy of one item out of the decks `/merge_staging` would
consider for it (the unarchived base decks of the usage type that deals its
kind) and into Staging, by the same UPDATE. Copies in any other deck, a starter
or curse deck say, are left where they are and named in the reply: the item is
still dealt from there. An item in no such deck gets one copy added to Staging,
unless Staging already holds it.

The two are not a round trip for a single item. `/merge_staging` empties ALL of
Staging, and sends each item to the deck holding the most of its kind, which is
not necessarily the deck `/stage` took it from.
"""
from __future__ import annotations

import supabase_helpers
from azoth_logic import rite_schema

STAGING_USAGE_TYPE = "staging"

# What a staged item is, for routing -> the usage type of the decks that deal it.
POOL = {"card": "draft", "combo card": "draft", "aspect": "draft", "rite": "weighted"}

_ITEM_COLUMNS = {"card": ["id", "name", "valence", "element"]}


class StagingError(Exception):
    """The merge was refused or failed. `lines` says what and what state it left."""

    def __init__(self, lines: list[str]):
        self.lines = lines
        super().__init__("\n".join(lines))


def _content_type(raw) -> str | None:
    """A `deck_contents.content_type` as card / aspect / rite, None for anything else."""
    if rite_schema.is_rite(raw):
        return "rite"
    return raw if raw in ("card", "aspect") else None


def kind_of(content_type: str, item: dict) -> str:
    """The routing kind of an item: a key of `POOL`."""
    if content_type == "card" and item.get("valence") is None and item.get("element") is None:
        return "combo card"
    return content_type


def _items(content_types: set) -> dict:
    """`(content_type, id) -> row` for every row of the named types."""
    items = {}
    for content_type in sorted(content_types):
        columns = _ITEM_COLUMNS.get(content_type, ["id", "name"])
        for row in supabase_helpers.fetch_all(_table(content_type), columns=columns):
            items[(content_type, row["id"])] = row
    return items


def _table(content_type: str) -> str:
    return supabase_helpers._db_content_type(content_type) + "s"


def _decks() -> tuple[dict, dict]:
    """The Staging deck, and `id -> deck` for every unarchived base deck. Raises StagingError."""
    decks = supabase_helpers.fetch_all("decks", filters={"archived_at": None, "type": "base"})
    staging_decks = [d for d in decks if d.get("usage_type") == STAGING_USAGE_TYPE]
    if not staging_decks:
        raise StagingError([f"No unarchived deck has the usage type `{STAGING_USAGE_TYPE}`."])
    if len(staging_decks) > 1:
        names = ", ".join(f"`{d['name']}`" for d in staging_decks)
        raise StagingError([f"{len(staging_decks)} decks have the usage type "
                            f"`{STAGING_USAGE_TYPE}` ({names}); archive all but one first."])
    return staging_decks[0], {d["id"]: d for d in decks}


def plan() -> dict:
    """Where every staged row goes. Reads only. Raises StagingError.

    Returns `{"staging": deck_row, "moves": [{"deck": deck_row, "rows": [...]}]}`,
    one move per destination deck, each row
    `{"id": deck_contents id, "content_type", "kind", "item": content row}`.
    `moves` is empty when Staging is.
    """
    staging, decks = _decks()
    pools = {i: d for i, d in decks.items() if d.get("usage_type") in POOL.values()}

    rows = supabase_helpers.fetch_all(
        "deck_contents", columns=["id", "deck_id", "content_id", "content_type"],
        filters={"deck_id": [staging["id"], *pools]})
    staged = [r for r in rows if r["deck_id"] == staging["id"]]
    if not staged:
        return {"staging": staging, "moves": []}

    items = _items({_content_type(r["content_type"]) for r in rows} - {None})

    # kind -> deck id -> how many of that kind the deck holds, counting only
    # decks of the usage type that deals the kind.
    held: dict = {}
    for row in rows:
        content_type = _content_type(row["content_type"])
        item = items.get((content_type, row["content_id"]))
        deck = pools.get(row["deck_id"])
        if deck is None or item is None:
            continue
        kind = kind_of(content_type, item)
        if deck.get("usage_type") == POOL[kind]:
            held.setdefault(kind, {})
            held[kind][deck["id"]] = held[kind].get(deck["id"], 0) + 1

    errors: list[str] = []
    by_kind: dict = {}
    for row in staged:
        content_type = _content_type(row["content_type"])
        if content_type is None:
            errors.append(f"`{staging['name']}` holds a `{row['content_type']}` "
                          f"(#{row['content_id']}), which no deck deals.")
            continue
        item = items.get((content_type, row["content_id"]))
        if item is None:
            errors.append(f"`{staging['name']}` holds {content_type} #{row['content_id']}, "
                          f"which no longer exists; remove it first.")
            continue
        kind = kind_of(content_type, item)
        by_kind.setdefault(kind, []).append(
            {"id": row["id"], "content_type": content_type, "kind": kind, "item": item})

    moves: dict = {}
    for kind, kind_rows in by_kind.items():
        counts = held.get(kind, {})
        best = max(counts.values(), default=0)
        top = [deck_id for deck_id, n in counts.items() if n == best]
        names = ", ".join(sorted({r["item"]["name"] for r in kind_rows}))
        if not top:
            errors.append(f"No `{POOL[kind]}` deck holds a {kind} yet, so there is nowhere "
                          f"to send {names}. Put one in its deck with `/add_to_deck` first.")
        elif len(top) > 1:
            tied = " and ".join(f"`{pools[d]['name']}`" for d in sorted(top))
            errors.append(f"{tied} hold {best} {kind}s each, so the deck for {names} "
                          f"would be a guess. Move them with `/bulk_add_to_deck`.")
        else:
            moves.setdefault(top[0], {"deck": pools[top[0]], "rows": []})["rows"] += kind_rows

    if errors:
        raise StagingError(errors)
    return {"staging": staging,
            "moves": sorted(moves.values(), key=lambda m: m["deck"]["name"])}


def apply(merge: dict) -> list[str]:
    """Carry out `plan()`'s moves. Returns warnings; raises StagingError on a failed move.

    One UPDATE per destination deck. Each names its rows AND the Staging deck, so
    a row removed or moved since the plan was read is left alone.
    """
    staging = merge["staging"]
    return _move([([r["id"] for r in move["rows"]], staging, move["deck"])
                  for move in merge["moves"]], staging)


def _move(steps: list, staging: dict) -> list[str]:
    """Run `(row ids, deck they leave, deck they enter)` steps, one UPDATE each.

    One side of every step is Staging. Returns warnings; raises StagingError on
    a failed step, saying what the steps before it already did.
    """
    warnings: list[str] = []
    done: list[dict] = []
    for ids, source, target in steps:
        merging = source is staging
        deck = target if merging else source
        try:
            moved = supabase_helpers.move_deck_contents(ids, source["id"], target["id"])
        except supabase_helpers.SupabaseQueryError as e:
            if not done:
                state = "Nothing was moved."
            else:
                _stamp([staging] + done, warnings)
                landed = ", ".join(f"`{d['name']}`" for d in done)
                state = (f"Already moved into {landed}; the rest is still in `{staging['name']}`."
                         if merging else
                         f"Already moved out of {landed}; the rest is still in its deck.")
                state += " Run it again to finish."
            raise StagingError([f"Moving {'into' if merging else 'out of'} `{deck['name']}` "
                                f"failed: {e}", state]) from e
        done.append(deck)
        if len(moved) != len(ids):
            warnings.append(f"`{deck['name']}`: {len(moved)} of {len(ids)} rows moved; the others "
                            f"left `{source['name']}` while this ran.")
    if done:
        _stamp([staging] + done, warnings)
    return warnings


def _stamp(decks: list[dict], warnings: list[str]) -> None:
    """Stamp `updated_at` on every deck whose contents changed."""
    for deck in decks:
        try:
            supabase_helpers.update_record("decks", deck["id"], {})
        except supabase_helpers.SupabaseQueryError as e:
            warnings.append(f"`{deck['name']}` changed but its `updated_at` was not stamped: {e}")


def report_lines(merge: dict) -> dict[str, list[str]]:
    """Per destination deck name, one line per item (copies folded into `xN`)."""
    out: dict[str, list[str]] = {}
    for move in merge["moves"]:
        counts: dict = {}
        for row in move["rows"]:
            key = (row["content_type"], row["item"]["id"], row["item"]["name"])
            counts[key] = counts.get(key, 0) + 1
        out[move["deck"]["name"]] = [
            f"{name} ({content_type} #{item_id})" + (f" x{n}" if n > 1 else "")
            for (content_type, item_id, name), n in counts.items()]
    return out


# ---------------------------------------------------------------------------
# /stage
# ---------------------------------------------------------------------------

def resolve_item(value: str) -> tuple[str, dict]:
    """`(content_type, row)` for a picked ref (`card:447`) or a typed name.

    A typed name matching more than one row, of one type or across types, is
    refused: `/add_to_deck` takes the first match, and a first match pulled out
    of the live decks is the wrong item gone from the game.
    """
    ref_type, ref_id = supabase_helpers.parse_item_ref(value)
    if ref_type:
        content_type = _content_type(ref_type)
        rows = supabase_helpers.fetch_all(
            _table(content_type), columns=_ITEM_COLUMNS.get(content_type, ["id", "name"]),
            filters={"id": ref_id})
        if not rows:
            raise StagingError([f"No {content_type} #{ref_id}."])
        return content_type, rows[0]

    name = (value or "").strip()
    matches = [
        (content_type, row)
        for content_type in ("card", "aspect", "rite")
        for row in supabase_helpers.fetch_all(
            _table(content_type), columns=_ITEM_COLUMNS.get(content_type, ["id", "name"]),
            filters={supabase_helpers.name_column_for(content_type): name})]
    if not matches:
        raise StagingError([f"No card, aspect or rite is named `{name}`."])
    if len(matches) > 1:
        found = ", ".join(f"{content_type} #{row['id']}" for content_type, row in matches)
        raise StagingError([f"`{name}` names {len(matches)} items ({found}); "
                            f"pick one from the list."])
    return matches[0]


def stage_plan(value: str) -> dict:
    """What staging one item would do. Reads only. Raises StagingError.

    Returns `{"staging", "content_type", "item", "moves": [{"deck", "ids"}],
    "staged": copies already in Staging, "add": whether a copy is added because
    no deck deals it, "elsewhere": names of other decks still holding it}`.
    """
    staging, decks = _decks()
    content_type, item = resolve_item(value)
    usage_type = POOL[kind_of(content_type, item)]

    rows = [r for r in supabase_helpers.fetch_all(
                "deck_contents", columns=["id", "deck_id", "content_type"],
                filters={"content_id": item["id"]})
            if _content_type(r["content_type"]) == content_type]

    moves: dict = {}
    elsewhere: set = set()
    staged = 0
    for row in rows:
        deck = decks.get(row["deck_id"])
        if deck is None:
            continue
        if deck["id"] == staging["id"]:
            staged += 1
        elif deck.get("usage_type") == usage_type:
            moves.setdefault(deck["id"], {"deck": deck, "ids": []})["ids"].append(row["id"])
        else:
            elsewhere.add(deck["name"])

    return {"staging": staging, "content_type": content_type, "item": item,
            "moves": sorted(moves.values(), key=lambda m: m["deck"]["name"]),
            "staged": staged, "add": not moves and not staged,
            "elsewhere": sorted(elsewhere)}


def stage_apply(staged: dict) -> list[str]:
    """Carry out `stage_plan()`. Returns warnings; raises StagingError on a failed write."""
    staging = staged["staging"]
    if staged["add"]:
        try:
            supabase_helpers.create_record("deck_contents", {
                "deck_id": staging["id"],
                "content_id": staged["item"]["id"],
                "content_type": supabase_helpers._db_content_type(staged["content_type"])})
        except supabase_helpers.SupabaseQueryError as e:
            raise StagingError([f"Adding to `{staging['name']}` failed: {e}",
                                "Nothing was changed."]) from e
        warnings: list[str] = []
        _stamp([staging], warnings)
        return warnings
    return _move([(move["ids"], move["deck"], staging) for move in staged["moves"]], staging)


def stage_report(staged: dict, warnings: list[str] = ()) -> str:
    """The reply for a staged item."""
    name = f"`{staged['item']['name']}`"
    into = f"`{staged['staging']['name']}`"
    if staged["moves"]:
        total = sum(len(move["ids"]) for move in staged["moves"])
        out_of = ", ".join(f"`{move['deck']['name']}` ×{len(move['ids'])}"
                           for move in staged["moves"])
        lines = [f"⏸️ Staged {name} ×{total}", f"• Out of: {out_of}", f"• Into {into}"]
    elif staged["add"]:
        lines = [f"⏸️ Staged {name}", "• It was in no live deck",
                 f"• Added 1 copy to {into}"]
    else:
        lines = [f"ℹ️ {name} is already in {into} (×{staged['staged']}) and in no live deck. "
                 f"Nothing was changed."]
    if staged["elsewhere"]:
        still = ", ".join(f"`{deck}`" for deck in staged["elsewhere"])
        lines.append(f"• Still in: {still}, which `/stage` does not empty")
    lines += [f"⚠️ {warning}" for warning in warnings]
    return "\n".join(lines)
