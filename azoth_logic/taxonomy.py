"""The small fixed vocabularies: elements, card types, attributes, deck types.

These used to live in six database tables -- `card_elements`, `card_types`,
`card_attributes`, `deck_types`, `deck_content_types`, `deck_usage_types` -- read
live on every autocomplete keystroke. All six were dropped on 2026-08-27.

WHY CODE IS THE RIGHT HOME

None of these are data. They are enumerations the *engine* understands, and the
game already hardcodes every one of them in GDScript (`Utils.ATTRIBUTE_ICONS_PATHS`,
`GlobalVars.ATTRIBUTE_NAMES`, `content_search.ELEMENT_OPTIONS`,
`codex.USAGE_TYPE_OPTIONS`). The tables were a second copy that could only ever
lag: a new element is a code change in the game, and adding a row to a table was
a separate step nobody was reminded to do.

WHY THE LISTS BELOW ARE NOT THE WHOLE ANSWER

Hardcoding drifts too, and there is proof in this codebase. The game's
`USAGE_TYPE_OPTIONS` lists seven usage types; the database has **nine** decks
worth -- `tutorial` and `rite` both postdate that constant and were never added.
`rite` is the one that hid the Rites deck from `draft_deck_view` for its whole
existence.

So `suggest()` returns the canonical list UNION whatever is actually in use.
A value that exists in the data can never be invisible in an autocomplete, even
when someone forgets to update this file -- and because Discord autocomplete is a
suggestion rather than a constraint, a genuinely new value can still be typed in
to create the first row that uses it.
"""
from __future__ import annotations

import time

from supabase_helpers import fetch_all

# Kept in step with the game repo, which is the source of truth. The comment on
# each is where to look when this needs updating.

# scripts/autoloads/global_vars.gd::ELEMENTS, scripts/UI/codex/content_search.gd
# ::ELEMENT_OPTIONS. A NULL element is "Colourless" and is not a value here --
# 64 cards have it, and it is the absence of an element rather than one of them.
CARD_ELEMENTS = ["anima", "blood", "sol"]

# scripts/UI/glossary/glossary.gd::_build_card_types and the `type` column.
# NOT "Card" -- see the note in docs/DB_SCHEMA.md about the eight rows carrying
# that as a type. It is the Codex's display label leaking into the data.
CARD_TYPES = ["spell", "catalyst", "power"]

# scripts/autoloads/global_vars.gd::ATTRIBUTE_NAMES.
#
# `Utils.ATTRIBUTE_ICONS_PATHS` additionally has "Up" and "Down", and they render
# if present -- but they are commented out of ATTRIBUTE_NAMES, so the game does
# not offer them. Following ATTRIBUTE_NAMES: suggesting an attribute the engine
# has withdrawn is worse than omitting one it can still draw.
CARD_ATTRIBUTES = ["Augment", "Ascending", "Decrement", "Descending", "Inert", "Spawner"]

# scripts/autoloads/deck_manager.gd. "custom" decks are created in-game and live
# in local JSON, so they never reach the `decks` table -- but the value is real
# and both `decks_with_contents` and `draft_deck_view` filter on it.
DECK_TYPES = ["base", "custom"]

# scripts/UI/codex/codex.gd::USAGE_TYPE_OPTIONS, plus `tutorial` and `rite`,
# which that constant is missing. See the module docstring.
#
# `reactant`, `boon_a`, `boon_b` and `boon_c` are deliberately NOT here: retired
# 2026-08-27, and the only decks that used them (32 Reactants, 33-35 Boon_*) are
# all archived. The game's USAGE_TYPE_OPTIONS still lists them, and
# `CardLogic.DRAFT_INJECTED_USAGE_TYPES` still understands `reactant` -- the
# engine support is alive, there is just no content using it, and offering a dead
# usage type in a picker is how a new deck ends up in one.
DECK_USAGE_TYPES = ["draft", "starter", "summon", "rite", "tutorial"]

CANONICAL = {
    "card_elements": CARD_ELEMENTS,
    "card_types": CARD_TYPES,
    "card_attributes": CARD_ATTRIBUTES,
    "deck_types": DECK_TYPES,
    "deck_usage_types": DECK_USAGE_TYPES,
}

# Where to look for values already in use: (table, column, filters).
#
# `card_attributes` is absent on purpose: no card carries one yet, so there is
# nothing to union and no reason to read 400 rows to find that out.
#
# The deck rows are filtered to UNARCHIVED. "In use" has to mean in use now, not
# ever -- the four decks on the retired `reactant` / `boon_*` usage types are all
# archived, and without this filter the union would hand straight back the values
# that were just removed from the canonical list above.
_IN_USE_SOURCE = {
    "card_elements": ("cards", "element", None),
    "card_types": ("cards", "type", None),
    "deck_types": ("decks", "type", {"archived_at": None}),
    "deck_usage_types": ("decks", "usage_type", {"archived_at": None}),
}

# Discord fires autocomplete on every keystroke. Same TTL as content_index, and
# the same reason: a live read is 0.85-2.3s and there is a 3s reply budget.
TTL_SECONDS = 60

_cache: dict[str, tuple[float, list[str]]] = {}


def invalidate() -> None:
    """Drop the in-use cache.

    The 60s TTL is the real staleness bound, and it is enough: a genuinely novel
    element or usage type appears about as often as an engine change. This exists
    for the one path that can introduce several at once -- `/bulk_insert` -- and
    for tests, which must not inherit a cache between cases.
    """
    _cache.clear()


def _in_use(kind: str) -> list[str]:
    """Distinct non-null values of this vocabulary's column, cached.

    A failure here is not fatal: the canonical list is still a good answer, and
    an autocomplete that silently loses the hardcoded values is worse than one
    that misses a novel value.
    """
    source = _IN_USE_SOURCE.get(kind)
    if not source:
        return []

    hit = _cache.get(kind)
    if hit and (time.monotonic() - hit[0]) < TTL_SECONDS:
        return hit[1]

    table, column, filters = source
    try:
        rows = fetch_all(table, columns=[column], filters=filters)
    except Exception:
        return []

    values = sorted({r[column] for r in rows if r.get(column)})
    _cache[kind] = (time.monotonic(), values)
    return values


def values(kind: str) -> list[str]:
    """Everything worth suggesting for this vocabulary: canonical, then extras.

    Canonical entries keep their declared order -- it is the order the game lists
    them in, not alphabetical. Anything found in the data but missing from the
    canonical list is appended, so it is visibly an extra rather than silently
    mixed in.
    """
    if kind not in CANONICAL:
        raise KeyError(f"unknown vocabulary `{kind}`; expected one of "
                       f"{', '.join(sorted(CANONICAL))}")

    canonical = CANONICAL[kind]
    known = {v.lower() for v in canonical}
    extras = [v for v in _in_use(kind) if v.lower() not in known]
    return canonical + extras


def suggest(kind: str, input: str, limit: int = 25) -> list[str]:
    """Autocomplete suggestions for `kind`, filtered by what has been typed.

    Substring rather than prefix match, so `boon` finds `boon_a` and `sol` finds
    itself without the user guessing where a value starts.
    """
    needle = (input or "").strip().lower()
    matches = [v for v in values(kind) if needle in v.lower()]
    return matches[:limit]
