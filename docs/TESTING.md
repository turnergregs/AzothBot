# Testing

AzothBot uses **pytest**. 438 tests, all offline — nothing in the suite touches
the live database.

```bash
.venv/bin/python -m pytest              # everything
.venv/bin/python -m pytest tests/test_daily_update.py
.venv/bin/python -m pytest -k "flood"   # by name
```

Added 2026-08-26. Before that there was no suite at all.

| File | Tests | Covers |
|---|---|---|
| `tests/test_card_render.py` | 63 | The card face: geometry from `card.tscn`, symbol tokens, wrapping, split borders, the measured constants |
| `tests/test_fate_render.py` | 56 | Aspects and rites: backgrounds, mask recolouring, the reversed aspect palette, the rite/event naming boundary |
| `tests/test_supabase_helpers.py` | 45 | The access layer: the RLS pre-flight guard, raise-don't-swallow, query construction, deck item refs |
| `tests/test_daily_update.py` | 40 | The daily report: turn-grain aggregation, send/claim ordering, state file, scheduling, embed limits |
| `tests/test_helpers.py` | 35 | Key-role detection, autocomplete degradation, filename slugging, embed packing, missing-asset guidance |
| `tests/test_search.py` | 30 | `/search` filters, the deep `actions`/`triggers`/`properties` scan, sort orders |
| `tests/test_bulk_report.py` | 24 | Bulk diffs: changed-fields-only, jsonb shape not contents, announced truncation |
| `tests/test_command_registration.py` | 22 | **What the cog actually exposes**, and that no name in it is undefined at runtime |
| `tests/test_art_cache.py` | 21 | Both caches: content-hash render keys, art TTL, invalidation after a re-upload |
| `tests/test_content_get.py` | 20 | The `/show` embed: which fields show, which are deliberately omitted |
| `tests/test_content_index.py` | 18 | The autocomplete index: TTL, explicit invalidation, ref encoding, match ranking |
| `tests/test_deck_render.py` | 17 | Grid and hand layout, art deduplication, per-kind bucket routing |
| `tests/test_sync_assets.py` | 8 | The vendored-asset sync, and the shader-exported backgrounds it deliberately cannot sync |

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

The 2026-08-27 round, covering the render overhaul's own defects:

```
create_card calls a deleted `renderer`     CAUGHT   (undefined-name scan)
/render defined but never attached to cog  CAUGHT
retired hero commands re-attached          CAUGHT
bulk report packed into one oversized embed CAUGHT
rite art fetched from the cards bucket     CAUGHT
aspect_colors rejects hex again            CAUGHT
forget_art becomes a no-op                 CAUGHT
sync_assets exits 0 on a missing background CAUGHT
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
- **`pyflakes` is a test dependency.** `test_command_registration.py` shells out
  to it and fails on **undefined names only** — unused imports are historical,
  harmless and noisy, and failing on them would make the test something people
  disable. It is the only thing that sees a name deleted out from under its call
  sites without a live Supabase and a live gateway.

## Gaps

Honest list of what is **not** covered:

- **The turn-grain queries have never run against live `turns`.** The aggregation
  is tested against fixtures; the actual PostgREST calls are unverified because
  the tables need the service-role key. First real daily report is the check.
- **Command BODIES are still not executed.** `test_command_registration.py`
  checks two things about them statically — that every defined command reaches
  the cog, and that no name in the module is undefined — but nothing runs a
  command end to end, because that needs a Discord interaction harness. So a
  logic error inside a body still ships.

  Two consequences worth naming, because both are call sites whose *helper* is
  tested and whose *use* is not — the exact gap that let the June 30 mutant
  through on the first pass:

  | Untested call site | Tested helper |
  |---|---|
  | `cards.py` dropping cached art after `regenerate_image` | `art_cache.forget_art` |
  | `misc.py` sending a multi-embed bulk report | `helpers.pack_fields_into_embeds` |

- **Renders are not compared against Godot.** The layout constants are pinned, and
  the renders are checked for shape, colour and determinism — but no golden-file
  comparison against the game's own output runs in the suite. The calibration was
  done by hand with `tools/CardRenderTool.tscn` in the azoth repo, which needs a
  real display driver and so cannot run headless. Layout drift in `card.tscn`
  therefore fails only if it moves something a constant test asserts on.
- **The legacy renderers are uncovered.** `card_renderer.py` and
  `fate_renderer.py` (2,700 lines) have no tests. Both are archives, unreachable
  at runtime — see [CARD_RENDERING.md § Retired](CARD_RENDERING.md#retired) — so
  this is deliberate rather than outstanding.
- **No CI.** The suite runs locally only.
