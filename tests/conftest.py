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

    def __init__(self, rows=None, raises=None, log=None, table=None):
        self._rows = rows if rows is not None else []
        self._raises = raises
        self.log = log if log is not None else {}
        self.table = table
        self.log.setdefault("filters", [])
        self.log.setdefault("order", [])

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

    @property
    def not_(self):
        self.log["filters"].append(("not", None, None)); return self

    def order(self, col, desc=False):
        self.log["order"].append((col, desc)); return self

    def limit(self, n):
        self.log["limit"] = n
        self._rows = self._rows[:n]
        return self

    def range(self, lo, hi):
        self.log["range"] = (lo, hi); return self

    def insert(self, data):
        self.log["insert"] = data; return self

    def update(self, data):
        self.log["update"] = data; return self

    def delete(self):
        self.log["delete"] = True; return self

    def execute(self):
        if self._raises:
            raise self._raises
        return type("Response", (), {"data": list(self._rows)})()


class FakeSupabase:
    """Routes .table(name) to canned rows per table name."""

    def __init__(self, tables=None, raises=None):
        self.tables = tables or {}
        self.raises = raises or {}
        self.log = {}

    def table(self, name):
        log = self.log.setdefault(name, {})
        return FakeQuery(self.tables.get(name, []), self.raises.get(name), log, name)


@pytest.fixture
def fake_supabase():
    return FakeSupabase
