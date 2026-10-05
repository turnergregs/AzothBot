# Analytics

> **The `/stats` commands render embeds, not raw JSON** (2026-08-27).
> `azoth_logic/stats_format.py` does the formatting; the commands do the I/O.
> `avg_combo_log10` displays as `10^x` because that is what it means. Every
> image report states its population and version cutoff in its header, and its
> counts beside its numbers, so no number arrives without its denominator. No
> report carries a text footer since 2026-10-02 (Turner: it repeated the header
> and added notes nobody needed).

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

**2. `0.9.10` is the analytics cutoff** (raised from `0.9.0` on 2026-09-25 —
`db/migrations/2026-09-25_bump_analytics_cutoff.sql` in the game repo — for the
release that let in the new playtesters; `0.8.2` → `0.9.0` was 2026-08-28), and as of
the 2026-08-26 rebuild every game-facing view enforces it via
`analytics_cutoff()`. Bump that one function to move the cutoff; don't edit WHERE
clauses. `azoth_logic/stats_format.CUTOFF_VERSION` mirrors it for the report headers
and must move with it, or they state a threshold the views aren't enforcing.

**3. The trustworthy dataset restarted at the 0.9.10 bump.** The figures below
are from **before** it and describe the `0.9.0` cutoff; the 0.9.10 population
has not been measured yet. As of **2026-09-03** there are **21 solo, non-restart runs** at `0.9.0`–`0.9.2` (90
games at those versions before the `restart` and co-op filters), carrying 590
card draft offers. It was **2 games** when the cutoff was raised on 2026-08-28,
so this is real growth — but 21 runs still means a per-hero or per-player split
is one or two runs wide, and any `/stats` number that looks substantial is
worth checking against its counts before it is quoted.

---

## The `/stats` commands

Each subcommand fetches one view. The aggregation is all in SQL; Python does the
rendering only — `azoth_logic/stats_format.py` turns a view into a table or a set
of embed fields. They dumped raw JSON into a code block until 2026-08-27.

| Command | View | Purpose |
|---|---|---|
| `/stats leaderboard` | `leaderboard_best_view` | Each player's best run, ranked, as an image table: combo, hero with that run's ritual, furthest act. Top three bold. Everyone by default. See [Leaderboard](#leaderboard-2026-09-29) |
| `/stats player` | `player_run_view` + `player_summary_view` + `player_info_view` + `survey_responses` | One player's profile, drawn as an image. See [The player card (2026-09-28)](#the-player-card-2026-09-28) |
| `/stats players` | `player_summary_view` | Who is playing and where their active time goes (runs, custom runs, Codex, art tools), and what was made in the Codex. Drawn as an image. See [Players](#players-2026-09-28) |
| `/stats breakdown by:hero\|ritual\|version` | `breakdown_view` | Runs grouped three ways, drawn as an image: how far runs got in the act colours, beat-act-3 rate with outlier flags, then hero activations (hero) or per-turn averages (ritual, version). `players:` cohort filter. See [Breakdown](#breakdown-2026-09-28) |
| `/stats bosses` | `boss_fight_view` | Each boss's win rate by act, read against the act's overall rate. **The first image report.** See [Reports as images](#reports-as-images-2026-09-28) |
| `/stats links` | `link_turn_view` + `link_type_view` | Links per regular and boss turn (zero-link turns included), the share of each link type, and each type's average length against every multi-card link's. `players:` cohort filter. See [Links](#links-2026-10-03) |
| `/stats surveys` | `survey_answer_view` | The game's one-click survey (game repo `docs/SURVEYS.md`): surveys shown, share answered, comments; where each was shown, split answered / skipped / ignored; then each question's answers, `understood` split run 1 vs run 3, under 5 answers grey. `players:` cohort filter, **default Everyone but us** (surveys began after the cutoff, so few "new" players have answered). No version cutoff: every row postdates it |
| `/stats item` | `boss_split_view` / `draft_item_split_view` / `hero_split_view` | One boss, card, aspect, rite or hero, its rate per version (or ritual, or hero), each against the rest of its kind in that group. Drawn in the item's colour beside its face or art. See [One item](#one-item-2026-09-29) |
| `/stats draft picks` | `draft_offer_view` | Pick rate by type (packs included), each kind of draft pack, element, valence and embellishment kind, each against its section (or bare cards), with flags. See [Draft reports](#draft-reports-2026-09-29) |
| `/stats draft items` | `draft_item_offer_view` | The five most and five least picked cards, aspects and rites, one group per type, each against its own type |
| `/stats draft pool` | `draft_deck_view` | What the draft pool holds (content, not play) |

### Rituals in /stats (2026-09-28)

New playtesters arrived with 0.9.10 and "which heroes and rituals are they on"
became the question asked most. `db/migrations/2026-09-28_ritual_stats.sql` in
the game repo adds what answers it:

| Change | Reason |
|---|---|
| `player_activity_view.max_ritual` | The highest ritual each player has *played*, on any hero. Unlock progress is stored on the player's machine and never reaches the database |
| `player_run_view` (one row per run) | Serves the player card's hero table and both charts. `furthest_act` is the larger of `act_reached` and the run's highest turn-row act, because `act_reached` is only written when a run ends and is NULL on every abandoned run. `cleared` is `run_cleared()`. Reads `turns`, so service_role only |
| "Highest Ritual" on the player card → a per-hero table | Ritual ladders are per hero. One max across heroes read R1 on Lumis as R1 everywhere |
| `/stats hero` + `/stats version` → `/stats breakdown by:` | Same columns, different GROUP BY. A third grouping (ritual) would have been a third copy |
| One bar chart, `stats_format.histogram()` | The daily report drew its act chart with its own copy until this change. Charts are used where a reply has one number per row; tables stay where it has several |

### Reports as images (2026-09-28)

`/stats all` put every report side by side and showed the limits of text in
embeds: Discord wraps code blocks instead of scrolling them, so every chart was
squeezed into 24 characters, and ANSI text has eight colours. The reports are
being redrawn as images, one at a time, each reviewed in Discord before the next.

- `azoth_logic/stats_charts.py` is the drawing layer and the house style: a
  `Card` of stacked blocks (section header, bar row, note, spacer) on a dark
  surface, drawn at 2x, in DejaVu Sans from matplotlib. Blocks are added as
  reports need them.
- `azoth_logic/stats_cards.py` holds one function per report, rows in, `Card`
  out: what comes first, what is grey, what each number is read against.
- `stats._send_card` draws off the event loop and sends the image in an embed
  with nothing else in it. Population and cutoff go in the card's header.

The rules, from the dataviz guidance the reports were audited against: pick the
chart from the data's job; label values directly (an image has no hover); one
accent colour for one measure and grey for anything not asserted; hairline
chrome; and every card states its population and sample size.

**Hero colours never carry identity alone.** Checked with a colour-blindness
validator: Eith and Essra are nearly indistinguishable to protanopes, and Lumis
and Nebul are too pale for a dark card. Hero charts always label the hero. The
element colours pass.

**`/stats bosses`** is the first. Each boss's win rate (player wins over
finished fights) is drawn against a line at its act's overall rate, pooled over
every fight in the act rather than averaged over bosses, with the difference in
points beside it: Caleb's review of the first draft, since later acts are meant
to be harder. Bosses under 5 finished fights are grey and sort after the
reliable ones. An act with one fought boss draws no line, since a baseline of
one boss is that boss. Unfought bosses are named, not drawn: "Not yet fought:
..." under their act, and an act nobody reached is its header alone.

**Outliers** are flagged red ▼ (clearly harder) or blue ▲ (clearly easier),
and every other bar is neutral grey so the flags are what the eye finds. Not
standard deviations across the act's bosses, which ignore fight counts and let
an outlier inflate its own baseline: a boss is flagged when its whole 95%
Wilson range sits below or above the pooled rate of the act's OTHER bosses
(`stats_cards.boss_flag`, `FLAG_Z`). Only bosses with 5+ finished fights are
flagged. The marker is drawn in the flag colour, and a 0% bar is a dot, so a
flagged boss with no wins is never invisible. The card carries no legend.

**Cohorts.** Every player is `developer` (`players.developer`: Turner and
Caleb), `veteran` (any game below the cutoff) or `new`, from the game repo's
`player_cohort_view`. Views that support it group by `cohort`, and
`stats_cards.select_cohort` sums the ones asked for: `players:` is New
playtesters (the default), Everyone but us, or Everyone. A boss only other
cohorts fought still appears, as unfought. On a view without the column the
filter is skipped and the header says Everyone, rather than labelling everyone
as new playtesters.

### Players (2026-09-28)

`/stats players` is the engagement report: who plays, and how each player
spends their time with the game. It replaced `/stats active_players` and
`/stats engagement`, and reads `player_summary_view` (one row per player,
with the cohort column, so `players:` works as elsewhere; the old
`stats.DEVELOPERS` name list is gone in favour of the `developer` cohort).

- **Headline tiles:** players, runs, time in runs, time in the Codex.
- **Where their time goes:** a bar per player whose length is their total
  active time (one scale for everyone) split into runs, custom runs, Codex and
  art tools, in the dataviz palette's first four slots. Then runs and top
  ritual. Most active first; capped at 15 rows with a "+ N more" line.
- **Made in the Codex:** one tile per kind of thing made (new cards, card
  edits, exports...), most first.

**Two sources of time.** Run time is `games.elapsed_sec`, recorded by every
build: wall-clock with the run open, idle included, so every player has it all
the way back. Custom runs, Codex and art tools exist only in the tracker's
active time (`engagement_spans`), from the first build carrying
`EngagementTracker`. The tracker's own `run` spans are ignored so runs are not
counted twice. Idle counts on one side only, so the tools' share reads slightly
low (the cautious direction). A roster with no time at all drops its bars and
time column.

**The legend rule** (Turner, 2026-09-28) applies to every image report: a
legend lists only what the chart draws, and a section with nothing in it is
not drawn. So the act legend omits acts nobody reached, this card's legend
omits surfaces nobody used, and "Made in the Codex" appears only once
something has been made. `stats_charts.Legend` states the rule.

### Breakdown (2026-09-28)

`/stats breakdown` groups runs by hero, ritual or version (`by:`) and is drawn
as an image from `breakdown_view`: one row per (dimension, group, cohort,
furthest act) carrying counts only, so the row count stays bounded however many
runs pile up and every rate is divided from totals in the bot.

Every card leads with **how far runs got**: a bar per group split by the
furthest act each run reached, in the game's act colours (`ACT_COLOURS`,
checked colour-blind-safe as neighbours), with each segment's run count inside
it and a legend below. Then the share that **beat act 3** and the run count.
Groups under 5 runs are dimmed (colours pulled toward the surface, so the acts
still read). A group is flagged red ▼ / blue ▲ when its whole 95% Wilson range
sits below / above the rest of the runs, with 5+ runs on **both** sides
(`stats_cards.group_flag`, the same `rate_flag` bosses use): 26 runs at ritual 0
are not judged against 2 at ritual 1. With two groups the flags come in pairs.

What follows depends on the question (Turner's review):

- **hero:** hero activations per regular turn and per boss fight: how usable
  and strong each ability is. Skips and links work the same on every hero and
  are left out.
- **ritual, version:** skips, hero activations and links, averaged per regular
  turn. Links scale to the node budget (5); the others to the largest group.
  Version only lists versions at the cutoff or above: the point is the releases
  this wave played.

Grouping by skips or by activations was prototyped and cut: a row of "runs that
never activated" beside those runs' average skips read as a percentage of
players. It retired `/stats habits` (players bucketed by their own rates) with
it; a better view of those habits is still to be designed.

### The player card (2026-09-28)

Redrawn as an image, and cut from ten sections to what a profile answers: how
much this person plays, how far they get with each hero and ritual, and what
they make.

- **Subtitle:** their cohort ("New playtester", "Veteran", "Developer") and
  when they last played, so a lapsed player stands out.
- **Tiles:** runs, act 3 wins, best combo (`player_info_view`), time in runs
  and in the Codex (`player_summary_view`), each only when it has a value.
- **Heroes:** one act bar per hero from `player_run_view`, as on the
  breakdown, labelled with the highest ritual played on that hero ("Lumis R0").
  It replaced the heroes table, the games-by-ritual chart and the furthest-act
  chart.
- **Most drafted** and **Made in the Codex** appear only when there is
  something in them.
- **Surveys** (2026-10-04), only for a player shown one: answered of shown,
  a line per question with their answers (the feedback post's layout), then
  their three newest comments full width, each under the question it
  answered, wrapped to two lines. Read straight from `survey_responses` by the
  uuids `players` holds for that name (`stats._player_surveys`); a failed read
  drops the section, not the reply.

**Dropped:** "max reached" (best and average act, level, deck size), which the
act bars say better; and the links-per-turn table and chart and both
pattern-clearing tables. How someone plays their turns is balance data, not a
profile, and averaged over one player's handful of runs it is noise.
`player_act_view` and `player_link_view` are no longer read.

### The player card (2026-08-27, text; superseded)


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
| `analytics_cutoff()` | The cutoff, in one place — `9010` (`0.9.10`) since 2026-09-25. Was duplicated across seven WHERE clauses, which is why it went stale |
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

### Leaderboard (2026-09-29)

A ranked table of PLAYERS, not runs: ranking runs let one player hold half a
small community's board. `leaderboard_best_view` holds each player's best run
per hero (ties to the earlier run), so the bot takes the best per player, or
per player with one hero when `hero:` is given. A table rather than bars:
combos grow exponentially (2^2048 against 65.5K), so any bar is one giant and
nine slivers; the number is the point, and huge ones read as powers of two.
The top three are bold with a hairline under them. It is the community board,
so it counts everyone by default; `players:` narrows it. `player:` and
`version:` went with the text version: a player's own best is on their card,
and every run is at the cutoff. The combo is compared as a Decimal: 2^2048
passes a float's range.

### One item (2026-09-29)

`/stats item item:<name> by:version|ritual|hero players:`. Built after Veln:
its hp was halved in 0.9.11, and `/stats bosses`, which pools every version at
the cutoff, still ranked it the hardest boss. A pooled rate cannot show a fix;
the same item split by version can.

- **Measures.** A boss: the player's win rate over finished fights. A card,
  aspect or rite: pick rate. A hero: beat act 3 (`run_cleared`, as
  `/stats breakdown`). `by:hero` is the run's starting hero, and is refused for
  a hero (a hero split by hero is itself).
- **Each group against the rest of its kind in that SAME group.** Veln in
  0.9.11 against act 2's other bosses in 0.9.11; a card against every other card
  offered that version. A version where everything got easier then does not
  read as a fix to this item. The flag is `rate_flag` against that rest, with
  the usual floors (5 fights, 10 offers, 5 runs) under which a group is grey.
- **A version is when players ran a build, not when the item changed.** Content
  is live from the database, so a 0.9.10 client that synced after an edit got
  the new numbers. Turner's call: dating every change is too much work, and the
  numbers moving is what matters.
- **Drawn as columns for an ordered axis** (version, ritual): one column per
  group in the item's colour, a white tick at the rest of its kind, the value on
  the column (lifted over the tick only when the tick would cut it). The scale
  tops at the next 25% above the tallest mark. A low sample is an outline. The
  latest 10 versions are drawn; the tiles count all of them. `by:hero`
  stays as horizontal bars: heroes have no order, and names fit rows better.
- **The item's own colour** (`stats_cards.item_colour`): a boss's act colour, a
  card's element, an aspect's art accent (`primary_color`: aspect colours are
  reversed, CARD_RENDERING.md), a rite's palette `primary_color`, a hero's
  `color`. So a flag is the ▼/▲ marker alone, never the column's colour: a blood
  card's red column is not a warning.
- **The picture.** `stats_thumbs.thumbnail`, the one I/O step, off the event
  loop: a card, aspect or rite is its rendered face (the deck grid's still, so
  its text is on it); a boss or hero is its art shaded in its colour and cut to
  a circle, with no ring (Turner's review), since the bot has no face renderer
  for either. No picture draws the card without one.

**Why three new views and not a column on the old ones.** Each split view
carries its kind's totals per group on every row (a window sum, item
included; the bot subtracts), so a lookup reads the one item's handful of rows.
Adding version, ritual and hero to `boss_fight_view` or
`draft_item_offer_view` would have multiplied the rows every other report
fetches, and at the time `fetch_all` did not page past PostgREST's 1000-row
cap. It does now (2026-09-29, ARCHITECTURE.md), but a lookup of one item's
rows is still one small request rather than several pages.

### Links (2026-10-03)

How many links a turn holds, which types they are, and how long they run.
The game repo's `2026-10-02_link_views.sql` adds two count views, both per
cohort so the bot sums the cohorts asked for (`select_cohort`):
`link_turn_view` (turns per regular/boss and links played) and
`link_type_view` (links per regular/boss, type key and size). Both read
`turns` / `turn_nodes`, so service_role only.

- **Zero-link turns are counted.** The link count is a correlated subquery, not
  an inner join to `turn_nodes`, for the reason under *Links per turn* above.
- **A one-card link is its own row ("All"),** and every link valid as all three types is
  folded into it (2026-10-03). A one-card link is valid as all three
  (`LINK_VALIDATION.md` in the game repo), so it arrives as
  `group+set+sequence` at size 1. A longer all-three link is the same thing
  padded: Group (equal valences) and Sequence (consecutive ones) cannot both
  hold for two cards that count, so it is one card plus Inert or valence-less
  cards (catalysts). Only Transmutable, or Inert scoped to one link type, could
  make it otherwise, and no shipped card has either. The row is left out of the
  length section and the average-length tile, which are the same number.
- **None** is a link with no type: Circumvent's ("next link ignores
  link requirements") replaces `valid` but not the types validation found, so a
  link of cards matching nothing resolves with none. Real play, not a dev
  setting: a run with `ignore_link_requirements` on is a testing run and never
  uploads. A Circumvent link that happens to match a type counts under that type.
- **Links per turn is a histogram** (2026-10-03, `stats_charts.Histogram`):
  one column per link count, gaps kept, with a line at the average. As rows it
  had to bucket boss turns in fives to fit; columns fit every count to ~30.
  Up to 10 columns each carries its share; past that only the tallest does
  and the gridlines carry the rest.
- **Link types merge regular and boss turns**; links per turn keeps them apart,
  since a boss turn is a different turn.
- A type with fewer than 10 links has its average length greyed, and does not
  set the length chart's scale (3 links averaging 10.7 cards once squeezed every
  other bar into the first quarter); a greyed row past the scale runs full.
- A share that rounds to 0 reads `<1%`.

The card was drawn on 2026-10-02 and the command added the next day; it shipped
without one, which `test_every_stats_report_is_run_by_stats_all` now catches.

### Draft reports (2026-09-29)

Redrawn as images, three subcommands where there were four. The views behind
`picks` and `items` count offers per cohort (`2026-09-29_draft_offer_views.sql`
in the game repo), so they take `players:`; `pool` is content and does not.

- **`picks`** (`draft_offer_view`): tiles for offers, picks and pick rate, then
  a pick-rate bar per bucket by type and element, each against its
  section's pooled rate with the `rate_flag` red/blue flags and 10+ offers to be
  flagged; then each embellishment kind (upgraded, attribute, enhanced) against
  **bare cards**, since one card can carry two kinds. The bare-vs-embellished
  split itself was cut (Turner's review): embellished cards are expected to be
  picked more; which kind lifts a card most is the useful part.
  **Draft packs** (game 2026-09-29, `2026-09-29_draft_packs.sql`): a pack offered
  in a draft is a `type` bucket whose `picked` means **opened**, so "By type" gains
  a Packs row; a **Packs** section under it has an open-rate bar per kind (named
  as printed on the pack: Atoms, Catalysts, Aspects, Rites, from `pack_type`),
  flagged against the rest, and its header gives the share of opened packs that
  gave a pick (`in_pack` picks over opens; the rest were Skipped). What is
  offered inside an opened pack still counts as offered everywhere else, element
  and valence included. A view with no pack offers draws
  the card exactly as before.
  **Valence is columns** (2026-10-03, `RateColumns` with one shared `baseline`
  line at the section's pooled rate): an ordered axis, and the question is the
  slope, whether heavier cards are picked less. Every valence to 10 is a
  column, "—" first; one never offered is empty. Same flags and 10-offer floor
  as the rows. Only the offer count sits under each column: picked/offered
  under twelve columns ran together, and the rate is on the column. `items` is unchanged: its view keeps one row per
  item and gained `offered_in_pack` / `picked_in_pack`, which it does not read
  yet.
- **`items`** (`draft_item_offer_view`): the five most and five least picked
  cards, then aspects, then rites, one group per type (split 2026-09-29 at
  Turner's request; they had ranked together against one all-items rate). Each
  item is read against its OWN type's pick rate and flagged against the rest of
  its type: ranked together, one kind could fill both lists, and a card is
  tuned against other cards. Only items offered 5+ times rank (the header
  says so). A type with nothing ranked is not drawn; one with fewer than
  ten ranked items draws a shorter Least picked, never an item twice.
- **`pool`** (`draft_deck_view`): cards, aspects, rite templates and the
  estimated rite slots per run as tiles (rites beside the pool, never in it),
  then cards by element in the game's element colours and by valence, the
  last a **histogram** since 2026-10-03 (`stats_charts.Histogram`, counts on
  the columns): every valence 1-10 drawn, "—" first, so a hole in the pool
  shows as one. The live pool had none at 7 or 10 when it was drawn.

It retired `draft_dimension_rates_view`, `draft_embellishment_rates_view` and
`draft_rates_view` from the bot, and the text formatting helpers with them.
The sections below describe the text versions and are kept for the reasoning.

#### Why these were three commands and not one (2026-09-03; superseded)

Grouped under `/stats draft` on 2026-09-03, and deliberately **not merged into
one reply**. They are neighbours, but they do not rest on the same thing:

| | Population | Cutoff |
|---|---|---|
| `composition` | content — 136 cards, 54 aspects | none; a deck has no version |
| `rates`, `breakdown` | games at `0.9.10`+ | `analytics_cutoff()` |

**One reply states one population.** A merged reply would have to either claim
`version >= 0.9.10` over the composition numbers, which are not version-filtered
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

⚠️ The offer count in the header is read off **one** dimension. Every offer is
counted once under its element and again under its valence, so a sum over the
view is exactly double. (`turn_scoreboard_view` has the same trap: every scored
turn is one row per axis plus the rollup, so summing `turns_sampled` counts each
turn six times.)

#### Rites: templates, not pool members

**A rite is drafted.** It is picked out of a pack like anything else and goes to
the rites zone to be held and later spent
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
reason. And the count is scoped to rite rows in `usage_type = 'weighted'` decks
(`rite` until 2026-09-15; `reactant` was folded into `weighted`). A weighted deck
can also hold cards and aspects, which share the same budget and draw, but none
does today, so the rite pool *is* the injected pool. If one gains them they
belong in that count, and the slot estimate is wrong until they are added.

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

### Embellished cards

A drafted card can arrive already upgraded, carrying a rolled attribute, or
wearing an enhancement — off the **Craft** curve, which a level-up reward
raises. Until 2026-09-04 nothing recorded it, which put a confound in **every**
card-level draft number on this page.

It is not a small one. P(the drafted card carries something) runs **7.3% at
Craft 0 to 57.8% at Craft 3**, so the contamination scales with how deep the run
got — it correlates with hero, act and skill rather than averaging out. A card
that looks popular may only be popular *enhanced*.

`/stats draft embellishments` splits it. Read the `bare` and `embellished` rows
against each other; the gap is stated in percentage **points**, because both
rates are already conditional on the card being offered, so their difference is
the quantity that means something. A ratio would be a multiplier on a
probability and explodes as the bare rate falls.

Three cautions:

1. **It excludes `reserved` offers**, which the other draft views do not. This
   changes nothing and never will: the reserve mechanic was **retired on
   2026-09-04**, and it had already been unreachable since 0.7 (1.72% of 28,938
   offers across 27 players in `0.6`, then 0 across 3,162 offers in
   `0.7`/`0.8`/`0.9`, because the Retain-draft-cards button was hidden in
   `hud.tscn`). Zero of the 1,002 offers above the cutoff are reserved, so the
   filter never excludes a row this view would otherwise return and the older
   views were deliberately left alone.
2. **Do not sum `times_offered` across the view.** An offer appears once in the
   `embellished` dimension and again under every kind it carries; the three
   kinds roll independently, so one card can be in `kind` twice.
3. **The per-name rows will be noise for months.** At Craft 0 only 7.3% of
   drafted cards carry anything, and that has to then split four ways across the
   enhancement roster. The `embellished` dimension is the only one with a
   denominator worth reading early — which is why the lift line carries its own
   `n`.

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

*(These two rules came from the row chart; the histogram keeps both: a column
for a count of one is at least `MIN_H` tall, and "—" leads.)*

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
| The trustworthy dataset restarted at `0.9.10` | Nothing to do but wait for playtester runs at `0.9.10`+ |

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
- A channel is owed every report from the day after the last one it got up to
  the newest one due (`_reports_due`): yesterday's, once today's send time has
  passed, and the day before until then. They go out **oldest first**, each
  claimed before it is sent. So a bot that was down, or a report that would not
  build, catches up on **every** day it missed, not only the most recent
  (2026-10-02; before that a gap of two days lost two reports for good).
- The backlog is capped at `MAX_BACKFILL_DAYS` (7). Older missed days are
  dropped with a console line; post one by hand with `/daily_update_repost`.
- A channel with no `last_sent_date` (just registered) is owed only today's
  report, at the send time: registering is not a request for history.
- A startup pass runs the same sweep, so a report missed while the bot was down
  goes out as soon as it is back, without waiting for the send time.
- Disabling preserves `last_sent_date`, so toggling off and on the same day
  doesn't re-send. Re-enabling moves it up to yesterday at the earliest: the
  days a channel was off were declined, not missed, and are not backfilled.
  Re-running the command on a live channel (to change the time) keeps its
  backlog.
- `/daily_update_repost date:` posts one past day's report to the current
  channel without touching the state, so it can neither re-send nor block the
  schedule.

State lives in `daily_update_state.json` at the repo root (gitignored).
`last_sent_date` is the CST day a report was SENT; it covered the day before.

```json
{"channels": {"<channel_id>": {
  "send_hour_utc": 18, "send_minute_utc": 0, "last_sent_date": "2026-08-25",
  "failure_notice_for": "2026-08-20"
}}}
```

### Four deliberate design decisions

All four fix real bugs. Don't undo them without understanding why they're there.

**The day is claimed *before* the send.** `_claim_and_send` writes
`last_sent_date` and persists it, then sends. A failed or partial send therefore
skips that day rather than retrying — the trade-off that stopped a duplicate
message flood. For a single-instance bot, skipping beats spamming.
`/daily_update_repost` is the way back for a day skipped like that.

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

**A report that will not build is said in the channel, once, and holds the
days after it.** The day stays unclaimed and is retried every cycle; the
channel gets one red "Daily Report — <date> failed" embed with the error
(`failure_notice_for` stops it repeating). Later days wait behind it rather
than skipping past, because sending them would advance `last_sent_date` past
the broken day and lose it. Once the fix ships, the whole backlog goes out in
order. Before this, a build error was a console line nobody watches.

**The sweep holds `_SEND_LOCK`** across load → claim → send → save. The loop
and the startup pass both run as soon as the bot is ready, each with its own
copy of the state, and a backlog awaits between reports, so without it the
second pass reads the state mid-backlog and sends a day twice. (One report at
a time relied on `_fetch_daily_stats` being synchronous instead; that stops
being enough at two.) Enabling a channel goes through the same sweep.

### Drawn as one image (2026-09-29)

The report is one image (`stats_cards.daily_card`), titled "Daily Report", with
the day and "Everyone" (developers included) in its header and no text under it
(2026-10-02; it had a footer, and was titled "Yesterday", until then).
`daily_update._build_update_messages` returns the
`channel.send` keyword sets, rendered before the day is claimed so a drawing
failure never uses the day up. A quiet day is still one line of text.

Top to bottom: tiles (players, new, solo runs, time played; then co-op runs,
legacy runs, and tutorial runs when there were any); one act bar for the day with the beat-act-3 share (reaching
act 4 means the act 3 boss fell); **boss fights per boss** (`boss_record`,
new: the text report gave only a total, and a day of Veln winning every fight
hid in it), against the day's overall rate; level-up reward pick rates; and the
three most and two least picked items, cards, aspects and rites together
(`item_rates`). It is a digest, not a verdict: one day is too little to flag, so
nothing is flagged and most rows are dimmed by the usual minimums.
`/stats bosses` and `/stats draft` give the verdicts. **Co-op and legacy runs**
(added the same day at Turner's request) are always shown, 0 included: not
balance data yet, but whether anyone played them is the answer.
`daily_update._count_runs` counts co-op by SESSION (distinct `shared_run_id`,
since every participant writes a row) and legacy on the `format` axis, a
legacy co-op session once. "Solo runs" is solo only, so co-op is never
counted twice. The links-per-turn chart
was dropped with the other per-turn habits. Sections with nothing in them are
not drawn; turn data the key cannot read is a note, not zeroes.

### What the report contained as text (superseded)

Built from `games`, `players`, `drafts`, `draft_items` and the turn-grain tables
(`turns`, `turn_nodes`, `levelups`) for the previous CST day:

| Section | Contents |
|---|---|
| Players / New / Runs | Three labelled counts. Tutorial runs get a fourth, shown only on a day that has any |
| Act Reached | Bar chart over `games.act_reached`, one row per act from 1 to the deepest reached |
| Level-Up Picks | Pick rate as `taken/offered`, from `levelups.chosen` vs `levelups.options` |
| Cards | Top 3 and bottom 3 drafted by pick rate, cards and aspects together |
| Rites | Top and bottom Rite alone |

#### What the report stopped saying (2026-09-17)

It was ~35 lines across ten fields. It is now ~10. What went, and why:

| Dropped | Why |
|---|---|
| Highlights (highest level / act / combo) | Fun, not actionable. The act chart carries progression |
| Session Stats (duration, turns, playtime, links per turn) | Five averages over a handful of runs |
| Game Results breakdown | **Replaced by the act chart** — see below |
| Boss Fights | Also the act chart — advancing an act *is* beating its boss |
| Picks Seen in High-Combo Games | `avg(log10(combo))` over two games by three players. A correlation with itself |
| Draft Activity volume line | Drafts and picks per day answered no question anyone had |
| The methodology asides | *"— of which 1 was a restart"*, *"(one per participant, not per session)"*, *"— not counted above"*, *"N restarts in the opening turn, excluded"*. **The rules did not change** — `_partition_games` still drops opening-turn restarts and still splits tutorial runs onto their own line — they are documented here instead of re-explained in every morning's embed. That prose was the main reason the report read as machine-written |
| `100% (2/2)` | Now `2/2`. The percentage was the same fact twice, and at a day's sample size it was the more misleading of the two |
| Top-5 draft lists | Now top 3. Five entries all at 100% is five ties padded out to length |

**The act chart replaced the results breakdown and the boss section together.**
An act is three regular turns then a boss, and *beating* that boss is what
advances the act, so `act_reached` already encodes boss progress — a run sitting
at act 2 cleared act 1's boss. One ladder says what an outcome list and a
separate "reached a boss" count said between them.

The ladder does not show how a run *ended*: a death and a win can sit at the
same act. A win-count footer was tried and dropped the same day as not worth
the line. If it comes back, count `no_boss_key` alongside `victory`
([DB_SCHEMA.md caveat 3](DB_SCHEMA.md#query-caveats)).

**An act nobody reached still gets a row.** Every act from 1 to the deepest one
reached is drawn, zeroes included: a gap in the middle of the ladder is the
shape worth seeing. The top of the range comes from the data, not from the five
acts the game has today — a fixed set of buckets cannot report a value that
postdates it, which this view layer has already been bitten by twice (the
`draft_deck_view` histograms, `2026-09-03_draft_pool_histograms.sql`).

**Cards and aspects share a list; Rites get their own.** A Rite is a template,
not a pool member: `_shuffle_in_injected_pools` draws Rites *with replacement*
into extra slots. Its pick **rate** is still comparable to a card's — the offer
denominator divides the injection budget out — but its raw **count** is not, so
never switch either list to rank by volume. The Rites list matches **both**
spellings of the item type (`rite` and `event`), because migrations are
hand-applied and this bot runs on either side of the rename.

#### The day is bucketed on `started_at` (2026-09-08)

The report reads runs **started** in the previous CST day. It filtered on
`finished_at` until 2026-09-08, when that column turned out never to have been
written by the game: it carried `default now()`, so it held the moment
`open_run()` inserted the stub row — equal to `created_at` to the microsecond on
124 of 124 rows measured, a median of 8s after `started_at`. The report was
already bucketing by run start; it just did not know that, and said "finished".

`started_at` is now the filter because it is the true run start, it is what this
section counts, and it is the only column that works on **both sides** of
`db/migrations/2026-09-08_games_finished_at.sql`. Once that drops the default, an
open run has `finished_at` NULL and a `finished_at` window would silently drop
the abandoned-run population `open_run` exists to expose. See
[DB_SCHEMA.md caveat 14](DB_SCHEMA.md#query-caveats).

#### Three populations, not one (2026-09-08)

`_partition_games` splits the day's `games` rows before anything else reads
them, and **every number in the report except the tutorial line and the unique-
player count derives from the `regular` bucket**. The split was added after a
report read *"15 games started — of which 13 were restarts"*: 13 of those 15
rows were runs on the Tutorial Deck, nine of them a developer iterating on the
tutorial. The report was describing tutorial iteration as if it were play.

**1. Opening-turn restarts are dropped outright.** `result = 'restart'` with
`turns_played <= 1`. They are counted nowhere. The report used to print how many
it had excluded; that line went with the rest of the methodology asides on
2026-09-17, so this doc is now the only place the exclusion is recorded.

> **The boundary is off by one from the obvious reading.**
> `GlobalVars.turn_count` is incremented at the *start* of a turn
> (`main.gd::handle_turn_start`) and `SaveManager.clear_save` reports the value
> stored in the save, so:
>
> | `turns_played` | means |
> |---|---|
> | 1 | abandoned **during** turn 1 — never reached the first draft (`deck_size` is still the starting size) |
> | 2 | one **completed** turn plus its draft, abandoned during turn 2 |
>
> "Didn't get past the first turn" is therefore `<= 1`, not `<= 2`. Reading it
> as `<= 2` silently deletes real one-turn runs — on the 0.9.0+ set that is the
> difference between 56 and 76 of 103 restarts.
> `test_turn_one_is_abandoned_during_the_first_turn_not_after_it` pins it.

NULL `turns_played` is *kept*: unknown is not "turn 1". No restart row has ever
had one, but assuming short is the wrong direction if that changes.

**2. Tutorial runs are split out, not dropped.** Any run whose `starter_deck`
has `usage_type = 'tutorial'` (deck 31). They get their own line and are
excluded from every average, highlight, draft and turn-grain figure. A scripted
tutorial run's duration, act and combo are not comparable to a drafted one, and
that is true from *both* ends — developer iteration on the tutorial does **not**
trip `TestingConfig.is_testing()` (which only fires on content overrides), so it
uploads exactly like a real player's first walkthrough and neither is a run.

**3. Everything else is `regular`,** and within it counts stay inclusive as
before — a restart that got past the opening turn is still someone playing.

Three failure directions are deliberate:

- **An unreadable or empty `decks` read classifies nothing as a tutorial**, so
  every game stays in `regular`. Over-reporting real activity beats hiding it —
  the same reasoning as the live-content filter in `content_index`.
- **A NULL `starter_deck` stays in `regular`** (186 historical rows have one).
- **A day of nothing but tutorial play is not a quiet day.** `total_games` can
  be 0 while the report still has something to say, so the "no runs were played"
  early return checks the tutorial count too.

Only the **unique-player** count spans `regular` + `tutorial`: someone who
played only the tutorial yesterday still played.

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

### Four bugs, all fixed

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

**2026-09-29 and -30 — two reports never arrived, with the bot up.** Draft
packs shipped that day. A pack offer is a `draft_items` row with
`item_type 'pack'` and `item_id` NULL, and `_resolve_item_names` built a table
name from the type, so the report asked PostgREST for `public.packs` and
raised. The schedule worked as designed: the day stayed unclaimed and was
retried every 10 minutes. But every retry raised the same way, the day being
retried moved on at midnight, and only the most recent day was ever sent, so
both reports were lost and the only trace was a console line. 10-01's report
built only because the shop builds recorded no drafts at all that day (their
migration was not yet applied).

Three fixes (2026-10-02): only content types (`card`, `aspect`, `ritual`,
`rite`/`event`) are ever looked up, a pack is named by `pack_type` as printed
on it (`stats_cards.PACK_LABELS`) and ranked with the type tag "pack", and
anything else (a shop level-up, `shopreward`) is left out, so a new kind of
draft item cannot take the report down again; a build failure is posted to the
channel once; and every missed day is backfilled.

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
- **`_SEND_LOCK` is what stops a double-send,** not `_fetch_daily_stats` being
  synchronous (it still is). A new path that claims a day must hold it.

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
