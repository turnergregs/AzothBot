"""`azoth_logic/bulk_apply.py` — the client half of the transactional bulk write.

The database half is exercised separately, against a throwaway Postgres:
`db/tests/bulk_apply_test.sql` in the game repo. Nothing here can prove the
rollback works; what it covers is that this module hands the payload over
unmangled, and turns a rejection into a sentence someone can act on.

The incident being guarded: before 2026-08-27 both commands looped in Python and
issued one request per record, so a failure at record 40 of 60 left 39 rows
written -- and once the `/delete_*` commands were retired there was no way to
remove them from Discord.
"""
import pytest

from azoth_logic import bulk_apply
from azoth_logic.bulk_apply import BulkApplyError


class FakeRPC:
    """Stands in for `supabase.rpc(fn, params).execute()`."""

    def __init__(self, data=None, raises=None):
        self.data = data if data is not None else []
        self.raises = raises
        self.calls = []

    def rpc(self, fn, params):
        self.calls.append((fn, params))
        return self

    def execute(self):
        if self.raises:
            raise self.raises
        return type("Response", (), {"data": self.data})()


@pytest.fixture
def rpc(monkeypatch):
    def install(data=None, raises=None):
        fake = FakeRPC(data, raises)
        monkeypatch.setattr(bulk_apply, "supabase", fake)
        return fake
    return install


# ---------------------------------------------------------------------------
# The payload reaches the function untouched
# ---------------------------------------------------------------------------

def test_payload_is_passed_through_verbatim(rpc):
    """No reshaping here.

    The function does the name matching, the `new_name` rename and the
    updated_at stamp. Any of that happening twice -- once here and once in SQL --
    is how the two halves drift.
    """
    payload = {"cards": [{"name": "Emberwake", "new_name": "Ashwake",
                          "actions": [{"name": "Draw", "amount": 2}]}]}
    fake = rpc([{"table": "cards", "index": 0, "name": "Ashwake",
                 "before": {"name": "Emberwake"}, "after": {"name": "Ashwake"}}])

    bulk_apply.apply(payload, "update")

    fn, params = fake.calls[0]
    assert fn == "bulk_apply"
    assert params == {"payload": payload, "mode": "update"}


def test_results_are_returned_as_given(rpc):
    rows = [{"table": "cards", "index": 0, "name": "A", "before": None, "after": {"id": 1}},
            {"table": "aspects", "index": 0, "name": "B", "before": None, "after": {"id": 2}}]
    fake = rpc(rows)
    assert bulk_apply.apply({"cards": [{"name": "A"}]}, "insert") == rows
    assert fake.calls


# ---------------------------------------------------------------------------
# Local rejections never reach the database
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload, fragment", [
    ([], "keyed by table name"),
    ("cards", "keyed by table name"),
    ({}, "empty"),
    ({"cards": "nope"}, "must map to a list"),
    ({"cards": []}, "empty record list"),
    ({"cards": ["nope"]}, "must be an object"),
    ({"cards": [{}]}, "is empty"),
])
def test_malformed_payloads_are_refused_without_a_round_trip(rpc, payload, fragment):
    fake = rpc()
    with pytest.raises(BulkApplyError) as excinfo:
        bulk_apply.apply(payload, "insert")
    assert fragment in str(excinfo.value)
    assert fake.calls == [], "a malformed payload should never be sent"


def test_update_requires_a_name_to_match_on(rpc):
    """Updates match by `name`; `new_name` is the rename. A record with only
    `new_name` would silently match nothing."""
    fake = rpc()
    with pytest.raises(BulkApplyError) as excinfo:
        bulk_apply.apply({"cards": [{"new_name": "Ashwake", "text": "x"}]}, "update")
    assert "no `name` to match on" in str(excinfo.value)
    assert fake.calls == []


def test_insert_does_not_require_a_name(rpc):
    """Only `update` matches by name. `deck_contents` rows have no name column
    at all, so requiring one on insert would make them unwritable."""
    fake = rpc([{"table": "deck_contents", "index": 0, "name": None,
                 "before": None, "after": {"id": 9}}])
    bulk_apply.apply({"deck_contents": [{"deck_id": 3, "content_type": "card",
                                         "content_id": 447}]}, "insert")
    assert fake.calls


@pytest.mark.parametrize("mode", ["", "delete", "Insert", None])
def test_only_insert_and_update_are_accepted(rpc, mode):
    fake = rpc()
    with pytest.raises(BulkApplyError):
        bulk_apply.apply({"cards": [{"name": "A"}]}, mode)
    assert fake.calls == []


def test_the_table_allowlist_is_not_duplicated_here():
    """It lives in the SQL function only.

    Two copies drift, and the database's is the one that actually enforces
    anything -- this module cannot stop a direct PostgREST call. A rejected
    table must come back as a database error, not a local one.
    """
    source = (bulk_apply.__doc__ or "") + (bulk_apply.check.__doc__ or "")
    assert "allowlist" in source.lower()
    assert not hasattr(bulk_apply, "ALLOWED_TABLES")


# ---------------------------------------------------------------------------
# Database errors become readable
# ---------------------------------------------------------------------------

def test_the_plpgsql_message_survives(rpc):
    """What the function raises is the only description of what went wrong.
    Losing it leaves "failed to bulk insert" and nothing else."""
    class APIError(Exception):
        message = "bulk_apply: `cards`[3] has no such column(s) on `cards`: `elemnt`"

    rpc(raises=APIError())
    with pytest.raises(BulkApplyError) as excinfo:
        bulk_apply.apply({"cards": [{"name": "A"}]}, "insert")
    text = str(excinfo.value)
    assert "`cards`[3]" in text and "`elemnt`" in text


def test_the_bulk_apply_prefix_is_stripped(rpc):
    """The prefix identifies the function in Postgres logs; in Discord it is
    noise repeated on every error."""
    class APIError(Exception):
        message = "bulk_apply: `cards` has no record named `Nope`"

    rpc(raises=APIError())
    with pytest.raises(BulkApplyError) as excinfo:
        bulk_apply.apply({"cards": [{"name": "Nope"}]}, "update")
    assert str(excinfo.value) == "`cards` has no record named `Nope`"


def test_a_dict_style_error_is_also_read(rpc):
    """supabase-py has carried the message as a mapping in `args[0]` as well as
    an attribute, depending on version."""
    rpc(raises=Exception({"message": "bulk_apply: payload contained no records"}))
    with pytest.raises(BulkApplyError) as excinfo:
        bulk_apply.apply({"cards": [{"name": "A"}]}, "insert")
    assert str(excinfo.value) == "payload contained no records"


def test_an_error_with_no_message_still_says_something(rpc):
    rpc(raises=RuntimeError("connection reset"))
    with pytest.raises(BulkApplyError) as excinfo:
        bulk_apply.apply({"cards": [{"name": "A"}]}, "insert")
    assert "connection reset" in str(excinfo.value)


def test_an_empty_response_is_treated_as_a_failure(rpc):
    """The function raises rather than returning empty, so an empty body means
    the call never reached it -- an unapplied migration, or a key without
    EXECUTE. Reporting "0 records applied" would read as success."""
    rpc([])
    with pytest.raises(BulkApplyError) as excinfo:
        bulk_apply.apply({"cards": [{"name": "A"}]}, "insert")
    message = str(excinfo.value)
    assert "migration" in message and "service-role" in message
