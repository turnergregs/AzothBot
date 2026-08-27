"""What a card looks like after it upgrades.

A transcription of `GameContentData.apply_upgrade` / `get_upgrade_at_level` from
the game repo (`scripts/game_data/GameContentData.gd`). Anything here that
disagrees with that file is a bug HERE -- the engine is the authority, and a
comparison render that shows a different card than the game would produce is
worse than no comparison at all.

The rules, in the engine's own order:

  * Tiers are 1-indexed, taken from an entry's `level` field or, failing that,
    its position in the `upgrades` array. A GAP STOPS THE CHAIN: if there is no
    entry for level 2, level 3 is unreachable even when it exists.
  * `level` is honoured, never merged into the data.
  * Keys ending `_added` append; every other key replaces.
  * Replacements land FIRST, then additions stack on the new state.
  * A string `x_added` is skipped entirely when `x` is also present in the same
    entry -- the engine treats it as display-only for the level-up reward card.
    Concatenation is with a newline, and no leading newline onto an empty base.

CARDS THAT UPGRADE INTO ASPECTS

28 upgrade payloads carry `content_type: "aspect"` along with `attunement` and a
new `image`. That is not corrupt data: the card transforms into an aspect and
moves to the aspect bar. It is why `kind_of()` exists -- the upgraded face has to
be drawn by the ASPECT renderer, and a comparison that drew it as a card would
show a card that cannot exist.
"""
from __future__ import annotations

import copy

ADDED_SUFFIX = "_added"

# `content_type` on a row is the database's vocabulary; the renderers use
# "rite" where the database still says "event".
CONTENT_TYPE_TO_KIND = {"card": "card", "aspect": "aspect", "event": "rite"}


def _level_of(entry: dict, position: int) -> int:
    """An entry's tier: its `level`, else its 1-indexed position."""
    try:
        return int(entry.get("level", position))
    except (TypeError, ValueError):
        return position


def upgrade_at_level(row: dict, level: int) -> dict:
    """The upgrade entry for an explicit tier, or {} if there is none."""
    if level <= 0:
        return {}
    entries = row.get("upgrades")
    if not isinstance(entries, list):
        return {}
    for position, entry in enumerate(entries, start=1):
        if isinstance(entry, dict) and _level_of(entry, position) == level:
            return entry
    return {}


def kind_of(row: dict, fallback: str) -> str:
    """Which renderer draws this row.

    `fallback` is what it was before, so a payload that says nothing about
    `content_type` leaves the kind alone.
    """
    return CONTENT_TYPE_TO_KIND.get(row.get("content_type"), fallback)


def apply(row: dict, upgrade: dict, current_level: int = 0) -> dict:
    """One tier applied to `row`, returning a new dict. `row` is not mutated."""
    data = copy.deepcopy(row)

    # Replacements first, so additions land on the new state and not the old.
    for key, value in upgrade.items():
        if key == "level" or key.endswith(ADDED_SUFFIX):
            continue
        data[key] = copy.deepcopy(value)

    for key, value in upgrade.items():
        if not key.endswith(ADDED_SUFFIX):
            continue
        base_key = key[:-len(ADDED_SUFFIX)]

        if isinstance(value, str):
            # Display-only when the same entry also replaces the base key.
            if base_key in upgrade:
                continue
            base = data.get(base_key)
            base = "" if base is None else str(base)
            data[base_key] = base + ("\n" if base else "") + value
        elif isinstance(value, list):
            existing = data.get(base_key)
            if not isinstance(existing, list):
                existing = []
            data[base_key] = existing + copy.deepcopy(value)

    data["upgrade_level"] = _level_of(upgrade, current_level + 1)
    return data


def tiers(row: dict, kind: str) -> list:
    """Every upgraded state of `row`, in order, as `(row, kind, level)`.

    CUMULATIVE, because the engine is: each `apply_upgrade` takes the next tier
    and applies it to the state the last one left behind, not to the printed
    card. Empty when the row has no upgrades.

    Every card in the database today has exactly one tier, so this returns a
    single entry in practice -- but the engine reads an array and honours
    explicit `level` fields, so this does too rather than assuming.
    """
    out = []
    current = row
    current_kind = kind
    level = 0
    while True:
        upgrade = upgrade_at_level(row, level + 1)
        if not upgrade:
            break
        current = apply(current, upgrade, level)
        level = current.get("upgrade_level", level + 1)
        current_kind = kind_of(current, current_kind)
        out.append((current, current_kind, level))
        if len(out) > 16:      # a malformed `level` chain must not spin here
            break
    return out


def has_upgrade(row: dict) -> bool:
    return bool(upgrade_at_level(row, 1))


def plus_name(name: str) -> str:
    """An upgraded card's displayed name.

    `base_card.gd::set_upgrade_card_visuals` appends a single `+`, and guards
    against doubling it:

        name_label.text = base_name if base_name.ends_with("+") else base_name + "+"

    ONE plus, whatever the tier -- the game does not write `++` for tier 2, and
    it never writes the suffix into the data, only onto the label. Same here:
    this is applied at render time, so the row that reaches the cache key and
    the filename is still the row the database holds.
    """
    name = name or ""
    return name if name.endswith("+") else name + "+"
