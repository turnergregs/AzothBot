"""Which side of the `events` -> `rites` rename the database is on.

The game repo's `db/migrations/2026-09-14_rename_events_to_rites.sql` renames
the `events` table to `rites`, the `content_type` / `item_type` value 'event' to
'rite'. Migrations are
applied by hand with no history table, so this bot can be running on either
side of that change -- and a hard-coded name is wrong on one of them.

So there are two rules:

  * Anything that NAMES the table or the type value -- a query, a
    write, a filter -- asks `current()`.
  * Anything that READS a row accepts both spellings (`CONTENT_TYPES`), so a ref
    encoded before the migration still resolves after it.

`current()` probes `rites` with a one-row read. PostgREST reports a table that
does not exist as PGRST205 (Postgres itself says 42P01); only those mean "not
renamed yet". Any other failure -- a timeout, a bad key -- raises instead of
being mistaken for the old side. The answer is cached for `TTL` seconds, so a
migration applied while the bot runs is picked up without a restart, and every
change of side is printed.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class Side:
    table: str
    content_type: str
    label: str


BEFORE = Side("events", "event", "before the rename (table `events`)")
AFTER = Side("rites", "rite", "after the rename (table `rites`)")

# Both spellings of a Rite's content_type, for code that reads rows.
CONTENT_TYPES = frozenset({BEFORE.content_type, AFTER.content_type})

TTL = 300.0

# How a missing table fails: PostgREST's own code, Postgres's, and their text.
_MISSING_TABLE = ("PGRST205", "42P01", "Could not find the table", "does not exist")

_lock = threading.Lock()
_side: Side | None = None
_stamp = 0.0
_pinned: Side | None = None


def _probe() -> Side:
    # Imported here: supabase_helpers imports this module for db_content_type.
    from supabase_helpers import fetch_all, SupabaseQueryError

    try:
        fetch_all(AFTER.table, ["id"], limit=1)
        return AFTER
    except SupabaseQueryError as e:
        if any(marker in str(e) for marker in _MISSING_TABLE):
            return BEFORE
        raise


def current(force: bool = False) -> Side:
    """The side the database is on now. Raises if it cannot be determined."""
    global _side, _stamp
    if _pinned is not None:
        return _pinned
    with _lock:
        if not force and _side is not None and time.time() - _stamp < TTL:
            return _side
    side = _probe()
    with _lock:
        if side is not _side:
            print(f"🗂  Rites schema: {side.label}")
        _side, _stamp = side, time.time()
    return side


def invalidate() -> None:
    """Forget the cached side, so the next call probes again."""
    global _side, _stamp
    with _lock:
        _side, _stamp = None, 0.0


def pin(side: Side | None) -> None:
    """Answer `side` without probing. For tests; `None` restores detection."""
    global _pinned
    _pinned = side


def is_rite(content_type) -> bool:
    """Whether a row's content_type names a Rite, in either spelling."""
    return content_type in CONTENT_TYPES


def db_content_type(content_type: str) -> str:
    """`content_type` in the spelling the database stores now.

    A Rite in either spelling becomes the current one; every other type passes
    through untouched. Use it before a table name is derived from the type or the
    type is written to `deck_contents`.
    """
    return current().content_type if is_rite(content_type) else content_type
