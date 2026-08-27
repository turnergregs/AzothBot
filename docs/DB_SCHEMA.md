# Database Schema (Supabase)

Full mirror of the live Supabase schema, plus the domain knowledge a schema dump
can't carry. **Read [Query caveats](#query-caveats) before writing any query
against this data** — several of them turn a query that runs cleanly into a query
that answers the wrong question.

## About this mirror

This file is a **deliberate duplicate** of `docs/DB_SCHEMA.md` in the
[game repo](https://github.com/turnergregs/azoth), so AzothBot is self-contained.
The game repo's copy is the **upstream**: the game writes this data, so schema
changes originate there.

**When the schema changes, both copies must be updated.** There is no automation.
If the two disagree, trust the game repo's copy and fix this one.

Sections marked **[AzothBot]** exist only here — they cover what the *bot* can
see and do, which the game-side copy has no reason to describe.

There is no Supabase CLI in either project and no migration history in the
database. This file, its upstream twin, and the game repo's
[`db/migrations/*.sql`](https://github.com/turnergregs/azoth/blob/main/db/migrations)
are the only record of the schema and how it got that way.

---

## [AzothBot] Which key you are holding

**This determines what the bot can see, and getting it wrong fails silently.**

| Key | Where it's used | What it can do |
|---|---|---|
| **service_role** | The deployed bot, on a teammate's machine | Full read/write on everything. **Bypasses RLS entirely.** |
| **anon** | Whatever a local `.env` happens to hold; also shipped inside the game binary | A subset of content tables, plus `games` / `drafts` / `draft_items` / `boss_fights` / `players`. Nothing else |

### The silent failure

The turn-grain tables (`turns`, `turn_nodes`, `levelups`) are **INSERT-only for
anon by design** — see [RLS posture](#rls-posture). PostgREST does not reject a
`select` against them. It applies the RLS predicate, matches nothing, and returns
**HTTP 200 with an empty array**.

`fetch_all()` then returns `[]`, exactly as it would for a genuinely empty table.
So with an anon key:

```
/stats  →  "❌ No data available."
```

…which is indistinguishable from "there is no data yet".

Two distinct causes, both verified 2026-08-26 (see [RLS posture](#rls-posture)):

| Cause | Tables |
|---|---|
| **INSERT-only policy** — anon may write, never read | `turns`, `turn_nodes`, `levelups`, `reports` |
| **RLS enabled with no policy at all** — deny-all | `rituals`, `consumables`, `card_attributes`, `card_elements`, `card_types`, `deck_types`, `deck_content_types`, `deck_usage_types`, `fate_types` |

`macros` is the exception that proves the rule: it has a public read policy and
still returns nothing, so it is genuinely empty.

**If a query returns nothing, check which key you are holding before concluding
anything about the data.**

### Consequences for the bot

- **RLS is not a safety net here.** With the service key, the only thing
  preventing a guild member from deleting production content is
  `require_authorized` in `safe_interaction`. See
  [ARCHITECTURE.md](ARCHITECTURE.md#safe_interaction).
- **The bot can read the turn-grain tables in production.** Anything in the
  worked examples below is available to `/stats` — none of it is used yet.
- **Local analytics work needs the service key**, or it will look like the
  dataset is empty.

---

## [AzothBot] Views

The `/stats` commands do not query tables. They query **views**. There are nine
in `public`; AzothBot uses eight of them.

All nine were captured into version control on 2026-08-26 at
`db/migrations/2026-08-26_capture_existing_views.sql` in the game repo, recorded
verbatim with their defects annotated but not fixed.

| View | Rows | Columns |
|---|---|---|
| `leaderboard_view` | ~1,830 | `player`, `combo`, `hero`, `deck_size`, `turns`, `act`, `level`, `version` |
| `player_info_view` | ~108 | `player`, `game_count`, `avg/max` × `turns`/`act`/`level`/`combo`, `most_drafted`, `most_picked_hero` |
| `player_activity_view` | ~105 | `player`, `game_count`, `hours_played`, `highest_combo` |
| `active_players_view` | ~184 | `name` (autocomplete source) |
| `hero_info_view` | 1 | `hero_name`, `game_count`, `avg/max` × `turns`/`act`/`level`/`combo` |
| `version_info_view` | 5 | `version`, `game_count`, `avg/max` × `turns`/`act`/`level`/`combo` |
| `draft_deck_view` | 1 | `deck_name`, `cards`, `aspects`, `events`, element and valence breakdowns, `combo`. Covers base, non-archived decks with `usage_type in ('draft', 'rite')` — the rite half added 2026-08-27, before which `events` was always 0. Surfaced by `/stats draft_pool` |
| `draft_rates_view` | 1 | Pre-formatted comma-joined strings of most/least picked items |
| `decks_with_contents` | — | **Not used by AzothBot.** Deck rows with contents inlined as JSON; consumed by the game / Codex editor |

**They predate the turn-grain schema and violate several caveats below** — every
one that touches `games` averages a `text` BigNum combo (caveat 7), none filters
`result` or `game_type`, and `draft_rates_view` computes no rate at all. Per-view
defects are annotated in the capture migration and summarised in
[ANALYTICS.md](ANALYTICS.md).

### The version filter is better than this document's advice

Five of them filter with a **numeric** encoding rather than a string compare:

```sql
  split_part(version,'.',1)::int * 1000000
+ split_part(version,'.',2)::int * 1000
+ split_part(version,'.',3)::int  >= 6007        -- i.e. >= 0.6.7
```

That already solves the `0.10.0`-sorts-below-`0.9.0` problem this document warns
about under **The analytics cutoff** above. Two real
problems remain: the **threshold is stale** (`6007` should be `8002`), and
`split_part('0.6','.',3)` returns `''`, which **raises** on `::integer` — a
two-component version string takes these views down. NULL versions are safe;
NULL propagates and the row is excluded.

### None of them sets `security_invoker`

`reloptions` is NULL on all nine, so each runs with its **owner's** privileges and
**bypasses RLS** on the tables beneath it. Both directions matter:

- It is the mechanism for serving aggregates to a restricted role without
  exposing rows — available today, unused.
- **A view over `turns`, `turn_nodes`, `levelups` or `reports` silently becomes
  anon-readable**, defeating the INSERT-only policy those tables rely on. Set
  `security_invoker = true` on any new view over them unless that exposure is
  intended.

---

## Conventions

- Primary keys are `id bigint generated by default as identity`.
- **Children reference a business key, not the parent's `id`.** `games(uuid)`, `drafts(uuid)`, `turns(uuid)`. The uuid is generated client-side with `gen_random_uuid()` as a column-default fallback, so the client can build a whole payload offline without reading anything back.
- Analytics tables also carry a **second** unique constraint derived from game state (`turns_game_turn_key`, etc.) purely for idempotency — see [Re-flush safety](#re-flush-safety).
- The turn-grain chain is `ON DELETE CASCADE` (2026-08-26), so deleting a `games` row takes its turns, nodes and level-ups with it. `drafts`/`draft_items` and `boss_fights` are deliberately not cascaded — legacy paths, left alone.
- All integer measures are `bigint`. Booleans are real booleans.
- `created_at timestamptz not null default now()` on essentially every table.
- Content payloads (actions, triggers, properties, visuals) are `jsonb`.

## Regenerating

Run these in the Supabase SQL editor and update the tables below — **in both
copies of this file**.

```sql
-- columns
select table_name, column_name, data_type, is_nullable, column_default
from information_schema.columns
where table_schema = 'public' order by table_name, ordinal_position;

-- constraints
select conrelid::regclass::text as table_name, conname, pg_get_constraintdef(oid) as definition
from pg_constraint where connamespace = 'public'::regnamespace order by 1, 2;

-- RLS policies
select tablename, policyname, roles, cmd, qual, with_check
from pg_policies where schemaname = 'public' order by tablename, policyname;

-- views
select table_name, view_definition
from information_schema.views where table_schema = 'public' order by table_name;

-- sizes
select relname, n_live_tup as approx_rows, pg_size_pretty(pg_total_relation_size(relid)) as total
from pg_stat_user_tables order by pg_total_relation_size(relid) desc;
```

## Not captured here

- **Postgres functions.** At least one exists and the game depends on it: `get_player_uuid_from_id`, called via `rpc/`. The introspection queries only cover tables.
- **Content table columns.** `custom_actions`, `custom_properties`, `decks`, `deck_types`, `deck_content_types`, `deck_usage_types`, `events`, `fate_types`, `heroes`, `macros`, `reports`, `rituals` exist with PKs and `created_by → players(id)` FKs, but their columns haven't been pulled. Fill in when needed. **This is the gap that matters most for AzothBot**, since the content CRUD commands write to exactly these tables.

---

## ⚑ The analytics cutoff is `0.8.2`

**Game version `0.8.2` is where trustworthy turn-grain analytics begins.**

- **`0.8.0`** introduced `turns`, `turn_nodes` and `levelups`, fixed
  `games.ritual` and froze `boss_fights` — but its rollout took several rounds
  of fixes, so its rows are debugging debris: runs with no turn rows, runs
  missing their nodes, runs missing their final turn, and phantom `games` rows
  from runs nobody actually played.
- **`0.8.1`** delivered reliably, but folded **Ascender's Bane into the ordinary
  pattern counts** — see [Ascender's Bane](#ascenders-bane). On any
  Ritual 5+ run its pattern metrics are unusable, and cannot be repaired: the
  two populations were never recorded apart.

Filter every analytics query with:

```sql
where g.version >= '0.8.2'
```

Everything before it is a different dataset: no turn rows at all, `ritual`
reading 0 on all non-restart runs, `result` NULL on ~67% of rows, and a
population dominated by developer testing (median `turns_played` of 1). Pooling
across the boundary produces confident nonsense.

`version` is a `text` column, so a bare `>=` is a lexicographic compare. That is
correct through `0.9.x` but breaks at `0.10.0`, which sorts *below* `0.9.0`.

**In SQL, use the helpers instead** (added 2026-08-26):

```sql
where version_key(g.version) >= analytics_cutoff()
```

---

# The game model, in one paragraph

Azoth runs on a **three-then-one cadence**: three regular turns of setup, where
you're dealt two new patterns per turn and solve them to earn drafts, then a
fourth turn that is the act boss. **A boss fight IS one turn** — it doesn't end
until the player or the boss dies. Boss turns differ structurally: the bin
reshuffles into the deck instead of letting you overdraw, damage flows both ways
(weapon cards out, boss attacks in), and the end-turn button becomes a *skip*
that spends one timeline node to redraw your hand. Beating the boss advances
the act. Everything in the analytics schema follows from that shape.

---

## How the analytics tables join

```
games ──< turns ──< turn_nodes ──< levelups
  │         │            │
  │         │            └── levelups also FKs (turn_uuid, node_index)
  │         │
  │         └── turns.game_uuid  → games(uuid)      [ON DELETE CASCADE]
  │             turn_nodes.turn_uuid → turns(uuid)  [ON DELETE CASCADE]
  │             levelups.turn_uuid   → turns(uuid)  [ON DELETE CASCADE]
  │
  ├──< drafts ──< draft_items        (legacy, still written)
  └──< boss_fights                   (FROZEN — no longer written)
```

**Children join on the parent's business key, never its `id`:**

```sql
join turns      t on t.game_uuid = g.uuid      -- games.uuid,  not games.id
join turn_nodes n on n.turn_uuid = t.uuid      -- turns.uuid,  not turns.id
join levelups   l on l.turn_uuid = t.uuid
```

Deleting a `games` row now cascades through all three turn-grain tables.

## Writing a query: start here

A checklist that covers most mistakes. Details for each are in
[Query caveats](#query-caveats).

1. **Filter `version_key(g.version) >= analytics_cutoff()`.** Always. Earlier
   rows are a different dataset, not older data. The helpers avoid the
   lexicographic trap that breaks a bare `>= '0.8.2'` at `0.10.0`.
2. **Decide regular vs boss turns.** `t.boss_id is null` for regular. They are
   not comparable — a boss turn runs until someone dies and holds many times
   the nodes.
3. **`left join turn_nodes`, never inner.** An inner join silently drops
   zero-node turns, which is exactly the bias the `turns` table exists to
   prevent.
4. **Add `g.game_type = 'solo'`** unless you specifically want co-op. Each
   participant records the same session, so co-op multiplies every turn-grain
   row.
5. **Never `avg()` a combo.** Use `turn_nodes.combo_log10`. (The `/stats` views
   average `log10(games.highest_combo)` instead — same quantity, and it works
   before turn rows are plentiful.)
6. **Report censored metrics as two numbers.** "Cleared in 2.3 links, 78% of
   the time" — never the mean alone.
7. **Don't filter on `result` for turn-grain questions.** An abandoned run's
   completed turns are perfectly good data; only its outcome is unknown.

## Analytics tables

### `games` — one row per completed run

| Column | Type | Notes |
|---|---|---|
| `id` | bigint | PK |
| `uuid` | uuid | UNIQUE. **Nullable**, despite being what every child FKs to. |
| `player_uuid` | uuid NOT NULL | → `players(uuid)` |
| `starting_hero` / `starter_deck` | bigint | → `heroes(id)` / `decks(id)` |
| `started_at` / `finished_at` | timestamptz | `finished_at` defaults `now()` |
| `elapsed_sec` | bigint | |
| `turns_played`, `act_reached`, `level_reached`, `deck_size` | bigint | Aggregates of series now held in `turns` |
| `highest_combo` | **text** | Serialized BigNum. Not numerically aggregatable. |
| `hero_activations` | bigint | |
| `created_at` | timestamptz NOT NULL | Insert time. `open_run` writes the row on the run's **first save**, so this is roughly run start |
| `result` | text | `death` / `patterned` / `no_boss_key` / `victory` / `restart`. NULL on legacy rows. |
| `ritual` | bigint NOT NULL default 0 | Unreliable before 2026-08-25 |
| `ascenders_bane_count` | bigint | Run contained the Bane, solved or not. Observed from the cards, not derived from `ritual`. Pair with `turn_nodes.banes_purged` — see [Ascender's Bane](#ascenders-bane) |
| `game_type` | text NOT NULL | CHECK: `solo` / `coop` / `pvp` |
| `shared_run_id` | text | Ties co-op participants; NULL for solo |
| `version` | text | **The standard analytics filter.** |

#### Planned deprecations on `games`

Five columns become derivable once turn rows are trusted, and the intent is to
stop writing them and eventually drop them. **Not yet** — see the sequence below.

| `games` column | Derivation |
|---|---|
| `hero_activations` | `sum(turns.hero_activations)` |
| `act_reached` | `max(turns.act)` |
| `turns_played` | `count(turns)` |
| `level_reached` | `max(levelups.level_before + cardinality(levelups.chosen))` |
| `highest_combo` | `max(turn_nodes.combo_log10)` — **in log space** |

Two that are *not* derivable and stay: `deck_size` and `elapsed_sec`.

`deck_size` is close — `turns.starting_deck_size` is measured on the same
consolidated basis (deck + hand + bin + exhaust, tokens excluded), so the last
turn's value is nearly the final total. But it misses whatever was drafted
during that last turn, and the final `reset_deck()` also folds in `board`. Near
enough to sanity-check against, not near enough to replace.

`elapsed_sec` can't come from `sum(turns.elapsed_sec)` — turn times don't cover
drafts, boss rewards, or act screens.

**Sequence — do not skip the validation step.** Dropping a column whose
replacement turns out to be wrong is unrecoverable on this plan.

1. **Now:** write both. Turn rows have to prove they land reliably first.
2. **After a few weeks of real data:** validate. Per run, compare each `games`
   column against its derivation and confirm they agree:
   ```sql
   select count(*) filter (where g.act_reached is distinct from d.act_reached) as act_mismatches,
          count(*) filter (where g.hero_activations is distinct from d.hero_acts) as hero_mismatches
   from games g
   join (select game_uuid, max(act) as act_reached, sum(hero_activations) as hero_acts
         from turns group by game_uuid) d on d.game_uuid = g.uuid
   where g.version >= '0.8.2';
   ```
   Non-zero mismatches mean turn rows are being lost, not that the derivation is
   wrong — investigate before proceeding.
3. **Then:** stop writing them, and record the version that stopped here.
4. **Later:** drop the columns.

**Keep `turns_played` permanently**, even though it's derivable. It's the
cheapest possible data-loss canary: when `games.turns_played != count(turns)`,
you know a flush was dropped. Deliberate redundancy at run grain, one column, and
it's the only way to detect silent loss in the per-act flush.

Note the `highest_combo` trade: the derived value is log10, so the exact digits
are gone. That's fine for analytics — and the exact peak is still kept locally by
`RunHistoryManager` — but say so out loud before dropping the text column.

### `turns` — one row per turn

The denominator table. Turns with zero nodes exist here, which is what makes
"average links per turn" honest.

| Column | Notes |
|---|---|
| `uuid` | What `turn_nodes` and `levelups` reference |
| `game_uuid`, `turn_index`, `act` | |
| `boss_id` → `bosses(id)` | **NULL = regular turn.** Doubles as the boss-turn flag |
| `boss_result` | `win` / `loss`, NULL on regular turns |
| `started_at`, `elapsed_sec` | Real turn time — **not** `created_at` |
| `starting_life`, `starting_level`, `starting_power` | Snapshot |
| `starting_deck_size` | The **whole run deck** — deck + hand + bin + exhaust, tokens excluded. Not the `deck` zone, which mid-turn measures draw progress rather than deck size. Same basis as `games.deck_size` |
| `starting_patterns` | **Unsolved recurring patterns across deck + hand + bin + exhaust.** *Not* "patterns dealt this turn" (always 2), and **excludes Ascender's Bane** |
| `hero_activations` | |
| `created_at` | Insert time, **not** turn time — batched flushing means it can be minutes later |

**The snapshot is taken in `start_turn()`, immediately after
`settle_hand_size()`** — not at `handle_turn_start()`. By then
`create_patterns()` has dealt the turn's new patterns, `draw_opener()` has
filled the hand, and turn-start triggers have drawn on top. Anything sampled
earlier describes a state no player ever saw.

#### Ascender's Bane

**The Bane is excluded from every `patterns_*` column.** Its lifecycle is
recorded by one boolean: `turn_nodes.banes_purged`, on the single node that
solved it.

It appears at **Ritual 5+**, is injected once at run start, and is designed to
survive until act 3 or 4 — whereas regular patterns arrive two per turn and are
meant to be solved within a turn or two. Folded together it put a permanent
floor under everything:

```
turn 1   starting_patterns 3   lowest_unsolved 1     (2 regular + Bane)
turn 5   starting_patterns 3   lowest_unsolved 2     (Bane + carried debt)
```

so `patterns_after = 0` was unreachable by construction, and "links to clear all
patterns" honestly reported **zero cleared turns out of six** — a correct query
over an incoherent metric.

They're also two different questions. The recurring load is the per-turn pacing
dial. The Bane is the **act 4 → 5 gate**: purging it grants `boss_key`, and a run
that never clears it ends in `no_boss_key` however well it otherwise went. So
`banes_purged` is a signal in its own right — it marks the exact link that opened
the gate.

Identity is the card's **name**, via `PatternManager.ASCENDERS_BANE_NAME` — the
same constant used to name it at creation, so renaming the card is a one-line
change. No analytics-only field was added to the card's data.

**Which runs even had a Bane?** `banes_purged` records an event, and an event
cannot record a non-event — so the two directions aren't symmetric:

- A `banes_purged` row **proves** one existed.
- **No** `banes_purged` row proves nothing. It means either "Ritual 5+, never
  solved it" or "no Bane at all" — opposite findings, and the first is the whole
  `no_boss_key` story.

**`games.ascenders_bane_count`** is the run-invariant half. Together they give
three unambiguous states:

| `had_ascenders_bane` | `banes_purged` row | Meaning |
|---|---|---|
| `0` | — | No Bane in this run |
| `> 0` | none | Had one, **never solved it** → the `no_boss_key` population |
| `> 0` | matching total | Solved, at those exact nodes |

**When it's captured:** with the run's first `games` write (`open_run`, on the
first save). The Bane is in the deck well before then —
`RitualManager.apply_ritual_effects()` runs in `main.gd::_ready()`, before
`start_game()` — and `save_requested` fires *before* a link resolves, so no
purge can precede it.

`open_run` also runs on resume, and it upserts, so it recounts and overwrites.
That's safe only because purged cards go to `trash`, `trash` is a configured
zone, and `CardZones.get_save_data()` persists every zone — so a Bane solved
before the reload is still found. `count_ascenders_banes()` scanning `trash` is
load-bearing here, not merely defensive: without it, resuming a run would
silently reset the count to 0.

It's **observed, not derived**. `ritual >= 5` would work today — the Bane comes
from ritual 5 (`assets/game_data/rituals/rituals.json` in the game repo, `effect:
"ascenders_bane"`) and `RitualManager.apply_ritual_effects` applies every ritual
from 1 up to the player's level, so effects are cumulative. But that rule lives
in a content file, and if the Bane is ever granted another way every historical
query built on the ritual level becomes silently wrong.
`CardLogic.run_has_ascenders_bane()` looks for the card instead, scanning
`trash` so the answer stays true after it's purged.

**Is the gate still shut on a given turn?** Derived, not stored — outstanding
from turn 1 until the node that purges it:

```sql
select t.turn_index,
       g.ritual >= 5
         and not exists (
           select 1 from turn_nodes n2
           join turns t2 on t2.uuid = n2.turn_uuid
           where t2.game_uuid = t.game_uuid
             and n2.banes_purged > 0
             and t2.turn_index <= t.turn_index
         ) as bane_outstanding
from turns t
join games g on g.uuid = t.game_uuid
where g.uuid = '<run uuid>'
order by t.turn_index;
```

A run with no `banes_purged` row anywhere and `ritual >= 5` never opened the
gate — those are the `no_boss_key` endings.

**When does the gate open?**

```sql
select g.ritual, t.act, t.turn_index, n.node_index
from turn_nodes n
join turns t on t.uuid = n.turn_uuid
join games g on g.uuid = t.game_uuid
where n.banes_purged > 0 and g.version >= '0.8.2'
order by t.act, t.turn_index;
```

Compare against runs ending in `no_boss_key` — those are the ones where it never
opened.

### `turn_nodes` — one row per timeline node

A node is one slot on the turn timeline, consumed by a **link** or a **skip**.
Both advance the boss timeline, so "links played" and "nodes consumed" are
different numbers.

| Column | Notes |
|---|---|
| `turn_uuid`, `node_index`, `kind` | `kind` is `link` \| `skip` |
| `link_types`, `link_size`, `combo_log10` | NULL on skips |
| `patterns_before`, `patterns_after` | Unsolved **recurring** patterns: deck + hand + bin + exhaust. Bane excluded |
| `patterns_purged` | **Not** `before - after` — cards can *create* patterns mid-turn |
| `patterns_in_hand` | Recorded **after** the node. Separates "couldn't solve them" from "never drew them" |
| `banes_purged` | bigint — this node solved Ascender's Bane, i.e. opened the act 4 → 5 gate. Excluded from `patterns_purged` |
| `boss_attack`, `blocked`, `damage_dealt`, `damage_received` | NULL on regular turns — NULL means "not applicable", not zero |
| `started_at` | Node time. `created_at` is insert time |

Boss attacks land **on** nodes — every attack coincides with a link or a skip.
That's why attack/block/damage live here: fight totals are `sum()` over nodes,
but the *timing* only exists at this grain.

**`patterns_after` must be read after the queue drains.** `link_resolved` fires
at [card_manager.gd:646](https://github.com/turnergregs/azoth/blob/main/scripts/managers/card_manager.gd#L646) *before* the
queue is idle — purges applied, replacement draws pending. Counting there gives
half-settled numbers that look plausible and aren't. The correct point is after
the second `queue_idle` await, just before `discard_to_hand_size()`.

### `levelups` — one row per level-up pack

A link crossing thresholds pays out N level-ups at once; the player picks N
rewards from a pack of X (`cards_shown(N)`: 1→3, 2→5, 3→7, 5→10, 10→19).

| Column | Notes |
|---|---|
| `turn_uuid`, `node_index` | → `turn_nodes`. One pack per node |
| `level_before` | |
| `options` | Reward names **offered** — the denominator for pick rate |
| `chosen` | Reward names **taken** |

Twelve rewards. **Rare**: Hero, Grasp, Form, Mind, Adept. **Common**: Life,
Sight, Power, Luck, Growth, Cure, Craft. The pool is filtered by
`available_rewards()` before the draw (in practice only Hero ever exhausts), so
`options` records what run state made available, not just what luck dealt.

### `boss_fights` — **FROZEN 2026-08-25**

888 historical rows, no longer written to. Superseded by `turns.boss_id` /
`turns.boss_result` plus the boss columns on `turn_nodes`.

Most of it was turn-grain data summed prematurely: `links_played`, `attacks`,
`blocks`, `skips`, `hero_activations`, `damage_dealt`, `damage_received` are all
per-node quantities, and `starting_/ending_level`, `starting_/ending_power`, and
the timestamps are just series sampled at the fight's edges.

**Boss-fight history breaks into two eras at 2026-08-25.** A query spanning both
needs a UNION. Given the pre-cutoff rows are almost entirely developer testing,
this rarely matters.

### `drafts` / `draft_items`

`drafts` (`uuid`, `game_uuid`, `turn`, `act`, `available_drafts`, `pack_size`)
with `draft_items` (`draft_uuid`, `item_type`, `item_id`, `picked`, `reserved`)
at ~6 rows per draft, matching `draft_window_size`.

Measured at ~4.7 `draft_items` rows and 0.5 kB per run. An earlier plan to
collapse these into arrays was **abandoned** — it would have saved fractions of
a kilobyte.

### `players`

`id`, `name`, `created_at`, `updated_at`, `uuid`. No email or PII beyond a
display name.

---

## Re-flush safety

Stats are flushed per act, not once at run end, so a save/reload can re-send
turns that already landed. Two mechanisms guard this:

1. **`turns_game_turn_key unique (game_uuid, turn_index)`** — derived from game
   state, so it can't drift. A duplicate turn is rejected regardless of what
   uuid the client generated.
2. **The generated `turns.uuid` must be persisted in `GameStats.get_save_data()`.**
   If it isn't, a re-flush after reload gets its turn row rejected by (1), and
   then its nodes reference a uuid that doesn't exist and orphan on the FK.
   `drafts` already works this way — its generated uuids live in the persisted
   `drafts` array.

Insert order per flush is **turns → turn_nodes → levelups**; each FKs the one
above, and each stage fires only on the previous one's success.

### How the flush works

`GameStats.flush_turn_grain()` runs at every act boundary
(`main.gd::start_new_act()`) and once more at run end, after the `games` upsert.

- **Rows leave the buffer only on a 2xx.** A failed act-1 flush rides along with
  the act-2 flush at no cost. This is what makes the FK-ordered chain safe —
  the legacy `games → drafts → draft_items → boss_fights` chain lost everything
  downstream of a failure (caveat 11); this one only delays it.
- **The open turn is never flushed.** It has no `elapsed_sec` yet and its nodes
  are still arriving, so it goes out with the next act.
- **Children wait for their parent.** A node whose turn is still buffered is
  held back rather than landing before the row it references.
- **Writes are upserts** — `Prefer: return=minimal,resolution=merge-duplicates`.
  `minimal` stops Supabase echoing every inserted row back as egress (the
  binding constraint on the free tier); `merge-duplicates` turns a re-flush from
  a constraint error into a no-op.
- **`games` is upserted twice** — a stub on the run's first save (`open_run()`) so turn
  rows have a parent, then the full row at run end merging onto the same uuid.
  No PATCH-vs-POST branching, and an offline run start can't orphan the run.

---

### `deck_contents.position` and `deck_contents.weight`

Two columns that surface only through `decks_with_contents`, verified 2026-08-26.

| Column | State |
|---|---|
| `position` | Set on **11 of 379** rows. Written by the Codex editor's reorder UI (`codex.gd`); nothing else reads it |
| `weight` | Set on **14 of 379** rows, all in deck 32 "Reactants" — which is archived. Values 0.01–2.0 |

**Neither is consumed by gameplay.** `Utils.pick_random_weighted()` is the only
weight-aware picker and it has no callers. AzothBot's `add_to_deck()` does not
write either column, which is therefore *not* currently a bug — but it would
become one the moment weight is wired into drafting, because 365 of 379 rows are
NULL.

A trap if that happens: `decks_with_contents` always emits a `weight` key, so
`item.get("weight", 1)` returns **null**, not the default — the key exists. In
Godot 4.6 `float(null)` then raises *"Invalid call. Nonexistent 'float'
constructor."* `Utils._entry_weight()` handles the missing-key and present-NULL
cases separately for exactly this reason; covered by
`tests/unit/autoloads/test_utils_weighted_pick.gd`.

## RLS posture

*Verified against `pg_policies`, `pg_class.relrowsecurity` and
`information_schema.role_table_grants` on 2026-08-26.*

The **anon key ships inside the game binary**
([api_manager.gd:6](https://github.com/turnergregs/azoth/blob/main/scripts/autoloads/api_manager.gd#L6))
and is trivially extractable. Any policy granted to `anon` is granted to every
player who has ever installed the game.

### Grants are wide open; RLS is the only gate

`anon` and `authenticated` hold **all seven privileges** — SELECT, INSERT,
UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER — on every table and view in
`public`. That is Supabase's default `GRANT ALL ON ALL TABLES ... TO anon,
authenticated`, and it has not been narrowed.

So grants restrict nothing. **RLS is doing all of the work.** It is enabled on
all 28 tables (`relrowsecurity = true`); only `games` additionally has it
*forced* (`relforcerowsecurity`).

The practical consequence: **removing or misconfiguring one policy exposes a
table completely**, because there is no second layer behind it. Treat every
policy change as a security change.

### Tables with policies

| Table group | anon policies | Assessment |
|---|---|---|
| `cards`, `aspects`, `bosses`, `events`, `heroes`, `decks`, `deck_contents`, `macros`, `custom_actions`, `custom_properties` | SELECT only | ✅ No vandalism path — confirmed, not assumed |
| `games` | SELECT + INSERT + UPDATE `using (result is null)` | ⚠️ World-readable run history. The UPDATE lets `send_stats` close the stub row `open_run` wrote; a finished run can never be rewritten |
| `drafts`, `draft_items`, `boss_fights` | SELECT + INSERT | ⚠️ Any player can read every other player's draft history |
| `players` | SELECT + INSERT | ⚠️ World-readable, though it holds no PII beyond a display name |
| `reports` | INSERT only | ✅ **The correct pattern** |
| `turns`, `turn_nodes`, `levelups` | INSERT only | ✅ Follows `reports` |

No UPDATE or DELETE policy exists anywhere except the narrow `games` one, so
anon can add and read but never modify or destroy.

### Tables with RLS enabled and NO policy

**RLS on with no policy means deny-all.** Nine tables are in this state:

`card_attributes`, `card_elements`, `card_types`, `consumables`,
`deck_content_types`, `deck_types`, `deck_usage_types`, `fate_types`, `rituals`

This is what produces the empty reads described in
[Which key you are holding](#azothbot-which-key-you-are-holding) — `rituals` and
`consumables` are not empty tables, they are invisible ones. It also means the
game itself cannot read them over the anon key, so either it doesn't need them or
something is quietly broken; worth confirming which.

`macros` is the opposite case: it *has* a public read policy and still returns
nothing, so that table is genuinely empty.

### Two consequences of INSERT-only turn-grain tables

Both cost a debugging cycle during the 0.8.0 rollout:

- **No upsert.** Any `Prefer: resolution=…` makes PostgREST emit `ON CONFLICT`,
  which must read the conflicting row and therefore needs SELECT.
- **`return=minimal` is mandatory**, not an optimisation. The default
  `return=representation` returns the inserted rows, which also needs SELECT.

Both fail with the same `42501 new row violates row-level security policy` as a
missing policy would.

**New analytics tables must be INSERT-only for anon and must not grant SELECT.**
The `games`/`drafts` pattern is the one to avoid, not the one to copy. And note
that a **view** over such a table bypasses this entirely unless it sets
`security_invoker = true` — see [Views](#azothbot-views).

### [AzothBot] None of this applies to the bot

The deployed bot holds the **service-role** key, which bypasses RLS completely.
Every restriction in this section — INSERT-only turn-grain tables, no UPDATE, no
DELETE — is invisible to it. The bot can read, rewrite and delete anything.

That is what makes `require_authorized` in `safe_interaction` the real access
control: a stray write from an authorized user has no database-level backstop.
It is also why the four `/delete_*` commands were removed on 2026-08-27 rather
than guarded — the guard was already the only thing there. See [ARCHITECTURE.md](ARCHITECTURE.md#safe_interaction).

**New analytics tables must be INSERT-only for anon and must not grant SELECT.**
The `games`/`drafts` pattern is the one to avoid, not the one to copy.

---

## Query caveats

**1. Always filter by `version`.** Old rows predate columns, predate bug fixes,
and include developer testing recorded before the `testing` guard was tightened.

**2. `result IS NULL` means two different things, split by version.**

- **Before `0.8.0`** — legacy rows written before the column existed (~4,338 of
  6,480 as of 2026-08-25). Noise; exclude them.
- **From `0.8.0`** — an **abandoned run**. `open_run()` writes the row when the
  run starts, so a NULL result now means the player quit, crashed, or is *still
  playing*. This is real data, not noise: it's the only way to see runs that
  never reached an outcome, which the end-of-run-only path could never record.

Distinguish "abandoned" from "in progress" by the last turn's timestamp:

```sql
select g.uuid, max(t.started_at) as last_turn
from games g join turns t on t.game_uuid = g.uuid
where g.result is null and g.version >= '0.8.2'
group by g.uuid
having max(t.started_at) < now() - interval '1 day';
```

Completion-rate questions must therefore say which population they mean.
`where result is not null` answers "among runs that finished"; including NULLs
answers "among runs that started" — and those are very different numbers.

**3. `no_boss_key` counts as a WIN.** It means the player beat act 4 but wasn't
at a high enough level to unlock the final boss. Any win-rate calculation must
treat `no_boss_key` and `victory` together. `victory` (defeating the act 5 boss)
is a rarer extra achievement — and as of 2026-08-25 that boss is overtuned, so
`victory` legitimately has zero rows.

**4. `restart` is a different population.** Written by
`SaveManager.clear_save` on abandonment, not a terminal game end.

**5. Most historical rows are developer testing.** Median `turns_played` is 1 —
the developers opening the game, confirming something, closing it. Filter on
`turns_played` and `version` or you're measuring dev workflow.

**6. `ritual` is unreliable before 2026-08-25.** `main.gd:404` had the field
commented out of the `send_stats` body, so normal runs fell through to the
column default of 0 while `clear_save` sent the real value. Non-zero rituals
before that date are almost all restarts.

**7. `highest_combo` is text and can't be averaged.** Combo is an
arbitrary-precision BigNum growing exponentially; even cast to numeric, a
linear-space mean is dominated by the largest observation. Use
`turn_nodes.combo_log10`.

**8. Boss turns and regular turns are not comparable.** A boss turn runs until
someone dies and holds many times the nodes of a regular turn. Pooling them
makes per-turn averages meaningless. Filter on `turns.boss_id is null`.

**9. Co-op runs are recorded once per participant — turn rows included.** Rows
sharing a `shared_run_id` describe one session from several clients, and each
client writes its own `games`, `turns` and `turn_nodes`. So an N-player co-op
session multiplies **every** turn-grain row by N.

This is worse than it is on `games`, because per-turn averages silently absorb
it: a co-op boss turn appears N times with near-identical numbers, dragging any
mean toward co-op pacing. Either restrict to `game_type = 'solo'` or dedupe on
`shared_run_id` before aggregating anything at turn grain.

```sql
-- the simple, safe default for balance questions
where g.game_type = 'solo'
```

**10. Custom-content runs are never recorded.** `GlobalVars.testing` is set at
startup by `main.gd::_check_for_custom_content()` when any custom deck, hero, or
boss exists, and suppresses `send_stats` entirely. Headless bot runs go through
`HeadlessMain` and never reach the send path. The dataset is players on stock
content only.

**11. Later tables under-record (legacy chain).** `send_stats` chained
sequentially — `games` → `drafts` → `draft_items` → `boss_fights` — each firing
only on the previous one's success
([game_stats.gd:395](https://github.com/turnergregs/azoth/blob/main/scripts/autoloads/game_stats.gd#L395)). Any failure
partway meant every downstream table silently got nothing, so absence of a
`boss_fights` row is not evidence that no boss was fought.

**12. `created_at` is not when the thing happened.** With per-act flushing it's
the insert time, potentially many minutes later. Use `started_at` for anything
time-series.

**13. Never store or trust a ratio.** Record numerator and denominator; divide
in SQL. Averaging per-run averages weights a 3-turn run equally with a 40-turn
one.

---

## Worked examples

### Links per regular turn, with variance

The question this schema was reshaped to answer.

```sql
select count(*)          as turns_sampled,
       avg(n)            as mean_links,
       var_samp(n)       as variance,
       stddev_samp(n)    as stddev
from (
  select t.id, count(tn.id) filter (where tn.kind = 'link') as n
  from turns t
  join games g on g.uuid = t.game_uuid
  left join turn_nodes tn on tn.turn_uuid = t.uuid
  where t.boss_id is null            -- regular turns only (caveat 8)
    and g.result is not null         -- exclude legacy rows (caveat 2)
    and g.version >= '<cutoff>'      -- (caveat 1)
  group by t.id
) x;
```

The `left join` is load-bearing. An inner join drops zero-link turns and
reintroduces the exact bias this design exists to prevent.

### Nodes until patterns cleared

**Right-censored** — turns that never clear contribute no numerator, so a bare
mean is biased optimistic precisely where difficulty is highest. Always report
both numbers.

```sql
with per_turn as (
  select t.uuid,
         min(tn.node_index) filter (where tn.patterns_after = 0) as nodes_to_clear
  from turns t
  left join turn_nodes tn on tn.turn_uuid = t.uuid
  where t.boss_id is null
  group by t.uuid
)
select count(*)               as turns_total,
       count(nodes_to_clear)  as turns_cleared,
       avg(nodes_to_clear)    as mean_when_cleared
from per_turn;
```

"Cleared in 2.3 nodes, 78% of the time" is the honest statement. "2.3" alone is
not.

### Level-up pick rate

```sql
select o.reward,
       count(*)                                              as times_offered,
       count(*) filter (where o.reward = any(l.chosen))      as times_taken,
       count(*) filter (where o.reward = any(l.chosen))::float / count(*) as pick_rate
from levelups l, unnest(l.options) as o(reward)
group by o.reward
order by pick_rate desc;
```

Raw pick counts are uninterpretable without `options` — common rewards look
popular purely because they're offered more.

---

## Changes

| Date | Change |
|---|---|
| 2026-08-26 | **Captured the nine views into `db/migrations/`**; they had existed only in the live database. |
| 2026-08-26 | **Rebuilt the eight analytics views.** Cutoff `0.6.7` → `0.8.2`, `restart`/co-op excluded, `avg_combo` → `avg_combo_log10`, `draft_rates_view` reshaped to one row per item. 1,836 games → 2. |
| 2026-08-26 | Added `version_key()`, `analytics_cutoff()`, `combo_numeric()`. |
| 2026-08-25 | **Bumped project version to `0.8.0` — the analytics cutoff.** Every query should filter `version >= '0.8.2'`. |
| 2026-08-25 | Added `turns`, `turn_nodes`, `levelups` (`db/migrations/2026-08-25_add_turn_grain.sql` in the game repo). |
| 2026-08-25 | `starting_patterns` / `patterns_*` count `exhaust` as well — exhausted cards shuffle back into the deck at end of turn, so an exhausted pattern is still unsolved. |
| 2026-08-25 | Per-act flush added. `games` rows are now opened at **run start**, so `result IS NULL` on a 0.8.0+ row means abandoned or in-progress — see caveat 2. |
| 2026-08-25 | Stats writes switched to `return=minimal` — Supabase no longer echoes inserted rows back as egress. |
| 2026-08-26 | Turn-grain writes reverted to **plain INSERT**. Upsert (`resolution=…`) and the default `return=representation` both require SELECT, which these INSERT-only tables deliberately withhold. Idempotency moved client-side: 409 is treated as success. |
| 2026-08-26 | Fixed `turn_nodes` batches being rejected with `PGRST102` — link and skip rows had different key sets. Rows are now projected onto a fixed column list. |
| 2026-08-26 | Fixed the run-end flush losing a race against `GameStats.initialize()`. It ran from an async callback, so every run that didn't cross an act boundary lost **all** of its turn data, silently. Now flushed synchronously before the POST. |
| 2026-08-26 | Added a `FLUSH_EVERY_TURNS` (5) trigger — act boundaries alone are too sparse for short sessions. |
| 2026-08-26 | `send_stats` now honours `GlobalVars.testing` itself rather than trusting callers. |
| 2026-08-26 | The run's `games` row is opened on its **first save**, not at `start_game()` — starting a run and backing out no longer writes a phantom row. |
| 2026-08-26 | `ON DELETE CASCADE` across `games → turns → turn_nodes → levelups` (`2026-08-26_cascade_turn_grain.sql` in the game repo). |
| 2026-08-26 | Both Bane fields are **counts, not flags** — multiple Banes per run is a design under consideration. |
| 2026-08-26 | Added `games.ascenders_bane_count` — the run-invariant half. `banes_purged` alone can't distinguish "never solved it" from "no Bane". |
| 2026-08-26 | Dropped `turns.bane_outstanding` — derivable from `games.ritual` plus `turn_nodes.banes_purged`; storing derived state is what this schema exists to avoid. |
| 2026-08-26 | **Ascender's Bane separated** from the recurring pattern load — `turn_nodes.banes_purged` (`2026-08-26_separate_ascenders_bane.sql` in the game repo). Cutoff moved to **0.8.2**: 0.8.1's pattern metrics are unusable on Ritual 5+ runs and can't be repaired. |
| 2026-08-25 | Froze `boss_fights` — 888 rows retained, no longer written. |
| 2026-08-25 | Dropped `cards_duplicate` (stale copy of `cards`), by hand. |
| 2026-08-25 | Dropped `card_stats` (unused) and `games.player_id` (superseded by `player_uuid`) (`db/migrations/2026-08-25_cleanup.sql` in the game repo). |
| 2026-08-25 | Fixed `games.ritual` never being sent on normal runs (`scripts/main.gd:404` in the game repo). |
| 2026-08-25 | Reverted an earlier `turns`/`links` draft before any rows were written — it modelled a boss fight as spanning many turns (it spans exactly one) and had no concept of skips. |

## Deferred

- **`turn_nodes.card_ids bigint[]`** — which cards were played in each link. The core card-balance question. Held back pending a check on whether boss weapon tokens have `cards(id)` rows.
- **`turns.starting_resources` / `starting_stats` jsonb** — full `GlobalVars` dicts for the long tail. Cheap, and the one category that can't be backfilled, so **add before launch** rather than "later."
- **Retention.** Nothing prunes. Not needed until roughly 100k real runs, but the schema grows without bound until something does.

---

## [AzothBot] Keeping this mirror current

| When | Do |
|---|---|
| The game repo's `DB_SCHEMA.md` changes | Copy the change here, preserving the `[AzothBot]` sections |
| A view is added or altered | Update [Views](#azothbot-views) **and** [ANALYTICS.md](ANALYTICS.md) |
| A new analytics table appears | Check whether the bot's key can read it, and note it under [Which key you are holding](#azothbot-which-key-you-are-holding) |
| Content table columns get pulled | Fill in [Not captured here](#not-captured-here) — the CRUD commands depend on those columns |

The `[AzothBot]` sections have no upstream equivalent and should not be pushed
back to the game repo.
