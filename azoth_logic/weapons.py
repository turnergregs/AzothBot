"""A weapon's numbers, and the text its card face leads with.

The game's weapons (azoth `docs/WEAPONS.md`): a card whose `Weapon` property
prints `damage` is a weapon. Its face shows two generated things content never
writes:

  * **the damage**, a pointer on the card's corner in its element's colour
    (`card_render`, `inline_icons.pointer`);
  * **the escalate**, a gold pointer leading the card's text, followed by when
    it escalates if that is not its play and what each step counts:
    `[escalate3], Draw 1`, `[escalate4] when discarded, ...`, `Held: [escalate1]
    per life lost`. `CardTextComposer._lead_with_escalate` is the original.

The numbers are read as `GameContentData.get_effective_attr` reads them: a key
of that name on the row wins (an upgrade tier writes `{"damage": 150}` there),
then the Weapon property.

The wording lives in the game's translations (`WEAPON_ESCALATES_WHEN_*`,
`WEAPON_ESCALATES_PER_*`) and is vendored by `tools/sync_assets.py` into
`assets/card_art/weapon_text.json`, so a reworded badge needs a sync, not a code
change.
"""
from __future__ import annotations

import json
from pathlib import Path

WEAPON_PROPERTY = "Weapon"
WEAPON_FIELDS = ("damage", "escalate", "escalate_on", "escalate_per")

ESCALATE_ON_PLAYED = "played"
# What raises a weapon on something other than its play: the badge says when.
# GameContentData.ESCALATE_TRIGGERS' keys.
ESCALATE_ON = ("discarded", "drawn", "forsaken", "recalled", "miracle", "held",
               "life_lost", "life_gained", "card_played", "rite_activated",
               "aspect_triggered")
# These count by the event's own amount, so an escalate_per is ignored.
ESCALATE_COUNTED_BY = ("life_lost", "life_gained")
ESCALATE_PER = ("link", "link_valence", "link_element",
                "other_link_valence", "other_link_element", "held")

ESCALATE_ICON = "[escalate%d]"

TEXT_FILE = Path(__file__).resolve().parent.parent / "assets" / "card_art" / "weapon_text.json"

_text_cache = None


def _text() -> dict:
    global _text_cache
    if _text_cache is None:
        if not TEXT_FILE.is_file():
            raise FileNotFoundError(
                f"{TEXT_FILE} is missing. Run: python -m tools.sync_assets --azoth <path>")
        _text_cache = json.loads(TEXT_FILE.read_text(encoding="utf-8"))["strings"]
    return _text_cache


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


def escalate(row: dict) -> int:
    try:
        return int(field(row, "escalate") or 0)
    except (TypeError, ValueError):
        return 0


def escalate_on(row: dict) -> str:
    on = field(row, "escalate_on")
    if on is None or str(on) in ("", ESCALATE_ON_PLAYED) or str(on) not in ESCALATE_ON:
        return ESCALATE_ON_PLAYED
    return str(on)


def escalate_per(row: dict) -> str:
    per = field(row, "escalate_per")
    if per is None or str(per) == "" or escalate_on(row) in ESCALATE_COUNTED_BY:
        return ""
    return str(per) if str(per) in ESCALATE_PER else ""


def lead(row: dict) -> str:
    """The escalate badge as tokens, or "" for a card that does not escalate:
    `[escalate3]`, `[escalate4] when discarded`, `Held: [escalate2] per card
    held`. CardTextComposer.weapon_badge_when / weapon_badge_per."""
    amount = escalate(row)
    if not is_weapon(row) or amount <= 0:
        return ""
    icon = ESCALATE_ICON % amount
    on = escalate_on(row)
    strings = _text()
    if on == ESCALATE_ON_PLAYED:
        badge = icon
    else:
        badge = strings.get("WEAPON_ESCALATES_WHEN_" + on.upper(), "{ESCALATE}")
        badge = badge.replace("{ESCALATE}", icon)
    per = escalate_per(row)
    if per:
        badge += " " + strings.get("WEAPON_ESCALATES_PER_" + per.upper(), "").replace("{OF}", "")
    return badge.rstrip()


def text_with_lead(row: dict, text: str) -> str:
    """The card's text with its escalate leading it: joined to its first line by
    a comma, or as the only line of a card that prints nothing else
    (CardTextComposer._lead_with_escalate)."""
    badge = lead(row)
    if not badge:
        return text
    if text.strip():
        return badge + ", " + text
    return badge
