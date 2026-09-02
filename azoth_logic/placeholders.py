"""Display placeholders: the `{...}` tokens card text is authored with.

Content text carries runtime tokens -- Gambit is authored as
`"{luck_chance.50} Draw 3, {luck_chance_down.50} Discard 1"`, Recollection as
`"Create last used Rite ({last_rite})"`. In game those resolve against the
current run; here there is no run, so they resolve the way the game's own
OUT-OF-RUN surfaces resolve them, and everything else is left visible.

The game's port map:

| Token | Game | Here |
|---|---|---|
| `{luck_chance.N}` | `LuckHelper.format_chance_display` against live Luck | `format_flat_chance` -- the authored base, no Luck, no colour |
| `{luck_chance_down.N}` | same, complemented | same, complemented |
| `{last_rite}` | the last Rite spent this run | `LAST_RITE_NONE` -- there is no history |
| anything else | resolved from run state | **left verbatim** |

`luck_display_flat` is the game's own name for the first row: run history passes
it (`run_card_tile.gd`) because those cards belong to a finished run, and the
Codex passes it when measuring text length. A Discord render is the same kind of
surface -- it shows content, not a run -- so it is always flat. See the azoth
repo's `docs/LUCK.md` and `scripts/placeholder_helper.gd`.

**Leaving an unresolved token visible is the game's behaviour, not a shortfall
here.** `PlaceholderHelper._replace_placeholders` skips a token whose value comes
back null, so the raw `{token}` reaches the player. Loud beats a silent wrong
number, and it is how a malformed token gets noticed. `{hand.size}` on a card
face has no honest answer outside a run, so it keeps that treatment.

Only 11 live rows carry a token at all (measured 2026-09-02): four
`luck_chance`, Recollection's `{last_rite}`, and Twinning's `{2}`, which is
authored text the game also prints verbatim.
"""
from __future__ import annotations

import math
import re

# `{key.path}`, non-greedy on the inside so `{a} {b}` is two tokens, not one.
_TOKEN = re.compile(r"\{([^{}]+)\}")

# What a `{luck_chance...}` suffix has to look like. Godot's `String.is_valid_float`
# is the gate on the game side; this is the same shape, minus the values Python's
# `float()` would otherwise wave through -- "nan", "inf" and "1_0" are not numbers
# a card was authored with, and a NaN base would silently render as 0%.
_FLOAT = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")

# PlaceholderHelper.LUCK_CHANCE_KEYS -- the value is `invert`.
#   {luck_chance.N}      the odds Luck pushes UP   (a chance condition, a random
#                        Split's favoured branch)
#   {luck_chance_down.N} the odds Luck pushes DOWN (the losing branch, Glass)
LUCK_CHANCE_KEYS = {"luck_chance": False, "luck_chance_down": True}

# PlaceholderHelper.LAST_RITE_TOKEN / LAST_RITE_NONE. The game prints this before
# a Rite has been spent; out of a run, nothing ever has been.
LAST_RITE_TOKEN = "last_rite"
LAST_RITE_NONE = "None"


def _percent_number(value: float) -> str:
    """`LuckHelper._percent_number`: the digits, without the unit.

    Both extremes are guarded rather than rounded. A rounded `0%` would call a
    0.2% shatter impossible and a rounded `100%` would promise a certainty, so
    those become `<1` and `>99`; genuine 0 and 1 still print flat.

    Rounds HALF AWAY FROM ZERO, which is Godot's `round()`. Python's built-in
    `round()` is banker's rounding and would print `{luck_chance.2.5}` as 2%
    where the game prints 3%.
    """
    if value <= 0.0:
        return "0"
    if value >= 1.0:
        return "100"
    rounded = int(math.floor(value * 100.0 + 0.5))
    if rounded <= 0:
        return "<1"
    if rounded >= 100:
        return ">99"
    return str(rounded)


def format_flat_chance(base_chance: float, invert: bool = False) -> str:
    """`LuckHelper.format_flat_chance`: the percentage at zero Luck.

    `base_chance` is a fraction, so `{luck_chance.50}` arrives as 0.5. The
    `_down` token prints the complement -- a two-option Split's other branch.
    """
    base = min(1.0, max(0.0, base_chance))
    return _percent_number((1.0 - base) if invert else base) + "%"


def _luck_chance(key: str, suffix: str) -> str | None:
    """`{luck_chance.N}` / `{luck_chance_down.N}` -> "N%", or None if malformed.

    `suffix` is everything after the first dot, dots included: the game splits
    the whole path on "." and rejoins it for exactly this reason, so a fractional
    base (`{luck_chance.2.5}`, which arrives as ["2", "5"]) survives.
    """
    if not _FLOAT.match(suffix):
        return None
    return format_flat_chance(float(suffix) / 100.0, LUCK_CHANCE_KEYS[key])


def _value(body: str) -> str | None:
    """One token's replacement, or None to leave it visible."""
    key, _, suffix = body.partition(".")
    if key in LUCK_CHANCE_KEYS:
        return _luck_chance(key, suffix)
    if body == LAST_RITE_TOKEN:
        return LAST_RITE_NONE
    return None


def _substitute(match: re.Match) -> str:
    # Explicitly against None, not falsiness: a token that legitimately resolves
    # to the empty string must vanish, not fall back to printing itself.
    value = _value(match.group(1))
    return match.group(0) if value is None else value


def resolve(text: str) -> str:
    """Replace what a run-free surface can resolve; leave the rest verbatim."""
    if not text or "{" not in text:
        return text
    return _TOKEN.sub(_substitute, text)
