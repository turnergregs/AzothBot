"""A cached (kind, id, name) index across cards, aspects and rites.

`/get` and `/render` autocomplete over all three tables at once, and Discord
fires an autocomplete on **every keystroke**. Reading the three tables live costs
0.85-2.3s each time, which is both unusable and needless traffic -- the index is
a few hundred rows that change only when someone edits content.

So it is cached in-process with a short TTL, and invalidated explicitly by the
commands that mutate content. The TTL is the backstop for edits made elsewhere
(the Codex, direct SQL, another bot instance); `invalidate()` is what makes this
bot's own writes show up immediately.
"""
from __future__ import annotations

import threading
import time

from supabase_helpers import fetch_all

# Kind -> table. "rite" is what the database still calls an "event".
TABLES = {"card": "cards", "aspect": "aspects", "rite": "events"}

# Kind -> the content_type an item ref encodes. Refs stay on the DB vocabulary so
# they keep working with deck_contents and parse_item_ref.
REF_TYPE = {"card": "card", "aspect": "aspect", "rite": "event"}
KIND_FOR_REF = {v: k for k, v in REF_TYPE.items()}

# Kind -> what a user sees in an autocomplete label.
DISPLAY = {"card": "Card", "aspect": "Aspect", "rite": "Rite"}

TTL = 60.0

_lock = threading.Lock()
_cache: list | None = None
_live: dict | None = None
_stamp = 0.0


def invalidate() -> None:
    """Drop the cached index. Call after creating or deleting content."""
    global _cache, _live, _stamp
    with _lock:
        _cache = None
        _live = None
        _stamp = 0.0


# --- Liveness ---------------------------------------------------------------
#
# `cards`, `aspects` and `events` have **no `archived_at` column** -- they hard
# delete, and a row that is no longer used just sits there. Today that is 400
# cards of which 154 are reachable in game, 149 aspects of which 58 are, and 77
# rites of which 21 are. Two thirds of every autocomplete was content the player
# can never see.
#
# The only signal is DECK MEMBERSHIP. The game reaches all of its content
# through `decks_with_contents` and drops archived decks on sync
# (CONTENT_LOADING.md § DeckManager), so:
#
#     live == present in at least one deck whose `archived_at` is null
#
# Verified against the content itself before this was built: no `find` clause
# names a content row -- all 177 of them address a runtime ZONE (`deck`,
# `link`, `rite_pool`) -- and the single `card_name` in the whole set builds a
# token from scratch rather than naming a row. So nothing outside a live deck is
# reachable by a live card's own rules.
#
# ⚠️ The game repo's CONTENT_LOADING.md § Reconcile warns against pruning its
# offline SNAPSHOT by deck membership. That is a different operation -- deleting
# files that an exported build cannot re-download -- and does not apply to
# hiding a row from an autocomplete, which `/add_to_deck` reverses in seconds.


def _fetch_live() -> dict:
    """{kind: {id, ...}} for content in at least one unarchived deck.

    An EMPTY result is treated as a failed read, not as "nothing is live", and
    the caller falls back to showing everything. Getting this backwards makes
    every command in the bot answer "not found" -- which reads as the content
    being gone rather than as a bad Supabase key. The game's own importer takes
    the same stance on an empty reconcile set, for the same reason.
    """
    live_decks = {d["id"] for d in fetch_all("decks", ["id", "archived_at"], limit=1000)
                  if not d.get("archived_at")}
    found = {kind: set() for kind in TABLES}
    if not live_decks:
        return found
    for row in fetch_all("deck_contents", ["deck_id", "content_type", "content_id"],
                         limit=10000):
        if row.get("deck_id") not in live_decks:
            continue
        kind = KIND_FOR_REF.get(row.get("content_type"))
        if kind is not None and row.get("content_id") is not None:
            found[kind].add(row["content_id"])
    return found


def _refresh() -> tuple:
    """(entries, live) read fresh from the database."""
    rows = []
    for kind, table in TABLES.items():
        for row in fetch_all(table, ["id", "name"], limit=1000):
            if row.get("name"):
                rows.append((kind, row["id"], row["name"]))
    rows.sort(key=lambda r: str(r[2]).lower())
    return rows, _fetch_live()


def _snapshot(force: bool = False) -> tuple:
    """The cached (entries, live) pair, refreshed if stale.

    One cache for both, because they are read together on every keystroke and a
    live set that lags the entry list would hide brand-new content.
    """
    global _cache, _live, _stamp
    with _lock:
        if not force and _cache is not None and time.time() - _stamp < TTL:
            return _cache, _live
    rows, live = _refresh()
    with _lock:
        _cache, _live, _stamp = rows, live, time.time()
    return rows, live


def live_ids(force: bool = False) -> dict:
    """{kind: {id, ...}} reachable in game. See the liveness note above."""
    return _snapshot(force)[1]


def is_live(kind: str, item_id, force: bool = False) -> bool:
    """Whether one item is reachable in game.

    True when the liveness read came back empty -- see `_fetch_live`. A bot that
    cannot see the decks must not claim every card is dead.
    """
    live = live_ids(force)
    if not any(live.values()):
        return True
    return item_id in live.get(kind, ())


def entries(force: bool = False, live_only: bool = True) -> list:
    """[(kind, id, name), ...] across all three tables.

    Live content only by default: there is no reason to render, show, update or
    search for something the player can never encounter. `live_only=False` is
    for the one place that needs the dead rows -- `/add_to_deck`, which is how
    content is brought BACK.
    """
    rows, live = _snapshot(force)
    if not live_only or not any(live.values()):
        return rows
    return [e for e in rows if e[1] in live.get(e[0], ())]


def label(kind: str, item_id, name: str) -> str:
    """Autocomplete label, e.g. 'Diversity (Card #447)'.

    Uses the DISPLAY name rather than the ref type, so an event shows as
    'Rite' -- the value behind it still encodes `event:13`.
    """
    return f"{name} ({DISPLAY.get(kind, kind.capitalize())} #{item_id})"


def choices(query: str, limit: int = 25, live_only: bool = True) -> dict:
    """{label: ref} for Discord, filtered by a substring of the name.

    Exact and prefix matches sort ahead of mid-string ones, so typing a full
    name puts it first even when it is a substring of several others.

    Live content only. `live_only=False` offers rows the player cannot reach.
    """
    from supabase_helpers import encode_item_ref

    needle = (query or "").strip().lower()
    scored = []
    for kind, item_id, name in entries(live_only=live_only):
        low = str(name).lower()
        if needle and needle not in low:
            continue
        rank = 0 if low == needle else (1 if low.startswith(needle) else 2)
        scored.append((rank, low, kind, item_id, name))
    scored.sort(key=lambda r: (r[0], r[1]))
    return {label(k, i, n): encode_item_ref(REF_TYPE[k], i)
            for _, _, k, i, n in scored[:limit]}


def resolve(value: str, live_only: bool = True):
    """An encoded ref -> (kind, row), or (None, None).

    Falls back to a name lookup so a typed value still works, which is what the
    deck commands do. Ambiguous names resolve in card/aspect/rite order.

    Dead content resolves to (None, None) by default, so a pasted ref for a
    retired card behaves like the autocomplete that no longer offers it. Pair a
    miss with `absence_reason` -- "could not find" is the wrong answer for a row
    that plainly exists in the database.
    """
    from supabase_helpers import parse_item_ref

    ref_type, item_id = parse_item_ref(value)
    if ref_type:
        kind = KIND_FOR_REF.get(ref_type)
        if kind:
            rows = fetch_all(TABLES[kind], filters={"id": item_id})
            if rows and (not live_only or is_live(kind, rows[0]["id"])):
                return kind, rows[0]
        return None, None

    name = (value or "").strip()
    if not name:
        return None, None
    matches = []
    for kind, table in TABLES.items():
        rows = fetch_all(table, filters={"name": name})
        if rows:
            matches.append((kind, rows[0]))
    if not matches:
        return None, None
    if not live_only:
        return matches[0]
    # A LIVE match wins over an earlier dead one. Names collide across types --
    # the rite "Mirror" and the card property of the same name are why this
    # cannot just take the first table that answers.
    for kind, row in matches:
        if is_live(kind, row["id"]):
            return kind, row
    return None, None


def absence_reason(value: str) -> str:
    """Why `resolve` came back empty, phrased for the person who asked.

    "Could not find X" is actively misleading for a row that exists and is
    simply in no live deck -- it reads as data loss. This says what is true and
    what fixes it, since `/add_to_deck` is one command away.
    """
    kind, row = resolve(value, live_only=False)
    if not row:
        return f"❌ Could not find `{value}`."
    return (f"🚫 **{row.get('name')}** ({DISPLAY.get(kind, kind)} #{row['id']}) is not in "
            f"any active deck, so it is not in the game — `/show`, `/render`, `/rules` "
            f"and `/search` only cover live content.\n"
            f"Add it to a deck with `/add_to_deck` to bring it back.")


def names(kind: str, query: str = "", limit: int = 25, live_only: bool = True) -> list:
    """Live names of one kind, for a single-table autocomplete.

    `/update_card` and friends take a bare NAME rather than an encoded ref, so
    they cannot use `choices`. Same liveness rule: editing a card the player
    cannot encounter is editing nothing.
    """
    needle = (query or "").strip().lower()
    found = [name for k, _, name in entries(live_only=live_only)
             if k == kind and (not needle or needle in str(name).lower())]
    return sorted(found, key=lambda s: str(s).lower())[:limit]
