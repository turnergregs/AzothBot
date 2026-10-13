"""What a /stats report replies when its view cannot be read.

REGRESSION (2026-10-12): every report answered "`<view>` is not migrated — run
`<file>`" for ANY failed read. One row under `player_summary_view` carried an
action count of "1.0", the view's integer cast raised on it, and /stats players
told the developer to run a migration that had been applied for two weeks. Only
a view that is not in the database is "not migrated"; anything else is the
database's own error and has to be shown as that.
"""
import asyncio

import pytest
from postgrest.exceptions import APIError

import supabase_helpers as h
from azoth_commands import AzothCommands, stats

MISSING = APIError({"code": "PGRST205", "details": None, "hint": None,
                    "message": "Could not find the table 'public.player_summary_view' in the schema cache"})
BAD_DATA = APIError({"code": "22P02", "details": None, "hint": None,
                     "message": 'invalid input syntax for type integer: "1.0"'})


def _query_error(cause):
    return h.SupabaseQueryError(f"select on `player_summary_view` failed: {cause}", cause)


def _players_reply(monkeypatch, error):
    """/stats players with its read failing: the command body itself, under
    the safe_interaction wrapper, which returns before it needs an interaction."""
    def failing(*args, **kwargs):
        raise error
    monkeypatch.setattr(stats, "fetch_all", failing)
    body = AzothCommands.stats_players.callback.__wrapped__
    return asyncio.run(body(None, None, players="new"))


def test_a_missing_view_names_its_migration(monkeypatch):
    reply = _players_reply(monkeypatch, _query_error(MISSING))
    assert reply == ("❌ `player_summary_view` is not migrated — run "
                     "`db/migrations/2026-09-28_player_summary_view.sql`.")


def test_a_data_error_under_the_view_is_shown_as_the_database_said_it(monkeypatch, capsys):
    reply = _players_reply(monkeypatch, _query_error(BAD_DATA))
    assert "not migrated" not in reply
    assert "`player_summary_view`" in reply
    assert 'invalid input syntax for type integer: "1.0"' in reply and "22P02" in reply
    assert "player_summary_view" in capsys.readouterr().out, "the full error goes to the console"


def test_a_key_that_cannot_read_the_view_is_not_told_to_migrate(monkeypatch):
    reply = _players_reply(monkeypatch, h.SupabaseUnreadableError("Cannot read `player_summary_view` with a `anon` key"))
    assert "not migrated" not in reply and "anon" in reply


@pytest.mark.parametrize("cause, missing", [
    (MISSING, True),
    (APIError({"code": "42P01", "message": 'relation "public.link_turn_view" does not exist'}), True),
    # A view from before a migration that added the column asked for.
    (APIError({"code": "42703", "message": "column breakdown_view.cohort does not exist"}), True),
    (BAD_DATA, False),
    (APIError({"code": "57014", "message": "canceling statement due to statement timeout"}), False),
    (RuntimeError("PostgREST 503"), False),
])
def test_only_something_absent_from_the_database_counts_as_missing(cause, missing):
    assert _query_error(cause).missing is missing


def test_several_views_and_migrations_read_as_one_sentence():
    reply = stats._read_failed(_query_error(MISSING), ("link_turn_view", "link_type_view"), "a.sql", "b.sql")
    assert reply == ("❌ `link_turn_view` / `link_type_view` are not migrated — run "
                     "`db/migrations/a.sql` then `b.sql`.")


def test_fetch_all_keeps_the_databases_code_and_message(monkeypatch, fake_supabase):
    monkeypatch.setattr(h, "SUPABASE_ROLE", "service_role")
    monkeypatch.setattr(h, "supabase", fake_supabase(raises={"player_summary_view": BAD_DATA}))
    with pytest.raises(h.SupabaseQueryError) as e:
        h.fetch_all("player_summary_view")
    assert e.value.code == "22P02"
    assert e.value.detail == 'invalid input syntax for type integer: "1.0"'
    assert not e.value.missing


def test_a_failure_with_no_database_error_still_has_a_detail(monkeypatch, fake_supabase):
    monkeypatch.setattr(h, "SUPABASE_ROLE", "service_role")
    monkeypatch.setattr(h, "supabase", fake_supabase(raises={"cards": RuntimeError("boom")}))
    with pytest.raises(h.SupabaseQueryError) as e:
        h.fetch_all("cards")
    assert (e.value.code, e.value.detail) == (None, "boom")
