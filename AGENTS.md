# AGENTS.md

Instructions for AI coding agents working in the AzothBot codebase.

## Project Overview

AzothBot is a **Discord bot** written in Python that serves as the two-person
team's interface to the Supabase database behind **Azoth**, a roguelike
deckbuilder built in Godot. It handles content CRUD, bulk content ingest,
procedural card-art rendering, and gameplay analytics reporting.

- **Language**: Python 3.11
- **Discord library**: `nextcord` 2.6.0 (a `discord.py` fork) — slash commands only
- **Database**: Supabase via `supabase` 1.0.3 (PostgREST). No ORM
- **Imaging**: Pillow, numpy, matplotlib
- **Entry point**: `bot.py`
- **Scope**: internal tool, single guild, two users. Not a public bot

The game itself lives in a **separate repository** (`azoth`), available in this
session as an additional working directory. Its `docs/` is the authority on game
systems.

## Three things that will mislead you

Read these before drawing any conclusion from a command's output or a query.

**1. The Supabase key determines what you can see, and failure is silent.**
The deployed bot uses the **service-role** key — full read/write, RLS bypassed. A
local `.env` may hold the **anon** key, which cannot read `turns`, `turn_nodes`,
`levelups`, `rituals`, `consumables` or `reports`. (**Not** `macros` — it has a
public read policy and is genuinely empty. The six `card_*` / `deck_*` taxonomy
tables were dropped 2026-08-27; a read of one now fails loudly with PGRST205.)
PostgREST returns **HTTP 200 with an empty array**, not an error.
If something reads as empty, check the key before concluding the table is empty.
See [DB_SCHEMA.md § Which key you are holding](docs/DB_SCHEMA.md#azothbot-which-key-you-are-holding).

**2. Supabase failures raise; `[]` now means genuinely empty.** Fixed
2026-08-26. `fetch_all` raises `SupabaseQueryError` on failure and
`SupabaseUnreadableError` *before* querying a table the loaded key can't read —
because an RLS denial is not an exception, it's an HTTP 200 with an empty array.
`safe_interaction` surfaces both to Discord. The one exception is
`autocomplete_from_table`, which catches and logs, since Discord autocomplete has
no error channel — **check the console when an autocomplete is empty**.

**3. A command can exist in the source and not exist at runtime.** Commands are
attached to the cog by `add_*_commands(cls)` functions called from
`azoth_commands/__init__.py`. A module whose attacher is never called is dead
code that still imports cleanly — `rituals.py` and `consumables.py` sat that way
for months before being deleted. Check `__init__.py`, not just the file.

## Project Structure

```
bot.py                    Entry point: intents, login, guild command sync, cog registration
constants.py              Env-derived constants; asset path and bucket maps
supabase_client.py        The single shared Supabase client
supabase_helpers.py       Generic CRUD + all deck-membership logic
supabase_storage.py       Image upload/download against Storage buckets

azoth_commands/
  __init__.py             Builds the AzothCommands cog by calling each attacher
  helpers.py              safe_interaction decorator, image helpers, JSON formatting
  autocomplete.py         Generic table-backed autocomplete
  cards.py aspects.py rites.py decks.py
  content.py              /show and /render, across all content types
  search.py               /search
  heroes.py               RETIRED -- attacher deliberately not called
  misc.py                 bulk_insert / bulk_update
  stats.py                /stats subcommands
  daily_update.py         Scheduled reports + background task

azoth_logic/
  image_generator.py          Facade over the eigenfunction generator
  eigenfunction_generator.py  Loads .npy eigenfunction data, produces art

  # The renderer, rewritten 2026-08-26. See docs/CARD_RENDERING.md.
  card_layout.py              Geometry and type styling, from card.tscn
  fate_layout.py              The same, for aspect_card.tscn / event_card.tscn
  rich_text.py                Symbol tokens, wrapping, centred layout
  eigenfunction_art.py        .exr art -- port of split_card_image.gdshader
  card_render.py              Composites a card face; PNG and GIF
  fate_render.py              Composites aspect and rite faces
  deck_render.py              Deck grid and fanned sample hand
  art_cache.py                On-disk caches for art and animated renders
  content_index.py            Cached index behind /show and /render autocomplete
  content_search.py           The filters behind /search
  bulk_report.py              Diffs and summaries for the bulk commands

  # ARCHIVES -- unreachable at runtime, kept as the record of the old templates.
  card_renderer.py            Superseded by card_render.py + deck_render.py
  fate_renderer.py            Superseded by fate_render.py

tools/sync_assets.py          Vendors game art into assets/card_art/
eigenfunctions/               .npy / .npz art source data
assets/card_art/              Vendored borders, symbols, shader backgrounds
docs/                         All documentation
```

## Documentation

Read the relevant doc before changing a system.

| Doc | Covers |
|---|---|
| `docs/AZOTH.md` | What the game is — vocabulary for reading the schema and content commands |
| `docs/ARCHITECTURE.md` | Cog-attachment pattern, `safe_interaction`, the Supabase helper layer, known structural issues |
| `docs/COMMANDS.md` | Every slash command, its parameters, and what it writes |
| `docs/DB_SCHEMA.md` | **Full schema mirror.** Read the query caveats before writing ANY query |
| `docs/ANALYTICS.md` | The `/stats` views, the daily report, and their known defects |
| `docs/CONTENT_PIPELINE.md` | How content gets from an idea to a database row |
| `docs/CARD_RENDERING.md` | **How `/render` draws cards, aspects and rites** — layout, symbols, animation, caching, vendored assets |
| `docs/RENDERING.md` | ⚠️ Legacy renderers. Still current for art *generation* and the Storage buckets |
| `docs/TESTING.md` | The pytest suite, what it guards, how it was mutation-tested |
| `docs/DEPLOYMENT.md` | Where it runs, configuration, security posture |

## The cog-attachment pattern

No commands are defined on the `AzothCommands` class. Each module exposes
`add_<area>_commands(cls)`, defines commands as closures, and assigns them:

```python
def add_card_commands(cls):
    @nextcord.slash_command(name="create_card", ..., guild_ids=[DEV_GUILD_ID])
    @safe_interaction(timeout=15, require_authorized=True)
    async def create_card_cmd(self, interaction, ...):
        ...
    cls.create_card_cmd = create_card_cmd   # registration happens HERE
```

**Adding a command:**

1. Define it inside the relevant `add_*_commands(cls)`.
2. `@nextcord.slash_command(..., guild_ids=[DEV_GUILD_ID])` — always guild-scoped.
3. `@safe_interaction(...)` — `require_authorized=True` if it writes anything.
4. Assign it onto `cls`. **`tests/test_command_registration.py` fails if you
   forget** — that is the whole point of it.
5. If the module is new, call its attacher in `azoth_commands/__init__.py`.
6. Document it in `docs/COMMANDS.md`.
7. If it renders anything, wrap the render in `asyncio.to_thread`. Blocking work
   on the event loop starves the gateway heartbeat, and `asyncio.wait_for` in
   `safe_interaction` cannot interrupt it — so the timeout never fires either.

A command **returns a string**; `safe_interaction` posts it as a followup.
Commands that send their own files or embeds return `None`.

## Security model

The deployed bot holds a service-role key, so **RLS protects nothing**. Access
control is entirely application-level:

1. `guild_ids=[DEV_GUILD_ID]` — commands exist in one guild only.
2. `require_authorized=True` — checked against `AUTHORIZED_USER_IDS`.

**Every mutating command must set `require_authorized=True`.** A write command
without it is an open door to production content. Verify this on every review.

## Key Conventions

- **Indentation is inconsistent across the repo.** Tabs: `bot.py`,
  `supabase_helpers.py`, `helpers.py`, `cards.py`, `aspects.py`, `heroes.py`,
  `rites.py`, `decks.py`. Four spaces: `constants.py`, `content.py`, `search.py`,
  `stats.py`, `daily_update.py`, everything in `azoth_logic/` and `tools/`.
  `misc.py` is **mixed** — tab-indented outer function, space-indented command
  bodies and module-level helpers. Match the block you are editing; never
  reformat a whole file.
- **Rituals use `challenge_name`, not `name`.** Use `get_display_name(obj, type)`
  and `name_column_for(content_type)` rather than re-deriving it.
- **Deck items are referenced by encoded ref**, `"card:447"`, not by bare name —
  names collide across content types. Use `encode_item_ref` / `parse_item_ref`.
- **Never commit `.env`.** It holds a service-role key on the deployed machine.
- **Never touch `mtime` in `get_art`.** It is the fetch time `ART_TTL` measures;
  touching it on a hit means art never expires, and flat-named upserting uploads
  mean never expiring is never noticing the bytes changed. `get_render` touches
  on purpose — content-hash keys cannot go stale, so there `mtime` is last-used.
- **Never render on the event loop.** Every render path goes through
  `asyncio.to_thread`; inline it and the gateway heartbeat stalls for the
  duration.
- **Never send someone to `sync_assets` for a missing aspect or rite
  background.** Those are shader output from `tools/BackgroundExportTool.tscn` in
  the azoth repo, and `sync_assets` cannot produce them — it only verifies they
  are there. `missing_asset_hint()` picks the right instruction.
- **Content lives in the database, not in files.** The game repo's
  `assets/game_data/` is a fallback snapshot, not the source of truth.
- **`created_by` is `BOT_PLAYER_ID`** on content the bot creates.

## Working with the database

**Read `docs/DB_SCHEMA.md` § Query caveats before writing any query.** Several of
them turn a query that runs cleanly into one that answers the wrong question. The
ones that bite most often:

- Filter `version >= '0.8.2'` — everything earlier is a different dataset.
- Never `avg()` a combo. It is an exponentially growing BigNum stored as `text`.
  Use `turn_nodes.combo_log10`.
- `no_boss_key` counts as a **win**, alongside `victory`.
- Boss turns and regular turns are not comparable — filter `turns.boss_id is null`.
- `left join turn_nodes`, never inner — an inner join drops zero-node turns.
- Add `game_type = 'solo'` unless you specifically want co-op, which records one
  row per participant.

## Testing

**pytest, 599 tests, all offline.** See `docs/TESTING.md`.

```bash
.venv/bin/python -m pytest
```

Most are regression tests for specific production bugs, named at each site. The
suite is **mutation-tested** — every fixed bug was reintroduced to confirm the
tests catch it. Do that again when you fix something worth a test; a test of a
helper is not a test of the code that calls it, and that gap let one mutant
through on the first pass.

`tests/conftest.py` stubs the environment before any project module imports and
points `SUPABASE_URL` at a fake host, so nothing reaches the live database.

`tests/test_command_registration.py` is the one to know about. It is the only
thing that sees two bug classes the 2026-08-26 overhaul shipped:

- **A command defined but never attached.** `/render_card` and `/render_aspect`
  were both complete function bodies reachable by nobody — the same failure that
  hid `rituals.py` for months, and invisible at import.
- **A name that does not exist at runtime.** Deleting the module-level
  `renderer = CardRenderer()` left four call sites behind, so `/create_card`
  raised `NameError` *after* writing the row and uploading the art. It shells out
  to `pyflakes` and fails on undefined names only.

Still uncovered: command BODIES are never executed, renders are not compared
against Godot's own output, and the turn-grain queries have never run against
real `turns` data. There is no CI. See `docs/TESTING.md` § Gaps.

## What NOT to Do

- **Don't write content directly into the game repo's `assets/game_data/`** — that
  is a fallback snapshot. Content goes into Supabase, via `/bulk_insert`.
- **Don't add a write command without `require_authorized=True`.**
- **Don't add a taxonomy table back.** Elements, card types, attributes and
  deck types live in `azoth_logic/taxonomy.py`, beside the game constants they
  mirror. Adding a value is a code change in both repos, not a new row.
- **Don't re-add a `/delete_*` command.** All four were removed 2026-08-27.
  `cards`, `aspects` and `events` have no `archived_at` column, so those deletes
  were unrecoverable — and the game's `prune_content_dirs()` reads a missing row
  as the deletion signal, so one misclick also pruned the offline snapshot.
  Retire content with `/remove_from_deck`; see `docs/COMMANDS.md` § Deletion.
- **Don't uncomment `/stage`, `/postpone` or `/merge_staging`** without first
  fixing their hardcoded deck IDs — deck 21 is archived and deck 22 is not the
  aspect deck.
- **Don't assume a command exists because the code does** — check
  `azoth_commands/__init__.py`.
- **Don't trust an empty result** — check which Supabase key is loaded.
- **Don't quote a `/stats` number as fact.** The views behind them pool every game
  version, average a BigNum in linear space, and include developer testing. See
  `docs/ANALYTICS.md`.
- **Don't undo the daily-update safeguards** — the claim-before-send ordering and
  the atomic state write each fix a real bug, and both are commented at the site.
- **Don't reformat whole files** to normalise tabs vs spaces; it destroys history
  for no benefit.
- **Don't change the schema from here.** The game writes this data; schema changes
  originate in the game repo and its `db/migrations/`.
