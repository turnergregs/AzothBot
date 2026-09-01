# AzothBot — Project Context for AI Agents

AzothBot is a **Discord bot** (Python 3.11 + `nextcord`) that is the two-person
team's interface to the Supabase database behind **Azoth**, a roguelike
deckbuilder built in Godot 4.6. It handles content CRUD, bulk ingest, procedural
card-art rendering, and analytics reporting.

The game lives in a separate repo (`azoth`), usually available as an additional
working directory. Its `docs/` is the authority on game systems.

## Read This First

Three things cause more wrong conclusions here than anything else.

**1. Which Supabase key is loaded changes what you can see — silently.**
The deployed bot uses the **service-role** key: full read/write, RLS bypassed.
A local `.env` may hold the **anon** key, which cannot read `turns`,
`turn_nodes`, `levelups`, `rituals`, `consumables` or `reports`. (**Not** `macros` — it has a public read
policy and is genuinely empty. It is deliberately absent from
`ANON_UNREADABLE`.) PostgREST returns **HTTP 200 with an empty array**, not
an error. Check the key before concluding a table is empty.

**2. Supabase failures raise; `[]` now means genuinely empty.** Fixed
2026-08-26. `fetch_all` raises `SupabaseQueryError` on failure, and
`SupabaseUnreadableError` *before* querying a table the loaded key can't read.
`autocomplete_from_table` is the only caller that catches — check the console
when an autocomplete comes back empty.

**3. Code existing ≠ command existing.** Commands are attached to the cog by
`add_*_commands(cls)` calls in `azoth_commands/__init__.py`. A module whose
attacher is never called is dead code that still imports cleanly — that is how
`rituals.py` and `consumables.py` went unnoticed for months.

## Documentation

All docs live in `docs/`. Read before changing a system.

| Doc | Covers |
|-----|--------|
| `AZOTH.md` | What the game is — vocabulary for the schema and content commands |
| `ARCHITECTURE.md` | Cog-attachment pattern, `safe_interaction`, Supabase helpers, known structural issues |
| `COMMANDS.md` | Every slash command, parameters, what it writes, what's broken |
| `DB_SCHEMA.md` | **Full schema mirror.** Read the query caveats before writing ANY query |
| `ANALYTICS.md` | `/stats` views, the daily report, and their defects |
| `CONTENT_PIPELINE.md` | Idea → JSON → database row |
| `CARD_RENDERING.md` | **How `/render` draws cards, aspects and rites.** Layout, symbols, animation, caching, and the vendored assets |
| `RENDERING.md` | ⚠️ Legacy renderers. Still current for art *generation* and the Storage buckets |
| `TESTING.md` | The pytest suite, what it guards, and how it was mutation-tested |
| `DEPLOYMENT.md` | Where it runs, config, security posture |

## Key Architecture Decisions

- **Cog attachment, not cog methods.** `AzothCommands` defines no commands. Each
  module's `add_*_commands(cls)` defines closures and assigns them onto the class.
  Both the assignment *and* the call from `__init__.py` are required.
- **`safe_interaction` is the security boundary.** With a service-role key, RLS
  protects nothing. `require_authorized=True` checked against
  `AUTHORIZED_USER_IDS` is the only guard on production writes. Every mutating
  command must set it.
- **Guild-scoped commands only.** Every command passes `guild_ids=[DEV_GUILD_ID]`
  and `bot.py` syncs to that guild. The global sync is commented out.
- **Commands return strings.** The decorator posts the return value as a followup.
  Commands sending their own files or embeds return `None`.
- **`deck_contents` is a universal join table** — `(deck_id, content_type,
  content_id)`. Because names collide across types, autocomplete encodes refs
  (`"card:447"`); use `encode_item_ref` / `parse_item_ref`.
- **Rituals use `challenge_name`, not `name`.** Use `get_display_name(obj, type)`
  and `name_column_for(content_type)`.
- **Art generation is random and destructive.** Uploads are flat-named and
  upserting, so regenerating destroys the previous image. No history, no seed.
  Because the name does not change, `art_cache.forget_art()` must be called at
  every upload site or the bot keeps drawing the old art for up to 7 days.
- **Two commands cover all content lookup.** `/show` and `/render` dispatch on an
  encoded ref (`card:447`) from one autocomplete; the six typed `/get_*` and
  `/render_*` commands were retired 2026-08-26.
- **Only LIVE content is findable** (2026-08-28). `cards`/`aspects`/`events` have
  no `archived_at`, so liveness is inferred as *in at least one unarchived deck*
  — 233 of 626 rows. `content_index` caches the deck membership beside the name
  index and every lookup path filters on it. Two invariants: `/add_to_deck` is
  NOT filtered (it is the only way back), and an empty live set means a failed
  read, so it filters nothing rather than hiding everything.
- **The render cache evicts on write, not on a timer.** Size-capped LRU
  (`art_cache._evict`). A daily sweep was rejected: the bot is hand-started, so a
  timer may not fire for weeks, and growth is bursty rather than
  time-proportional. `mtime` means *last used* for renders (touched on hit) and
  *last fetched* for art (never touched — `ART_TTL` measures it). Don't unify them.
- **Renders must not run on the event loop.** They download art and run PIL/numpy
  for seconds at a time. `asyncio.wait_for` cannot interrupt blocking work, so a
  render left inline starves the gateway heartbeat *and* defeats its own timeout.
  Every render path goes through `asyncio.to_thread`.

## Running the Bot

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                 # then fill it in
python bot.py
```

Success is **two** lines — logged in, *and* commands synced to the dev guild.

Deployment is a teammate's Windows machine, started by hand, not reliably
always-on. See `docs/DEPLOYMENT.md`.

## Testing

**pytest, 620 tests, all offline** (`docs/TESTING.md`):

```bash
.venv/bin/python -m pytest
```

Mostly regression tests for real production bugs, each naming its incident. The
suite is mutation-tested — reintroduce a bug and confirm the tests go red before
trusting them.

`test_command_registration.py` is the one to know about: it checks **what the cog
actually exposes** and that **no name in the command layer is undefined**. Both
bug classes shipped in the 2026-08-26 overhaul, and nothing else can see either —
a command with no `cls.x = x` imports cleanly, and a deleted module-level name
only raises when someone runs the command in Discord. It shells out to `pyflakes`
and fails on undefined names only.

Still uncovered: command BODIES are never executed, renders are not compared
against Godot, and the turn-grain queries have never run against live data. No
CI. See `docs/TESTING.md` § Gaps.

## Writing Queries

**Read `docs/DB_SCHEMA.md` § Query caveats first.** The ones that bite most:

- Filter `version_key(version) >= analytics_cutoff()` — `0.9.0` since
  2026-08-28. Earlier rows are a different dataset — no turn rows, `result`
  NULL on most, dominated by developer testing.
- **Never `avg()` a combo.** Exponentially growing BigNum stored as `text`. Use
  `turn_nodes.combo_log10`.
- `no_boss_key` counts as a **win**, with `victory`.
- Boss turns aren't comparable to regular turns — filter `turns.boss_id is null`.
- `left join turn_nodes`, never inner — inner drops zero-node turns.
- Add `game_type = 'solo'` unless you want co-op, which records one row per
  participant.
- Report censored metrics as two numbers: "cleared in 2.3 links, 78% of the time".

Note the trustworthy dataset is currently ~2 games (`0.9.0`, measured
2026-08-28; it was 19 at the old `0.8.2` cutoff). Almost everything `/stats`
reports comes from data the cutoff exists to exclude.

## Authoring Content

**`assets/game_data/` in the game repo is not authoritative.** It's a fallback
snapshot exported from Supabase. The database is the source of truth, and this bot
is how content gets into it.

For anything with real mechanics (`actions`, `triggers`, `properties` — all
`jsonb`), the `create_*` slash commands are insufficient. Produce a bulk-insert
JSON per the game repo's `skills/content-creation/` contract and upload it with
`/bulk_insert`. To change existing rows use `/bulk_update` — different rules
(matched by `name`, never send `id`, partial fields, rename via `new_name`).
See `docs/CONTENT_PIPELINE.md`.

## What NOT to Do

- Don't add a write command without `require_authorized=True`
- Don't assume a command exists because the code does — check `__init__.py`
- Don't trust an empty result — check which Supabase key is loaded
- Don't quote a `/stats` number as fact — see `docs/ANALYTICS.md` for why
- Don't write content into the game repo's `assets/game_data/` — hand over a
  bulk_insert JSON instead
- Don't undo the daily-update safeguards — claim-before-send, the atomic state
  write, and the catch-everything sweep in `_send_due_channels` each fix a real
  bug, all commented at the site. The sweep looks like swallowing and is not: an
  exception reaching `tasks.Loop` stops the daily report until the process is
  restarted
- Don't reformat whole files over tabs vs spaces; indentation is inconsistent by
  file and history matters more
- Don't change the schema from here — it originates in the game repo's
  `db/migrations/`
- Don't add a taxonomy table back. Elements, card types, attributes and
  deck types live in `azoth_logic/taxonomy.py`, beside the game constants
  they mirror; adding a value is a code change in both repos
- Don't re-add a `/delete_*` command. All four were removed 2026-08-27 —
  `cards`/`aspects`/`events` have no `archived_at`, so those deletes were
  unrecoverable and also pruned the game's offline snapshot. Retire content
  with `/remove_from_deck` instead — since 2026-08-28 that also hides it from
  every lookup command; see `docs/COMMANDS.md` § Deletion
- Don't filter `/add_to_deck`'s item picker to live content. Every other lookup
  is filtered; that one is the only route back for a retired row, and there is
  no `/delete_*` left to undo a mistake
- Don't uncomment `/stage`, `/postpone` or `/merge_staging` without first
  fixing their hardcoded deck IDs (21 is archived, 22 is not the aspect deck)
- Don't commit `.env`
- Don't add a command without assigning it onto the cog — `test_command_registration.py`
  will fail, and that is the point
- Don't run a render inline on the event loop; use `asyncio.to_thread`
- Don't tell anyone to run `sync_assets` for a missing aspect/rite background —
  those are shader output and it cannot produce them (`missing_asset_hint`)
