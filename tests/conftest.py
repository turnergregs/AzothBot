"""Shared fixtures.

Environment is stubbed BEFORE any project module imports. Three reasons:

  * `constants.py` calls int(os.getenv(...)) at import with no guard, and
    `supabase_client.py` raises outright on missing credentials -- so importing
    anything at all requires a populated environment.
  * `load_dotenv()` does not override variables that are already set, so these
    stubs win over a developer's real `.env`.
  * Nothing here should ever touch the live database. A fake URL guarantees a
    test that accidentally makes a request fails loudly instead of writing to
    production.
"""
import os
import sys

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("DEV_GUILD_ID", "1")
os.environ.setdefault("BOT_PLAYER_ID", "1")
os.environ.setdefault("AUTHORIZED_USER_IDS", "1,2")
# Not a real project ref, and the anon-shaped JWT below decodes to role "anon".
os.environ.setdefault("SUPABASE_URL", "https://testproject.supabase.co")
os.environ.setdefault(
    "SUPABASE_KEY",
    "eyJhbGciOiJIUzI1NiJ9."
    "eyJyb2xlIjoiYW5vbiIsImlzcyI6InN1cGFiYXNlIn0."
    "c2lnbmF0dXJl",
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


class FakeQuery:
    """Minimal stand-in for a PostgREST query builder.

    Records the calls made against it so tests can assert on the request that
    *would* have been sent, and returns canned rows.
    """

    # PostgREST's max-rows: no response carries more, whatever is asked for.
    MAX_ROWS = 1000

    def __init__(self, rows=None, raises=None, log=None, table=None, shuffle=None):
        self._rows = rows if rows is not None else []
        self._raises = raises
        self.log = log if log is not None else {}
        self.table = table
        self._order = None
        self._range = None
        self._limit = None
        # A callable reordering rows that TIE under the requested order, per
        # request, the way Postgres may return equal rows in any order. Lets a
        # test catch paging over a sort that is not total.
        self._shuffle = shuffle
        self.log.setdefault("filters", [])
        self.log.setdefault("order", [])
        self.log.setdefault("requests", [])

    def select(self, *a, **k):
        self.log["select"] = a[0] if a else "*"
        return self

    def eq(self, col, val):
        self.log["filters"].append(("eq", col, val)); return self

    def in_(self, col, vals):
        self.log["filters"].append(("in", col, list(vals)))
        self._rows = [r for r in self._rows if r.get(col) in vals]
        return self

    def is_(self, col, val):
        self.log["filters"].append(("is", col, val)); return self

    def _compare(self, op, col, val, keep):
        # Like Postgres, a NULL never passes a comparison.
        self.log["filters"].append((op, col, val))
        self._rows = [r for r in self._rows if r.get(col) is not None and keep(r.get(col), val)]
        return self

    def gt(self, col, val):
        return self._compare("gt", col, val, lambda a, b: a > b)

    def gte(self, col, val):
        return self._compare("gte", col, val, lambda a, b: a >= b)

    def lt(self, col, val):
        return self._compare("lt", col, val, lambda a, b: a < b)

    def lte(self, col, val):
        return self._compare("lte", col, val, lambda a, b: a <= b)

    def neq(self, col, val):
        return self._compare("neq", col, val, lambda a, b: a != b)

    @property
    def not_(self):
        self.log["filters"].append(("not", None, None)); return self

    def order(self, col, desc=False):
        self.log["order"].append((col, desc))
        self._order = col
        return self

    def limit(self, n):
        self.log["limit"] = n
        self._limit = n
        return self

    def range(self, lo, hi):
        # postgrest-py 0.10.7's reading: `hi` is EXCLUSIVE (Range: lo-(hi-1)).
        self.log["range"] = (lo, hi)
        self._range = (lo, hi)
        return self

    def insert(self, data):
        self.log["insert"] = data; return self

    def update(self, data):
        self.log["update"] = data; return self

    def delete(self):
        self.log["delete"] = True; return self

    def _sorted(self, rows):
        terms = [t.rsplit(".", 1) for t in (self._order or "").split(",") if t]

        def key(row):
            return tuple(row.get(col) for col, _ in terms)

        if self._shuffle:
            rows = self._shuffle(list(rows))
        # Stable sorts from the last term to the first: a multi-column order.
        for col, direction in reversed(terms):
            rows = sorted(rows, key=lambda r: (r.get(col) is None, r.get(col)),
                          reverse=direction == "desc")
        return rows

    def execute(self):
        self.log["requests"].append({"order": self._order, "range": self._range,
                                     "limit": self._limit})
        if self._raises:
            raise self._raises
        rows = self._sorted(self._rows)
        if self._range:
            rows = rows[self._range[0]:self._range[1]]
        if self._limit is not None:
            rows = rows[:self._limit]
        return type("Response", (), {"data": rows[:self.MAX_ROWS]})()


class FakeSupabase:
    """Routes .table(name) to canned rows per table name."""

    def __init__(self, tables=None, raises=None, shuffle=None):
        self.tables = tables or {}
        self.raises = raises or {}
        self.shuffle = shuffle
        self.log = {}

    def table(self, name):
        log = self.log.setdefault(name, {})
        return FakeQuery(self.tables.get(name, []), self.raises.get(name), log, name,
                         shuffle=self.shuffle)


@pytest.fixture
def fake_supabase():
    return FakeSupabase


@pytest.fixture(autouse=True)
def _rite_schema_after_the_rename():
    """Answer "after the rename" without probing Supabase.

    rite_schema.current() reads the database, which no test may do. Tests of the
    detection itself unpin it (tests/test_rite_schema.py); tests of the old side
    pin BEFORE. Both are undone here.
    """
    from azoth_logic import rite_schema
    rite_schema.pin(rite_schema.AFTER)
    yield
    rite_schema.pin(None)
    rite_schema.invalidate()
