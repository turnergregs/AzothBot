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
`levelups`, `rituals`, `consumables`, `macros`, `reports`, or the `deck_*`
taxonomy tables. PostgREST returns **HTTP 200 with an empty array**, not an error.
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
`azoth_commands/__init__.py`. `rituals.py` and `consumables.py` are complete,
import cleanly, and are **never registered** — 10 commands that do not exist.

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
  cards.py aspects.py heroes.py events.py decks.py
  consumables.py rituals.py    NOT REGISTERED
  misc.py                 bulk_insert / bulk_update
  stats.py                /stats subcommands
  daily_update.py         Scheduled reports + background task

azoth_logic/
  image_generator.py          Facade over the eigenfunction generator
  eigenfunction_generator.py  Loads .npy eigenfunction data, produces art
  card_renderer.py            Composites card images
  ritual_renderer.py          Composites ritual (challenge/reward) images

utils/interaction_helpers.py  Dead duplicate of helpers.safe_interaction
eigenfunctions/               .npy / .npz art source data
assets/                       fonts, icons, renders, downloaded images
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
| `docs/RENDERING.md` | Card/ritual image generation and Supabase Storage |
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
4. Assign it onto `cls`.
5. If the module is new, call its attacher in `azoth_commands/__init__.py`.
6. Document it in `docs/COMMANDS.md`.

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
  `supabase_helpers.py`, `cards.py`, `aspects.py`, `heroes.py`, `events.py`,
  `decks.py`, `consumables.py`, `rituals.py`. Four spaces: `constants.py`,
  `stats.py`, `daily_update.py`, everything in `azoth_logic/`. `misc.py` is
  **mixed** — tab-indented outer function, space-indented command bodies. Match
  the block you are editing; never reformat a whole file.
- **Rituals use `challenge_name`, not `name`.** Use `get_display_name(obj, type)`
  and `name_column_for(content_type)` rather than re-deriving it.
- **Deck items are referenced by encoded ref**, `"card:447"`, not by bare name —
  names collide across content types. Use `encode_item_ref` / `parse_item_ref`.
- **Never commit `.env`.** It holds a service-role key on the deployed machine.
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

**There is no test suite, no linter config, and no CI.** Verification is manual:
run the bot against the dev guild and exercise the command.

If you add tests, note that nearly everything reaches Supabase through the
module-level client in `supabase_client.py`, so that is the seam to mock.

## What NOT to Do

- **Don't write content directly into the game repo's `assets/game_data/`** — that
  is a fallback snapshot. Content goes into Supabase, via `/bulk_insert`.
- **Don't add a write command without `require_authorized=True`.**
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
