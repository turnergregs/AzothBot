"""`azoth_logic/deck_bulk.py` — `/bulk_add_to_deck`.

What matters is the all-or-nothing promise: every problem in a payload is found
before anything is written, and the write is ONE insert. The database is faked
at `supabase_helpers`' three entry points, which is all the module touches.
"""
import pytest

import supabase_helpers
from azoth_logic import deck_bulk
from azoth_logic.deck_bulk import DeckBulkError


TABLES = {
    "decks": [
        {"id": 3, "name": "Base Draft Deck"},
        {"id": 22, "name": "Testing Fates"},
    ],
    "cards": [
        {"id": 500, "name": "Gouge"},
        {"id": 501, "name": "Alms"},
        # A card and an aspect sharing a name: the type has to pick.
        {"id": 502, "name": "Echo"},
        {"id": 600, "name": "Twin"},
        {"id": 601, "name": "Twin"},
    ],
    "aspects": [
        {"id": 900, "name": "Pittance"},
        {"id": 901, "name": "Echo"},
    ],
    "rites": [
        {"id": 70, "name": "Rebirth"},
    ],
}


@pytest.fixture
def db(monkeypatch):
    log = {"inserts": [], "updates": [], "fetches": []}

    def fetch_all(table, columns=None, filters=None, sort=None, limit=None):
        log["fetches"].append((table, dict(filters or {})))
        rows = TABLES.get(table, [])
        return [r for r in rows if all(r.get(k) == v for k, v in (filters or {}).items())]

    def create_record(table, data):
        log["inserts"].append((table, data))
        return data

    def update_record(table, record_id, data):
        log["updates"].append((table, record_id))
        return [{"id": record_id}]

    monkeypatch.setattr(supabase_helpers, "fetch_all", fetch_all)
    monkeypatch.setattr(supabase_helpers, "create_record", create_record)
    monkeypatch.setattr(supabase_helpers, "update_record", update_record)
    return log


def _payload(*decks):
    return {"decks": [{"name": name, "contents": contents} for name, contents in decks]}


def test_adds_every_item_in_one_insert(db):
    payload = _payload(
        ("Testing Fates", [{"content_type": "aspect", "name": "Pittance"},
                           {"content_type": "rite", "name": "Rebirth"}]),
        ("Base Draft Deck", [{"name": "Gouge"}, {"content_type": "card", "content_id": 501}]),
    )

    deck_bulk.apply(payload)

    assert len(db["inserts"]) == 1, "one request, so one transaction"
    table, rows = db["inserts"][0]
    assert table == "deck_contents"
    assert rows == [
        {"deck_id": 22, "content_type": "aspect", "content_id": 900},
        {"deck_id": 22, "content_type": "rite", "content_id": 70},
        {"deck_id": 3, "content_type": "card", "content_id": 500},
        {"deck_id": 3, "content_type": "card", "content_id": 501},
    ]
    assert sorted(db["updates"]) == [("decks", 3), ("decks", 22)]


def test_content_type_defaults_to_card_and_picks_between_shared_names(db):
    deck_bulk.apply(_payload(("Testing Fates", [
        {"name": "Echo"}, {"content_type": "aspect", "name": "Echo"}])))

    rows = db["inserts"][0][1]
    assert [(r["content_type"], r["content_id"]) for r in rows] == [("card", 502), ("aspect", 901)]


def test_one_entry_per_copy(db):
    plan = deck_bulk.apply(_payload(("Base Draft Deck", [{"name": "Alms"}, {"name": "Alms"}])))

    assert len(db["inserts"][0][1]) == 2
    assert deck_bulk.report_lines(plan) == {"Base Draft Deck": ["Alms (card #501) x2"]}


def test_every_problem_is_reported_and_nothing_is_written(db):
    payload = _payload(
        ("Testing Fates", [{"content_type": "aspect", "name": "Pittance"},
                           {"content_type": "aspect", "name": "Nope"}]),
        ("No Such Deck", [{"name": "Gouge"}]),
        ("Base Draft Deck", [{"name": "Twin"}]),
    )

    with pytest.raises(DeckBulkError) as e:
        deck_bulk.apply(payload)

    text = str(e.value)
    assert "Testing Fates: No aspect `Nope`." in text
    assert "No deck named `No Such Deck`." in text
    assert "matches 2 rows (ids 600, 601)" in text, "an ambiguous name is refused, not guessed"
    assert db["inserts"] == [] and db["updates"] == []


@pytest.mark.parametrize("payload, message", [
    ([], '"decks" list'),
    ({"decks": []}, "nothing to add"),
    ({"decks": [{"contents": [{"name": "Gouge"}]}]}, "needs a deck `name`"),
    ({"decks": [{"name": "Testing Fates", "contents": []}]}, "non-empty `contents`"),
    (_payload(("Testing Fates", [{"content_type": "hero", "name": "Lumis"}])), "content_type must be"),
    (_payload(("Testing Fates", [{"name": "Gouge", "content_id": 500}])), "exactly one of"),
    (_payload(("Testing Fates", [{}])), "exactly one of"),
])
def test_malformed_payloads_never_reach_the_database(db, payload, message):
    with pytest.raises(DeckBulkError) as e:
        deck_bulk.apply(payload)

    assert message in str(e.value)
    assert db["fetches"] == [] and db["inserts"] == []


def test_the_pre_rename_rite_spelling_is_accepted(db):
    deck_bulk.apply(_payload(("Testing Fates", [{"content_type": "event", "name": "Rebirth"}])))

    assert db["inserts"][0][1] == [{"deck_id": 22, "content_type": "rite", "content_id": 70}]


def test_a_failed_insert_says_nothing_was_added(db, monkeypatch):
    def broken(table, data):
        raise supabase_helpers.SupabaseQueryError("boom")
    monkeypatch.setattr(supabase_helpers, "create_record", broken)

    with pytest.raises(DeckBulkError) as e:
        deck_bulk.apply(_payload(("Base Draft Deck", [{"name": "Gouge"}])))

    assert "nothing was added" in str(e.value)
    assert db["updates"] == []
