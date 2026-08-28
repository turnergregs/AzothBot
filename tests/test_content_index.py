"""Tests for the merged `/get` and `/render` lookup.

The index exists because Discord fires an autocomplete on every keystroke and
reading the three content tables live costs 0.85-2.3s. It is cached in-process
with a TTL and invalidated explicitly on writes.
"""
import sys
import time

import pytest

from azoth_logic import content_index as ci

ROWS = {
    "cards": [{"id": 447, "name": "Diversity"}, {"id": 384, "name": "Anima Shrinker"},
              {"id": 1, "name": "Ablution"},
              # In the ARCHIVED deck only -- the retired-content fixture.
              {"id": 999, "name": "Retired Relic"}],
    "aspects": [{"id": 100, "name": "Anima Shrinker"}, {"id": 120, "name": "Readiness"}],
    "events": [{"id": 82, "name": "Amplification"}],
    # `Zeta` is an EXACT match for "zeta" but sorts alphabetically AFTER the two
    # that merely contain it. Without ranking it would come last -- which is what
    # makes this the fixture that can tell the two behaviours apart.
    "ranking": [{"id": 900, "name": "Zeta"},
                {"id": 901, "name": "Alpha Zeta"},
                {"id": 902, "name": "Beta Zeta"}],
}

# One live deck, one archived. `cards`, `aspects` and `events` have no
# `archived_at` of their own, so deck membership is the ONLY liveness signal.
DECKS = [{"id": 1, "archived_at": None},
         {"id": 2, "archived_at": "2026-01-23T19:33:34+00:00"}]

# (content_type, id) pairs that sit in the ARCHIVED deck instead of the live one.
DEAD = {("card", 999)}

CONTENT_TYPE = {"cards": "card", "aspects": "aspect", "events": "event"}


def _deck_contents():
    """Every row in ROWS, filed under the live deck unless listed in DEAD.

    Derived rather than hardcoded so a test that monkeypatches ROWS (the ranking
    fixtures do) gets live content without having to restate deck membership.
    """
    return [{"deck_id": 2 if (ctype, r["id"]) in DEAD else 1,
             "content_type": ctype, "content_id": r["id"]}
            for table, ctype in CONTENT_TYPE.items()
            for r in ROWS.get(table, [])]


@pytest.fixture(autouse=True)
def stub_tables(monkeypatch):
    calls = []

    def fake_fetch(table, columns=None, filters=None, sort=None, limit=None):
        calls.append(table)
        if table == "decks":
            return list(DECKS)
        if table == "deck_contents":
            return _deck_contents()
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
    """Three content tables, plus the two that say what is live."""
    ci.entries()
    assert sorted(stub_tables) == ["aspects", "cards", "deck_contents", "decks", "events"]


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
    assert len(stub_tables) == 5


def test_ttl_expiry_refetches(stub_tables, monkeypatch):
    """The TTL is the backstop for edits made elsewhere -- the Codex, direct
    SQL, another bot instance -- which cannot call invalidate()."""
    ci.entries()
    stub_tables.clear()
    monkeypatch.setattr(ci, "TTL", -1)
    ci.entries()
    assert len(stub_tables) == 5


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


# ---------------------------------------------------------------------------
# Liveness
# ---------------------------------------------------------------------------
# `cards`, `aspects` and `events` have no `archived_at`. A row that is no longer
# used just sits there, and two thirds of the live database is exactly that --
# 400 cards, 154 of them reachable. Deck membership is the only signal.

def test_retired_content_is_not_offered():
    assert "Retired Relic (Card #999)" not in ci.choices("Retired")
    assert ci.choices("Retired") == {}


def test_live_content_is_still_offered():
    assert "Diversity (Card #447)" in ci.choices("Diversity")


def test_entries_can_still_see_everything_on_request():
    """`/add_to_deck` needs the dead rows -- it is how they come back."""
    names = [n for _, _, n in ci.entries(live_only=False)]
    assert "Retired Relic" in names
    assert "Retired Relic" not in [n for _, _, n in ci.entries()]


def test_membership_of_an_archived_deck_does_not_count():
    """Retired Relic IS in a deck -- deck 2, which is archived. Counting any
    deck rather than an unarchived one would make this indistinguishable."""
    assert ci.is_live("card", 999) is False
    assert ci.is_live("card", 447) is True


def test_a_retired_ref_does_not_resolve():
    assert ci.resolve("card:999") == (None, None)
    assert ci.resolve("Retired Relic") == (None, None)


def test_a_retired_ref_still_resolves_when_asked_for_explicitly():
    kind, row = ci.resolve("card:999", live_only=False)
    assert (kind, row["name"]) == ("card", "Retired Relic")


def test_absence_explains_retirement_rather_than_claiming_it_is_missing():
    """"Could not find" is wrong for a row that plainly exists -- it reads as
    data loss. The message has to name the fix, since /add_to_deck is one
    command away."""
    message = ci.absence_reason("card:999")
    assert "Retired Relic" in message
    assert "add_to_deck" in message
    assert "Could not find" not in message


def test_absence_of_something_that_never_existed_says_so():
    assert "Could not find" in ci.absence_reason("card:123456")
    assert "Could not find" in ci.absence_reason("No Such Thing")


def test_a_live_match_beats_a_retired_one_of_the_same_name(monkeypatch):
    """Names collide across types. Resolving in card/aspect/rite order alone
    would let a retired card shadow the live aspect someone meant."""
    monkeypatch.setitem(ROWS, "cards", [{"id": 999, "name": "Twin"}])
    monkeypatch.setitem(ROWS, "aspects", [{"id": 100, "name": "Twin"}])
    ci.invalidate()   # card 999 is already in DEAD
    kind, row = ci.resolve("Twin")
    assert (kind, row["id"]) == ("aspect", 100), "the card is retired; the aspect is live"


# --- The failure mode that would break every command ------------------------
# If the deck read fails or returns nothing, "no live content" and "cannot see
# the decks" look identical. Concluding the former hides the ENTIRE catalogue
# behind a "not in any active deck" message that is false for all 626 rows.

def test_an_unreadable_deck_table_shows_everything_rather_than_nothing(monkeypatch):
    monkeypatch.setattr(ci, "fetch_all",
                        lambda table, *a, **k: [] if table in ("decks", "deck_contents")
                        else list(ROWS.get(table, [])))
    ci.invalidate()
    assert len(ci.entries()) == 7, "an empty live set must not filter anything out"
    assert ci.is_live("card", 999) is True


def test_no_unarchived_decks_also_falls_back(monkeypatch):
    """Every deck archived is indistinguishable from a failed read, and the
    same fallback is the safe answer."""
    # Patch the module OBJECT, not "tests.test_content_index" -- there is no
    # tests/__init__.py, so the string form imports a second copy of this module
    # and the patch lands on something the fixture never reads.
    monkeypatch.setattr(sys.modules[__name__], "DECKS",
                        [{"id": 2, "archived_at": "2026-01-01"}])
    ci.invalidate()
    assert "Retired Relic" in [n for _, _, n in ci.entries()]


# ---------------------------------------------------------------------------
# Single-kind names, for the /update_* pickers
# ---------------------------------------------------------------------------

def test_names_are_scoped_to_one_kind_and_to_live_content():
    assert ci.names("card") == ["Ablution", "Anima Shrinker", "Diversity"]
    assert "Retired Relic" not in ci.names("card")
    assert ci.names("aspect") == ["Anima Shrinker", "Readiness"]


def test_names_filter_by_substring():
    assert ci.names("card", "abl") == ["Ablution"]
