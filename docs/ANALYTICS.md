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

**2. `0.9.0` is the analytics cutoff** (raised from `0.8.2` on 2026-08-28 —
`db/migrations/2026-08-28_bump_analytics_cutoff.sql` in the game repo), and as of
the 2026-08-26 rebuild every game-facing view enforces it via
`analytics_cutoff()`. Bump that one function to move the cutoff; don't edit WHERE
clauses. `azoth_logic/stats_format.CUTOFF_VERSION` mirrors it for the footer and
must move with it, or the footer states a threshold the views aren't enforcing.

**3. The trustworthy dataset is small, but no longer negligible.** As of
**2026-09-03** there are **21 solo, non-restart runs** at `0.9.0`–`0.9.2` (90
games at those versions before the `restart` and co-op filters), carrying 590
card draft offers. It was **2 games** when the cutoff was raised on 2026-08-28,
so this is real growth — but 21 runs still means a per-hero or per-player split
is one or two runs wide, and any `/stats` number that looks substantial is
worth checking against its footer before it is quoted.

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
| `/stats draft composition` | `draft_deck_view` | Draft pool composition, as bar charts. **The view kept its old name** through two command renames. See [The draft pool](#the-draft-pool) |
| `/stats draft rates` | `draft_rates_view` | Pick rate, per item |
| `/stats draft breakdown` | `draft_dimension_rates_view` | Pick rate by type, element and valence (2026-09-03) |

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
| Added `max_ritual`, `cleared` / `full_clears` / `finished`, `avg_deck_size`, `last_played` | Difficulty reached, and an outcome record — see [What counts as a win](#what-counts-as-a-win) |
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

**Rites are excluded from "Most drafted"** (`2026-08-27_most_drafted_excludes_rites.sql`).
They are an *injected* pool, not a drafted one:
`CardLogic._shuffle_in_injected_pools()` mixes them into every pack from a
weighted budget shared with reactants ([CONTENT_LOADING.md](../../azoth/docs/CONTENT_LOADING.md)),
so a rite is **offered** far more often than any single card. Including them
measured that injection rate rather than the player's choices — and it showed:
before the filter, two of the three players with a `most_drafted` had a **rite**
as the answer.

`draft_picks` is filtered the same way on purpose. It backs the "N picks, nothing
picked twice" message, so counting rites there while the search ignored them
would have described a different set than the one searched.

"Most drafted" is **dropped entirely** when there is nothing to show. ⚠️ That
also swallows the `draft_picks = 0` case, which is not the same news: no picks at
all means draft rows are missing for those runs — a recording fault rather than a
small sample. Nothing has that shape today, but if draft capture ever breaks,
this is where the silence would come from.

### What counts as a win

**Beating the act 3 boss.** That is the milestone the game itself rewards:
[main.gd:1464](../../azoth/scripts/main.gd:1464) grants the next ritual there and
nowhere else. Acts 4 and 5 are bonus content — a run that cleared act 3 and then
died to the act 4 boss **cleared**, and `games.result` still correctly says
`death`.

`public.run_cleared(uuid)` holds that definition
(`2026-08-27_run_cleared.sql`), beside `analytics_cutoff()` and for the same
reason: the version threshold was once duplicated across seven WHERE clauses and
went stale. Only `player_info_view` counts clears today; the next view that needs
to should call this rather than invent its own test.

**The fact was already recorded.** `end_boss_fight("win")`
([game_stats.gd:783](../../azoth/scripts/autoloads/game_stats.gd:783)) stamps
`boss_result` onto the turn row at `main.gd:1460` — the line immediately before
the unlock. So a cleared run is one with a turn at act 3 whose boss fight was
won. No new column, no game change, and it holds retroactively for everything at
0.8.2+.

Two options that were considered and rejected:

| Rejected | Why |
|---|---|
| Write `result = 'victory'` on the act 3 clear | Collapses three different outcomes into one — cleared and stopped, cleared then died in act 4, cleared act 5 — with no way back, and silently changes what `leaderboard_view.result`, the daily report and `bot_runner.gd`'s win counter mean |
| Infer `act_reached >= 4` in the view | A proxy, not the fact. It correlates today, but a run reaching act 4 any other way silently becomes a win — and one view's private definition is how the other five drift |

`cleared` also accepts `result in ('victory','no_boss_key')` as a belt: both
imply the act 3 boss fell, and neither depends on turn rows existing.
`full_clears` counts `result = 'victory'` — the act 5 boss ([main.gd:1478](../../azoth/scripts/main.gd:1478)).

⚠️ **Turn rows only exist from 0.8.0**, so `run_cleared` is false for anything
older. Everything below the cutoff is already excluded, so this costs nothing
today — but lowering the cutoff below 0.8.0 would make those clears invisible.

### Per-act links and pattern clearing (`2026-08-27_player_act_and_pattern_clearing.sql`)

`turn_clearing_view` is one row per regular turn that **had patterns to solve**,
carrying the links and seconds either side of the first node where
`patterns_after = 0` (that column already excludes Ascender's Bane, so no extra
filter is needed). Both `player_info_view` and `player_act_view` aggregate it —
the calculation lives in **one** place, because asking the same question per act
would otherwise have meant a second copy, and two copies of a definition is how
every drift in this schema started.

A turn that never cleared is **kept as a row** with `clear_index IS NULL`.
Dropping it would right-censor the average silently, which is the failure
`DB_SCHEMA.md` calls out by name.

**Tables are built for a phone.** `MOBILE_TABLE_WIDTH = 24`, measured from a
wrapped screenshot: a 24-character header survived, a 36-character one did not.
A wrapped monospace table is worse than no table — the columns stop lining up and
every row breaks somewhere different. The clearing breakdown is therefore **two**
narrow tables (links, then seconds) rather than one wide one.

⚠️ The four generic `/stats` tables are **not** yet within that budget:
`leaderboard` 49, `active_players` 39, `hero` 52, `version` 54 characters. They
wrap on a phone today.

`player_act_view` is one row per (player, act): links per regular and boss turn,
plus the same clearing split. It `FULL JOIN`s the two halves — an act can have
link data with no clearable turns (every turn started with nothing to solve), and
dropping either side would lose a row that has something to say.

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
| `draft_deck_view` loses `7v`–`10v` | ⚠️ **This was wrong.** Stated as "valence is 1–6, so those columns were permanently zero". The pool holds four cards above valence 6, and 24 with no valence at all, so the field described 108 of 136 cards. Reversed 2026-09-03 — see [The draft pool](#the-draft-pool) |
| `draft_deck_view` gains rites | `events` was permanently zero for the same reason — the view could not see them. Widened to `usage_type in ('draft', 'rite')` on 2026-08-27. **Reshaped 2026-09-03**: `events` was the wrong unit, not the wrong subject. Rites are templates drawn with replacement into injected slots, so they are reported as `rite_templates` + `rite_weight_counts` beside the pool rather than pooled into it |
| `leaderboard_view` gains `combo_numeric`, `result` | An explicitly sortable combo column |
| Row counts drop everywhere | Cutoff moved `0.6.7` → `0.8.2`; `restart` runs and co-op duplicates excluded |

Three helper functions now carry the rules:

| Function | Purpose |
|---|---|
| `version_key(text)` | Numeric sort key — `0.8.2` → `8002`. Returns NULL on anything unparseable instead of raising, which is what the old inline `split_part(...)::integer` did on a two-component version string |
| `analytics_cutoff()` | The cutoff, in one place — `9000` (`0.9.0`) since 2026-08-28. Was duplicated across seven WHERE clauses, which is why it went stale |
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

### The draft pool (2026-09-03)

`/stats draft_pool` renders three fields: what is in the pool, the element
split, and the valence spread. The last two are **bar charts**, and the element
one is coloured — the nearest of the eight colours Discord will draw inside an
` ```ansi ` fence to the colours the game itself uses (`GlobalVars.ELEMENTS`,
mirrored in `card_layout.ELEMENT_COLORS`).

| Element | Game | Discord | Why |
|---|---|---|---|
| anima | `#8769E9` | blue `34` | **None of the eight is purple.** Blue `#268bd2` beats pink `#d33682` on RGB distance (105 vs 138) and hue (49° off, against 77°) — anima is a blue-violet at hue 254, and pink lands on the magenta side of it, reading as a different colour family. It was pink until 2026-09-03 |
| blood | `#EF1212` | red `31` | |
| sol | `#F9A410` | yellow `33` | |
| catalyst | — | white `37` | The default: no colour, for no element. Grey `30` was the first choice for that reason and is unreadable in practice — `#4f545c` on a `#2b2d31` block |

**`catalyst` is the bucket of cards with no element**, named after what 23 of
those 24 cards are. ⚠️ It is still *defined* as a NULL element, not as the type:
Waxix is an elementless `spell` and is counted there. If more elementless spells
arrive the label will describe fewer and fewer of the cards under it, and wants
renaming again.

The charts are not decoration. Both fields are **distributions**, and a
distribution written out as `1v 23 · 2v 26 · 3v 20` is arithmetic homework — the
shape only appears if you do the division yourself.

#### Why these are three commands and not one

Grouped under `/stats draft` on 2026-09-03, and deliberately **not merged into
one reply**. They are neighbours, but they do not rest on the same thing:

| | Population | Cutoff |
|---|---|---|
| `composition` | content — 136 cards, 54 aspects | none; a deck has no version |
| `rates`, `breakdown` | ~2 games at `0.9.0` | `analytics_cutoff()` |

**One embed carries one footer.** A merged reply would have to either claim
`version >= 0.9.0` over the composition numbers, which are not version-filtered
at all, or drop the cutoff over the rate numbers — the thing this document opens
by saying not to do. Grouping gets the tidiness without the lie.

Two smaller reasons. `rates` takes `limit`/`order`/`item_type` and the other two
take nothing, so a merged reply would re-render identical static content on
every *"least picked, aspects only"* query while pushing the half you asked for
further down. And `draft_rates_view` still counts rites while
`draft_deck_view` no longer does — one reply showing them in half of itself
invites the misreading they were removed to prevent.

#### The breakdown (2026-09-03)

`draft_dimension_rates_view` — one row per (`dimension`, `bucket`), where
dimension is `type` (card / aspect / rite, every offer), `element` or `valence`
(cards only — nothing else carries either, and that is a real absence rather
than a gap to fill). It answers what neither neighbour can:
not what the pool holds, and not how one item does, but whether **a whole class
of card is being ignored**. On 2026-09-03 it says catalysts are 18% of the pool
and taken 12% of the times they are offered, against 30% for anima and blood —
which is the shape you would retune a pack on, over 590 offers.

Three things about it are load-bearing:

- **It aggregates from `draft_items`, not from `draft_rates_view`.** That view
  ends in `having count(*) >= 5` — a floor so a single offer cannot report a
  100% pick rate. Grouping over its output would average only the items that
  cleared the floor, biasing every bucket toward frequently-offered cards. A
  censored sample, which is the failure [DB_SCHEMA.md](DB_SCHEMA.md) names by
  hand — and not a small one: on 2026-09-03 `draft_rates_view` held **331** card
  offers against the **590** that actually happened, so 44% of the data was gone
  before any grouping started.
- **No floor of its own, and `times_offered` on every row.** At bucket grain the
  denominators are large because they pool many items; the ones that aren't are
  visible as thin rather than hidden. `9v` is 0% off five offers, and the table
  says so.
- **Cards only, by construction.** `element` and `valence` live on `cards`, so
  the inner join is the whole scope statement. It carries the same
  cutoff/`restart`/solo filters as `draft_rates_view` on purpose — two views
  answering neighbouring questions over different populations is how a
  comparison between them goes wrong.

It renders as a **table, not a bar chart**: these are four independent rates
that do not sum to 100, and bars would invite reading them as slices of one pie.
The bucket order comes from the same functions that order the composition charts
(`_element_order` / `_valence_order`), so the two line up row for row — which is
what makes *"27% of the pool, 38% of picks"* legible at a glance.

⚠️ The offer count in the footer is read off **one** dimension. Every offer is
counted once under its element and again under its valence, so a sum over the
view is exactly double — the same trap `scoreboard_sample` documents.

#### Rites: templates, not pool members

**A rite is drafted.** It is picked out of a pack like anything else and goes to
the events zone to be held and later spent
([EVENTS.md](../../azoth/docs/EVENTS.md) in the game repo). An earlier pass
today said rites were "injected, not drafted" and removed them from this view
entirely; the first half of that is true and the conclusion does not follow.

What differs is **how a rite reaches the pack**, and therefore what
*composition* means for it:

| | How it enters the pool | Copies |
|---|---|---|
| card, aspect | The three `draft` decks load 190 items into the draft zone | exactly one each |
| rite | `_shuffle_in_injected_pools()` then adds `floor(p·pool/(7−p))` further slots — **21** at the default `p = reactant_pool_percent = 0.7` — each filled by an independent weighted draw **with replacement** | 0, 1 or more; the deck is never consumed |

So `22` and `136` are different quantities and adding them gives a number that
is neither. Rites get **their own field, in their own units** — templates and
weights, with the injected-slot count derived beside them:

> **22** rites · equal weight
> *≈21 injected slots (~10% of the pool) at the default rate, drawn with
> replacement — so ~38% of templates miss a given run*

That last figure is the one a flat count of 22 hides. Twenty-one draws over
twenty-two equally-weighted templates is `(1 − 1/22)²¹` ≈ **38%** chance that any
given rite is absent from a run entirely.

**Weight is what governs a template's share** — `deck_contents.weight`, falling
back to `CardLogic.REACTANT_DEFAULT_WEIGHT = 0.25` when NULL, which is every row
today, so the draw is uniform. `rite_weight_counts` reports the distribution
rather than 22 names; the per-rite view is `/stats draft rates`. When the
weights stop being uniform the closed form above stops holding, and the field
**withholds** the absent-share rather than printing a plausible wrong number.

⚠️ Two constants here are mirrored from the game and can go stale:
`stats_format.INJECTED_POOL_PERCENT` (0.7) and the reactant fallback weight.
Every number derived from them is labelled *"at the default rate"* for that
reason. And the count is scoped to `usage_type = 'rite'` — reactants share the
same budget and the same draw, but every reactant deck has been archived since
2026-08-27, so the rite pool *is* the injected pool today. If reactants return
they belong in that count, and the slot estimate is wrong until they are added.

#### The correction about rates

The same earlier pass said counting rites beside cards "implies a comparability
that does not exist". For a raw **count** that is right, and it is why
[`most_drafted` excludes them](#the-player-card-2026-08-27) — a count-ranked list
measures the injection rate rather than the player's choices. For a **rate** it
is backwards: pick rate is conditional on the item being offered, so the
injection budget — which governs how often a rite is offered and nothing else —
divides straight back out.

So `/stats draft breakdown` compares all three types directly, and on 21 solo
runs at `0.9.0`+ they land within three points of each other:

| Type | Pick rate | Offers |
|---|---|---|
| card | 26% | 590 |
| aspect | 27% | 212 |
| rite | 25% | 126 |

**Rites are taken at the same rate as everything else** — a fact the old framing
could not have surfaced, because it had ruled the comparison out.

⚠️ One loose end. Rites were **13.6%** of observed offers against the **10%** the
injection formula predicts (126 of 928, where ~93 was expected — about 3.6
standard deviations). That is unexplained. It could be the Sacrament aspect, a
`reactant_pool_percent` that is not 0.7 in play, pack sampling that is not
uniform over the pool, or the pool size at load differing from the 190
`deck_contents` rows. **It has not been chased down**, and it is worth a look
before anyone tunes the injection rate on these numbers.

#### The valence field was short by 28 cards

The reason for the migration underneath. `draft_deck_view` carried a **column
per valence** and only six of them: `2026-08-26_rebuild_analytics_views.sql`
dropped `7v`–`10v` on the stated grounds that "valence is 1–6
(docs/GAME_OVERVIEW.md), so those four columns were permanently zero".

That was false when it was written. The live pool holds

| Card | Element | Valence |
|---|---|---|
| Circumvent | blood | 7 |
| Ouroboros | anima | 9 |
| Trifold | blood | 9 |
| Apex | sol | 9 |

plus **24 cards with no valence at all** — the colourless catalysts and one
spell — which no column ever counted either. So the field summed to 108 of the
pool's 136 cards. Crucially it did not *read* as a distribution missing 28
cards; it read as a complete one that happens to stop at 6.

This was the **second** time this view failed this way. Its `events` column was
permanently zero for the view's whole life because the Rites deck's usage type
postdated the filter (2026-08-27). One cause both times: **a fixed set of
columns standing in for an open set of values**, where a value with no column of
its own is not reported as missing, it is not reported at all.

`db/migrations/2026-09-03_draft_pool_histograms.sql` (game repo) replaces the
eight element/valence columns with two jsonb histograms keyed by the value
itself:

```
element_counts  {"anima": 37, "blood": 38, "sol": 37, "catalyst": 24}
valence_counts  {"1": 23, ..., "6": 7, "7": 1, "9": 3, "none": 24}
```

Every card lands in a bucket by construction, valence-less ones under `none`
rather than nowhere, and there is nothing left to widen the next time the game
grows a value. `combo` is gone by name as well — it counted cards with a NULL
element while `combo` means the exponential run score in every other view here.

**Breaking:** `anima`, `blood`, `sol`, `combo` and `1v`–`6v` no longer exist.
`/stats draft_pool` is the only consumer; it reads the histograms when they are
present and the old columns when they are not, and **says in the reply** that
the numbers are incomplete in the second case — the bot is hand-started and may
be running either side of the migration, but a partial distribution must never
be drawn as a whole one.

#### Two rules the rendering keeps

- **A non-zero count always gets at least one bar cell.** Scaled to the largest
  bucket, a rare valence rounds to nothing, and a bar that renders empty says
  *none* — the same false statement the missing column made. Small buckets are
  never dropped or merged either.
- **The `—` row leads the valence chart.** Having no valence is not having more
  of it than 9, and a row sitting under the scale reads as the far end of it. It
  carried a caption explaining itself when it was at the bottom; at the top it
  does not need one.

### Still open

| Issue | Detail |
|---|---|
| `hero_info_view` returns one row | The data, not the SQL — see below |
| ~~`draft_deck_view.combo` definition~~ | Settled 2026-09-03. The two definitions — NULL element, vs NULL element **and** NULL valence — select the **same 24 rows**: every colourless card in the pool is valence-less and vice versa. The column is now `colourless` in `element_counts` and the valence histogram reports the `none` bucket beside it, so a future divergence is visible as two different numbers rather than hidden inside one |
| `most_drafted` has no denominator | Still a comma-joined label on `player_info_view`. Per-item numbers live in `draft_rates_view` now |
| The trustworthy dataset is ~2 games | Nothing to do but wait for play at `0.9.0`+ |

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

### Three deliberate design decisions

All three fix real bugs. Don't undo them without understanding why they're there.

**The day is claimed *before* the send.** `_claim_and_send` writes
`last_sent_date` and persists it, then sends. A failed or partial send therefore
skips that day rather than retrying — the trade-off that stopped a duplicate
message flood. For a single-instance bot, skipping beats spamming.

**The state file is written atomically** — `mkstemp` in the same directory, then
`os.replace`. A crash mid-write would otherwise truncate the file, which
`_load_state` silently reads back as "no channels registered", losing every
channel's config.

**Nothing propagates out of `_send_due_channels`.** The sweep both the loop and
the startup pass run catches everything, per channel and per cycle. That looks
like the swallowing this codebase otherwise avoids, and it is the opposite: an
exception reaching `tasks.Loop` **stops the loop for the life of the process**,
so absorbing it here is what keeps the schedule alive. See the third bug
below.

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

### Three bugs, all fixed

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

**2026-09-01 — the report stopped, silently and permanently.** `_claim_and_send`
raises on a data or build error *on purpose*: raising is what leaves the day
unclaimed and therefore retryable, and a test pins it
(`test_report_error_does_not_consume_the_day`).

But the raise went straight into nextcord's `tasks.Loop`, whose
`_valid_exception` tuple covers only `OSError`, `GatewayNotFound`,
`ConnectionClosed`, `aiohttp.ClientError` and `asyncio.TimeoutError`. Anything
else is printed to stderr and **re-raised**, ending the loop. No
`@daily_update_task.error` handler was registered, so nothing restarted it.

So the failure was not "one report is late". The day stayed correctly unclaimed
while the mechanism that would have retried it no longer existed — every
subsequent report was lost too, with no Discord-visible symptom and only a
traceback on a console nobody watches. The catch-up pass could not help either:
it runs at startup, and the process never restarted.

The loop body is now `_send_due_channels`, which catches per channel (one bad
channel cannot take the others with it) and per cycle. A restart-on-error
handler was considered and rejected: `Loop.restart()` from inside the error
handler cancels the task from within itself, and a failure that recurs every
iteration turns into a hot restart loop. Making the body total is simpler and
has no such edge.

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
   | ~~Drop the permanently-zero `7v`–`10v` columns (valence is 1–6)~~ — done, and **wrong**; reversed 2026-09-03 | `draft_deck_view` |
5. **Add turn-grain commands** once there's enough post-cutoff data to be worth
   querying.
