# Architecture

How AzothBot is put together, and the non-obvious patterns you need to know
before adding a command.

## Stack

| Piece | Choice | Notes |
|---|---|---|
| Discord library | `nextcord` 2.6.0 | A `discord.py` fork. Slash commands only — there is no message-command prefix |
| Database | `supabase` 1.0.3 (PostgREST) | No ORM, no migrations from this side |
| Imaging | `Pillow`, `numpy`, `matplotlib` | Procedural card art — see [RENDERING.md](RENDERING.md) |
| Config | `python-dotenv` | Everything comes from `.env` |

Python 3.11 (`.python-version`).

## Layout

```
bot.py                      Entry point: intents, login, command sync, cog registration
constants.py                Env-derived constants and asset path maps
supabase_client.py          The single shared `supabase` client instance
supabase_helpers.py         Generic CRUD + all deck-membership logic
supabase_storage.py         Image upload/download against Storage buckets

azoth_commands/
  __init__.py               Builds the AzothCommands cog by attaching command modules
  helpers.py                safe_interaction decorator, image helpers, JSON formatting
  autocomplete.py           Generic table-backed autocomplete
  cards.py  aspects.py  rites.py  decks.py
  content.py                /show, /render and /rules, across all content types
  search.py                 /search
  heroes.py                 RETIRED -- attacher deliberately not called
  misc.py                   /bulk_insert, /bulk_update (both via azoth_logic/bulk_apply)
  stats.py                  /stats subcommands (formatting in azoth_logic/stats_format)
  daily_update.py           Scheduled analytics reports + its background task

azoth_logic/
  image_generator.py        Thin facade over the eigenfunction generator
  eigenfunction_generator.py  Loads .npy eigenfunction data, produces art

  # The current renderer (2026-08-26). See docs/CARD_RENDERING.md.
  card_layout.py            Geometry and type styling, transcribed from card.tscn
  fate_layout.py            The same, for aspect_card.tscn / event_card.tscn
  rich_text.py              Symbol tokens, wrapping, centred layout
  eigenfunction_art.py      .exr art -- the port of split_card_image.gdshader
  card_render.py            Composites a card face; PNG and GIF output
  fate_render.py            Composites aspect and rite faces
  deck_render.py            Deck grid, fanned sample hand, upgrade comparison
  art_cache.py              On-disk caches for art and animated renders
  content_index.py          Cached (kind, id, name) index behind /show and /render,
                            plus the deck-membership liveness filter
  content_search.py         The filters behind /search
  bulk_report.py            Diffs and summaries for /bulk_insert and /bulk_update
  bulk_apply.py             The transactional write itself -- one RPC, one transaction
  taxonomy.py               Elements, card types, attributes, deck types -- was six tables
  upgrades.py               What a card becomes when it upgrades; mirrors the engine
  holo.py                   The upgraded card's holographic sheen (ported shader)
  stats_format.py           /stats views as tables and fields, not raw JSON

  # ARCHIVES -- unreachable at runtime, kept as the record of the old templates.
  card_renderer.py          Superseded by card_render.py + deck_render.py
  fate_renderer.py          Superseded by fate_render.py (was ritual_renderer.py)

eigenfunctions/             .npy / .npz source data for procedural art
assets/                     fonts, icons, renders, downloaded images
combinations/               Generated art samples (not used at runtime)
```

## The cog-attachment pattern

This is the least obvious thing in the codebase.

`AzothCommands` is a normal `commands.Cog` subclass, but **no commands are
defined on it**. Each module exposes `add_<area>_commands(cls)`, which defines
its commands as closures and then assigns them onto the class:

```python
# azoth_commands/cards.py
def add_card_commands(cls):
    @nextcord.slash_command(name="create_card", ..., guild_ids=[DEV_GUILD_ID])
    @safe_interaction(timeout=15, require_authorized=True)
    async def create_card_cmd(self, interaction, ...):
        ...

    cls.create_card_cmd = create_card_cmd   # <-- registration happens HERE
```

`__init__.py` then calls each attacher at import time:

```python
add_deck_commands(AzothCommands)
add_card_commands(AzothCommands)
...
```

Two consequences that bite:

1. **A command that isn't assigned onto `cls` does not exist.** The decorator
   alone is not enough.
2. **A module whose attacher isn't called in `__init__.py` does not exist**, even
   though the file is complete and imports cleanly. `rituals.py` and
   `consumables.py` sat in exactly that state for months before being removed
   entirely on 2026-08-26.

### Adding a command

1. Define it inside the relevant `add_*_commands(cls)` function.
2. Decorate with `@nextcord.slash_command(..., guild_ids=[DEV_GUILD_ID])`.
3. Decorate with `@safe_interaction(...)` — set `require_authorized=True` if it
   writes anything.
4. Assign it: `cls.my_command = my_command`.
5. If the module is new, call its attacher from `__init__.py`.
6. Document it in [COMMANDS.md](COMMANDS.md).

## Command registration and scope

Every command passes `guild_ids=[DEV_GUILD_ID]`, and `bot.py` syncs to that guild
on ready:

```python
await bot.sync_application_commands(guild_id=dev_guild_id)
```

Guild-scoped commands appear within seconds; global commands would take up to an
hour. The global sync call is present but commented out. **The bot is not
designed to be used from more than one server.**

## `safe_interaction`

Every command wraps in this decorator (`azoth_commands/helpers.py`). It does four
things:

| Concern | Behaviour |
|---|---|
| **Authorization** | If `require_authorized=True`, rejects any user not in `AUTHORIZED_USER_IDS` with an ephemeral message |
| **Deferral** | Calls `interaction.response.defer()` — Discord's 3-second ack window is short and Supabase round-trips are not |
| **Timeout** | Wraps the body in `asyncio.wait_for(timeout=…)` |
| **Error capture** | Catches everything and posts the exception in a code block |

A command **returns a string** rather than sending it; the decorator posts it as
a followup. Commands that send their own files or embeds return `None`.

> **`require_authorized` is the security boundary.** The deployed bot holds a
> service-role Supabase key, so RLS provides no protection whatsoever — this
> decorator is the only thing standing between a guild member and the production
> content tables. Every mutating command currently sets it; keep it that way.
> Read commands, including all of `/stats`, are open to anyone in the guild.

## The Supabase layer

`supabase_client.py` creates one module-level client from `SUPABASE_URL` /
`SUPABASE_KEY` and raises at import if either is missing.

`supabase_helpers.py` wraps it in generic CRUD:

| Function | Notes |
|---|---|
| `fetch_all(table, columns, filters, sort)` | `filters` values dispatch by type: `None` → `is null`, `list` → `in_`, else `eq`. `sort` takes `["-col"]` for descending |
| `create_record(table, data)` | |
| `update_record(table, id, data)` | Stamps `updated_at`. Returns the updated rows; `[]` means no row matched |
| `delete_record(table, id)` | Hard delete. **No command calls this any more** — the four `/delete_*` commands were removed 2026-08-27 |
| `soft_delete_record(table, id)` | Sets `archived_at`; delegates to `update_record` |

Bulk writes do **not** go through these. `/bulk_insert` and `/bulk_update` call
the `public.bulk_apply` database function through `azoth_logic/bulk_apply.py`, so
the whole payload lands in one transaction — see
[CONTENT_PIPELINE.md](CONTENT_PIPELINE.md#bulk-gotchas).

### Failures raise; only genuine emptiness returns `[]`

**Fixed 2026-08-26.** These helpers used to catch every exception and return
`[]` / `None`, so a missing table, an RLS denial, a network failure and an empty
result were indistinguishable — and every call site renders `[]` as "not found".

They now raise:

| Exception | Meaning |
|---|---|
| `SupabaseUnreadableError` | The loaded key **provably cannot read** this table. Raised *before* the query |
| `SupabaseQueryError` | The query reached Supabase and failed |
| `SupabaseError` | Base class; catch this to handle either |

`safe_interaction` already catches everything and posts the exception to Discord,
so errors reach the user with no per-command work.

#### The pre-flight guard

RLS denial is **not an exception** — PostgREST answers a blocked SELECT with HTTP
200 and an empty array. No amount of exception handling catches it. So
`fetch_all` checks the table against a verified list before querying:

```python
ANON_INSERT_ONLY = {"turns", "turn_nodes", "levelups", "reports"}
ANON_NO_POLICY   = {"rituals", "consumables"}
```

If `SUPABASE_ROLE` is anything other than `service_role` — including `unknown`,
which is treated as not-proven — reading one of these raises with the reason.

`SUPABASE_ROLE` comes from `supabase_client.py`, which reads the `role` claim
from the key's JWT payload (and recognises the newer `sb_secret_` /
`sb_publishable_` formats). `bot.py` prints it at startup.

**Keep the two sets in sync with
[DB_SCHEMA.md § RLS posture](DB_SCHEMA.md#rls-posture).** A table listed here that
is actually readable only costs a confusing error; one omitted costs silent wrong
answers.

#### The one place that still catches

`autocomplete_from_table` catches `SupabaseError`, logs it, and returns `[]` —
Discord autocomplete has no error channel, so a raised exception would just yield
no suggestions with nothing to explain why. **If an autocomplete is silently
empty, check the bot console.**

### Display names

Every content type uses `name`. Rituals were the one exception
(`challenge_name`) and that table is retired, so `get_display_name(obj, type)`
and `name_column_for(content_type)` are now trivial — kept so a future exception
has one place to live rather than being re-derived at 60 call sites.

### Deck membership

`deck_contents` is a universal join table — `(deck_id, content_type, content_id)`
— so one deck can hold cards, aspects and events together.

⚠️ It also has **`position` and `weight`** columns, which the bot never writes.
`add_to_deck_by_ref` inserts only the three keys above, so every bot-added entry
takes the column defaults. Both columns are read by the `decks_with_contents`
view that the game / Codex editor consumes, and `weight` looks like draft
probability — so this may be silently affecting how bot-added content is drawn.
Confirm the intended defaults before adding more content this way.

Because names collide across content types, autocomplete encodes an explicit
reference rather than a bare name:

```
"Diversity (Card #447)"   ← what the user sees
"card:447"                ← what Discord sends back
```

`encode_item_ref` / `parse_item_ref` / `make_item_label` handle this.
`add_to_deck` and `remove_from_deck` accept either form; a raw typed name falls
back to `_resolve_name_to_ref`, which takes the **first match** in the priority
order `card, aspect, event, ritual, consumable`. That fallback is legacy and can
pick the wrong item — always select from autocomplete.

## Autocomplete

`autocomplete_from_table(table, input, column, filters)` fetches the table and
filters in Python. Simple, and fine at current content volumes (~400 cards), but
it is a full table read on **every keystroke** and it inherits `fetch_all`'s
silent-empty behaviour: a broken autocomplete looks like "no matches".

## The daily-update background task

`daily_update.py` is the only module that touches the cog's lifecycle. It
monkey-patches `cls.__init__` to start a `tasks.loop(minutes=10)` and a
startup catch-up pass:

```python
original_init = cls.__init__
def new_init(self, bot):
    original_init(self, bot)
    self._daily_update_task.start(self)
    bot.loop.create_task(_check_missed_updates(self))
cls.__init__ = new_init
```

Both call the same `_send_due_channels(bot, source)` sweep, which decides per
channel whether a report is due and sends it.

⚠️ **That sweep must never let an exception escape.** `tasks.Loop` tolerates only
five connection-ish exception types; anything else it prints and **re-raises**,
which ends the loop for the life of the process. Since the bot is hand-started
and rarely restarted, one unhandled error stopped the daily report indefinitely
— see [ANALYTICS.md](ANALYTICS.md#three-bugs-all-fixed). The sweep therefore
catches per channel and per cycle, which is deliberate rather than sloppy.

State lives in `daily_update_state.json` at the repo root (gitignored), written
atomically via `mkstemp` + `os.replace`. See [ANALYTICS.md](ANALYTICS.md#the-daily-report)
for the scheduling and deduplication rules — several of the comments there record
real bugs that were fixed and should not be reintroduced.

## Known structural issues

Documented so they aren't rediscovered. Struck-through rows have since been
fixed; the rest are still open.

| Issue | Location | Effect |
|---|---|---|
| ~~`safe_interaction` duplicated verbatim~~ | — | **Fixed 2026-08-26** — `utils/interaction_helpers.py` deleted |
| ~~`rituals.py` / `consumables.py` never registered~~ | — | **Fixed 2026-08-26** — both retired and deleted |
| ~~`fetch_all` returns `[]` on any error~~ | `supabase_helpers.py` | **Fixed 2026-08-26** — failures now raise |
| ~~`soft_delete_record` always returned `None`~~ | `supabase_helpers.py` | **Fixed 2026-08-26** — `/delete_deck` and `/delete_hero` reported failure on every success |
| `game_stats` table does not exist | `stats.py` version autocomplete | Autocomplete always returns nothing. Now logs the reason to the console |
| Leaderboard sorts client-side after a capped fetch | `stats.py` | Hits PostgREST's 1000-row default against a larger view |
| Hardcoded deck IDs | `decks.py` `stage` / `merge_staging` | IDs 20/21/22/3; deck 21 ("Staging") is **archived** and 22 is "Testing Fates" despite the constant being `ASPECT_DECK_ID`. **Both commands hidden 2026-08-27** — the bug is parked, not fixed. Derive the decks from `type`/`usage_type` before restoring them |
| Six pseudo-docstrings placed above `def` | `supabase_helpers.py` | Not real docstrings; `help()` shows nothing |
| Stale comment | `supabase_storage.py` `download_image` | Says "timestamped filename"; it writes a flat name |
| `add_to_deck` never sets `position` or `weight` | `supabase_helpers.py` | Bot-added deck entries take column defaults; `weight` appears to be draft probability |
| ~~No tests, no linter config~~ | — | **Fixed 2026-08-26/27** — 620 pytest tests; `test_command_registration.py` runs `pyflakes` over the whole tree. Still no CI |
| ~~The render cache grows without bound~~ | — | **Fixed 2026-08-27** — size-capped LRU eviction on write (art 300 MB, renders 400 MB), and `/cache` now reaches `stats()` / `clear()`. See [CARD_RENDERING.md § Eviction](CARD_RENDERING.md#eviction) |
