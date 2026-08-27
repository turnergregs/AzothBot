# Command Reference

Every slash command AzothBot registers. All are scoped to the dev guild
(`DEV_GUILD_ID`) — none are global.

**🔒 = `require_authorized=True`.** Restricted to `AUTHORIZED_USER_IDS`. Because
the deployed bot uses a service-role key, these write directly to production with
no database-level guard. See [ARCHITECTURE.md § safe_interaction](ARCHITECTURE.md#safe_interaction).

**⚠️ = registered but broken.** Details in the notes.

---

## Content lookup

| Command | Access | Parameters |
|---|---|---|
| `/show` | — | `name`* — any card, aspect or rite |
| `/render` | — | `name`* — any card, aspect or rite |

**One command each, across all content types.** Replaced the six typed
`/get_*` and `/render_*` commands on 2026-08-26.

The autocomplete disambiguates by type and id — `Diversity (Card #447)` — which
matters because **17 names exist on more than one content type**: "Anima
Shrinker" is both a Card and an Aspect. The old split handled that by making you
pick the right command; now the label does it.

The value behind each choice is the same encoded ref the deck commands use
(`card:447`), so one lookup path serves both. Rites label as **Rite** while their
ref still encodes `event:82`, matching the naming boundary.

Autocomplete is served from an in-process index
(`azoth_logic/content_index.py`) with a 60s TTL — Discord fires it on every
keystroke and reading the three tables live costs 0.85–2.3s. `create_*` and
`update_*` invalidate it, so new content is selectable immediately; the TTL is
the backstop for edits made in the Codex or by direct SQL.

`/show` returns a **detail embed**, accented in the item's own colour: rules text
as the body, and only the attributes that define the thing — element, valence,
subtypes, split face, attunement or foresight, whichever apply. Empty and null
values are dropped rather than printed.

It used to dump the raw database row as JSON. Deliberately **not** shown now:

| Omitted | Why |
|---|---|
| `upgrades` | A nested blob that dwarfs the card, and its rules text reads as the card's own |
| `created_at` / `updated_at` / `created_by` | Audit metadata |
| `image` / `image_data` | Rendering internals — `/render` is the view |
| `actions` / `triggers` / `properties` | `jsonb`, and past Discord's 2000-char limit on their own |

`type` is shown only when it is *not* `spell` — 328 of 400 cards are spells, so
printing it on each is noise; a catalyst is the exception worth naming. A null
element reads as **Colourless** rather than blank, which is what 64 cards have.

---

## Cards

| Command | Access | Parameters |
|---|---|---|
| `/create_card` | 🔒 | `name`, `type`*, `valence`, `element`*, `text`, `attributes?`, `subtypes?`, `deck?`*, `quantity?` |
| `/update_card` | 🔒 | `name`*, `new_name?`, `type?`*, `valence?`, `element?`*, `text?`, `subtypes?`, `attributes?`, `regenerate_image?` |

`*` = autocompleted. `?` = optional.

- `attributes` and `subtypes` are comma-separated strings.
- `create_card` optionally adds the new card straight into a deck (`deck` +
  `quantity`) in the same call.
- `update_card` only regenerates art when `regenerate_image=True`; otherwise the
  existing image is kept. When it does, the newly uploaded art is dropped from
  the render cache first — Storage names are flat and upserting, so the same
  filename now holds different bytes.
- `create_card` and `update_card` (with `regenerate_image`) reply with the
  rendered card. To look one up without touching it, use `/show` or `/render`.
- To read `actions`, `triggers` or `properties`, query the database directly.
  `/show` omits them on purpose — each is past Discord's 2000-char limit on its
  own. `/search` does reach inside them.

## Aspects

| Command | Access | Parameters |
|---|---|---|
| `/create_aspect` | 🔒 | `name`, `text`, `attunement`, `image?`, `deck?`*, `quantity?` |
| `/update_aspect` | 🔒 | `name`*, `new_name?`, `text?`, `attunement?`, `image?` |

Aspects take an existing image name in the `aspectimages` bucket rather than
generating art. `update_aspect` has its `regenerate_image` parameter commented
out. Render one with `/render`.

## Rites

> **"Rite" is the current name for what the database calls an "event."** The
> commands and all new code say rite; the `events` table, the `event`
> `content_type` and the `eventimages` bucket keep the old name until a
> migration. See [CARD_RENDERING.md](CARD_RENDERING.md#naming-rite-vs-event).

| Command | Access | Parameters |
|---|---|---|
| `/create_rite` | 🔒 | `name`, `text`, `foresight`, `deck?`*, `quantity?` |
| `/update_rite` | 🔒 | `name`*, `new_name?`, `text?`, `foresight?`, `regenerate_image?` |

## Heroes ⚠️ RETIRED

All `/*_hero` commands were unregistered 2026-08-26. `azoth_commands/heroes.py`
still exists but its attacher is **deliberately** not called from
`azoth_commands/__init__.py` — unlike the rituals/consumables case, this is not
an oversight to fix. Hero cards were also never ported to the new renderer, so
`/render_hero` would draw the wrong frame.

## Deletion — removed 2026-08-27

**There is no longer any command that deletes content.** `/delete_card`,
`/delete_aspect`, `/delete_rite` and `/delete_deck` are all commented out in
their modules, and `tests/test_command_registration.py` asserts they stay off the
cog.

Three of the four hard-deleted. `cards`, `aspects` and `events` have **no
`archived_at` column** — the row was gone, with no undo and no backups configured
from this repo. Worse, the game's `prune_content_dirs()` reads a missing row as
the deletion signal, so one misclick also tore the item out of the offline
snapshot in `assets/game_data/` (game repo,
[CONTENT_LOADING.md](../../azoth/docs/CONTENT_LOADING.md) § Archive-based deletion).

`/delete_deck` was the safe member of the set — it set `archived_at` via
`soft_delete_record` — and went with the others for consistency. **Archiving a
deck is still possible:** `/update_deck` takes an `archived` parameter.

**To retire content, pull it from the draft decks** with `/remove_from_deck`.
That is what the balance workflow was for; it leaves the row intact and
recoverable. Restoring a delete command means deciding what "delete" should mean
first: the honest version adds `archived_at` to the three content tables,
switches the commands to `soft_delete_record`, filters archived rows out of
`content_index` / `/search`, and teaches the game's `prune_content_dirs()` to
read the column instead of inferring deletion from a missing row.

---

## Decks

| Command | Access | Parameters |
|---|---|---|
| `/create_deck` | 🔒 | `name`, `description`, `type`*, `content_type`*, `usage_type`* |
| `/update_deck` | 🔒 | `name`*, `new_name?`, `description?`, `type?`*, `usage_type?`*, `archived?` |
| `/show_deck` | — | `name`* — details plus contents |
| `/render_deck` | — | `name`* — every card, tiled, static (120s timeout) |
| `/render_hand` | — | `name`*, `hand_size?` (default 6) — a fanned sample draw, static |

`type` / `content_type` / `usage_type` autocomplete from `deck_types`,
`deck_content_types` and `deck_usage_types`. ⚠️ Those three tables read as empty
with an anon key — verify against the service key before trusting an empty
autocomplete.

### Deck curation

| Command | Access | Parameters |
|---|---|---|
| `/add_to_deck` | 🔒 | `deck_name`*, `item_name`*, `quantity?` |
| `/remove_from_deck` | 🔒 | `deck_name`*, `item_name`*, `quantity?` |

**Always pick items from autocomplete.** Typed names fall back to a first-match
lookup across content types and can resolve to the wrong item — see
[ARCHITECTURE.md § Deck membership](ARCHITECTURE.md#deck-membership).

### Hidden 2026-08-27: `/stage`, `/postpone`, `/merge_staging`

The **balance workflow** — pulling content out of the live draft pool and putting
it back — is commented out in `azoth_commands/decks.py`, not deleted. What it did:

- **`/postpone`** — removed every copy of an item from all active base draft decks
  and moved them into the "Removed" decks. Used when benching content.
- **`/stage`** — moved every copy from live draft decks into the Staging deck (or
  added it if missing). A holding area for content being reworked.
- **`/merge_staging`** — emptied Staging back into live draft decks, routing each
  item by type: aspects → the aspect deck, cards with null valence *and* null
  element → Combo Cards, everything else → Base Draft Deck.

⚠️ **Do not simply uncomment them.** `/stage` and `/merge_staging` hardcode deck
IDs (`21` Staging, `22`, `20` Combo Cards, `3` Base Draft Deck) and the database
has moved: deck 21 is **archived** and deck 22 is named "Testing Fates" despite
the constant being called `ASPECT_DECK_ID`. Fix the IDs — ideally by deriving the
decks from `type`/`usage_type` the way `draft_deck_view` does — before restoring.

The bodies must stay **commented**, not merely unattached:
`tests/test_command_registration.py` fails a command that a module defines but
never assigns onto the cog, and a separate test asserts these three names stay
off the cog.

---

## Bulk ingest

| Command | Access | Parameters |
|---|---|---|
| `/bulk_insert` | 🔒 | `json_file` (attachment) |
| `/bulk_update` | 🔒 | `json_file` (attachment) |

Both take a JSON object keyed by table name, with a list of records per table.
`bulk_insert` inserts rows as given; `bulk_update` matches existing rows **by
`name`** and applies partial field updates (use `new_name` to rename).

Neither is transactional — rows are applied one at a time and a failure part-way
leaves earlier rows written.

Both reply with an embed describing the action: `/bulk_update` gives a per-record
field diff **and renders the updated items**; `/bulk_insert` lists each new row
with its id and attributes but does **not** render, because art is uploaded after
an insert. Errors are listed with the rest, and any truncation is announced. See
[CONTENT_PIPELINE.md](CONTENT_PIPELINE.md#what-the-reply-tells-you).

Full format specification and workflow: [CONTENT_PIPELINE.md](CONTENT_PIPELINE.md).

---

## Search

| Command | Access | Parameters |
|---|---|---|
| `/search` | — | `query?`, `content_type?`, `element?`, `valence?`, `subtype?`*, `card_type?`*, `action?`*, `sort?`, `limit?` |

Finds cards, aspects and rites and renders the matches as a grid. Every filter is
optional and they AND together.

**`query` mirrors the Codex's search** (`content_search.gd` in the game repo): it
scans name, rules text, type, subtypes, valence and attunement — **and deep-
searches the `actions` / `triggers` / `properties` JSON**. That last part is the
useful bit: `query: Magnify` finds every card carrying that property even though
the word appears in no flat column, and `query: {link.size}` finds every card
using that placeholder.

`subtype`, `card_type` and `action` autocomplete from live content, so a new
subtype or action shows up without a code change. `action` walks nested actions
and triggers, so a `Recall` inside a `Split` inside a trigger still matches. It
does **not** match properties — the free-text query is what reaches those.

Results are **static**, like `/render_deck`: twenty animated cards would be tens
of megabytes and unreadable at grid scale. Default 20 results, hard cap 40 —
rendering is ~0.7s per item on a cold cache. Truncation is always reported
(`showing 20 of 47`), never silent.

---

## Cache

| Command | Access | Parameters |
|---|---|---|
| `/cache status` | — | — |
| `/cache clear` | 🔒 | `which?` — everything / renders only / art only |

The on-disk render cache (`cache/`, gitignored). `status` shows both caches
against their size caps; `clear` drops them — always safe, but the next render of
each item pays full price again.

Eviction is automatic and size-capped, so clearing by hand is for forcing a
redraw rather than for reclaiming space. **`renders` alone is usually the one you
want** — art is expensive to re-download and bounded anyway, while a render is
what you would want to force. Full policy:
[CARD_RENDERING.md § Eviction](CARD_RENDERING.md#eviction).

---

## Analytics

| Command | Access | Parameters |
|---|---|---|
| `/stats active_players` | — | `limit?` (default 25) |
| `/stats leaderboard` | — | `limit?` (default 10), `player?`*, `hero?`*, `version?`* |
| `/stats player` | — | `player`* |
| `/stats hero` | — | — |
| `/stats version` | — | — |
| `/stats draft_pool` | — | — |
| `/stats draft_rates` | — | `limit?` (default 15), `order?` (most/least), `item_type?` (card/aspect/event) |
| `/daily_update` | 🔒 | `enabled`, `send_time?` (HH:MM, default 12:00), `utc_offset?` (default -6) |

All `/stats` subcommands dump raw JSON from a database **view** into a code block.
They are open to anyone in the guild.

`/stats draft_pool` was `/stats draft_deck` until 2026-08-27. It still reads
`draft_deck_view` — the command was renamed, the view was not, so the bot works
whether or not the migration below has been applied. Its `events` column was
permanently zero until `db/migrations/2026-08-27_draft_pool_include_rites.sql`
in the game repo widened the view to `usage_type in ('draft', 'rite')`; the Rites
deck (id 36, 21 events) is `usage_type = 'rite'` and was excluded by a filter
that predates that usage type. Until that migration runs, `events` reads 0.

> The views behind `/stats` were rebuilt on 2026-08-26 — cutoff enforced at
> `0.8.2`, `restart` runs and co-op duplicates excluded, and combo averaged in
> log space as **`avg_combo_log10`** (an order of magnitude, not a linear mean).
> Read [ANALYTICS.md](ANALYTICS.md) before quoting a number: the trustworthy
> dataset is still only a couple of runs deep, so most of these will be thin or
> empty until there is play at `0.8.2`+.

`/daily_update` is per-channel: enabling it in a channel registers that channel
with its own send time, and the report covers the previous day (CST). Disabling
preserves the dedup date so re-enabling the same day doesn't re-send.

The report reads the turn-grain tables for links-per-turn, boss outcomes and
level-up pick rates. Those are **service-role only** — on an anon key those
sections say "unavailable" rather than silently reporting zero. See
[ANALYTICS.md](ANALYTICS.md#the-daily-report).

---

## Quick index

**Open to any guild member:** `/show`, `/render`, `/search`, `/show_deck`,
`/render_deck`, `/render_hand`, `/cache status`, and all of `/stats`.

**Authorized users only:** every `create_*` and `update_*`, `/add_to_deck`,
`/remove_from_deck`, `/cache clear`, `/bulk_insert`, `/bulk_update`, `/daily_update`.

**Removed 2026-08-26:** all 10 ritual and consumable commands, along with their
modules. Both content types are retired — see [AZOTH.md](AZOTH.md#ritual-means-two-different-things-one-of-them-is-dead).

**Renamed 2026-08-27:** `/get` → `/show`, `/get_deck` → `/show_deck`,
`/stats draft_deck` → `/stats draft_pool`. The old names are asserted gone in
`tests/test_command_registration.py`.

**Removed 2026-08-27:** all four `/delete_*` commands — see
[Deletion](#deletion--removed-2026-08-27). Nothing deletes content now.

**Hidden 2026-08-27:** `/stage`, `/postpone`, `/merge_staging` — commented out
in `azoth_commands/decks.py`, see [Deck curation](#hidden-2026-08-27-stage-postpone-merge_staging).

**Retired 2026-08-26:** all `/*_hero` commands. `heroes.py` is deliberately not
attached in `azoth_commands/__init__.py` — see
[CARD_RENDERING.md § Retired](CARD_RENDERING.md#retired).
