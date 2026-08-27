"""`azoth_logic/taxonomy.py` — the vocabularies that used to be database tables.

Six tables (`card_elements`, `card_types`, `card_attributes`, `deck_types`,
`deck_content_types`, `deck_usage_types`) were dropped on 2026-08-27 and their
contents moved here, next to the game constants they mirror.

The incident that shaped the design: the game hardcodes these too, and its
`USAGE_TYPE_OPTIONS` was missing `rite` — which is a large part of why the Rites
deck stayed invisible to `draft_deck_view` for its whole existence. Hardcoding
alone drifts, so `suggest()` unions the canonical list with what is actually in
use. Most of what follows guards that union.
"""
import pytest

from azoth_logic import taxonomy


@pytest.fixture(autouse=True)
def _clean_cache():
    """The module caches across calls; a test must not inherit another's."""
    taxonomy.invalidate()
    yield
    taxonomy.invalidate()


@pytest.fixture
def rows(monkeypatch):
    """Install canned `fetch_all` results and count the reads it makes."""
    calls = []

    def install(table_rows, raises=None):
        def fake_fetch_all(table, columns=None, filters=None, **kw):
            calls.append((table, filters))
            if raises:
                raise raises
            return table_rows.get(table, [])
        monkeypatch.setattr(taxonomy, "fetch_all", fake_fetch_all)
        return calls
    return install


# ---------------------------------------------------------------------------
# The canonical lists
# ---------------------------------------------------------------------------

def test_the_vocabularies_match_the_game(rows):
    """Transcribed from the game repo. If the engine gains an element, this is
    the file that has to change with it."""
    rows({})
    assert taxonomy.CARD_ELEMENTS == ["anima", "blood", "sol"]
    assert taxonomy.CARD_TYPES == ["spell", "catalyst", "power"]
    assert taxonomy.CARD_ATTRIBUTES == ["Augment", "Ascending", "Decrement",
                                        "Descending", "Inert", "Spawner"]
    assert taxonomy.DECK_TYPES == ["base", "custom"]


def test_rite_and_tutorial_are_present(rows):
    """The regression this module exists for. The game's USAGE_TYPE_OPTIONS has
    seven entries and omits both; the database has decks using each."""
    rows({})
    assert "rite" in taxonomy.DECK_USAGE_TYPES
    assert "tutorial" in taxonomy.DECK_USAGE_TYPES


@pytest.mark.parametrize("retired", ["reactant", "boon_a", "boon_b", "boon_c"])
def test_retired_usage_types_are_not_offered(rows, retired):
    """Retired 2026-08-27. The engine still understands `reactant`
    (`CardLogic.DRAFT_INJECTED_USAGE_TYPES`), but no content uses any of these,
    and a dead usage type in a picker is how a new deck ends up on one."""
    rows({})
    assert retired not in taxonomy.values("deck_usage_types")


def test_an_archived_deck_does_not_resurrect_a_retired_value(rows):
    """`in use` has to mean in use NOW.

    Decks 32-35 carry exactly these retired usage types and are all archived.
    Unfiltered, the union would hand straight back what the canonical list just
    dropped -- which would make removing a value impossible."""
    rows({"decks": [{"usage_type": "draft"}]})   # the filter excludes the rest
    assert taxonomy.values("deck_usage_types") == taxonomy.DECK_USAGE_TYPES


def test_deck_reads_are_filtered_to_unarchived(rows):
    calls = rows({"decks": []})
    taxonomy.values("deck_usage_types")
    assert calls == [("decks", {"archived_at": None})]


def test_card_reads_are_not_filtered(rows):
    """`cards` has no `archived_at` column -- those tables hard-delete."""
    calls = rows({"cards": []})
    taxonomy.values("card_elements")
    assert calls == [("cards", None)]


def test_card_is_not_a_card_type(rows):
    """Eight rows carry `type = 'Card'` — the Codex's display label leaking into
    the data. Suggesting it would spread the bug."""
    rows({})
    assert "Card" not in taxonomy.CARD_TYPES


def test_canonical_order_is_preserved_not_sorted(rows):
    """Declared order is the game's order, and it is meaningful — `spell` first
    because 328 of 400 cards are spells."""
    rows({})
    assert taxonomy.values("card_types")[:3] == ["spell", "catalyst", "power"]


# ---------------------------------------------------------------------------
# Union with what is actually in use
# ---------------------------------------------------------------------------

def test_a_value_in_use_but_not_canonical_still_appears(rows):
    """The whole point. Someone adds a usage type to the engine, forgets this
    file, and the deck they made must not vanish from the picker."""
    rows({"decks": [{"usage_type": "draft"}, {"usage_type": "epilogue"}]})
    assert "epilogue" in taxonomy.values("deck_usage_types")


def test_extras_are_appended_after_the_canonical_list(rows):
    """Not interleaved: an unrecognised value should read as an extra, because
    that is what it is."""
    rows({"decks": [{"usage_type": "epilogue"}]})
    result = taxonomy.values("deck_usage_types")
    assert result[:len(taxonomy.DECK_USAGE_TYPES)] == taxonomy.DECK_USAGE_TYPES
    assert result[-1] == "epilogue"


def test_a_value_in_both_is_not_duplicated(rows):
    rows({"cards": [{"element": "blood"}, {"element": "sol"}]})
    result = taxonomy.values("card_elements")
    assert result.count("blood") == 1
    assert result == ["anima", "blood", "sol"]


def test_the_duplicate_check_ignores_case(rows):
    """`Sol` in the data and `sol` in the list are the same element, not two."""
    rows({"cards": [{"element": "Sol"}]})
    assert taxonomy.values("card_elements") == ["anima", "blood", "sol"]


def test_nulls_and_blanks_are_not_values(rows):
    """64 cards have a NULL element. That is Colourless — the absence of an
    element, not one of them."""
    rows({"cards": [{"element": None}, {"element": ""}, {"element": "blood"}]})
    assert taxonomy.values("card_elements") == ["anima", "blood", "sol"]


def test_card_attributes_never_queries(rows):
    """No card carries an attribute yet, so there is nothing to union — and no
    reason to read 400 rows on every keystroke to discover that."""
    calls = rows({"cards": [{"element": "blood"}]})
    assert taxonomy.values("card_attributes") == taxonomy.CARD_ATTRIBUTES
    assert calls == []


# ---------------------------------------------------------------------------
# Failure and caching
# ---------------------------------------------------------------------------

def test_a_failed_read_still_returns_the_canonical_list(rows):
    """Losing the hardcoded values because Supabase blinked would be far worse
    than missing a novel one."""
    rows({}, raises=RuntimeError("supabase down"))
    assert taxonomy.values("card_elements") == ["anima", "blood", "sol"]


def test_results_are_cached_between_calls(rows):
    """Discord fires autocomplete per keystroke; a read is 0.85-2.3s against a
    3s reply budget."""
    calls = rows({"decks": [{"usage_type": "draft"}]})
    for _ in range(5):
        taxonomy.values("deck_usage_types")
    assert len(calls) == 1


def test_the_cache_expires(rows, monkeypatch):
    calls = rows({"decks": [{"usage_type": "draft"}]})
    taxonomy.values("deck_usage_types")

    clock = [0.0]
    monkeypatch.setattr(taxonomy.time, "monotonic", lambda: clock[0])
    taxonomy.invalidate()
    taxonomy.values("deck_usage_types")
    clock[0] = taxonomy.TTL_SECONDS + 1
    taxonomy.values("deck_usage_types")

    assert len(calls) == 3


def test_invalidate_forces_a_reread(rows):
    """`/bulk_insert` can introduce several novel values at once."""
    calls = rows({"decks": [{"usage_type": "draft"}]})
    taxonomy.values("deck_usage_types")
    taxonomy.invalidate()
    taxonomy.values("deck_usage_types")
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# suggest()
# ---------------------------------------------------------------------------

def test_suggest_matches_a_substring_not_just_a_prefix(rows):
    """`tut` should find `tutorial`, and a user should not have to guess where a
    value starts."""
    rows({})
    assert taxonomy.suggest("deck_usage_types", "tut") == ["tutorial"]
    assert taxonomy.suggest("card_types", "yst") == ["catalyst"]


def test_suggest_is_case_insensitive(rows):
    rows({})
    assert taxonomy.suggest("card_attributes", "aug") == ["Augment"]


def test_empty_input_offers_everything(rows):
    rows({})
    assert taxonomy.suggest("card_elements", "") == ["anima", "blood", "sol"]


def test_suggest_respects_discords_limit(rows):
    """Discord rejects more than 25 choices outright."""
    rows({"decks": [{"usage_type": f"type_{i}"} for i in range(60)]})
    assert len(taxonomy.suggest("deck_usage_types", "")) == 25


def test_an_unknown_vocabulary_is_a_loud_error(rows):
    """A typo'd kind must not quietly autocomplete to nothing — that reads as
    'the database is empty', which is the failure this whole module removed."""
    rows({})
    with pytest.raises(KeyError) as excinfo:
        taxonomy.values("card_elemnts")
    assert "card_elemnts" in str(excinfo.value)
