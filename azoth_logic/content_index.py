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
_stamp = 0.0


def invalidate() -> None:
    """Drop the cached index. Call after creating or deleting content."""
    global _cache, _stamp
    with _lock:
        _cache = None
        _stamp = 0.0


def entries(force: bool = False) -> list:
    """[(kind, id, name), ...] across all three tables."""
    global _cache, _stamp
    with _lock:
        if not force and _cache is not None and time.time() - _stamp < TTL:
            return _cache
    rows = []
    for kind, table in TABLES.items():
        for row in fetch_all(table, ["id", "name"], limit=1000):
            if row.get("name"):
                rows.append((kind, row["id"], row["name"]))
    rows.sort(key=lambda r: str(r[2]).lower())
    with _lock:
        _cache = rows
        _stamp = time.time()
    return rows


def label(kind: str, item_id, name: str) -> str:
    """Autocomplete label, e.g. 'Diversity (Card #447)'.

    Uses the DISPLAY name rather than the ref type, so an event shows as
    'Rite' -- the value behind it still encodes `event:13`.
    """
    return f"{name} ({DISPLAY.get(kind, kind.capitalize())} #{item_id})"


def choices(query: str, limit: int = 25) -> dict:
    """{label: ref} for Discord, filtered by a substring of the name.

    Exact and prefix matches sort ahead of mid-string ones, so typing a full
    name puts it first even when it is a substring of several others.
    """
    from supabase_helpers import encode_item_ref

    needle = (query or "").strip().lower()
    scored = []
    for kind, item_id, name in entries():
        low = str(name).lower()
        if needle and needle not in low:
            continue
        rank = 0 if low == needle else (1 if low.startswith(needle) else 2)
        scored.append((rank, low, kind, item_id, name))
    scored.sort(key=lambda r: (r[0], r[1]))
    return {label(k, i, n): encode_item_ref(REF_TYPE[k], i)
            for _, _, k, i, n in scored[:limit]}


def resolve(value: str):
    """An encoded ref -> (kind, row), or (None, None).

    Falls back to a name lookup so a typed value still works, which is what the
    deck commands do. Ambiguous names resolve in card/aspect/rite order.
    """
    from supabase_helpers import parse_item_ref

    ref_type, item_id = parse_item_ref(value)
    if ref_type:
        kind = KIND_FOR_REF.get(ref_type)
        if kind:
            rows = fetch_all(TABLES[kind], filters={"id": item_id})
            if rows:
                return kind, rows[0]
        return None, None

    name = (value or "").strip()
    if not name:
        return None, None
    for kind, table in TABLES.items():
        rows = fetch_all(table, filters={"name": name})
        if rows:
            return kind, rows[0]
    return None, None
