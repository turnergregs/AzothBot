"""`azoth_logic/deck_staging.py` — `/merge_staging` and `/stage`.

Two promises. The destinations are DERIVED: the command this replaces pinned
deck ids, was hidden 2026-08-27 when they stopped meaning what its constants
said, and nothing here may name one. And a row MOVES: the old command deleted
from Staging and then inserted, so a failure part-way lost content.

The database is faked at the `supabase_helpers` entry points the module touches.
"""
import pytest

import supabase_helpers
from azoth_logic import deck_staging
from azoth_logic.deck_staging import StagingError


def _deck(deck_id, name, usage_type, archived=False, deck_type="base"):
    return {"id": deck_id, "name": name, "type": deck_type, "usage_type": usage_type,
            "archived_at": "2026-01-01" if archived else None}


DECKS = [
    _deck(1, "Holding", "staging"),
    _deck(2, "Pool", "draft"),
    _deck(3, "Combos", "draft"),
    _deck(4, "Fates", "draft"),
    _deck(5, "Rite Pool", "weighted"),
    # Decks that hold the same kinds and deal none of them to a draft.
    _deck(6, "Starter", "starter"),
    _deck(7, "Curses", "curse"),
    _deck(8, "Old Fates", "draft", archived=True),
    _deck(9, "Someone's Deck", "draft", deck_type="custom"),
]

CARDS = [
    {"id": 100, "name": "Staged Card", "valence": 2, "element": "sol"},
    {"id": 101, "name": "Staged Combo", "valence": None, "element": None},
    {"id": 110, "name": "Pool Card", "valence": 1, "element": "blood"},
    {"id": 111, "name": "Pool Combo", "valence": None, "element": None},
]
ASPECTS = [{"id": 200, "name": "Staged Aspect"}, {"id": 210, "name": "Pool Aspect"}]
RITES = [{"id": 300, "name": "Staged Rite"}, {"id": 310, "name": "Pool Rite"}]


def _row(row_id, deck_id, content_type, content_id):
    return {"id": row_id, "deck_id": deck_id, "content_type": content_type,
            "content_id": content_id}


def _rows(*triples):
    return [_row(i + 1, *t) for i, t in enumerate(triples)]


# Each kind's home holds more of it than any other draft deck; the starter,
# curse, archived and custom decks hold more still and must not win.
LIVE = [
    (2, "card", 110), (2, "card", 110), (2, "aspect", 210),
    (3, "card", 111), (3, "card", 111), (3, "card", 110),
    (4, "aspect", 210), (4, "aspect", 210),
    # A deck deals only the kinds its usage type deals: the aspects here outnumber
    # the aspect deck's and are still not where a staged aspect belongs.
    (5, "rite", 310), (5, "aspect", 210), (5, "aspect", 210), (5, "aspect", 210),
    (6, "card", 110), (6, "card", 110), (6, "card", 110), (6, "card", 111), (6, "card", 111),
    (6, "card", 111),
    (7, "aspect", 210), (7, "aspect", 210), (7, "aspect", 210),
    (8, "aspect", 210), (8, "aspect", 210), (8, "aspect", 210),
    (9, "card", 110), (9, "card", 110), (9, "card", 110),
]
STAGED = [(1, "card", 100), (1, "card", 101), (1, "aspect", 200), (1, "rite", 300)]


@pytest.fixture
def db(monkeypatch):
    state = {
        "decks": DECKS, "cards": CARDS, "aspects": ASPECTS, "rites": RITES,
        "deck_contents": _rows(*STAGED, *LIVE),
        "moves": [], "stamped": [], "inserts": [], "fail_move_to": None, "fail_move_from": None,
    }

    def matches(row, filters):
        for key, value in (filters or {}).items():
            if isinstance(value, list):
                if row.get(key) not in value:
                    return False
            elif row.get(key) != value:
                return False
        return True

    def fetch_all(table, columns=None, filters=None, sort=None, limit=None):
        return [dict(r) for r in state[table] if matches(r, filters)]

    def move_deck_contents(row_ids, from_deck_id, to_deck_id):
        if to_deck_id == state["fail_move_to"] or from_deck_id == state["fail_move_from"]:
            raise supabase_helpers.SupabaseQueryError("boom")
        state["moves"].append((sorted(row_ids), from_deck_id, to_deck_id))
        moved = [r for r in state["deck_contents"]
                 if r["id"] in row_ids and r["deck_id"] == from_deck_id]
        for r in moved:
            r["deck_id"] = to_deck_id
        return moved

    def update_record(table, record_id, data):
        state["stamped"].append((table, record_id))
        return [{"id": record_id}]

    monkeypatch.setattr(supabase_helpers, "fetch_all", fetch_all)
    monkeypatch.setattr(supabase_helpers, "move_deck_contents", move_deck_contents)
    def create_record(table, data):
        state["inserts"].append((table, data))
        return [data]

    monkeypatch.setattr(supabase_helpers, "update_record", update_record)
    monkeypatch.setattr(supabase_helpers, "create_record", create_record)
    return state


def _destinations(merge):
    return {row["item"]["name"]: move["deck"]["name"]
            for move in merge["moves"] for row in move["rows"]}


def test_each_kind_goes_to_the_live_deck_already_holding_the_most_of_it(db):
    """No ids: the hidden command sent aspects to a constant that had become a
    different deck. The starter, curse, archived and custom decks all hold more
    than the real homes here and are not candidates."""
    assert _destinations(deck_staging.plan()) == {
        "Staged Card": "Pool",
        "Staged Combo": "Combos",
        "Staged Aspect": "Fates",
        "Staged Rite": "Rite Pool",
    }


def test_planning_writes_nothing(db):
    deck_staging.plan()
    assert db["moves"] == [] and db["stamped"] == []


def test_rows_move_out_of_staging_and_every_touched_deck_is_stamped(db):
    """A row is moved by its own id, from Staging: the old command deleted it and
    inserted another, and lost it when the second request failed."""
    merge = deck_staging.plan()

    assert deck_staging.apply(merge) == []

    assert sorted(db["moves"]) == [([1], 1, 2), ([2], 1, 3), ([3], 1, 4), ([4], 1, 5)]
    assert not [r for r in db["deck_contents"] if r["deck_id"] == 1]
    assert len(db["deck_contents"]) == len(STAGED) + len(LIVE), "nothing copied or dropped"
    assert sorted(db["stamped"]) == [("decks", n) for n in (1, 2, 3, 4, 5)]


def test_an_empty_staging_deck_is_not_an_error(db):
    db["deck_contents"] = _rows(*LIVE)
    merge = deck_staging.plan()
    assert merge["moves"] == []
    assert deck_staging.apply(merge) == []
    assert db["moves"] == []


def test_a_kind_with_no_home_refuses_the_whole_merge(db):
    """Every problem at once, and the items that DO have a home stay put too."""
    db["deck_contents"] = _rows(*STAGED, (2, "card", 110))

    with pytest.raises(StagingError) as e:
        deck_staging.plan()

    text = "\n".join(e.value.lines)
    assert len(e.value.lines) == 3
    for name in ("Staged Combo", "Staged Aspect", "Staged Rite"):
        assert name in text
    assert "Staged Card" not in text


def test_two_decks_tied_for_a_kind_is_not_guessed(db):
    db["deck_contents"] = _rows((1, "aspect", 200), (2, "aspect", 210), (4, "aspect", 210))

    with pytest.raises(StagingError) as e:
        deck_staging.plan()

    assert "`Pool` and `Fates`" in e.value.lines[0]


@pytest.mark.parametrize("extra, said", [
    ([], "No unarchived deck"),
    ([_deck(1, "Holding", "staging"), _deck(20, "Holding 2", "staging")], "2 decks"),
])
def test_exactly_one_staging_deck_is_required(db, extra, said):
    db["decks"] = [d for d in DECKS if d["usage_type"] != "staging"] + extra
    with pytest.raises(StagingError) as e:
        deck_staging.plan()
    assert said in e.value.lines[0]


def test_an_archived_staging_deck_is_not_the_staging_deck(db):
    """The state that hid the old command: its deck 21 was archived."""
    db["decks"] = [_deck(1, "Holding", "staging", archived=True)] + DECKS[1:]
    with pytest.raises(StagingError):
        deck_staging.plan()


def test_a_staged_row_naming_a_deleted_item_is_refused(db):
    """`deck_contents.content_id` has no foreign key; the row would go live naming nothing."""
    db["deck_contents"] = _rows(*STAGED, *LIVE, (1, "card", 999))
    with pytest.raises(StagingError) as e:
        deck_staging.plan()
    assert "card #999" in e.value.lines[0]


def test_a_rite_stored_under_its_old_spelling_is_still_a_rite(db):
    db["deck_contents"] = _rows((1, "event", 300), (5, "event", 310))
    assert _destinations(deck_staging.plan()) == {"Staged Rite": "Rite Pool"}


def test_a_failed_move_says_what_already_landed(db):
    """The destinations are separate requests. The reply has to say which state
    the decks are in, and the decks that did change still get stamped."""
    merge = deck_staging.plan()
    order = [move["deck"]["id"] for move in merge["moves"]]
    db["fail_move_to"] = order[1]

    with pytest.raises(StagingError) as e:
        deck_staging.apply(merge)

    first = merge["moves"][0]["deck"]["name"]
    assert f"Already moved into `{first}`" in e.value.lines[1]
    assert len(db["moves"]) == 1
    assert sorted(db["stamped"]) == sorted([("decks", 1), ("decks", order[0])])


def test_a_failure_on_the_first_move_says_nothing_moved(db):
    merge = deck_staging.plan()
    db["fail_move_to"] = merge["moves"][0]["deck"]["id"]
    with pytest.raises(StagingError) as e:
        deck_staging.apply(merge)
    assert e.value.lines[1] == "Nothing was moved."


def test_a_row_that_left_staging_meanwhile_is_reported_not_chased(db):
    merge = deck_staging.plan()
    aspect_row = next(r for r in db["deck_contents"] if r["id"] == 3)
    aspect_row["deck_id"] = 7

    warnings = deck_staging.apply(merge)

    assert aspect_row["deck_id"] == 7
    assert len(warnings) == 1 and "0 of 1 rows moved" in warnings[0]


def test_copies_fold_in_the_report(db):
    db["deck_contents"] = _rows((1, "card", 100), (1, "card", 100), (2, "card", 110))
    assert deck_staging.report_lines(deck_staging.plan()) == {
        "Pool": ["Staged Card (card #100) x2"]}


# ---------------------------------------------------------------------------
# /stage
# ---------------------------------------------------------------------------

def test_staging_pulls_every_copy_out_of_the_decks_that_deal_its_kind(db):
    """The same move as the merge, the other way: by row id, out of the deck.
    The hidden command deleted the copies and then inserted them into a pinned
    deck id, so a failure between the two took the item out of the game."""
    staged = deck_staging.stage_plan("card:110")

    assert deck_staging.stage_apply(staged) == []

    assert sorted(db["moves"]) == [([5, 6], 2, 1), ([10], 3, 1)]
    assert db["inserts"] == []
    assert sorted(db["stamped"]) == [("decks", 1), ("decks", 2), ("decks", 3)]
    assert len(db["deck_contents"]) == len(STAGED) + len(LIVE), "nothing copied or dropped"


def test_copies_in_decks_that_do_not_deal_the_kind_stay_and_are_named(db):
    """A starter or curse deck is not emptied by benching a draft item, and a
    custom or archived deck is nobody's business. The reply has to say the item
    is still dealt from the first two."""
    card = deck_staging.stage_plan("card:110")
    assert [m["deck"]["name"] for m in card["moves"]] == ["Combos", "Pool"]
    assert card["elsewhere"] == ["Starter"]

    aspect = deck_staging.stage_plan("aspect:210")
    assert [m["deck"]["name"] for m in aspect["moves"]] == ["Fates", "Pool"]
    assert aspect["elsewhere"] == ["Curses", "Rite Pool"]


def test_planning_a_stage_writes_nothing(db):
    deck_staging.stage_plan("card:110")
    assert db["moves"] == [] and db["inserts"] == [] and db["stamped"] == []


def test_an_item_no_deck_deals_gets_one_copy_added(db):
    db["cards"] = CARDS + [{"id": 120, "name": "Unplaced", "valence": 1, "element": "sol"}]

    staged = deck_staging.stage_plan("card:120")
    deck_staging.stage_apply(staged)

    assert db["moves"] == []
    assert db["inserts"] == [
        ("deck_contents", {"deck_id": 1, "content_id": 120, "content_type": "card"})]
    assert db["stamped"] == [("decks", 1)]


def test_an_item_already_staged_is_not_added_twice(db):
    """The hidden command added a copy whenever the item was in no draft deck,
    so staging the same item twice left two copies to merge back."""
    staged = deck_staging.stage_plan("card:100")

    assert staged["staged"] == 1 and not staged["add"] and not staged["moves"]
    deck_staging.stage_apply(staged)
    assert db["moves"] == [] and db["inserts"] == [] and db["stamped"] == []


def test_a_typed_name_is_exact_and_never_a_first_match(db):
    """`/add_to_deck` takes the first match across types for a typed name. Here
    that would pull the wrong item out of the live decks."""
    assert deck_staging.stage_plan("Pool Aspect")["content_type"] == "aspect"

    db["aspects"] = ASPECTS + [{"id": 220, "name": "Pool Card"}]
    with pytest.raises(StagingError) as e:
        deck_staging.stage_plan("Pool Card")
    assert "card #110" in e.value.lines[0] and "aspect #220" in e.value.lines[0]

    with pytest.raises(StagingError):
        deck_staging.stage_plan("No Such Thing")
    with pytest.raises(StagingError):
        deck_staging.stage_plan("card:999")
    assert db["moves"] == [] and db["inserts"] == []


def test_a_failed_stage_says_which_decks_it_already_left(db):
    staged = deck_staging.stage_plan("card:110")
    db["fail_move_from"] = staged["moves"][1]["deck"]["id"]

    with pytest.raises(StagingError) as e:
        deck_staging.stage_apply(staged)

    first = staged["moves"][0]["deck"]
    assert f"Already moved out of `{first['name']}`" in e.value.lines[1]
    assert sorted(db["stamped"]) == sorted([("decks", 1), ("decks", first["id"])])

