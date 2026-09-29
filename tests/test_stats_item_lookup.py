"""/stats item's lookup: what the autocomplete offers, and what it resolves.

REGRESSION (2026-09-29): the first build offered Veln as `boss:8` and then
parsed it with parse_item_ref, which accepts deck content only (card, aspect,
rite). The ref fell through to a name lookup for "boss:8", so every boss and
hero the autocomplete offered answered "Could not find a live boss ... called
boss:8". Nothing ran the resolver on the autocomplete's own values.
"""
import pytest

import supabase_helpers as h
from azoth_commands import stats
from azoth_logic import content_index

TABLES = {
    "bosses": [{"id": 8, "name": "Veln", "act": 2, "archived_at": None},
               {"id": 3, "name": "Caerul", "act": 4, "archived_at": "2026-09-01"}],
    "heroes": [{"id": 7, "name": "Lumis", "archived_at": None}],
}
CONTENT = {("card", 447): {"id": 447, "name": "Misfire"},
           ("aspect", 22): {"id": 22, "name": "Nullity"},
           ("rite", 13): {"id": 13, "name": "Banishment"}}


def _fetch_all(table, columns=None, filters=None, sort=None, limit=None):
    rows = TABLES.get(table, [])
    for key, value in (filters or {}).items():
        rows = [r for r in rows if r.get(key) == value]
    return [dict(r) for r in rows]


def _resolve(value, live_only=True):
    kind, item_id = h.parse_item_ref(value)
    if kind:
        row = CONTENT.get((content_index.KIND_FOR_REF[kind], item_id))
        return (content_index.KIND_FOR_REF[kind], row) if row else (None, None)
    for (k, _), row in CONTENT.items():
        if row["name"] == value:
            return k, row
    return None, None


@pytest.fixture(autouse=True)
def _fake_db(monkeypatch):
    monkeypatch.setattr(stats, "fetch_all", _fetch_all)
    monkeypatch.setattr(content_index, "entries",
                        lambda force=False, live_only=True: [(k, i, r["name"]) for (k, i), r in CONTENT.items()])
    monkeypatch.setattr(content_index, "resolve", _resolve)


def test_every_offered_item_resolves_to_what_its_label_names():
    offered = stats._item_choices("")
    assert len(offered) == 5                       # Caerul is archived
    for label, value in offered.items():
        kind, row = stats._resolve_item(value)
        assert row is not None, f"{label} -> {value} did not resolve"
        assert label.startswith(row["name"]) and f"({kind.capitalize()} #" in label


def test_veln_by_its_ref_and_by_its_name():
    assert stats._resolve_item("boss:8") == ("boss", TABLES["bosses"][0])
    assert stats._resolve_item("Veln")[0] == "boss"
    assert stats._resolve_item("hero:7")[1]["name"] == "Lumis"
    assert stats._resolve_item("card:447")[1]["name"] == "Misfire"


def test_an_archived_boss_does_not_resolve_by_ref():
    assert stats._resolve_item("boss:3") == (None, None)


def test_the_deck_ref_parser_still_refuses_bosses_and_heroes():
    """The fix is local on purpose: widening parse_item_ref would let a boss
    ref resolve in the deck commands, which only hold cards, aspects and rites."""
    assert h.parse_item_ref("boss:8") == (None, None)
    assert h.parse_item_ref("hero:7") == (None, None)
