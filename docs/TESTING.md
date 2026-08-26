# Testing

AzothBot uses **pytest**. 109 tests, all offline — nothing in the suite touches
the live database.

```bash
.venv/bin/python -m pytest              # everything
.venv/bin/python -m pytest tests/test_daily_update.py
.venv/bin/python -m pytest -k "flood"   # by name
```

Added 2026-08-26. Before that there was no suite at all.

| File | Tests | Covers |
|---|---|---|
| `tests/test_supabase_helpers.py` | 45 | The access layer: the RLS pre-flight guard, raise-don't-swallow, query construction, deck item refs |
| `tests/test_daily_update.py` | 40 | The daily report: turn-grain aggregation, send/claim ordering, state file, scheduling, embed limits |
| `tests/test_helpers.py` | 24 | Key-role detection, autocomplete degradation, filename slugging |

## What these tests are for

Most are **regression tests for specific production bugs**, each named at its
site with the date and the failure. Two came from real incidents:

| Incident | Pinned by |
|---|---|
| **2026-06-30** — `unsupported operand type(s) for +: 'int' and 'str'` when enabling the daily update. `level_reached` (bigint) + `highest_combo` (**text**) | `test_draft_stats_survives_a_text_combo_end_to_end` |
| **2026-06-19** — ~30 duplicate messages. The day was claimed only *after* a successful send, so a send that failed partway left nothing claimed and the 10-minute loop retried forever | `test_failed_send_does_not_re_fire` |

The rest guard invariants that are easy to break while producing plausible
numbers — zero-node turns staying in the denominator, skips not counting as
links, boss and regular turns staying apart, reward rates dividing by offers.

## The suite is mutation-tested

A passing suite proves nothing on its own. Every fixed bug was **reintroduced**
one at a time to confirm the tests actually fail:

```
June 30: int + str combo score            CAUGHT
June 19: claim AFTER send                 CAUGHT
zero-node turns dropped from denominator  CAUGHT
skips counted as links                    CAUGHT
boss + regular turns pooled               CAUGHT
reward rate = raw pick count              CAUGHT
fetch_all swallows errors                 CAUGHT
RLS pre-flight guard removed              CAUGHT
unknown key treated as privileged         CAUGHT
soft_delete returns None again            CAUGHT
retired types parse as deck refs          CAUGHT
limit no longer pushed to server          CAUGHT
```

The first pass **missed** the June 30 mutant: the test exercised `_to_number` in
isolation while the bug lived at the call site. That is the failure mode to watch
for — a test of the helper is not a test of the code that uses it. An end-to-end
test through `_fetch_draft_stats` fixed it.

**Do this again after fixing a bug worth a test.** Reintroduce it, confirm red,
revert.

## Conventions

- **No network.** `tests/conftest.py` stubs the environment *before* any project
  module imports — `constants.py` calls `int(os.getenv(...))` unguarded at import
  and `supabase_client.py` raises on missing credentials, so a populated
  environment is required just to import. `load_dotenv()` does not override
  variables that are already set, so the stubs beat a developer's real `.env`.
  The stub `SUPABASE_URL` is a fake host: a test that accidentally makes a
  request fails loudly instead of writing to production.
- **The stub key decodes to `anon`.** Tests needing privileged reads
  `monkeypatch.setattr(h, "SUPABASE_ROLE", "service_role")` explicitly, which
  also documents which paths need it.
- **Fake PostgREST, not mocks.** `FakeSupabase` / `FakeQuery` in `conftest.py`
  mimic the query-builder chain and record calls, so tests can assert on the
  request that *would* have been sent (`fs.log["cards"]["filters"]`).
- **Name the bug in the test.** A regression test whose docstring doesn't say
  what broke gets deleted by the next person who finds it inconvenient.

## Gaps

Honest list of what is **not** covered:

- **The turn-grain queries have never run against live `turns`.** The aggregation
  is tested against fixtures; the actual PostgREST calls are unverified because
  the tables need the service-role key. First real daily report is the check.
- **No command-level tests.** `safe_interaction`, the slash-command bodies and
  the nextcord layer are untested — they need a Discord interaction harness.
- **No renderer tests.** `card_renderer.py` and `fate_renderer.py` (2,700 lines)
  have no coverage; image output needs golden-file comparison.
- **No CI.** The suite runs locally only.
