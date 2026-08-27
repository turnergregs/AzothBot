"""Tests for `/search` and the content-matching behind it.

The matcher mirrors the Codex's `ContentSearch._matches_query`, so a query means
the same thing in Discord as it does in-game. Several tests exist specifically to
pin the parts of that behaviour that are easy to lose: the deep JSON search, the
nested action walk, and where null sorts.
"""
import pytest

from azoth_logic import content_search as cs

CARD = {"name": "Ablution", "text": "Draw 1, Gain [1life] per card drawn this link",
        "type": "spell", "element": "sol", "valence": 2, "subtypes": ["Sacred"],
        "actions": [{"name": "Draw", "amount": 1},
                    {"name": "Gain", "resource": "Life",
                     "count": {"event": "drawn", "scope": "link"}}],
        "triggers": [], "properties": []}
CATALYST = {"name": "Multmaxer", "text": "If you would gain mult...", "type": "catalyst",
            "element": None, "valence": None, "subtypes": [],
            "actions": [], "triggers": [], "properties": []}
MAGNIFY = {"name": "Fervor", "text": "Magnify 1 per power", "type": "spell",
           "element": "blood", "valence": 3, "subtypes": [],
           "actions": [], "triggers": [],
           "properties": [{"name": "Magnify", "amount": {"count": {"stat": "power"}}}]}
ASPECT = {"name": "Readiness", "text": "+1 Starting Hand Size", "attunement": 2,
          "actions": [], "triggers": [], "properties": []}
RITE = {"name": "Amplification", "text": "[8mult] next link",
        "actions": [], "triggers": [], "properties": []}

POOL = [(CARD, "card"), (CATALYST, "card"), (MAGNIFY, "card"),
        (ASPECT, "aspect"), (RITE, "rite")]


# ---------------------------------------------------------------------------
# Free-text matching
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,expected", [
    ("ablution", "name"), ("per card drawn", "rules text"),
    ("sacred", "subtype"), ("spell", "type"), ("ABLUTION", "case-insensitive"),
])
def test_query_scans_the_obvious_fields(query, expected):
    assert cs.matches_query(CARD, query), f"should match on {expected}"


# A card whose JSON holds terms that appear NOWHERE in its visible fields. An
# earlier version of the deep-search test used needles that were also in the
# rules text, so it passed with the deep search removed entirely -- the mutation
# run is what exposed that.
HIDDEN = {"name": "Opaque", "text": "Do a thing.", "type": "spell",
          "element": "anima", "valence": 4, "subtypes": [],
          "actions": [{"name": "Exhaust", "amount": 1,
                       "count": {"zone": "bin", "scope": "link"}}],
          "triggers": [{"name": "link_played",
                        "actions": [{"name": "Recall", "seek": "instance_id"}]}],
          "properties": [{"name": "Transmutable"}]}


@pytest.mark.parametrize("needle,where", [
    ("exhaust", "an action name"),
    ("bin", "a nested count value"),
    ("link_played", "a trigger name"),
    ("recall", "an action nested inside a trigger"),
    ("transmutable", "a property name"),
    ("instance_id", "a nested field value"),
])
def test_query_deep_searches_nested_json(needle, where):
    """The most valuable part of the Codex search: `actions`, `triggers` and
    `properties` are JSON, so the useful queries live in their keys and values.

    Every needle here is absent from the name, rules text, type and subtypes, so
    only the deep search can find it.
    """
    for field in ("name", "text", "type"):
        assert needle not in str(HIDDEN.get(field) or "").lower(), \
            f"fixture is wrong: {needle!r} leaks into {field}"
    assert cs.matches_query(HIDDEN, needle), f"should match {where}"


def test_query_does_not_match_absent_json_terms():
    assert not cs.matches_query(HIDDEN, "dissolve")


def test_query_matches_numeric_fields():
    assert cs.matches_query(CARD, "2"), "valence"
    assert cs.matches_query(ASPECT, "2"), "attunement"


def test_empty_query_matches_everything():
    for q in ("", None, "   "):
        assert cs.matches_query(CATALYST, q)


def test_unmatched_query_matches_nothing():
    assert not cs.matches_query(CARD, "zzzznotpresent")


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

def test_content_type_filter():
    assert [i["name"] for i, _ in cs.search(POOL, content_type="aspect")] == ["Readiness"]
    assert [i["name"] for i, _ in cs.search(POOL, content_type="rite")] == ["Amplification"]


def test_colourless_matches_null_and_default():
    """64 of 400 cards have no element. `default` is the same face in-game."""
    assert cs._element_matches({"element": None}, cs.COLOURLESS)
    assert cs._element_matches({"element": "default"}, cs.COLOURLESS)
    assert cs._element_matches({"element": ""}, cs.COLOURLESS)
    assert not cs._element_matches({"element": "sol"}, cs.COLOURLESS)


def test_element_filter_excludes_colourless():
    names = [i["name"] for i, _ in cs.search(POOL, element="sol")]
    assert names == ["Ablution"]


def test_valence_filter_is_exact():
    assert len(cs.search(POOL, valence=2)) == 1
    assert len(cs.search(POOL, valence=99)) == 0


def test_subtype_filter_is_exact_not_substring():
    """`Sacred` must not be matched by `sac` -- the dropdown offers whole values,
    and a substring match would silently widen the result set."""
    assert len(cs.search(POOL, subtype="Sacred")) == 1
    assert len(cs.search(POOL, subtype="sacred")) == 1, "but it is case-insensitive"
    assert len(cs.search(POOL, subtype="sac")) == 0


def test_card_type_filter():
    assert [i["name"] for i, _ in cs.search(POOL, card_type="catalyst")] == ["Multmaxer"]


def test_filters_are_anded():
    assert len(cs.search(POOL, element="sol", valence=2)) == 1
    assert len(cs.search(POOL, element="sol", valence=3)) == 0


# ---------------------------------------------------------------------------
# Action search
# ---------------------------------------------------------------------------

def test_action_matches_by_name():
    assert cs.has_action(CARD, "Draw")
    assert cs.has_action(CARD, "draw"), "case-insensitive"
    assert not cs.has_action(CARD, "Exhaust")


def test_action_walks_nested_actions():
    """Actions nest -- a Split carries sub-actions, a trigger carries its own
    list. A top-level scan misses most of them."""
    nested = {"name": "N", "actions": [{"name": "Split", "actions": [{"name": "Recall"}]}],
              "triggers": [{"name": "link_played", "actions": [{"name": "Exhaust"}]}]}
    assert cs.has_action(nested, "Recall"), "inside a nested action"
    assert cs.has_action(nested, "Exhaust"), "inside a trigger"


def test_action_does_not_match_properties():
    """Magnify is a PROPERTY. `action=` should not find it -- the free-text
    query is what reaches properties."""
    assert not cs.has_action(MAGNIFY, "Magnify")
    assert cs.matches_query(MAGNIFY, "Magnify")


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------

def test_name_sort_is_case_insensitive():
    pool = [({"name": "zebra"}, "card"), ({"name": "Apple"}, "card")]
    assert [i["name"] for i, _ in cs.search(pool)] == ["Apple", "zebra"]


def test_valence_sort_puts_null_last():
    """Catalysts, aspects and rites have no valence. Sorting them first would
    bury every real result under 63 catalysts.

    All the null-valence items go to the END as a group, ordered by name within
    it -- not just one of them, which is what an earlier version of this test
    wrongly asserted.
    """
    ranked = cs.search(POOL, sort="valence")
    valences = [i.get("valence") for i, _ in ranked]
    first_null = valences.index(None)
    assert all(v is None for v in valences[first_null:]), \
        f"nulls must be contiguous at the end, got {valences}"
    assert all(v is not None for v in valences[:first_null])
    tail = [i["name"] for i, _ in ranked[first_null:]]
    assert tail == sorted(tail), "nulls ordered by name within the group"


def test_element_sort_uses_the_canonical_order():
    """Alphabetising elements is meaningless; the Codex uses a fixed order."""
    pool = [({"name": "s", "element": "sol"}, "card"),
            ({"name": "a", "element": "anima"}, "card"),
            ({"name": "b", "element": "blood"}, "card")]
    assert [i["element"] for i, _ in cs.search(pool, sort="element")] == ["anima", "blood", "sol"]


# ---------------------------------------------------------------------------
# Reply summary
# ---------------------------------------------------------------------------

def test_describe_lists_active_filters_only():
    assert cs.describe({"query": "draw", "element": "sol"}) == '"draw" · element: sol'
    assert cs.describe({"valence": 0}) == "valence: 0", "zero is a filter, not absence"
    assert cs.describe({}) == "no filters"


def test_command_caps_are_sane():
    from azoth_commands import search
    assert search.DEFAULT_LIMIT <= search.MAX_LIMIT
    assert search.MAX_LIMIT <= 50, "rendering is ~0.7s per item on a cold cache"
