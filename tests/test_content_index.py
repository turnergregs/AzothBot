"""Tests for the merged `/get` and `/render` lookup.

The index exists because Discord fires an autocomplete on every keystroke and
reading the three content tables live costs 0.85-2.3s. It is cached in-process
with a TTL and invalidated explicitly on writes.
"""
import time

import pytest

from azoth_logic import content_index as ci

ROWS = {
    "cards": [{"id": 447, "name": "Diversity"}, {"id": 384, "name": "Anima Shrinker"},
              {"id": 1, "name": "Ablution"}],
    "aspects": [{"id": 100, "name": "Anima Shrinker"}, {"id": 120, "name": "Readiness"}],
    "events": [{"id": 82, "name": "Amplification"}],
    # `Zeta` is an EXACT match for "zeta" but sorts alphabetically AFTER the two
    # that merely contain it. Without ranking it would come last -- which is what
    # makes this the fixture that can tell the two behaviours apart.
    "ranking": [{"id": 900, "name": "Zeta"},
                {"id": 901, "name": "Alpha Zeta"},
                {"id": 902, "name": "Beta Zeta"}],
}


@pytest.fixture(autouse=True)
def stub_tables(monkeypatch):
    calls = []

    def fake_fetch(table, columns=None, filters=None, sort=None, limit=None):
        calls.append(table)
        rows = ROWS.get(table, [])
        if filters and "id" in filters:
            return [r for r in rows if r["id"] == filters["id"]]
        if filters and "name" in filters:
            return [r for r in rows if r["name"] == filters["name"]]
        return list(rows)

    monkeypatch.setattr(ci, "fetch_all", fake_fetch)
    ci.invalidate()
    yield calls
    ci.invalidate()


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def test_index_reads_every_table_once(stub_tables):
    ci.entries()
    assert sorted(stub_tables) == ["aspects", "cards", "events"]


def test_second_call_is_served_from_cache(stub_tables):
    ci.entries()
    stub_tables.clear()
    ci.entries()
    assert stub_tables == [], "a warm index must not touch the database"


def test_invalidate_forces_a_refetch(stub_tables):
    """Creating or deleting content calls this, so a new item is
    autocompletable at once rather than after the TTL."""
    ci.entries()
    stub_tables.clear()
    ci.invalidate()
    ci.entries()
    assert len(stub_tables) == 3


def test_ttl_expiry_refetches(stub_tables, monkeypatch):
    """The TTL is the backstop for edits made elsewhere -- the Codex, direct
    SQL, another bot instance -- which cannot call invalidate()."""
    ci.entries()
    stub_tables.clear()
    monkeypatch.setattr(ci, "TTL", -1)
    ci.entries()
    assert len(stub_tables) == 3


def test_rows_without_a_name_are_skipped(monkeypatch, stub_tables):
    monkeypatch.setitem(ROWS, "cards", [{"id": 1, "name": None}, {"id": 2, "name": "Real"}])
    ci.invalidate()
    assert [n for _, _, n in ci.entries() if n is None] == []


# ---------------------------------------------------------------------------
# Labels and refs
# ---------------------------------------------------------------------------

def test_label_shows_type_and_id():
    assert ci.label("card", 447, "Diversity") == "Diversity (Card #447)"


def test_rites_are_labelled_rite_not_event():
    """The database calls them events; users see rites. The REF still encodes
    `event:13`, so deck_contents and parse_item_ref keep working."""
    assert ci.label("rite", 13, "Sever") == "Sever (Rite #13)"
    assert ci.REF_TYPE["rite"] == "event"
    assert ci.choices("Amplification")["Amplification (Rite #82)"] == "event:82"


def test_choices_disambiguate_colliding_names():
    """17 names exist on more than one content type. The old split handled that
    by making you pick /get_card vs /get_aspect; here the label does it."""
    got = ci.choices("Anima Shrinker")
    assert set(got) == {"Anima Shrinker (Card #384)", "Anima Shrinker (Aspect #100)"}
    assert set(got.values()) == {"card:384", "aspect:100"}


def test_exact_matches_rank_first(monkeypatch):
    """Typing a full name must put it at the top even when it is a substring of
    other entries that sort ahead of it alphabetically.

    An earlier version of this used a query with a single match, so it passed
    with the ranking removed entirely -- the mutation run caught that.
    """
    monkeypatch.setitem(ROWS, "cards", ROWS["ranking"])
    ci.invalidate()
    order = list(ci.choices("zeta"))
    assert order[0].startswith("Zeta"), f"exact match should lead, got {order}"
    assert len(order) == 3, "all three still match as substrings"


def test_prefix_matches_rank_above_mid_string(monkeypatch):
    monkeypatch.setitem(ROWS, "cards", [{"id": 1, "name": "Beta Alpha"},
                                        {"id": 2, "name": "Alpha Beta"}])
    ci.invalidate()
    assert list(ci.choices("alpha"))[0].startswith("Alpha Beta")


def test_choices_are_capped():
    assert len(ci.choices("", limit=2)) == 2


def test_empty_query_returns_a_sample():
    assert len(ci.choices("")) > 0


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ref,kind,name", [
    ("card:447", "card", "Diversity"),
    ("aspect:100", "aspect", "Anima Shrinker"),
    ("event:82", "rite", "Amplification"),
])
def test_resolve_an_encoded_ref(ref, kind, name):
    got_kind, row = ci.resolve(ref)
    assert (got_kind, row["name"]) == (kind, name)


def test_resolve_falls_back_to_a_typed_name():
    """A user can type instead of picking; the deck commands behave the same."""
    kind, row = ci.resolve("Readiness")
    assert (kind, row["id"]) == ("aspect", 120)


def test_resolve_a_missing_ref_returns_nothing():
    assert ci.resolve("card:99999") == (None, None)
    assert ci.resolve("nope") == (None, None)
    assert ci.resolve("") == (None, None)


def test_ambiguous_typed_name_resolves_in_a_fixed_order():
    """`Anima Shrinker` is both a card and an aspect. A typed name cannot say
    which, so card wins -- deterministically, not by chance."""
    kind, _ = ci.resolve("Anima Shrinker")
    assert kind == "card"
