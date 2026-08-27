# Content Pipeline

How a piece of Azoth content gets from an idea to a row in the production
database, and what AzothBot's role is at each step.

## The one thing to know

**The Supabase database is authoritative. The game repo's `assets/game_data/`
JSON is not.**

Those files are a fallback snapshot exported from Supabase so the game plays
offline and the test suite has fixtures. The running game pulls content from the
database. Writing a JSON file into `assets/game_data/` does **not** add content to
the game — it desynchronises the fallback from the real store.

AzothBot is how content actually enters the authoritative store.

## Three routes in

| Route | Use when | Scale |
|---|---|---|
| **Slash commands** (`/create_card`, …) | Making or tweaking one thing, interactively | 1 item |
| **`/bulk_insert`** | Adding a batch — a whole draft pack, a new boss with its content | Many items, many tables |
| **`/bulk_update`** | Changing fields on content that already exists | Many items, many tables |
| **Direct SQL** | Anything the above can't express | Escape hatch |

`bulk_insert` and `bulk_update` are **not interchangeable**. Different rules,
different match semantics.

---

## Slash-command route

`/create_card`, `/create_aspect`, `/create_rite` (and, once
registered, `/create_ritual` and `/create_consumable`).

Each one:

1. Validates and assembles the row.
2. Generates card art via the eigenfunction renderer — see [RENDERING.md](RENDERING.md).
3. Uploads the image to the matching Supabase Storage bucket.
4. Inserts the row with `created_by = BOT_PLAYER_ID`.
5. Optionally adds the item straight to a deck (`deck` + `quantity` parameters).

`/update_*` only regenerates art when `regenerate_image=True`.

**Limitation:** these commands cover the flat scalar fields — name, text, element,
valence, attunement, foresight, colour. They cannot author `actions`, `triggers`
or `properties`, which are `jsonb`. Anything with real mechanics goes through
bulk insert.

---

## Bulk route

Both commands take a **JSON file attachment** whose top level is an object keyed
by Supabase table name:

```json
{
  "cards":             [ /* ... */ ],
  "aspects":           [ /* ... */ ],
  "events":            [ /* ... */ ],
  "heroes":            [ /* ... */ ],
  "bosses":            [ /* ... */ ],
  "custom_actions":    [ /* ... */ ],
  "custom_properties": [ /* ... */ ]
}
```

Include only tables with at least one item.

> **The full specification lives in the game repo**, at
> `skills/content-creation/references/EXPORT_FORMAT.md` — field-by-field minimums
> per table, which runtime fields to strip, and worked examples. This document
> covers what the *bot* does; that one covers what to put in the file.

### `bulk_insert`

Each entry **is the row**, so every key must be a real column. Iterates tables,
then entries, inserting one at a time.

- **Never send `id`** — Supabase assigns it.
- **Never send** `created_at` / `updated_at` / `archived_at`, or runtime-only
  fields (`custom`, `editable`, `instance_id`, `phantom`, `upgraded`, and the rest
  listed in the spec).
- Errors are **per row**. A bad entry is reported and skipped; the rest still land.

### `bulk_update`

Matches existing rows **by `name`** and PATCHes them.

```python
original_name = entry.get("name")     # the match key
update_data.pop("name")               # name selects; it never writes
if "new_name" in update_data:         # renaming goes through new_name
    update_data["name"] = update_data.pop("new_name")
```

Five rules that catch people:

1. **`name` is the match key, not `id`.** It selects the row and is then popped
   off the payload. An entry without `name` is skipped.
2. **Never send `id`.** It's a PATCH — every key present gets written, so an `id`
   is an attempt to overwrite the primary key.
3. **It's partial.** Send only what's changing. Unsent columns keep their values.
4. **Rename with `new_name`.** `{"name": "Old", "new_name": "New"}`.
5. **Don't send `updated_at`** — the bot stamps it.

To replace a repeated field, send the whole new array (`"triggers": [...]`
overwrites); to clear one, send `[]`.

### What the reply tells you

Both commands report what the action actually did, not just a count.

**`/bulk_update`** shows a **per-record diff** — the fields that changed, old → new
— and **renders the updated items** as a grid (up to 12). It has both rows to
hand: the pre-update record it matched by name, and the row the write returned.

- `updated_at`, `created_at`, `created_by` and `id` are excluded. `updated_at`
  changes on every write, so reporting it would put a spurious line on every
  record.
- `actions`, `triggers`, `properties`, `upgrades`, `image_data` and `split`
  report their **shape** (`2 entries → 3 entries`), not their contents — an
  actions array runs to hundreds of characters and would bury everything else.
- Long values truncate, and truncation is **always announced**. Silent
  truncation in a write report reads as "that is everything that changed" when
  it is not.

**`/bulk_insert`** lists each new row with its id and identifying attributes —
`• **Newbie** #501 — Sol · v3 · Wild · no art` — so a wrong element or a missing
valence is visible without opening anything.

It **does not render**, on purpose: art is uploaded *after* an insert, not with
it, so every card would come back with a hole in the middle. That reads as a
broken renderer rather than as "no art yet". The per-row summary flags `no art`
instead, and the footer says why.

Both invalidate the content index, so new or renamed items are selectable in
`/show`, `/render` and `/search` immediately.

### Bulk gotchas

- **Not transactional.** Rows apply one at a time. A failure part-way leaves
  earlier rows written. There is no rollback — re-run with a corrected file, or
  clean up by hand.
- **Duplicate names win silently.** `bulk_update` takes `matches[0]`. If two rows
  share a name you cannot tell which one you changed.
- **A missing row is skipped, not created.** `bulk_update` never inserts.
- **The reply is truncated.** 15 error lines, 1,800 characters. The rest goes to
  the bot console — which lives on a teammate's machine. If a bulk run reports
  more errors than it shows, ask for the console output.
- **Both are 🔒 authorized-only**, and the deployed bot's service-role key means
  there is no database-level guard behind that check.

---

## Where content is authored

The game repo carries a `content-creation` **skill** (`skills/content-creation/`)
that turns a prose pitch — "a card that purges a pattern when discarded" — into a
valid bulk-insert payload. It knows the action catalog, trigger events,
placeholder paths, and the complexity limits of the engine.

That skill is the recommended front end for anything non-trivial. It writes to
`generated_content/<slug>.bulk.json` (insert) or
`generated_content/<slug>.bulk_update.json` (update), and the file gets uploaded
here.

The game also has an in-game **Codex editor** that exports the same format —
`docs/CODEX_EDITOR.md` in the game repo.

---

## The full loop

```
  idea
   │
   ├─ content-creation skill ─┐
   ├─ Codex editor exporter ──┤──→  <slug>.bulk.json
   └─ hand-written JSON ──────┘            │
                                           ▼
                              /bulk_insert  or  /bulk_update   (AzothBot)
                                           │
                                           ▼
                                  Supabase  ← authoritative
                                           │
                        ┌──────────────────┴──────────────────┐
                        ▼                                     ▼
              game pulls at runtime              asset re-export refreshes
                                                 assets/game_data/ fallback
```

After rows exist, the game repo's asset re-export refreshes
`assets/game_data/` so the offline fallback and the test fixtures stay in sync.
**That step is separate and manual** — new content is live in the game before the
fallback snapshot knows about it.

---

## Checklist for adding content

1. Author the payload — skill, Codex export, or by hand.
2. Confirm it's the right shape: insert for new rows, update for existing.
3. Upload via `/bulk_insert` or `/bulk_update`.
4. **Read the reply.** Partial success is normal; the summary is the only place
   per-row failures appear.
5. Verify with `/show` — note it omits `actions`,
   `triggers` and `properties`, so check those in the database directly.
6. Add to a deck if it should be draftable — `/add_to_deck`, or the `deck`
   parameter on a `create_*` command. **Content not in a deck never appears in
   the game.**
7. Trigger an asset re-export in the game repo when convenient, to resync the
   fallback snapshot.
