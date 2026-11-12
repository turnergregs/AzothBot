"""A weapon's numbers.

The game's weapons (azoth `docs/WEAPONS.md`): a card whose `Weapon` property
prints `damage` is a weapon. Its face shows the damage as a generated pointer
on the card's corner in its element's colour (`card_render`,
`inline_icons.pointer`).

Its escalate is NOT generated (since the game's Escalate rollout, 2026-10-27):
it is an `Escalate` custom action in the card's own actions or triggers, and the
card's authored `text` says so with an `[escalateN]` token, drawn like any other
token (`inline_icons`). The Weapon property's `escalate` / `escalate_on` /
`escalate_per` fields, which the game used to turn into a lead on the text, are
gone from every row, and this module no longer reads them.

The damage is read as `GameContentData.get_effective_attr` reads it: a key of
that name on the row wins (an upgrade tier writes `{"damage": 150}` there), then
the Weapon property.
"""
from __future__ import annotations

WEAPON_PROPERTY = "Weapon"


def field(row: dict, key: str):
    """`key` as the card reads it: its own key first, then the Weapon property."""
    own = row.get(key)
    if own is not None:
        return own
    for prop in row.get("properties") or []:
        if isinstance(prop, dict) and prop.get("name") == WEAPON_PROPERTY:
            return prop.get(key)
    return None


def is_weapon(row: dict) -> bool:
    """A card with a `damage`. A boss prints a top-level `damage` too, its
    attack, and is not one; callers only pass cards."""
    return field(row, "damage") is not None


def damage(row: dict) -> int | None:
    value = field(row, "damage")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
