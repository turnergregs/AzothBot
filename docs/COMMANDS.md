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
| `/render` | — | `name`*, `show_upgrade?` — any card, aspect or rite |
| `/rules` | — | `name`* — the mechanics JSON, as a file |

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
keystroke and reading the tables live costs 0.85–2.3s. `create_*` and
`update_*` invalidate it, so new content is selectable immediately; the TTL is
the backstop for edits made in the Codex or by direct SQL.

### ⚑ Only live content is findable

Since 2026-08-28, `/show`, `/render`, `/rules`, `/search` and the `/update_*`
name pickers cover **only content that is reachable in game**. That is
**233 of 626 rows** — 154 of 400 cards, 58 of 149 aspects, 21 of 77 rites. The
rest is retired work that nobody can encounter, and it used to fill two thirds
of every autocomplete.

`cards`, `aspects` and `events` have **no `archived_at`** — they hard-delete,
and an unused row just sits there. So liveness is inferred:

> **live == in at least one deck whose `archived_at` is null**

which is exactly what the game does: it reaches all of its content through
`decks_with_contents` and drops archived decks on sync
([CONTENT_LOADING.md](../../azoth/docs/CONTENT_LOADING.md)). The deck membership
is read alongside the index, cached in the same snapshot, and costs ~0.4s per
refresh.

- **`/add_to_deck` is deliberately NOT filtered.** Adding a card to a deck is
  what makes it live, so that picker still offers every row — it is the way
  back, and the only one, since the `/delete_*` commands were retired.
- **A retired item does not read as missing.** Pasting its ref into `/show`
  answers *"X is not in any active deck… add it to a deck to bring it back"*,
  not "could not find" — which for a row that plainly exists would read as data
  loss.
- **If the deck read fails, nothing is filtered.** An empty live set means "the
  bot cannot see the decks", not "no content is live"; concluding the latter
  would hide the entire catalogue behind a message that is false for all 626
  rows. Same stance the game's importer takes on an empty reconcile set.
- **Heroes filter their own `archived_at`** — 19 of 20 hero rows are archived
  and all 20 were being offered.

`/show` returns a **detail embed**, accented in the item's own colour: rules text
as the body, and only the attributes that define the thing — element, valence,
subtypes, split face or foresight, whichever apply. Empty and null
values are dropped rather than printed.

It used to dump the raw database row as JSON. Deliberately **not** shown now:

| Omitted | Why |
|---|---|
| `upgrades` | A nested blob that dwarfs the card, and its rules text reads as the card's own. **`/rules` carries it** |
| `created_at` / `updated_at` / `created_by` | Audit metadata |
| `image` / `image_data` | Rendering internals — `/render` is the view |
| `actions` / `triggers` / `properties` | `jsonb`, and past Discord's 2000-char limit on their own |
| `attunement` (aspects) | Every live aspect is 1 — it distinguishes nothing (dropped 2026-08-28) |

Its rules text carries the same `{...}` placeholder substitution the rendered
face does — Recollection reads *Create last used Rite (None)*, not
`({last_rite})`. `/show` is the one surface that does not draw its text, so it
resolves them itself; see
[CARD_RENDERING.md § Display placeholders](CARD_RENDERING.md#display-placeholders).
`/search` still matches the **raw** authored text, so `{luck_chance` is findable.

`type` is shown only when it is *not* `spell` — 328 of 400 cards are spells, so
printing it on each is noise; a catalyst is the exception worth naming. A null
element reads as **Colourless** rather than blank, which is what 64 cards have.

### `/render` and the upgrade comparison

`/render` draws the single face by default. **`show_upgrade:True`** draws it
**beside its upgraded state**, captioned `Base` / `Upgraded` — the only view that
shows what an upgrade actually does. 197 of 400 cards have one.

Opt-in rather than automatic: the plain face is what `/render` is usually for,
and a comparison costs a second face's art and drawing. `show_upgrade:True` on a
card with no upgrade says so rather than returning one face and leaving you to
wonder which one it is.

(Discord option names are lowercase with no spaces, so the flag reads
`show_upgrade` rather than `Show Upgrade`.)

**A card can upgrade into an aspect** — it transforms and moves to the aspect
bar — so the upgraded face is drawn by the *aspect* renderer and captioned
`Upgraded (Aspect)`. 28 cards do this. Drawing that face as a card would show a
card that cannot exist.

**The comparison animates.** Both faces run their eigenfunction art at once, and
a still side simply holds its frame while the other moves — which is the common
shape, since an animated `.exr` card often upgrades into a flat-art aspect. When
neither side animates it falls back to a PNG (16 of the 197).

This is the one multi-face layout here that is not static, and it earns it: two
faces, not a hundred. Measured across 60 comparisons — **largest GIF 0.71 MB
against Discord's 10 MB cap, slowest render 4.5s against a 30s timeout.** GIFs
are cached like any other animated render, so a repeat is instant.

`show_upgrade:False` is the default and gives the single face on its own.

The merge follows the engine exactly (`GameContentData.apply_upgrade`):
`_added` keys append and everything else replaces, replacements land before
additions, and a string `x_added` is dropped when `x` is replaced in the same
entry. Tiers are cumulative. `azoth_logic/upgrades.py` is the transcription, and
the game is the authority — a disagreement is a bug there, not here.

---

### `/rules`

The four `jsonb` fields `/show` deliberately omits — `actions`, `triggers`,
`properties`, `upgrades` — as a **file attachment**, which has no 2,000-character
limit. The richest card currently runs to 5.8 KB, nearly 3× what a message can
carry, which is why this is a file and not an embed.

Empty fields are dropped rather than printed as `[]`: "no triggers" and "does not
use triggers" are different claims. 27 cards have no mechanics at all, and the
command says so instead of sending an empty file.

`upgrades` is included, and is often the most interesting part — **a card can
upgrade into an aspect**, transforming and moving to the aspect bar, and the
upgrade payload is the only place that transformation is visible.

Until this existed, the documented way to read any of these was "query the
database directly" — for the fields that actually define the mechanic.

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
- To read `actions`, `triggers` or `properties`, use **`/rules`**. `/show` omits
  them on purpose — each is past Discord's 2000-char limit on its own. `/search`
  also reaches inside them.

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

**To retire content, pull it from the every unarchived deck** with
`/remove_from_deck`. That is what the balance workflow was for; it leaves the
row intact and recoverable, and since 2026-08-28 it also **hides the item from
every lookup command** — see [Only live content is
findable](#-only-live-content-is-findable). Removing from the last live deck is
now the closest thing to a delete, and `/add_to_deck` undoes it.

Restoring a real delete command means deciding what "delete" should mean first:
the honest version adds `archived_at` to the three content tables and switches
the commands to `soft_delete_record`. The `content_index` / `/search` filtering
that entry also called for **is now done** — by deck membership rather than by a
column, since the column still does not exist.

---

## Decks

| Command | Access | Parameters |
|---|---|---|
| `/decks` | — | *(none)* — every unarchived deck, grouped by usage type |
| `/create_deck` | 🔒 | `name`, `description`, `type`*, `usage_type`* |
| `/update_deck` | 🔒 | `name`*, `new_name?`, `description?`, `type?`*, `usage_type?`*, `archived?` |
| `/show_deck` | — | `name`* — details plus contents |
| `/render_deck` | — | `name`* — every card, tiled, static (120s timeout) |
| `/render_hand` | — | `name`*, `hand_size?` (default 6) — a fanned sample draw, static |

`/decks` lists the **8 live decks** with their ids and contents, grouped by usage
type. Unarchived only — 20 of the 28 rows are archived, and a list that is
two-thirds dead content is not one you can scan; `/show_deck` still opens an
archived deck by name. The **id** is shown because it is the thing you cannot get
anywhere else and the thing that keeps mattering: `/stage` and `/merge_staging`
are parked precisely because they pinned ids that had since moved.

`type` and `usage_type` autocomplete from `azoth_logic/taxonomy.py`, not from the
database. The six taxonomy tables were dropped 2026-08-27 — see
[Taxonomy](#taxonomy-lives-in-code).

`content_type` was **removed** the same day along with the `decks.content_type`
column. `deck_contents` carries the type per row, so a deck holds anything and
there was nothing left for a deck-level type to mean.

The deck pickers on `/create_card`, `/create_aspect` and `/create_rite` used to
narrow by that column and now list **every unarchived deck**.

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

## Taxonomy lives in code

The small fixed vocabularies — card elements, card types, card attributes, deck
types, deck usage types — are in `azoth_logic/taxonomy.py`. They were six
database tables (`card_elements`, `card_types`, `card_attributes`, `deck_types`,
`deck_content_types`, `deck_usage_types`) until 2026-08-27.

They are enumerations the engine defines, and the game already hardcodes every
one of them in GDScript. The tables were a second copy that could only lag: a new
element is a code change, and the database row was the half nobody was reminded
to add. It showed — `deck_usage_types` had no `rite`, and `rite` is a large part
of why the Rites deck was invisible to `draft_deck_view` for its entire life.

**Autocomplete offers the canonical list plus anything actually in use.**
Hardcoding drifts too, so a value present in the data can never be missing from
the picker even when someone forgets to update the file. And because Discord
autocomplete is a suggestion rather than a constraint, a genuinely new value can
still be typed in to create the first row that uses it.

Adding a value: edit `taxonomy.py`, keeping it in step with the game constant
named in the comment above each list.

**"In use" means in use now.** The deck vocabularies only union values from
**unarchived** decks. Without that, removing a value would be impossible — the
retired `reactant` / `boon_a` / `boon_b` / `boon_c` usage types all still have
decks carrying them (32–35, all archived), and the union would hand them
straight back.

---

## Bulk ingest

| Command | Access | Parameters |
|---|---|---|
| `/bulk_insert` | 🔒 | `json_file` (attachment) |
| `/bulk_update` | 🔒 | `json_file` (attachment) |

Both take a JSON object keyed by table name, with a list of records per table.
`bulk_insert` inserts rows as given; `bulk_update` matches existing rows **by
`name`** and applies partial field updates (use `new_name` to rename).

**Both are transactional** (2026-08-27). The payload goes to the `bulk_apply`
database function and applies in one statement, so a rejected record rolls the
whole payload back — there is no half-applied state. The reply names the table,
the record index and what was wrong with it.

This replaced a Python loop that issued one request per record. PostgREST wraps
each *request* in a transaction, so 60 records meant 60 transactions and a
failure at record 40 left 39 rows written — with no command able to remove them
once `/delete_*` was retired the same day.

Both reply with an embed describing the action: `/bulk_update` gives a per-record
field diff of the **player-facing** fields — the mechanic blobs (`actions`,
`triggers`, `properties`, `upgrades`) collapse into one note, and only when the
rules text did not move — **and renders the updated items**; `/bulk_insert` lists each new row
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
optional and they AND together. The pool is **live content only** (233 rows, not
626) — see [Only live content is findable](#-only-live-content-is-findable).

**`query` mirrors the Codex's search** (`content_search.gd` in the game repo): it
scans name, rules text, type, subtypes, valence and attunement — **and deep-
searches the `actions` / `triggers` / `properties` JSON**. That last part is the
useful bit: `query: Magnify` finds every card carrying that property even though
the word appears in no flat column, and `query: {link.size}` finds every card
using that placeholder.

`subtype`, `card_type` and `action` autocomplete from live content, so a new
subtype or action shows up without a code change — and they are scoped to the
same live pool `/search` covers, so a suggestion cannot return zero results. `action` walks nested actions
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
| `/stats player` | — | `player`* — a full player card |
| `/stats hero` | — | — |
| `/stats version` | — | — |
| `/stats scoreboard` | — | — |
| `/stats draft_pool` | — | — |
| `/stats draft_rates` | — | `limit?` (default 15), `order?` (most/least), `item_type?` (card/aspect/event) |
| `/daily_update` | 🔒 | `enabled`, `send_time?` (HH:MM, default 12:00), `utc_offset?` (default -6) |

All `/stats` subcommands reply with an **embed** — an aligned table for the
multi-row views, labelled fields for the single-row ones. They are open to anyone
in the guild. They dumped raw `json.dumps` into a code fence until 2026-08-27,
which was complete and nearly unreadable.

Three things the formatting fixes rather than decorates:

- **`avg_combo_log10` renders as `10^4.8`.** It is an order of magnitude, not an
  average. Printed raw as `4.78` beside `max_combo: 652298` it reads as an
  average combo of about five, which is the exact misreading
  [ANALYTICS.md](ANALYTICS.md) warns about.
- **Every reply carries a footer** naming the cutoff and how many games are
  behind the number. `/stats version` says **"all versions"** instead —
  `version_info_view` is the one view with no cutoff, because comparing versions
  is its whole job, and claiming the cutoff over a table showing 0.7.0 rows
  would be a lie.
- **Dropped columns are named.** A table too wide for a phone loses columns from
  the right, and the footer says which — the same rule `/search` follows when it
  truncates.

Big combos are compacted (`652.3K`, `53.3T`), playtime reads as time (`13m`,
`2.1h`), and an empty value is `—` rather than `None`.

`/stats scoreboard` (2026-08-31) reports the end-of-turn bonus axes — Precision,
Overdraw, Overload — as three tables of act x axis: how often each crossed its
threshold, what it averaged against the threshold in force, and which one
actually paid. Only the winner pays, never the sum, so the "which axis paid"
row sums to 100%: an axis can clear its threshold constantly and still never pay
because another crowds it out, which is invisible in the hit rates alone.

It reads `turn_scoreboard_view` and is the **second** command after
`/stats version` whose footer does not claim the cutoff. The view filters
`bonus_key is not null` rather than a version — the columns postdate `0.9.1`
and are NULL on every earlier run, so they date themselves. An unmigrated view
is named in the reply rather than shown as "no data": "not migrated" and "no
turns yet" are different problems.

The turn counts sit in a caption under the hit-rate table instead of a fifth
column, and the thresholds under the counts table, purely for width — both
tables land at 21 characters, inside the measured wrap point. `sum()` of
`turns_sampled` is NOT the sample size: every scored turn produces one row per
axis plus a rollup row, so the column totals six times the real count.
`stats_format.scoreboard_sample()` reads it off a single rollup row instead.

`/stats draft_pool` was `/stats draft_deck` until 2026-08-27. It still reads
`draft_deck_view` — the command was renamed, the view was not, so the bot works
whether or not the migration below has been applied. Its `events` column was
permanently zero until `db/migrations/2026-08-27_draft_pool_include_rites.sql`
in the game repo widened the view to `usage_type in ('draft', 'rite')`; the Rites
deck (id 36, 21 events) is `usage_type = 'rite'` and was excluded by a filter
that predates that usage type. Until that migration runs, `events` reads 0.

> The views behind `/stats` were rebuilt on 2026-08-26 — a cutoff enforced in
> one place, `restart` runs and co-op duplicates excluded, and combo averaged in
> log space as **`avg_combo_log10`** (an order of magnitude, not a linear mean).
> The cutoff moved to `0.9.0` on 2026-08-28. Read [ANALYTICS.md](ANALYTICS.md)
> before quoting a number: the trustworthy dataset is still only a couple of runs
> deep, so most of these will be thin or empty until there is play at `0.9.0`+.

`/daily_update` is per-channel: enabling it in a channel registers that channel
with its own send time, and the report covers the previous day (CST). Disabling
preserves the dedup date so re-enabling the same day doesn't re-send.

The report reads the turn-grain tables for links-per-turn, boss outcomes and
level-up pick rates. Those are **service-role only** — on an anon key those
sections say "unavailable" rather than silently reporting zero. See
[ANALYTICS.md](ANALYTICS.md#the-daily-report).

---

## Quick index

**Open to any guild member:** `/show`, `/render`, `/rules`, `/search`,
`/decks`, `/show_deck`, `/render_deck`, `/render_hand`, `/cache status`, and all
of `/stats`.

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
