"""Display placeholders -- the `{...}` tokens in authored content text.

The expectations here are transcribed from the game's own suite,
`tests/unit/helpers/test_luck_chance_display.gd` and
`tests/unit/placeholders/test_last_event_placeholder.gd`, restricted to its
zero-luck / `luck_display_flat` rows: a Discord render shows content, not a run,
so it always takes the flat path. Anything asserted here that disagrees with
`scripts/placeholder_helper.gd` or `scripts/helpers/luck_helper.gd` is a bug
HERE -- the engine is the authority.
"""
import pytest

from azoth_logic import placeholders, rich_text


# ---------------------------------------------------------------------------
# luck_chance
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    # The two live cards, as the codex prints them at zero luck.
    ("{luck_chance.50} Draw 3, {luck_chance_down.50} Discard 1",
     "50% Draw 3, 50% Discard 1"),
    ("{luck_chance_down.10} Draw 1, {luck_chance.10} Draw 2",
     "90% Draw 1, 10% Draw 2"),
    # And the four live aspects.
    ("[Easy]: {luck_chance.10} to create a Rite", "[Easy]: 10% to create a Rite"),
    ("{luck_chance.5} Aspects Trigger Twice", "5% Aspects Trigger Twice"),
])
def test_live_content_renders_its_authored_odds(text, expected):
    assert placeholders.resolve(text) == expected


def test_down_token_is_the_complement():
    """`_down` prints the other branch of a two-option Split."""
    assert placeholders.resolve("{luck_chance_down.25}") == "75%"
    assert placeholders.resolve("{luck_chance_down.50}") == "50%"


def test_certainties_print_flat():
    """0 and 1 are genuine, not rounding artefacts, so they keep their digits."""
    assert placeholders.resolve("{luck_chance.0}") == "0%"
    assert placeholders.resolve("{luck_chance.100}") == "100%"
    assert placeholders.resolve("{luck_chance_down.0}") == "100%"
    assert placeholders.resolve("{luck_chance_down.100}") == "0%"


def test_tiny_and_near_certain_odds_are_guarded():
    """A rounded 0% would call a real chance impossible; 100% would promise one.

    Unreachable from a live row today -- every authored base is a whole percent --
    but `_percent_number` is a transcription of the game's, and the guard is the
    part of it most easily lost.
    """
    assert placeholders.format_flat_chance(0.002) == "<1%"
    assert placeholders.format_flat_chance(0.998) == ">99%"
    assert placeholders.format_flat_chance(0.0) == "0%"
    assert placeholders.format_flat_chance(1.0) == "100%"


def test_fractional_base_survives_and_rounds_away_from_zero():
    """`{luck_chance.2.5}` is 3%, not 2%.

    Two failure modes in one token. The game splits the placeholder path on "."
    and has to rejoin it, so a fractional base is the case that catches a parser
    reading only the first segment. And Godot's `round()` goes half away from
    zero where Python's built-in is banker's rounding -- which would print 2%.
    """
    assert placeholders.resolve("{luck_chance.2.5}") == "3%"


@pytest.mark.parametrize("text", [
    "{luck_chance.abc}",     # not a number
    "{luck_chance}",         # no base at all
    "{luck_chance.}",        # empty base
    "{luck_chance_down}",
    "{luck_chance.nan}",     # float() would take it; a NaN base prints as 0%
    "{luck_chance.inf}",
    "{luck_chance.1_0}",     # float() would take it as 10
])
def test_malformed_token_stays_visible(text):
    """Loud beats a silent wrong number -- the game's rule, and its reason.

    An unresolved token is obvious on a rendered card; a plausible-looking
    percentage that nobody authored is not.
    """
    assert placeholders.resolve(text) == text


# ---------------------------------------------------------------------------
# last_rite
# ---------------------------------------------------------------------------

def test_last_rite_prints_none():
    """Recollection, the only live user. There is no run, so nothing was spent."""
    assert (placeholders.resolve("Create last used Rite ({last_rite})")
            == "Create last used Rite (None)")


def test_last_event_is_not_the_display_token():
    """`{last_event.name}` is the GAMEPLAY path and is null on an empty history.

    Recollection gates itself on that one. It has no printable value here, so it
    keeps the unresolved treatment rather than borrowing `{last_rite}`'s "None".
    """
    assert placeholders.resolve("{last_event.name}") == "{last_event.name}"


# ---------------------------------------------------------------------------
# Everything else
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "{hand.size}",                  # run state: no honest answer outside a run
    "{resources.life}",
    "{self.valence}",
    "{2} gain \"Gain [1mult]\" while in hand",   # Twinning, authored as-is
])
def test_run_state_placeholders_are_left_verbatim(text):
    """`PlaceholderHelper` leaves a token whose value is null in the string.

    Reproduced deliberately: inventing a value for `{hand.size}` on a card face
    with no hand would state something false.
    """
    assert placeholders.resolve(text) == text


def test_text_without_placeholders_is_untouched():
    assert placeholders.resolve("Draw 1, Gain [1life] per card drawn") == \
        "Draw 1, Gain [1life] per card drawn"
    assert placeholders.resolve("") == ""


def test_adjacent_tokens_do_not_merge():
    """`{a} {b}` is two tokens. A greedy `\\{.*\\}` would eat the gap between."""
    assert placeholders.resolve("{luck_chance.10} {luck_chance.20}") == "10% 20%"


# ---------------------------------------------------------------------------
# The chokepoint
# ---------------------------------------------------------------------------

def test_tokenize_resolves_placeholders():
    """Every drawn string goes through `tokenize`, which is why it resolves.

    If this moves elsewhere, a renderer that draws text some other way starts
    printing raw tokens -- the failure this placement exists to prevent.
    """
    runs = rich_text.tokenize("{luck_chance.50} Draw 3")
    assert runs == [("text", "50% Draw 3")]


def test_a_resolved_line_still_finds_its_symbols():
    """Substitution must not disturb the symbol pass that follows it."""
    runs = rich_text.tokenize("{luck_chance.10} Gain [1life]")
    assert [kind for kind, _ in runs] == ["text", "img"]
    assert runs[0][1] == "10% Gain "
