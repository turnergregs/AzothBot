"""Tests for detecting which side of the events -> rites rename the database is on.

The bot outlives a hand-applied migration, so every name it sends has to follow
the database. The failure that matters most is a false "old side": a timeout
read as a missing table would point every write at `events` after the rename.
"""
import pytest

import supabase_helpers
from azoth_logic import rite_schema as rs


@pytest.fixture
def detect(monkeypatch):
    """Unpin the conftest default and route the probe through a fake read."""
    calls = []
    outcome = {"raise": None}

    def fake_fetch(table, columns=None, filters=None, sort=None, limit=None):
        calls.append((table, limit))
        if outcome["raise"] is not None:
            raise outcome["raise"]
        return []

    monkeypatch.setattr(supabase_helpers, "fetch_all", fake_fetch)
    rs.pin(None)
    rs.invalidate()
    yield calls, outcome
    rs.invalidate()


def _missing(code):
    return supabase_helpers.SupabaseQueryError(
        f"select on `rites` failed: {{'code': '{code}', 'message': \"Could not find the table 'public.rites' in the schema cache\"}}")


def test_a_readable_rites_table_is_the_new_side(detect):
    calls, _ = detect
    assert rs.current() is rs.AFTER
    assert calls == [("rites", 1)], "one row, one table"


@pytest.mark.parametrize("code", ["PGRST205", "42P01"])
def test_a_missing_rites_table_is_the_old_side(detect, code):
    _, outcome = detect
    outcome["raise"] = _missing(code)
    assert rs.current() is rs.BEFORE


def test_any_other_failure_raises_rather_than_guessing_old(detect):
    _, outcome = detect
    outcome["raise"] = supabase_helpers.SupabaseQueryError("select on `rites` failed: timed out")
    with pytest.raises(supabase_helpers.SupabaseQueryError):
        rs.current()


def test_the_answer_is_cached(detect):
    calls, _ = detect
    rs.current()
    rs.current()
    assert len(calls) == 1


def test_a_migration_applied_while_running_is_picked_up(detect, monkeypatch):
    calls, outcome = detect
    outcome["raise"] = _missing("PGRST205")
    assert rs.current() is rs.BEFORE
    outcome["raise"] = None
    monkeypatch.setattr(rs, "TTL", -1)
    assert rs.current() is rs.AFTER


def test_a_change_of_side_is_printed(detect, capsys):
    rs.current()
    assert "after the rename" in capsys.readouterr().out
    rs.current(force=True)
    assert capsys.readouterr().out == "", "only a CHANGE of side is announced"


@pytest.mark.parametrize("side", [rs.BEFORE, rs.AFTER])
def test_a_rite_type_follows_the_database(side):
    rs.pin(side)
    assert rs.db_content_type("event") == side.content_type
    assert rs.db_content_type("rite") == side.content_type


def test_other_types_pass_through():
    for ct in ("card", "aspect", "hero", "wat"):
        assert rs.db_content_type(ct) == ct


def test_both_spellings_are_rites():
    assert rs.is_rite("event") and rs.is_rite("rite")
    assert not rs.is_rite("card")


def test_the_two_sides_name_everything_consistently():
    assert (rs.BEFORE.table, rs.BEFORE.content_type) == ("events", "event")
    assert (rs.AFTER.table, rs.AFTER.content_type) == ("rites", "rite")
