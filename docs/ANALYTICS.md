# Analytics

> **The `/stats` commands render embeds, not raw JSON** (2026-08-27).
> `azoth_logic/stats_format.py` does the formatting; the commands do the I/O.
> `avg_combo_log10` displays as `10^x` because that is what it means, and every
> reply footers the cutoff and the game count so no number arrives without its
> denominator.

What AzothBot reports, where those numbers come from, and which of them you can
currently trust.

**This is the *read* side.** For the schema, the columns and the caveats that make
a query right or wrong, see [DB_SCHEMA.md](DB_SCHEMA.md). For how the game
*writes* this data, see `docs/ANALYTICS.md` in the game repo.

---

## Read this first

Three facts govern everything below.

**1. The key in your `.env` decides what you can see.** The turn-grain tables
return zero rows with an HTTP 200 under an anon key — not an error. See
[DB_SCHEMA.md § Which key you are holding](DB_SCHEMA.md#azothbot-which-key-you-are-holding).

**2. `0.8.2` is the analytics cutoff**, and as of the 2026-08-26 rebuild every
game-facing view enforces it via `analytics_cutoff()`. Bump that one function to
move the cutoff; don't edit WHERE clauses.

**3. The trustworthy dataset is currently tiny.** As of 2026-08-26 there are
roughly **2 games** at `version >= '0.8.2'`, out of ~6,500 total. Any `/stats`
number that looks substantial is drawn almost entirely from data the cutoff exists
to exclude.

---

## The `/stats` commands

Each subcommand fetches one view. The aggregation is all in SQL; Python does the
rendering only — `azoth_logic/stats_format.py` turns a view into a table or a set
of embed fields. They dumped raw JSON into a code block until 2026-08-27.

| Command | View | Purpose |
|---|---|---|
| `/stats leaderboard` | `leaderboard_view` | Top combos, optionally by player / hero / version |
| `/stats player` | `player_info_view` | One player's aggregates |
| `/stats active_players` | `player_activity_view` | Play counts and hours |
| `/stats hero` | `hero_info_view` | Per-hero aggregates |
| `/stats version` | `version_info_view` | Per-version aggregates |
| `/stats draft_pool` | `draft_deck_view` | Draft pool composition. Command renamed 2026-08-27; **the view kept its old name** |
| `/stats draft_rates` | `draft_rates_view` | Global pick rates |

### The player card (2026-08-27)

`player_info_view` was rebuilt (`db/migrations/2026-08-27_player_info_view_v2.sql`
in the game repo). What changed and why:

| Change | Reason |
|---|---|
| `most_picked_hero` **dropped** | One hero exists. It answered "Lumis" for everyone |
| `avg_combo_log10` **dropped**, `max_combo` → **`best_combo`** (full text) | Log space is the right summary for an *average* over an exponential quantity — which is why `hero_info_view` and `version_info_view` keep it. A player card shows one player's single best run, and a maximum is not distorted by a distribution, so there was nothing for log space to fix; the two columns were one number twice |
| `most_drafted` floor **3 → 2**, plus `most_drafted_count` | **This is why it read NULL.** At 0.8.2 Turner has 30 picked items over 2 games and nothing was picked more than twice, so `having count(*) >= 3` excluded everything and `string_agg` over an empty set returned NULL |
| Turn **counts** dropped (`avg_turns`, `max_turns`) | Act reached says the same thing at the granularity anyone reads it at, and `avg_act` / `max_act` were already there |
| **Links per turn** added, regular vs boss | The measure the turn-grain schema was reshaped to answer. Follows the worked example in [DB_SCHEMA.md](../../azoth/docs/DB_SCHEMA.md) exactly, including its two load-bearing parts: `left join turn_nodes` (an inner join drops zero-link turns and reintroduces the bias the design exists to prevent) and `kind = 'link'` (so skip nodes are not counted) |
| **`regular_turns_sampled`** / **`boss_turns_sampled`** | The link sample is scoped to *finished* runs — an abandoned run's last turn is mid-flight — so it covers a smaller population than `game_count`. An average with no denominator is what caveat 6 is about |
| Added `max_ritual`, `wins` / `finished`, `avg_deck_size`, `last_played` | Difficulty reached, and an outcome record. A win is `victory` **or** `no_boss_key` |
| **`draft_picks`** added (`2026-08-27_player_info_draft_picks.sql`) | A NULL `most_drafted` has two causes that look identical: *picked things, none of them twice* (expected on a small sample) and *no draft rows at all* (a recording problem). Without the pick count the card can only shrug, and the second hides behind the first. Appended with `CREATE OR REPLACE`, so grants survive |

⚠️ **The link averages are unverified against live data.** `turns` and
`turn_nodes` are INSERT-only for anon, so AzothBot cannot check coverage. Before
trusting them:

```sql
select count(*) filter (where t.boss_id is null)     as regular_turns,
       count(*) filter (where t.boss_id is not null) as boss_turns,
       count(tn.id) filter (where tn.kind = 'link')  as link_nodes
  from turns t
  join games g on g.uuid = t.game_uuid
  left join turn_nodes tn on tn.turn_uuid = t.uuid
 where version_key(g.version) >= analytics_cutoff();
```

Zeros there mean the link columns are NULL by construction — and the card says
*"no turn-level data yet"* rather than showing **0.0 links per turn**, which
would be a striking and completely false statistic.

The rendered card withholds a **win rate** below 5 finished runs: "50%" over two
runs is one win wearing a decimal point.

"Most drafted" is **dropped entirely** when there is nothing to show. ⚠️ That
also swallows the `draft_picks = 0` case, which is not the same news: no picks at
all means draft rows are missing for those runs — a recording fault rather than a
small sample. Nothing has that shape today, but if draft capture ever breaks,
this is where the silence would come from.

### Per-act links and pattern clearing (`2026-08-27_player_act_and_pattern_clearing.sql`)

`player_act_view` is one row per (player, act): links per regular turn and per
boss turn, each with the turn count behind it. Turn counts are in the table
rather than a footnote — "4.9 links in act 3" can rest on three turns, and a
difference between acts is only a difference if the samples are real.

`player_info_view` gains the pattern-clearing columns: links and seconds either
side of the first node where `patterns_after = 0`. That column already excludes
Ascender's Bane, so no extra filter is needed for it.

Three things make it honest, and all three are load-bearing:

1. **Right-censored.** Turns that never clear contribute no numerator, so a bare
   mean is biased optimistic exactly where difficulty is highest.
   `cleared_turns` / `clearable_turns` travel with it, and the card says
   *"cleared on 7 of 9 turns (78%)"*. A player who never cleared gets
   *"Never cleared"*, not an average over an empty set.
2. **Turns with nothing to clear are excluded** (`starting_patterns = 0`). Such
   a turn "clears" at node one having done nothing, and counting it drags every
   before-average toward zero.
3. **Regular turns only.** Every pattern question in `DB_SCHEMA.md` filters
   `boss_id is null`, and a boss turn is a different activity.

The clearing NODE counts as *before* — it is the link that finished the job. The
boundary in time is the start of the *next* node, since the clear happened during
the clearing one, so the two phases partition the turn exactly.

Autocomplete sources: `active_players_view` for players, `heroes` for heroes, and
`game_stats` for versions — ⚠️ **`game_stats` does not exist**, so the version
autocomplete silently returns nothing on every keystroke.

### Rebuilt 2026-08-26

`db/migrations/2026-08-26_rebuild_analytics_views.sql` in the game repo fixes the
defects the capture migration documented. **Breaking changes for anyone reading
these views by column name:**

| Change | Detail |
|---|---|
| `avg_combo` → **`avg_combo_log10`** | Renamed on purpose so stale readers break loudly instead of quietly reporting a meaningless number |
| `draft_rates_view` reshaped | One row **per item** with numerator and denominator, not one row of comma-joined strings |
| `draft_deck_view` loses `7v`–`10v` | Valence is 1–6; those columns were permanently zero |
| `draft_deck_view` gains rites | `events` was permanently zero for the same reason — the view could not see them. Widened to `usage_type in ('draft', 'rite')` on 2026-08-27 (`db/migrations/2026-08-27_draft_pool_include_rites.sql`, game repo) |
| `leaderboard_view` gains `combo_numeric`, `result` | An explicitly sortable combo column |
| Row counts drop everywhere | Cutoff moved `0.6.7` → `0.8.2`; `restart` runs and co-op duplicates excluded |

Three helper functions now carry the rules:

| Function | Purpose |
|---|---|
| `version_key(text)` | Numeric sort key — `0.8.2` → `8002`. Returns NULL on anything unparseable instead of raising, which is what the old inline `split_part(...)::integer` did on a two-component version string |
| `analytics_cutoff()` | The cutoff, in one place. Was duplicated across seven WHERE clauses, which is why it went stale |
| `combo_numeric(text)` | `highest_combo` as numeric, or NULL if malformed — so one bad row can't take down every combo view |

#### The combo fix

This is the substantive change. The views reported
`round(avg(highest_combo::numeric), 2)` — a linear-space mean of an exponentially
growing BigNum, which is dominated entirely by the largest observation.
`hero_info_view` was reporting an "average" of `1.9e30`.

It is now `avg(log10(combo))` — a mean in log space, i.e. the geometric mean.
**Read it as an order of magnitude:** `4.2` means "typically around 10^4.2".
`max_combo` stays linear, since a maximum isn't distorted by the distribution.

`docs/DB_SCHEMA.md` names `turn_nodes.combo_log10` as the canonical source. The
rebuild computes log10 from `games` instead, because turn-grain data starts at
`0.8.0` and is ~2 runs deep — sourcing it there would make these views empty
today. Same quantity; revisit when turn rows are plentiful.

### Still open

| Issue | Detail |
|---|---|
| `hero_info_view` returns one row | The data, not the SQL — see below |
| `draft_deck_view.combo` definition | Counts cards with NULL element; AzothBot's `merge_staging` used NULL element **and** NULL valence. The two disagree, and which is right is a content question. `/merge_staging` was hidden 2026-08-27, so nothing acts on the second definition today — but it is the one to reconcile against if the command comes back |
| `most_drafted` has no denominator | Still a comma-joined label on `player_info_view`. Per-item numbers live in `draft_rates_view` now |
| The trustworthy dataset is ~2 games | Nothing to do but wait for play at `0.8.2`+ |

### A note on `hero_info_view`

It returns exactly **one row** — Lumis, ~1,836 games. That one is the *data*, not
the SQL: every sampled game has `starting_hero = 7`. There are 20 heroes in the
`heroes` table and essentially no diversity in recorded play. Don't build hero
comparisons until that changes.

### The view definitions are in version control (2026-08-26)

Captured as-found in the game repo at
`db/migrations/2026-08-26_capture_existing_views.sql` — nine views, recorded
verbatim from `pg_get_viewdef()` with their defects annotated but **not** fixed,
so the file is a trustworthy restore point. Fixes go in a later migration.

The capture turned up a ninth view nobody was tracking: **`decks_with_contents`**,
which inlines deck contents as JSON and is consumed by the game / Codex editor,
not by AzothBot. It is also the only place `deck_contents.position` and
`deck_contents.weight` appear — two columns documented nowhere, and which
AzothBot's `add_to_deck()` never sets.

Re-run the capture query after any view change:

```sql
select c.relname, pg_get_viewdef(c.oid, true)
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind in ('v','m') order by 1;
```

### None of the views set `security_invoker`

`reloptions` is NULL on all nine, so each runs with its **owner's** privileges and
bypasses RLS on the tables beneath it. This cuts both ways:

- It is exactly the mechanism needed to serve aggregates to a restricted role
  without exposing rows — so the option we wanted is already available.
- It also means **a view placed over `turns`, `turn_nodes`, `levelups` or
  `reports` would silently become anon-readable**, defeating the INSERT-only
  policy on those tables. Set `security_invoker = true` on any new view over
  them unless anon exposure is intended.

---

## What the bot does not use yet

The game gained three turn-grain tables in `0.8.0` — `turns`, `turn_nodes` and
`levelups`. **No AzothBot command reads any of them.** With the service-role key
the deployed bot can, so this is unbuilt surface rather than a blocker.

What they make answerable, none of which the current views can express:

| Question | Source |
|---|---|
| Links per regular turn, with variance | `turns` left-joined to `turn_nodes` — the `turns` table is the honest denominator, including zero-node turns |
| Nodes until patterns cleared | `turn_nodes.patterns_after = 0`, reported as a pair — "cleared in 2.3 nodes, 78% of the time" |
| Level-up reward pick rates | `levelups.options` vs `levelups.chosen`. Raw pick counts are uninterpretable without the offer denominator |
| When the act 4→5 gate opens | `turn_nodes.banes_purged` against `games.ascenders_bane_count` |
| Combo growth over a run | `turn_nodes.combo_log10` — **in log space**, the only correct way to aggregate combo |
| Boss fight pacing | `turns.boss_id` / `boss_result` plus the boss columns on `turn_nodes` |

Worked SQL for several of these is in
[DB_SCHEMA.md § Worked examples](DB_SCHEMA.md#worked-examples).

**Before writing any of them, walk the checklist** in
[DB_SCHEMA.md § Writing a query: start here](DB_SCHEMA.md#writing-a-query-start-here).
It covers the mistakes that produce a query which runs cleanly and answers the
wrong question.

---

## The daily report

`/daily_update enabled:True` registers the current channel for a scheduled report
covering the previous day (CST). It is **per-channel** — each channel carries its
own send time and its own dedup date.

### Scheduling

- A `tasks.loop(minutes=10)` checks every registered channel.
- A channel fires when the current UTC time is past its `send_hour_utc` /
  `send_minute_utc` and it hasn't already sent today.
- A startup pass catches up on reports missed while the bot was down — which
  matters, because [the bot is not reliably always-on](DEPLOYMENT.md).
- Disabling preserves `last_sent_date`, so toggling off and on the same day
  doesn't re-send.

State lives in `daily_update_state.json` at the repo root (gitignored):

```json
{"channels": {"<channel_id>": {
  "send_hour_utc": 18, "send_minute_utc": 0, "last_sent_date": "2026-08-25"
}}}
```

### Two deliberate design decisions

Both fix real bugs. Don't undo them without understanding why they're there.

**The day is claimed *before* the send.** `_claim_and_send` writes
`last_sent_date` and persists it, then sends. A failed or partial send therefore
skips that day rather than retrying — the trade-off that stopped a duplicate
message flood. For a single-instance bot, skipping beats spamming.

**The state file is written atomically** — `mkstemp` in the same directory, then
`os.replace`. A crash mid-write would otherwise truncate the file, which
`_load_state` silently reads back as "no channels registered", losing every
channel's config.

### What the report contains

Built from `games`, `players`, `drafts`, `draft_items` and the turn-grain tables
(`turns`, `turn_nodes`, `levelups`) for the previous CST day:

| Section | Contents |
|---|---|
| Players & Games | Unique players, new players, games started, restarts, co-op rows |
| Highlights | Highest level / act / combo |
| Session Stats | Avg duration, avg turns, total playtime, **avg links per regular turn**, **avg links per boss turn** |
| Game Results | Outcome breakdown; NULL shows as `abandoned / in progress` |
| Boss Fights | Boss turns, wins and losses — from `turns.boss_result` |
| Most Picked Level-Up Rewards | Pick rate as `taken/offered`, from `levelups.chosen` vs `levelups.options` |
| Draft Activity | Most / least drafted, and picks seen in high-combo games |

**Regular and boss turns are reported separately and must stay that way.** A boss
fight *is* one turn and runs until someone dies, so it holds many times the nodes
of a regular turn — pooling them makes both averages meaningless
([caveat 8](DB_SCHEMA.md#query-caveats)). On a sample day the two were 2.4 and
7.9 links; a pooled figure would describe neither.

Two denominators are load-bearing:

- **Turns with zero nodes stay in the link average.** That is the entire reason
  the `turns` table exists; counting only turns that produced nodes reintroduces
  the bias it was built to remove.
- **Level-up rewards divide by `options`, not by pick count.** Common rewards are
  offered far more often than rare ones and would top any raw-count list on
  volume alone. On the sample, `Life` was offered 38 times and taken 9 (24%)
  while `Hero` was offered 11 and taken 9 (82%) — opposite conclusions from the
  same data depending on the denominator.

Only solo games feed the turn-grain section: co-op records one row per
participant and would multiply every row
([caveat 9](DB_SCHEMA.md#query-caveats)). `result` is deliberately *not* filtered
there — an abandoned run's completed turns are perfectly good data.

Embeds split automatically at 5,800 characters (Discord's limit is 6,000) and
field values truncate at 1,024.

### Two bugs, both fixed

**June 30 — `unsupported operand type(s) for +: 'int' and 'str'`.** The draft
score was `level_reached + highest_combo`. `level_reached` is `bigint` → `int`;
`highest_combo` is **`text`** → `str`. Fixed by `_to_number`, which coerces both.

**June 19 — ~30 duplicate messages.** The old code persisted `last_sent_date`
only *after* a successful send. If `channel.send` raised partway through the
embed list, the messages already sent stayed out, nothing was claimed, and the
10-minute loop retried forever. `_claim_and_send` now persists the claim
**before** sending. Simulated over six cycles with every send failing: 1 message,
then five skips. The old logic gave 6 and climbing.

The trade-off is deliberate: a genuinely failed send means that day is **skipped,
not retried**. For a single-instance bot, skipping beats spamming.

### Fixed 2026-08-26

| Was | Now |
|---|---|
| Boss section read `boss_fights`, frozen since 2026-08-25 — reported zero every day | Reads `turns` where `boss_id is not null`, using `turns.boss_result`. **A boss fight is one turn** |
| "Top performing picks" scored `level_reached + highest_combo` — linear plus exponential, so combo swamped it | `avg(log10(combo))`, relabelled **"Picks Seen in High-Combo Games"** — it is a correlation, and the name now says so |
| Averages pooled restarts and co-op rows | Counts stay inclusive (a restart is still activity); averages use completed solo runs only, and the field label states the denominator |
| NULL `result` displayed as `unknown` | `abandoned / in progress` — on 0.8.0+ that is real data, not a gap |
| `game_type` was never selected | Added, along with `version` |

### Caveats specific to the report

- **`_to_number` exists because PostgREST returns large numerics as strings.**
  `highest_combo`, and sometimes `elapsed_sec` and `turns_played`, arrive as
  `str`. It falls back to a default rather than crashing — so a value it cannot
  parse silently contributes **0**, not its real value.
- **The boss section needs the service-role key.** `turns` is INSERT-only for
  anon. The report checks `SUPABASE_ROLE` and prints an explicit "unavailable"
  rather than reporting zero, which is what the frozen-table bug looked like.
- **No version filter.** Deliberate — a daily activity report covers whatever was
  played yesterday, and yesterday's builds are current by definition.
- **Draft batching is capped at 50 ids per request** to keep URLs short, so a
  heavy day makes many round trips.
- **The claim-before-send window depends on `_fetch_daily_stats` being
  synchronous.** It blocks the event loop, which is what stops the startup task
  and the catch-up pass from both claiming the same day. Making it async without
  adding a lock reintroduces a double-send.

---

## If you're fixing this

A rough order, cheapest and most valuable first:

1. ~~**Dump the view definitions into `db/migrations/`.**~~ Done 2026-08-26 —
   `2026-08-26_capture_existing_views.sql`, nine views.
2. ~~**Make `fetch_all` distinguish failure from emptiness.**~~ Done 2026-08-26 —
   failures raise, and a pre-flight guard rejects reads the loaded key can't
   perform. See [ARCHITECTURE.md § The Supabase layer](ARCHITECTURE.md#the-supabase-layer).
3. **Fix the version autocomplete** — point it at `games`, not the nonexistent
   `game_stats`.
4. **Rebuild the views on the caveats.** Smaller than it first looked — the
   version-filter machinery already exists and just has a stale threshold:

   | Change | Where |
   |---|---|
   | Bump the threshold `6007` → `8002` | 5 views |
   | Add a version filter | `active_players_view` (has none) |
   | Guard `split_part(...)::integer` against a 2-component version, which raises | all 5 filtered views |
   | Replace `avg(highest_combo::numeric)` with `turn_nodes.combo_log10` | `hero_info_view`, `player_info_view`, `version_info_view`, `player_activity_view` |
   | Exclude `result = 'restart'` | all game-facing views |
   | Add `game_type = 'solo'` | all game-facing views |
   | Emit numerator/denominator instead of `string_agg` | `draft_rates_view` — a rewrite, not a patch |
   | Version-filter the `most_drafted` LATERAL, which is currently unfiltered while its own row is | `player_info_view` |
   | Drop the permanently-zero `7v`–`10v` columns (valence is 1–6) | `draft_deck_view` |
5. **Add turn-grain commands** once there's enough post-cutoff data to be worth
   querying.
