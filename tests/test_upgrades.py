"""`azoth_logic/upgrades.py` — a transcription of the engine's upgrade rules.

Everything here is really one assertion: that this module agrees with
`GameContentData.apply_upgrade` / `get_upgrade_at_level` in the game repo. A
comparison render that shows a card the game would never produce is worse than
showing no comparison, because it looks authoritative.

The rules being pinned, in the engine's order: tiers are 1-indexed by `level` or
array position, a gap stops the chain, `level` is honoured but never merged,
`_added` keys append while everything else replaces, replacements land before
additions, and a string `x_added` is dropped entirely when `x` is replaced in the
same entry.
"""
import pytest

from azoth_logic import upgrades


CARD = {"id": 1, "name": "Harbinger", "text": "Draw 1", "element": "sol",
        "valence": 2, "triggers": [], "properties": [{"name": "Inert"}]}


def _with(**fields):
    return {**CARD, **fields}


# ---------------------------------------------------------------------------
# Tier selection
# ---------------------------------------------------------------------------

def test_position_is_the_default_tier():
    row = _with(upgrades=[{"text": "one"}, {"text": "two"}])
    assert upgrades.upgrade_at_level(row, 1) == {"text": "one"}
    assert upgrades.upgrade_at_level(row, 2) == {"text": "two"}


def test_an_explicit_level_wins_over_position():
    row = _with(upgrades=[{"level": 3, "text": "third"}])
    assert upgrades.upgrade_at_level(row, 3) == {"level": 3, "text": "third"}
    assert upgrades.upgrade_at_level(row, 1) == {}


def test_a_gap_stops_the_chain():
    """The engine is explicit about this: level 3 is unreachable when level 2 is
    missing, even though the entry exists."""
    row = _with(upgrades=[{"level": 1, "text": "one"}, {"level": 3, "text": "three"}])
    assert upgrades.upgrade_at_level(row, 2) == {}
    assert len(upgrades.tiers(row, "card")) == 1


@pytest.mark.parametrize("level", [0, -1])
def test_level_zero_and_below_have_no_entry(level):
    assert upgrades.upgrade_at_level(_with(upgrades=[{"text": "x"}]), level) == {}


@pytest.mark.parametrize("value", [None, [], "nope", {}, 3])
def test_a_missing_or_malformed_upgrades_field_is_not_an_error(value):
    assert upgrades.upgrade_at_level(_with(upgrades=value), 1) == {}
    assert upgrades.has_upgrade(_with(upgrades=value)) is False


# ---------------------------------------------------------------------------
# Applying one tier
# ---------------------------------------------------------------------------

def test_scalar_fields_are_replaced():
    out = upgrades.apply(CARD, {"text": "Draw 2", "valence": 4})
    assert out["text"] == "Draw 2"
    assert out["valence"] == 4
    assert out["element"] == "sol", "untouched fields survive"


def test_the_source_row_is_never_mutated():
    """`/render` draws the base face from the same dict it upgrades."""
    row = _with(upgrades=[{"text": "Draw 2"}], triggers=[{"name": "a"}])
    upgrades.apply(row, {"text": "Draw 2", "triggers_added": [{"name": "b"}]})
    assert row["text"] == "Draw 1"
    assert row["triggers"] == [{"name": "a"}]


def test_level_is_honoured_but_not_merged_into_the_data():
    out = upgrades.apply(CARD, {"level": 2, "text": "Draw 2"})
    assert "level" not in out
    assert out["upgrade_level"] == 2


def test_upgrade_level_defaults_to_the_next_tier():
    assert upgrades.apply(CARD, {"text": "x"}, current_level=1)["upgrade_level"] == 2


def test_added_arrays_append_rather_than_replace():
    row = _with(triggers=[{"name": "existing"}])
    out = upgrades.apply(row, {"triggers_added": [{"name": "new"}]})
    assert out["triggers"] == [{"name": "existing"}, {"name": "new"}]


def test_added_arrays_land_on_top_of_a_replacement():
    """Replacements first, then additions stack on the NEW state — mixing both
    in one entry is supported, and the order decides the result."""
    row = _with(triggers=[{"name": "old"}])
    out = upgrades.apply(row, {"triggers": [{"name": "replaced"}],
                               "triggers_added": [{"name": "added"}]})
    assert out["triggers"] == [{"name": "replaced"}, {"name": "added"}]


def test_added_arrays_cope_with_a_non_array_base():
    out = upgrades.apply(_with(triggers=None), {"triggers_added": [{"name": "a"}]})
    assert out["triggers"] == [{"name": "a"}]


def test_added_strings_concatenate_with_a_newline():
    out = upgrades.apply(CARD, {"text_added": "Exhaust"})
    assert out["text"] == "Draw 1\nExhaust"


def test_no_leading_newline_onto_an_empty_base():
    out = upgrades.apply(_with(text=""), {"text_added": "Exhaust"})
    assert out["text"] == "Exhaust"


def test_an_added_string_is_dropped_when_the_base_key_is_also_replaced():
    """The engine treats it as display-only in that case — used by the level-up
    reward card. Concatenating anyway would print the rules text twice."""
    out = upgrades.apply(CARD, {"text": "Draw 2", "text_added": "Exhaust"})
    assert out["text"] == "Draw 2"


# ---------------------------------------------------------------------------
# Card into aspect
# ---------------------------------------------------------------------------

def test_content_type_changes_which_renderer_draws_it():
    """28 cards upgrade INTO aspects: the card transforms and moves to the
    aspect bar. Drawing that face as a card would show a card that cannot
    exist."""
    row = _with(upgrades=[{"content_type": "aspect", "attunement": 1,
                           "image": "ctrig.png"}])
    upgraded, kind, _ = upgrades.tiers(row, "card")[0]
    assert kind == "aspect"
    assert upgraded["attunement"] == 1
    assert upgraded["image"] == "ctrig.png", "the aspect has its own art"


def test_the_database_says_event_and_the_renderer_says_rite():
    assert upgrades.kind_of({"content_type": "event"}, "card") == "rite"


def test_an_upgrade_that_says_nothing_about_type_stays_the_same_kind():
    assert upgrades.kind_of({"text": "Draw 2"}, "card") == "card"
    assert upgrades.kind_of({"content_type": "wat"}, "aspect") == "aspect"


# ---------------------------------------------------------------------------
# tiers()
# ---------------------------------------------------------------------------

def test_no_upgrades_means_nothing_to_compare():
    assert upgrades.tiers(CARD, "card") == []
    assert upgrades.has_upgrade(CARD) is False


def test_tiers_are_cumulative():
    """Each `apply_upgrade` lands on the state the last one left, not on the
    printed card. Tier 2 keeps tier 1's trigger."""
    row = _with(upgrades=[{"triggers_added": [{"name": "one"}]},
                          {"triggers_added": [{"name": "two"}]}])
    first, second = upgrades.tiers(row, "card")
    assert [t["name"] for t in first[0]["triggers"]] == ["one"]
    assert [t["name"] for t in second[0]["triggers"]] == ["one", "two"]


def test_tiers_report_their_level():
    row = _with(upgrades=[{"text": "a"}, {"text": "b"}])
    assert [level for _, _, level in upgrades.tiers(row, "card")] == [1, 2]


def test_a_self_referential_level_chain_cannot_spin_forever():
    """A malformed `level` that keeps resolving to itself would otherwise loop
    inside an autocomplete-adjacent code path."""
    row = _with(upgrades=[{"level": 1, "text": "loop"}] * 3)
    assert len(upgrades.tiers(row, "card")) <= 16


# ---------------------------------------------------------------------------
# The `+` suffix
# ---------------------------------------------------------------------------

def test_an_upgraded_card_gains_a_plus():
    """`base_card.gd::set_upgrade_card_visuals` puts it on the label."""
    assert upgrades.plus_name("Harbinger") == "Harbinger+"


def test_the_plus_is_not_doubled():
    """The game guards this explicitly:
    `base_name if base_name.ends_with("+") else base_name + "+"`."""
    assert upgrades.plus_name("Harbinger+") == "Harbinger+"


def test_one_plus_whatever_the_tier():
    """The game does not write `++` for tier 2 — it appends to the printed name,
    which never carried a suffix to begin with."""
    once = upgrades.plus_name("Harbinger")
    assert upgrades.plus_name(once) == once


@pytest.mark.parametrize("name", [None, ""])
def test_a_missing_name_does_not_crash(name):
    assert upgrades.plus_name(name) == "+"


def test_the_suffix_never_reaches_the_data():
    """It is a render-time label. In the row it would change the cache key, the
    filename, and anything that looks the card back up by name."""
    row = _with(upgrades=[{"text": "Draw 2"}])
    upgraded, _, _ = upgrades.tiers(row, "card")[0]
    assert upgraded["name"] == "Harbinger", "no plus in the row itself"
