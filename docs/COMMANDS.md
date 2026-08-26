# Command Reference

Every slash command AzothBot registers. All are scoped to the dev guild
(`DEV_GUILD_ID`) — none are global.

**🔒 = `require_authorized=True`.** Restricted to `AUTHORIZED_USER_IDS`. Because
the deployed bot uses a service-role key, these write directly to production with
no database-level guard. See [ARCHITECTURE.md § safe_interaction](ARCHITECTURE.md#safe_interaction).

**⚠️ = registered but broken.** Details in the notes.

---

## Cards

| Command | Access | Parameters |
|---|---|---|
| `/create_card` | 🔒 | `name`, `type`*, `valence`, `element`*, `text`, `attributes?`, `subtypes?`, `deck?`*, `quantity?` |
| `/update_card` | 🔒 | `name`*, `new_name?`, `type?`*, `valence?`, `element?`*, `text?`, `subtypes?`, `attributes?`, `regenerate_image?` |
| `/get_card` | — | `name` |
| `/delete_card` | 🔒 | `name` |
| `/render_card` | — | `name`* |

`*` = autocompleted. `?` = optional.

- `attributes` and `subtypes` are comma-separated strings.
- `create_card` optionally adds the new card straight into a deck (`deck` +
  `quantity`) in the same call.
- `update_card` only regenerates art when `regenerate_image=True`; otherwise the
  existing image is kept.
- `get_card` returns JSON with `actions`, `triggers` and `properties` **stripped**
  (`record_to_json` in `helpers.py`) so the reply fits Discord's 2000-char limit.
  To see those fields, query the database directly.

## Aspects

| Command | Access | Parameters |
|---|---|---|
| `/create_aspect` | 🔒 | `name`, `text`, `attunement`, `image?`, `deck?`*, `quantity?` |
| `/update_aspect` | 🔒 | `name`*, `new_name?`, `text?`, `attunement?`, `image?` |
| `/get_aspect` | — | `name` |
| `/delete_aspect` | — 🔒 | `name` |
| ~~`/render_aspect`~~ | ⚠️ | Commented out in `aspects.py:201` |

Aspects take an existing image name in the `aspectimages` bucket rather than
generating art. `update_aspect` has its `regenerate_image` parameter commented
out.

## Events

| Command | Access | Parameters |
|---|---|---|
| `/create_event` | 🔒 | `name`, `text`, `foresight`, `deck?`*, `quantity?` |
| `/update_event` | 🔒 | `name`*, `new_name?`, `text?`, `foresight?`, `regenerate_image?` |
| `/get_event` | — | `name` |
| `/delete_event` | 🔒 | `name` |
| `/render_event` | — | `name`* |

## Heroes

| Command | Access | Parameters |
|---|---|---|
| `/create_hero` | 🔒 | `name`, `text`, `r`, `g`, `b` |
| `/update_hero` | 🔒 | `name`*, `new_name?`, `text?`, `r?`, `g?`, `b?`, `regenerate_image?` |
| `/get_hero` | — | `name` |
| `/delete_hero` | 🔒 | `name` |
| `/render_hero` | — | `name` |

`r`/`g`/`b` are 0–255 and set the hero's colour.

---

## Decks

| Command | Access | Parameters |
|---|---|---|
| `/create_deck` | 🔒 | `name`, `description`, `type`*, `content_type`*, `usage_type`* |
| `/update_deck` | 🔒 | `name`*, `new_name?`, `description?`, `type?`*, `usage_type?`*, `archived?` |
| `/delete_deck` | 🔒 | `name`* — hard delete if empty, soft delete if in use |
| `/get_deck` | — | `name`* — details plus contents |
| `/render_deck` | — | `name`* — renders every item (60s timeout) |
| `/render_hand` | — | `name`*, `hand_size?` (default 6) — a sample draw |

`type` / `content_type` / `usage_type` autocomplete from `deck_types`,
`deck_content_types` and `deck_usage_types`. ⚠️ Those three tables read as empty
with an anon key — verify against the service key before trusting an empty
autocomplete.

### Deck curation

| Command | Access | Parameters |
|---|---|---|
| `/add_to_deck` | 🔒 | `deck_name`*, `item_name`*, `quantity?` |
| `/remove_from_deck` | 🔒 | `deck_name`*, `item_name`*, `quantity?` |
| `/stage` | 🔒 | `item_name`* |
| `/postpone` | 🔒 | `item_name`* |
| `/merge_staging` | 🔒 | — |

These three are a **balance workflow** for pulling content out of the live draft
pool and putting it back:

- **`/postpone`** — removes every copy of an item from all active base draft decks
  and moves them into the "Removed" decks. Use when benching content.
- **`/stage`** — moves every copy from live draft decks into the Staging deck (or
  adds it if missing). A holding area for content being reworked.
- **`/merge_staging`** — empties Staging back into live draft decks, routing each
  item by type: aspects → the aspect deck, cards with null valence *and* null
  element → Combo Cards, everything else → Base Draft Deck.

⚠️ **`/stage` and `/merge_staging` use hardcoded deck IDs** (`21` Staging, `22`,
`20` Combo Cards, `3` Base Draft Deck). In the current database deck 21 is
archived and deck 22 is named "Testing Fates" despite the constant being called
`ASPECT_DECK_ID`. Verify these before relying on either command.

**Always pick items from autocomplete.** Typed names fall back to a first-match
lookup across content types and can resolve to the wrong item — see
[ARCHITECTURE.md § Deck membership](ARCHITECTURE.md#deck-membership).

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
leaves earlier rows written. Both report per-row errors, capped at 15 lines in
the Discord reply with the rest going to the bot console.

Full format specification and workflow: [CONTENT_PIPELINE.md](CONTENT_PIPELINE.md).

---

## Analytics

| Command | Access | Parameters |
|---|---|---|
| `/stats active_players` | — | `limit?` (default 25) |
| `/stats leaderboard` | — | `limit?` (default 10), `player?`*, `hero?`*, `version?`* |
| `/stats player` | — | `player`* |
| `/stats hero` | — | — |
| `/stats version` | — | — |
| `/stats draft_deck` | — | — |
| `/stats draft_rates` | — | `limit?` (default 15), `order?` (most/least), `item_type?` (card/aspect/event) |
| `/daily_update` | 🔒 | `enabled`, `send_time?` (HH:MM, default 12:00), `utc_offset?` (default -6) |

All `/stats` subcommands dump raw JSON from a database **view** into a code block.
They are open to anyone in the guild.

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

**Open to any guild member:** every `get_*` and `render_*`, and all of `/stats`.

**Authorized users only:** every `create_*`, `update_*`, `delete_*`, all deck
mutation, `/bulk_insert`, `/bulk_update`, `/daily_update`.

**Removed 2026-08-26:** all 10 ritual and consumable commands, along with their
modules. Both content types are retired — see [AZOTH.md](AZOTH.md#ritual-means-two-different-things-one-of-them-is-dead).

**Does not exist despite having code:** `/render_aspect`, commented out at
`aspects.py:201`.
