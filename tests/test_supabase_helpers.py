"""Tests for the Supabase access layer.

Most of these are REGRESSION tests for specific bugs, named at each site. The
theme is the one that cost the most debugging time: **a failure must never look
like an empty result.** Every call site in this codebase renders `[]` as
"not found", so a swallowed error is silently wrong rather than loudly broken.
"""
import pytest

import supabase_helpers as h


# ---------------------------------------------------------------------------
# The pre-flight guard
# ---------------------------------------------------------------------------
# RLS denial is NOT an exception: PostgREST answers a blocked SELECT with HTTP
# 200 and an empty array. No amount of error handling catches that, so the table
# is checked against the loaded key's capabilities BEFORE the query is sent.

@pytest.mark.parametrize("table", sorted(h.ANON_INSERT_ONLY))
def test_insert_only_tables_are_refused_under_anon(monkeypatch, table):
    monkeypatch.setattr(h, "SUPABASE_ROLE", "anon")
    with pytest.raises(h.SupabaseUnreadableError) as e:
        h.fetch_all(table)
    assert "INSERT-only" in str(e.value)
    assert table in str(e.value)


@pytest.mark.parametrize("table", sorted(h.ANON_NO_POLICY))
def test_no_policy_tables_are_refused_under_anon(monkeypatch, table):
    monkeypatch.setattr(h, "SUPABASE_ROLE", "anon")
    with pytest.raises(h.SupabaseUnreadableError) as e:
        h.fetch_all(table)
    assert "no SELECT policy" in str(e.value)


def test_service_role_may_read_everything(monkeypatch, fake_supabase):
    monkeypatch.setattr(h, "SUPABASE_ROLE", "service_role")
    monkeypatch.setattr(h, "supabase", fake_supabase({"turns": [{"uuid": "t1"}]}))
    assert h.fetch_all("turns") == [{"uuid": "t1"}]


def test_unknown_role_is_treated_as_not_proven(monkeypatch):
    # An unrecognised key format must NOT be assumed privileged -- guessing
    # "probably service_role" reintroduces the silent-empty failure.
    monkeypatch.setattr(h, "SUPABASE_ROLE", "unknown")
    with pytest.raises(h.SupabaseUnreadableError):
        h.fetch_all("turns")


def test_unguarded_tables_are_unaffected(monkeypatch, fake_supabase):
    monkeypatch.setattr(h, "SUPABASE_ROLE", "anon")
    monkeypatch.setattr(h, "supabase", fake_supabase({"cards": [{"id": 1}]}))
    assert h.fetch_all("cards") == [{"id": 1}]


def test_dropped_table_is_not_in_the_guard():
    # fate_types was dropped 2026-08-26. A dropped table already fails loudly
    # with PGRST205; listing it here would misreport it as a permissions problem.
    assert "fate_types" not in h.ANON_UNREADABLE


# ---------------------------------------------------------------------------
# Failures raise; only genuine emptiness returns []
# ---------------------------------------------------------------------------

def test_query_failure_raises_rather_than_returning_empty(monkeypatch, fake_supabase):
    # REGRESSION: fetch_all used to catch everything and return []. A missing
    # table, an RLS denial and an empty result were indistinguishable.
    monkeypatch.setattr(h, "SUPABASE_ROLE", "service_role")
    monkeypatch.setattr(h, "supabase", fake_supabase(raises={"cards": RuntimeError("boom")}))
    with pytest.raises(h.SupabaseQueryError) as e:
        h.fetch_all("cards")
    assert "cards" in str(e.value) and "boom" in str(e.value)


def test_genuinely_empty_still_returns_empty(monkeypatch, fake_supabase):
    monkeypatch.setattr(h, "SUPABASE_ROLE", "service_role")
    monkeypatch.setattr(h, "supabase", fake_supabase({"cards": []}))
    assert h.fetch_all("cards") == []


def test_both_error_types_share_a_base_class():
    # Callers that only want "did the database work" catch one thing.
    assert issubclass(h.SupabaseQueryError, h.SupabaseError)
    assert issubclass(h.SupabaseUnreadableError, h.SupabaseError)


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------

def test_filters_dispatch_by_value_type(monkeypatch, fake_supabase):
    monkeypatch.setattr(h, "SUPABASE_ROLE", "service_role")
    fs = fake_supabase({"cards": []})
    monkeypatch.setattr(h, "supabase", fs)
    h.fetch_all("cards", filters={"archived_at": None, "id": [1, 2], "name": "X"})
    kinds = {f[0] for f in fs.log["cards"]["filters"]}
    assert kinds == {"is", "in", "eq"}, "None -> is null, list -> in, scalar -> eq"


def test_sort_prefix_controls_direction(monkeypatch, fake_supabase):
    monkeypatch.setattr(h, "SUPABASE_ROLE", "service_role")
    fs = fake_supabase({"games": []})
    monkeypatch.setattr(h, "supabase", fs)
    h.fetch_all("games", sort=["-created_at", "name"])
    assert fs.log["games"]["order"] == [("created_at.desc,name.asc", False)]


def test_multi_column_sort_is_one_request_parameter(monkeypatch, fake_supabase):
    """REGRESSION (2026-08-27): every column after the first was ignored.

    postgrest-py's .order() does `params.add("order", ...)`, so a call per column
    sends `order=a&order=b` and PostgREST honours only the first. `/decks` asked
    for `["usage_type", "name"]` and got decks grouped by usage type and then
    ordered arbitrarily inside each group -- which reads as a working sort until
    you look at it. PostgREST wants one `order=a.asc,b.asc`.
    """
    monkeypatch.setattr(h, "SUPABASE_ROLE", "service_role")
    fs = fake_supabase({"decks": []})
    monkeypatch.setattr(h, "supabase", fs)
    h.fetch_all("decks", sort=["usage_type", "name"])
    assert len(fs.log["decks"]["order"]) == 1, "one call, not one per column"
    assert fs.log["decks"]["order"][0][0] == "usage_type.asc,name.asc"


def test_a_single_column_sort_still_carries_its_direction(monkeypatch, fake_supabase):
    monkeypatch.setattr(h, "SUPABASE_ROLE", "service_role")
    fs = fake_supabase({"games": []})
    monkeypatch.setattr(h, "supabase", fs)
    h.fetch_all("games", sort=["-created_at"])
    assert fs.log["games"]["order"] == [("created_at.desc", False)]


def test_limit_is_pushed_to_the_server(monkeypatch, fake_supabase):
    # REGRESSION: the leaderboard fetched with no limit and sliced in Python,
    # hitting PostgREST's 1000-row default against a larger view.
    monkeypatch.setattr(h, "SUPABASE_ROLE", "service_role")
    fs = fake_supabase({"leaderboard_view": [{"n": i} for i in range(50)]})
    monkeypatch.setattr(h, "supabase", fs)
    rows = h.fetch_all("leaderboard_view", limit=10)
    assert fs.log["leaderboard_view"]["limit"] == 10
    assert len(rows) == 10


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

def test_soft_delete_returns_the_updated_rows(monkeypatch, fake_supabase):
    # REGRESSION: soft_delete_record called `.data` on update_record's return
    # value, which is already a list -> AttributeError, swallowed, returned None.
    # Callers check `if not success`, so /delete_deck and /delete_hero reported
    # "Failed to delete" on every SUCCESSFUL archive.
    monkeypatch.setattr(h, "supabase", fake_supabase({"decks": [{"id": 7}]}))
    assert h.soft_delete_record("decks", 7), "must be truthy on success"


def test_soft_delete_sets_archived_at(monkeypatch, fake_supabase):
    fs = fake_supabase({"decks": [{"id": 7}]})
    monkeypatch.setattr(h, "supabase", fs)
    h.soft_delete_record("decks", 7)
    payload = fs.log["decks"]["update"]
    assert "archived_at" in payload and payload["archived_at"]


def test_update_record_stamps_updated_at(monkeypatch, fake_supabase):
    fs = fake_supabase({"cards": [{"id": 1}]})
    monkeypatch.setattr(h, "supabase", fs)
    h.update_record("cards", 1, {"name": "New"})
    assert "updated_at" in fs.log["cards"]["update"]


@pytest.mark.parametrize("fn,args", [
    (lambda: h.create_record("cards", {"name": "x"}), None),
    (lambda: h.update_record("cards", 1, {"name": "x"}), None),
    (lambda: h.delete_record("cards", 1), None),
])
def test_mutations_raise_on_failure(monkeypatch, fake_supabase, fn, args):
    monkeypatch.setattr(h, "supabase", fake_supabase(raises={"cards": RuntimeError("nope")}))
    with pytest.raises(h.SupabaseQueryError):
        fn()


# ---------------------------------------------------------------------------
# Deck item references
# ---------------------------------------------------------------------------
# Names collide across content types, so autocomplete round-trips an encoded
# ref rather than a bare name.

@pytest.mark.parametrize("ct,cid", [("card", 447), ("aspect", 12), ("event", 3)])
def test_item_ref_round_trips(ct, cid):
    assert h.parse_item_ref(h.encode_item_ref(ct, cid)) == (ct, cid)


@pytest.mark.parametrize("value", ["ritual:1", "consumable:2"])
def test_retired_content_types_no_longer_parse(value):
    # Both retired 2026-08-26. They must fall through to the raw-name path
    # rather than resolving to a table that no longer participates in decks.
    assert h.parse_item_ref(value) == (None, None)


@pytest.mark.parametrize("value", ["", None, "Just A Name", "card:", "card:abc", "  "])
def test_non_refs_are_rejected(value):
    assert h.parse_item_ref(value) == (None, None)


def test_label_is_disambiguated_by_type_and_id():
    assert h.make_item_label("Diversity", "card", 447) == "Diversity (Card #447)"


@pytest.mark.parametrize("ct", ["card", "aspect", "event"])
def test_name_column_is_uniform(ct):
    # `challenge_name` was the ritual-only exception; that table is retired.
    assert h.name_column_for(ct) == "name"


def test_display_name_reads_name():
    assert h.get_display_name({"name": "Salvage"}, "card") == "Salvage"


def test_deck_content_types_are_current():
    assert h.DECK_CONTENT_TYPES == ["card", "aspect", "event"]
