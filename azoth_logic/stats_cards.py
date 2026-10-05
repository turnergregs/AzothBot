"""Each /stats report's rows, laid out as a stats_charts.Card.

One function per report. The views do the aggregation; these decide what the
reader sees first, what is grey, and what every number is read against. Pure:
rows in, Card out.
"""
from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

from azoth_logic import stats_charts as sc
from azoth_logic import survey_labels as sl
from azoth_logic.stats_format import CUTOFF_VERSION
from azoth_logic.stats_format import value as sf_value

# ---------------------------------------------------------------------------
# Cohorts: who a report describes
# ---------------------------------------------------------------------------
# The game repo's 2026-09-28_player_cohorts.sql sorts every player into one
# cohort: `developer` (Turner and Caleb), `veteran` (played before the cutoff)
# or `new`. Views group by it and the bot sums the ones asked for, so the rule
# is defined once, in SQL, and every report filters the same way.

COHORTS = {
    "new": {"new"},
    "players": {"new", "veteran"},
    "all": {"new", "veteran", "developer"},
}
COHORT_LABELS = {
    "new": "New playtesters",
    "players": "Everyone but us",
    "all": "Everyone",
}


def select_cohort(rows: list, key: str, merge_on, counts: tuple):
    """`(merged_rows, filtered)`: one row per `merge_on` (a column, or a tuple
    of columns), with the `counts` columns summed over the cohorts `key` names.

    Every entity that appears in ANY row is kept, zeroed if none of its rows
    are in the cohort: a boss fought only by developers is still a boss the new
    playtesters have not fought, not a boss that does not exist.

    `filtered` is False when the view predates the cohort migration (no
    `cohort` column): every row is then counted, and the caller should say the
    filter did not apply rather than label everyone as new playtesters.
    """
    filtered = not rows or "cohort" in rows[0]
    wanted = COHORTS[key]
    columns = (merge_on,) if isinstance(merge_on, str) else tuple(merge_on)
    merged: dict = {}
    for row in rows:
        ident = tuple(row.get(c) for c in columns)
        entry = merged.get(ident)
        if entry is None:
            entry = {k: v for k, v in row.items() if k != "cohort" and k not in counts}
            entry.update({c: 0 for c in counts})
            merged[ident] = entry
        if not filtered or row.get("cohort") in wanted:
            for c in counts:
                entry[c] += int(row.get(c) or 0)
    return list(merged.values()), filtered


BOSS_COUNTS = ("fights", "wins", "losses", "unfinished")


# ---------------------------------------------------------------------------
# /stats bosses
# ---------------------------------------------------------------------------
# Built 2026-09-28 after a hand-run query showed Veln winning most of its
# fights; this makes that visible without writing SQL.

# Below this many FINISHED fights a win rate is shown grey: one fight gives
# 0% or 100%, which reads as a verdict and is a coin flip.
MIN_BOSS_FIGHTS = 5


def _finished(row: dict) -> int:
    return int(row.get("wins") or 0) + int(row.get("losses") or 0)


# The confidence behind a flag. 95%: with ~16 bosses, about one in twenty may
# still be flagged by chance at any moment, so a flag says "look at this", not
# "this is broken". 90% flags more on thin data; 99% almost nothing yet.
FLAG_Z = 1.96


def wilson_interval(wins: int, n: int, z: float = FLAG_Z):
    """`(low, high)`: the range a boss's true win rate plausibly sits in.

    Wilson's interval rather than rate ± z·SE: that one collapses to a single
    point at 0/n and n/n, exactly the small samples where the range should be
    widest, and runs past 0 and 1.
    """
    if n <= 0:
        return 0.0, 1.0
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def rate_flag(hits: int, n: int, baseline: float):
    """`"below"`, `"above"` or None: does the whole Wilson range for hits / n
    sit below or above `baseline`? The one test behind every red ▼ / blue ▲,
    so bosses and breakdown groups flag by the same rule."""
    low, high = wilson_interval(hits, n)
    if high < baseline:
        return "below"
    if low > baseline:
        return "above"
    return None


def boss_flag(row: dict, act_rows: list):
    """`"below"`, `"above"` or None: is this boss clearly harder or easier than
    the REST of its act?

    Not standard deviations across the act's bosses. That ignores how many
    fights each has (Caerul's 1/1 would be the biggest outlier), and with four
    bosses an outlier inflates the spread it is measured against, hiding
    itself. Instead: the whole Wilson range for this boss's win rate must sit
    below or above the pooled rate of the act's OTHER bosses. Against the whole
    act, the boss would pull its own baseline toward itself.

    Only bosses with MIN_BOSS_FIGHTS or more are flagged. The range already
    accounts for sample size, but a grey row saying "not enough fights" and a
    red one saying "clearly too hard" cannot both be true.
    """
    finished = _finished(row)
    if finished < MIN_BOSS_FIGHTS:
        return None
    rest = [r for r in act_rows if r is not row]
    rest_wins, rest_finished = act_win_rate(rest)
    if rest_finished == 0:
        return None
    return rate_flag(int(row.get("wins") or 0), finished, rest_wins / rest_finished)


def act_win_rate(rows: list):
    """`(wins, finished)` over every boss in an act.

    POOLED, not a mean of the bosses' rates: a boss fought 16 times weighs
    more than one fought once, which is what "the act's win rate" means. A
    mean of rates would let Caerul's 1/1 pull act 4 halfway to 100%.
    """
    wins = sum(int(r.get("wins") or 0) for r in rows)
    return wins, sum(_finished(r) for r in rows)


def boss_order(rows: list) -> list:
    """Hardest first, but reliable rates before grey ones, and unfought last.

    The report exists to find a boss that is too hard, so the lowest win rate
    leads. A grey 0% from two fights must not outrank a real 18% from eleven,
    so bosses under MIN_BOSS_FIGHTS sort after every reliable one.
    """
    def key(row):
        finished = _finished(row)
        if finished == 0:
            return (2, 0.0, row.get("boss") or "")
        tier = 0 if finished >= MIN_BOSS_FIGHTS else 1
        return (tier, int(row.get("wins") or 0) / finished, row.get("boss") or "")
    return sorted(rows, key=key)


def bosses_card(rows: list, population: str = "") -> sc.Card:
    """Boss win rates by act, each read against its act's overall rate.

    The reference line and the `±` column are from Caleb's review of the first
    draft: a boss is hard or easy relative to the others at its act, not in
    absolute terms, since later acts are meant to be harder.

    Unfought bosses are named, not drawn (Turner's review): an act nobody has
    reached is its header alone, and unfought bosses in a fought act are one
    line under it. Knowing they exist is the information; empty tracks are not.
    """
    by_act: dict = {}
    for row in rows:
        by_act.setdefault(int(row.get("act") or 0), []).append(row)

    fights = sum(_finished(r) for r in rows)
    subtitle = f"Solo fights · version ≥ {CUTOFF_VERSION} · {fights} finished"
    card = sc.Card("Boss win rates", f"{population} · {subtitle}" if population else subtitle)

    for act in sorted(by_act):
        bosses = by_act[act]
        wins, finished = act_win_rate(bosses)
        average = wins / finished if finished else None
        # A baseline of one boss is that boss: the line would sit on its own
        # bar end and every delta would read ±0.
        fought = sum(1 for b in bosses if _finished(b))
        compare = average is not None and fought >= 2

        if average is None:
            card.add(sc.SectionHeader(f"Act {act}", "no fights yet"))
            continue
        card.add(sc.SectionHeader(f"Act {act}", f"{round(average * 100)}% overall · {wins}/{finished}"))
        unfought = []
        for row in boss_order(bosses):
            done = _finished(row)
            if done == 0:
                unfought.append(row.get("boss") or "—")
                continue
            rate = int(row.get("wins") or 0) / done
            flag = boss_flag(row, bosses)
            flag_colour = {"below": sc.BELOW, "above": sc.ABOVE}.get(flag)
            card.add(sc.BarRow(
                row.get("boss") or "—", rate,
                value_text=f"{round(rate * 100)}%",
                count_text=f"{row.get('wins') or 0}/{done}",
                delta=sc.signed((rate - average) * 100) if compare else "",
                faded=done < MIN_BOSS_FIGHTS,
                reference=average if compare else None,
                # Emphasis: ordinary bars recede, so the flagged ones are
                # what the eye lands on. The marker is in the flag colour so a
                # flagged 0% (a bar with no length) is still obvious.
                fill=flag_colour or sc.NEUTRAL,
                marker={"below": "▼", "above": "▲"}.get(flag, ""),
                marker_fill=flag_colour,
            ))
        if unfought:
            card.add(sc.Note("Not yet fought: " + ", ".join(sorted(unfought))))
        card.add(sc.Spacer(4))

    # No legend. A first draft carried three lines of footnotes (the line, the
    # colours, what dim means); Turner's review: the chart reads without them.
    # Unfinished fights are not counted and not mentioned: no report carries
    # a text footer since 2026-10-02 (Turner).
    return card



# ---------------------------------------------------------------------------
# /stats breakdown
# ---------------------------------------------------------------------------
# Runs grouped by hero, ritual or version (breakdown_view), redrawn as an image
# 2026-09-28. Every card leads with how far runs got, in the game's act
# colours. What follows depends on the question:
#
#   hero     hero activations, per regular turn and per boss fight: how usable
#            and how strong each hero's ability is. Skips and links work the
#            same on every hero, so they are not shown (Turner's review).
#   ritual   the average per regular turn of skips, activations and links:
#            players play differently at each rung.
#   version  the same, across releases, to see what a fix or a rebalance did.
#
# Grouping by skips or activations was prototyped and cut: reading "runs that
# never activated" beside their average skips per turn confused more than it
# showed. A better view of those habits is still to be found.

MIN_RUNS = 5

BREAKDOWN_COUNTS = ("runs", "cleared", "regular_turns", "regular_skips", "regular_links",
                    "regular_activations", "boss_turns", "boss_activations")

BREAKDOWN_TITLES = {"hero": "Runs by hero", "ritual": "Runs by ritual",
                    "version": "Runs by version"}

# A regular turn's node budget (links_per_turn), so a full links bar means
# every node was a link.
LINKS_PER_TURN = 5


def _version_key(version: str):
    parts = []
    for part in str(version).split("."):
        parts.append(int(part) if part.isdigit() else -1)
    return parts


def breakdown_groups(rows: list, by: str) -> list:
    """One dict per group of dimension `by`, rows already cohort-selected:
    `{grp, acts: {act: runs}, runs, cleared, <turn totals>}`, in display order.

    Groups with no runs in the chosen cohorts are dropped: unlike bosses, a
    hero nobody in this cohort played is not part of the answer.
    """
    groups: dict = {}
    for row in rows:
        if row.get("dimension") != by:
            continue
        g = groups.setdefault(row.get("grp"), {"grp": row.get("grp"), "acts": {},
                                               **{c: 0 for c in BREAKDOWN_COUNTS}})
        runs = int(row.get("runs") or 0)
        act = int(row.get("furthest_act") or 1)
        g["acts"][act] = g["acts"].get(act, 0) + runs
        for c in BREAKDOWN_COUNTS:
            g[c] += int(row.get(c) or 0)
    live = [g for g in groups.values() if g["runs"]]
    if by == "hero":
        return sorted(live, key=lambda g: (-g["runs"], str(g["grp"])))
    if by == "ritual":
        return sorted(live, key=lambda g: int(g["grp"]) if str(g["grp"]).isdigit() else 99)
    return sorted(live, key=lambda g: _version_key(g["grp"]))


def group_flag(group: dict, groups: list):
    """Is this group's beat-act-3 rate clearly below or above the REST of the
    runs? rate_flag's rule, with MIN_RUNS on BOTH sides: two runs at ritual 1
    are too few to judge ritual 0 against."""
    rest = [g for g in groups if g is not group]
    rest_runs = sum(g["runs"] for g in rest)
    if group["runs"] < MIN_RUNS or rest_runs < MIN_RUNS:
        return None
    return rate_flag(group["cleared"], group["runs"],
                     sum(g["cleared"] for g in rest) / rest_runs)


def _per(total: int, turns: int):
    """A per-turn average from totals, or None when there were no turns."""
    return total / turns if turns else None


def _label(group: dict, by: str) -> str:
    return f"Ritual {group['grp']}" if by == "ritual" else str(group["grp"])


def breakdown_card(rows: list, by: str, population: str = "") -> sc.Card:
    groups = breakdown_groups(rows, by)
    runs = sum(g["runs"] for g in groups)
    parts = [population] if population else []
    parts.append("solo runs")
    if by != "version":            # the rows ARE the versions
        parts.append(f"version ≥ {CUTOFF_VERSION}")
    parts.append(f"{runs} run{'' if runs == 1 else 's'}")
    card = sc.Card(BREAKDOWN_TITLES[by], " · ".join(parts))

    right = sc.WIDTH - sc.PAD
    card.add(sc.SectionHeader("How far runs got"))
    card.add(sc.ColumnHeads([("Furthest act", sc.PAD + sc.LABEL_W, "lm"),
                             ("Beat act 3", right - sc.ActStripRow.COUNT_W, "rm"),
                             ("Runs", right, "rm")]))
    for g in groups:
        flag = group_flag(g, groups)
        card.add(sc.ActStripRow(_label(g, by), g["acts"], g["cleared"],
                                faded=g["runs"] < MIN_RUNS,
                                marker={"below": "▼", "above": "▲"}.get(flag, ""),
                                marker_fill={"below": sc.BELOW, "above": sc.ABOVE}.get(flag)))
    # The baseline every row and every flag is read against: a ▼ on Lumis
    # means little without the rate it is below (Turner's review). A totals
    # row rather than a header note, so the act spread has a baseline too.
    if len(groups) > 1:
        totals: dict = {}
        for g in groups:
            for act, n in g["acts"].items():
                totals[act] = totals.get(act, 0) + n
        card.add(sc.Rule())
        card.add(sc.ActStripRow("All runs", totals, sum(g["cleared"] for g in groups),
                                strong=True))
    card.add(sc.act_legend(a for g in groups for a, n in g["acts"].items() if n))
    card.add(sc.Spacer(6))

    if by == "hero":
        regular = [_per(g["regular_activations"], g["regular_turns"]) for g in groups]
        boss = [_per(g["boss_activations"], g["boss_turns"]) for g in groups]
        card.add(sc.SectionHeader("Hero activations"))
        card.add(sc.ColumnHeads([("Per regular turn", sc.column_x(0, 2), "lm"),
                                 ("Per boss fight", sc.column_x(1, 2), "lm")]))
        scales = [max([v for v in regular if v is not None], default=0),
                  max([v for v in boss if v is not None], default=0)]
        for g, reg, bos in zip(groups, regular, boss):
            card.add(sc.MetricRow(_label(g, by), [(reg, scales[0]), (bos, scales[1])],
                                  faded=g["runs"] < MIN_RUNS))
        return card

    columns = [("Skips", "regular_skips"), ("Hero activations", "regular_activations"),
               ("Links", "regular_links")]
    values = [[_per(g[col], g["regular_turns"]) for _, col in columns] for g in groups]
    scales = [max([v[i] for v in values if v[i] is not None], default=0) for i in range(2)]
    scales.append(LINKS_PER_TURN)
    card.add(sc.SectionHeader("Average per regular turn"))
    card.add(sc.ColumnHeads([(name, sc.column_x(i, 3), "lm") for i, (name, _) in enumerate(columns)]))
    for g, vals in zip(groups, values):
        card.add(sc.MetricRow(_label(g, by), list(zip(vals, scales)), faded=g["runs"] < MIN_RUNS))
    return card



# ---------------------------------------------------------------------------
# /stats players
# ---------------------------------------------------------------------------
# The engagement side (2026-09-28): who plays, and how each player spends their
# time with the game -- runs, custom runs, the Codex, the art tools -- from
# player_summary_view. It replaced /stats active_players and /stats engagement.
#
# Run time is games.elapsed_sec (wall-clock with the run open, idle included),
# recorded by every build, so every player has it all the way back. Custom
# runs, the Codex and the art tools exist only in the tracker's active time
# (engagement_spans), from the first build carrying EngagementTracker. Idle
# counts on one side and not the other, so the tools' share reads slightly
# low.

# Where time goes, as (label, view column, colour). The dataviz palette's
# first four slots, which pass the colour-blindness checks as neighbours on
# the dark card, in that validated order.
TIME_SURFACES = [
    ("Runs", "run_sec", "#3987e5"),
    ("Custom runs", "custom_run_sec", "#d95926"),
    ("Codex", "codex_sec", "#199e70"),
    ("Art tools", "art_sec", "#c98500"),
]

# Beyond this the card gets tall enough to scroll past on a phone; the rest
# are counted in one line.
MAX_ROSTER = 15


def duration(seconds) -> str:
    """`45s`, `12m`, `1.5h`."""
    seconds = int(seconds or 0)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{round(seconds / 60)}m"
    return f"{seconds / 3600:.1f}".rstrip("0").rstrip(".") + "h"


def _tracked(row: dict) -> int:
    """All the time a row has, runs included."""
    return sum(int(row.get(col) or 0) for _, col, _ in TIME_SURFACES)


def _plural(noun: str) -> str:
    return noun + ("es" if noun.endswith(("s", "o")) else "s")


def action_label(key: str) -> str:
    """A Codex action key (`verb:type`) as a tile label. Creations and edits
    keep their type ("New cards", "Deck edits"); everything else is one tile
    per verb, since "Exports" says enough."""
    verb, _, what = str(key).partition(":")
    what = what or "content"
    if verb == "created":
        return f"New {_plural(what)}"
    if verb == "edited":
        return f"{what.capitalize()} edits"
    return {"cloned": "Clones", "drafted": "Shipped edits", "exported": "Exports",
            "design_saved": "Designs saved", "look_saved": "Deck looks"}.get(verb, verb)


def codex_tiles(rows: list) -> list:
    """`[(label, count)]` for every kind of thing made, most first. Only kinds
    that happened: a tile reading 0 is the empty legend entry again."""
    totals: dict = {}
    for row in rows:
        for key, n in (row.get("actions") or {}).items():
            label = action_label(key)
            totals[label] = totals.get(label, 0) + int(n or 0)
    return sorted(((k, v) for k, v in totals.items() if v), key=lambda kv: (-kv[1], kv[0]))


def players_card(rows: list, population: str = "") -> sc.Card:
    rows = [r for r in rows if int(r.get("runs") or 0) or _tracked(r)]
    play = sum(int(r.get("run_sec") or 0) + int(r.get("custom_run_sec") or 0) for r in rows)
    tools = sum(int(r.get("codex_sec") or 0) + int(r.get("art_sec") or 0) for r in rows)
    card = sc.Card("Players", " · ".join(filter(None, [population, f"version ≥ {CUTOFF_VERSION}"])))

    # Only numbers that exist (the house rule): no Codex tile before anyone
    # has Codex time.
    tiles = [("Players", str(len(rows))), ("Runs", str(sum(int(r.get("runs") or 0) for r in rows)))]
    if play:
        tiles.append(("Time in runs", duration(play)))
    if tools:
        tiles.append(("Time in the Codex", duration(tools)))
    card.add(sc.StatTiles(tiles))
    card.add(sc.Spacer(6))

    # Most active time first; players with no tracked time after them, by runs.
    ordered = sorted(rows, key=lambda r: (-_tracked(r), -int(r.get("runs") or 0),
                                          str(r.get("player"))))
    shown, rest = ordered[:MAX_ROSTER], ordered[MAX_ROSTER:]
    scale = max((_tracked(r) for r in rows), default=0)
    right = sc.WIDTH - sc.PAD
    # Before anyone has run a tracked build there is no time to draw: no bars,
    # no time column, and a heading that promises only what is there.
    timed = scale > 0
    card.add(sc.SectionHeader("Where their time goes" if timed else "Who is playing"))
    heads = [("Runs", right - sc.TimeRow.EXTRA_W, "rm"), ("Ritual", right, "rm")]
    if timed:
        heads.insert(0, ("Time", right - sc.TimeRow.EXTRA_W * 2, "rm"))
    card.add(sc.ColumnHeads(heads))
    for r in shown:
        tracked = _tracked(r)
        ritual = r.get("max_ritual")
        card.add(sc.TimeRow(
            str(r.get("player") or "—"),
            [(int(r.get(col) or 0), colour) for _, col, colour in TIME_SURFACES],
            scale,
            total_text=duration(tracked) if tracked else "—",
            extra=(str(int(r.get("runs") or 0)), f"R{ritual}" if ritual is not None else "—"),
            bar=timed,
        ))
    if rest:
        card.add(sc.Note(f"+ {len(rest)} more player{'' if len(rest) == 1 else 's'} "
                         "with less time"))
    # Only the surfaces someone used (the house legend rule), and no legend at
    # all before anyone has tracked time: there is nothing for it to key.
    used = [(label, colour) for label, col, colour in TIME_SURFACES
            if any(int(r.get(col) or 0) for r in rows)]
    # One colour needs no key: the heading already says what it is.
    if len(used) > 1:
        card.add(sc.Legend(used))

    made = codex_tiles(rows)
    if made:
        card.add(sc.Spacer(6))
        card.add(sc.SectionHeader("Made in the Codex"))
        card.add(*sc.tile_rows([(label, str(n)) for label, n in made]))
    return card



# ---------------------------------------------------------------------------
# /stats player
# ---------------------------------------------------------------------------
# One player's profile, drawn as an image (2026-09-28): how much they play,
# how far they get with each hero and ritual, and what they make. It replaced a
# ten-section text card. What it dropped, and why: "max reached" (the act bars
# say more), and the links-per-turn and pattern-clearing tables -- how someone
# plays their turns is balance data, not a profile, and averaged over one
# player's handful of runs it is noise.

COHORT_SINGULAR = {"new": "New playtester", "veteran": "Veteran", "developer": "Developer"}


def hero_rows(runs: list) -> list:
    """`[(hero, {act: runs}, cleared, top ritual)]` from player_run_view rows,
    most-played hero first."""
    heroes: dict = {}
    for run in runs:
        h = heroes.setdefault(run.get("hero") or "—", {"acts": {}, "cleared": 0, "ritual": 0})
        act = int(run.get("furthest_act") or 1)
        h["acts"][act] = h["acts"].get(act, 0) + 1
        h["cleared"] += 1 if run.get("cleared") else 0
        h["ritual"] = max(h["ritual"], int(run.get("ritual") or 0))
    order = sorted(heroes, key=lambda n: (-sum(heroes[n]["acts"].values()), n))
    return [(n, heroes[n]["acts"], heroes[n]["cleared"], heroes[n]["ritual"]) for n in order]


def most_drafted_line(info: dict) -> str:
    """"Echo, Bloom · 3 times each", or "" when nothing was drafted twice."""
    names, count = info.get("most_drafted"), info.get("most_drafted_count")
    if not names or not count:
        return ""
    each = " each" if "," in str(names) else ""
    return f"{names} · {count} times{each}"


def player_card(name: str, runs: list, summary: dict | None, info: dict | None) -> sc.Card:
    """`runs` from player_run_view, `summary` from player_summary_view (None
    before that migration), `info` from player_info_view (best combo, most
    drafted)."""
    summary, info = summary or {}, info or {}
    cleared = sum(1 for r in runs if r.get("cleared"))
    last = max((str(r.get("started_at") or "")[:10] for r in runs), default="")
    last = max(last, str(summary.get("last_seen") or "")[:10])

    parts = [COHORT_SINGULAR.get(summary.get("cohort"), ""), f"version ≥ {CUTOFF_VERSION}"]
    if last:
        parts.append(f"last played {last}")
    card = sc.Card(name, " · ".join(p for p in parts if p))

    tiles = [("Runs", str(len(runs))), ("Beat act 3", str(cleared))]
    best = info.get("best_combo")
    if best not in (None, "", "0"):
        tiles.append(("Best combo", sf_value("max_combo", best)))
    play = int(summary.get("run_sec") or 0)
    tools = int(summary.get("codex_sec") or 0) + int(summary.get("art_sec") or 0)
    if play:
        tiles.append(("Time in runs", duration(play)))
    if tools:
        tiles.append(("Time in the Codex", duration(tools)))
    card.add(*sc.tile_rows(tiles, height=58))
    card.add(sc.Spacer(6))

    heroes = hero_rows(runs)
    if heroes:
        right = sc.WIDTH - sc.PAD
        card.add(sc.SectionHeader("Heroes"))
        card.add(sc.ColumnHeads([("Furthest act", sc.PAD + sc.LABEL_W, "lm"),
                                 ("Beat act 3", right - sc.ActStripRow.COUNT_W, "rm"),
                                 ("Runs", right, "rm")]))
        for hero, acts, won, ritual in heroes:
            # The label carries the highest ritual played on that hero:
            # ladders are per hero, so it belongs beside the hero, not in a tile.
            card.add(sc.ActStripRow(f"{hero} R{ritual}", acts, won,
                                    faded=sum(acts.values()) < MIN_RUNS))
        card.add(sc.act_legend(a for _, acts, _, _ in heroes for a, n in acts.items() if n))

    drafted = most_drafted_line(info)
    if drafted:
        card.add(sc.Spacer(4))
        card.add(sc.SectionHeader("Most drafted"))
        card.add(sc.Note(drafted))

    made = codex_tiles([summary]) if summary else []
    if made:
        card.add(sc.Spacer(6))
        card.add(sc.SectionHeader("Made in the Codex"))
        card.add(*sc.tile_rows([(label, str(n)) for label, n in made]))
    return card



# ---------------------------------------------------------------------------
# /stats draft picks, items and pool
# ---------------------------------------------------------------------------
# Redrawn as images 2026-09-29. picks and items read draft_offer_view and
# draft_item_offer_view (counts per cohort); pool reads draft_deck_view, which
# is content rather than play and so takes no players: filter.
#
# picks replaced /stats draft breakdown and /stats draft embellishments;
# items replaced the text /stats draft rates; pool is the renamed composition.
#
# A pick rate is conditional on the offer, so rites rank beside cards and
# aspects: the injection budget changes how often a rite is offered, and that
# divides out. Raw pick counts would not be comparable.

# Below this many offers a bucket is dimmed and never flagged.
MIN_OFFERS = 10
# Items are ranked only from this many offers: below it one lucky offer puts
# a card at 100% above everything.
MIN_ITEM_OFFERS = 5
ITEMS_PER_SECTION = 5

DRAFT_COUNTS = ("offered", "picked")

# The game's element colours (GlobalVars.ELEMENTS), for the pool's element
# bars. Catalysts are the game's "any" white, a step off pure white so the bar
# does not glare on the dark card.
ELEMENT_COLOURS = {"anima": "#8769e9", "blood": "#ef1212", "sol": "#f9a410",
                   "catalyst": "#e8e6dc"}
ELEMENT_ORDER = ["anima", "blood", "sol", "catalyst"]
TYPE_LABELS = {"card": "Cards", "aspect": "Aspects", "rite": "Rites", "pack": "Packs"}
# Draft packs (game 2026-09-29, 2026-09-29_draft_packs.sql): a pack offered in a
# draft is a `type` bucket whose `picked` means OPENED, with its kind in the
# `pack_type` dimension. Labelled by the name printed on the pack.
PACK_LABELS = {"card": "Atoms", "catalyst": "Catalysts", "aspect": "Aspects", "rite": "Rites",
               "upgrade": "Upgrade"}
KIND_LABELS = {"upgrade": "Upgraded", "attribute": "Attribute", "enhancement": "Enhanced"}


def _flag_style(flag):
    colour = {"below": sc.BELOW, "above": sc.ABOVE}.get(flag)
    return {"fill": colour or sc.NEUTRAL, "marker": {"below": "▼", "above": "▲"}.get(flag, ""),
            "marker_fill": colour}


def _rate_rows(rows: list, baseline: tuple | None = None) -> list:
    """BarRows for `[(label, picked, offered)]`, each read against the pooled
    rate of the section, or against `baseline` `(picked, offered)` when given.
    A row is flagged against the REST of its section (rate_flag's rule), or
    against the baseline, with MIN_OFFERS on both sides."""
    picked = sum(p for _, p, _ in rows)
    offered = sum(o for _, _, o in rows)
    base_p, base_o = baseline or (picked, offered)
    average = base_p / base_o if base_o else 0
    bars = []
    for label, p, o in rows:
        rate = p / o if o else 0
        ref_p, ref_o = baseline or (picked - p, offered - o)
        flag = (rate_flag(p, o, ref_p / ref_o)
                if o >= MIN_OFFERS and ref_o >= MIN_OFFERS else None)
        bars.append(sc.BarRow(label, rate, value_text=f"{round(rate * 100)}%",
                              count_text=f"{p}/{o}", delta=sc.signed((rate - average) * 100),
                              faded=o < MIN_OFFERS, reference=average,
                              label_w=100, count_w=66, **_flag_style(flag)))
    return bars


def _buckets(rows: list, dimension: str) -> dict:
    return {r.get("bucket"): (int(r.get("picked") or 0), int(r.get("offered") or 0))
            for r in rows if r.get("dimension") == dimension and int(r.get("offered") or 0)}


def _valence_key(bucket) -> tuple:
    text = str(bucket)
    return (0, 0) if text == "none" else (1, int(text)) if text.isdigit() else (2, text)


def _valence_name(bucket) -> str:
    text = str(bucket)
    return "—" if text == "none" else f"{text}v" if text.isdigit() else text


def draft_picks_card(rows: list, population: str = "") -> sc.Card:
    """Pick rates by type, element, valence and embellishment kind, and, once
    draft packs have been offered, how often each kind of pack is opened.

    No bare-vs-embellished section (Turner's review): embellished cards are
    expected to be picked more. What is worth knowing is WHICH kind lifts a
    card most, so each kind is read against bare cards.
    """
    types = _buckets(rows, "type")
    offered = sum(o for _, o in types.values())
    picked = sum(p for p, _ in types.values())
    parts = [population, f"version ≥ {CUTOFF_VERSION}", f"{offered:,} offers"]
    card = sc.Card("Draft picks", " · ".join(p for p in parts if p))
    card.add(sc.StatTiles([("Offers", f"{offered:,}"), ("Picked", f"{picked:,}"),
                           ("Pick rate", f"{round(100 * picked / offered)}%" if offered else "—")]))
    card.add(sc.Spacer(4))

    def section(title, entries, baseline=None, detail=None):
        if not entries:
            return
        p = sum(e[1] for e in entries)
        o = sum(e[2] for e in entries)
        if detail is None:
            detail = f"{round(100 * p / o)}% overall · {p}/{o}" if o else ""
        card.add(sc.SectionHeader(title, detail))
        card.add(*_rate_rows(entries, baseline))
        card.add(sc.Spacer(4))

    order = [t for t in TYPE_LABELS if t in types] + sorted(t for t in types if t not in TYPE_LABELS)
    section("By type", [(TYPE_LABELS.get(t, t), *types[t]) for t in order])

    # Draft packs: how often each kind is opened, against the rest, and how
    # often an opened pack gave a card (the rest were Skipped). Only once packs
    # have been offered, so a view from before them shows nothing new.
    packs = _buckets(rows, "pack_type")
    if packs:
        opened = sum(p for p, _ in packs.values())
        taken = _buckets(rows, "in_pack").get("in_pack", (0, 0))[0]
        detail = f"{round(100 * opened / sum(o for _, o in packs.values()))}% opened"
        if opened:
            detail += f" · {round(100 * taken / opened)}% of opened packs gave a pick"
        order = [k for k in PACK_LABELS if k in packs] + sorted(k for k in packs if k not in PACK_LABELS)
        section("Packs", [(PACK_LABELS.get(k, str(k)), *packs[k]) for k in order], detail=detail)

    elements = _buckets(rows, "element")
    order = [e for e in ELEMENT_ORDER if e in elements] + sorted(e for e in elements if e not in ELEMENT_ORDER)
    section("By element", [(str(e).capitalize(), *elements[e]) for e in order])

    # Valence is an ordered axis, so its rates are columns read left to right
    # (2026-10-03): the question is the slope, whether heavier cards are
    # picked less, which a list of rows makes you work out from the numbers.
    valences = _buckets(rows, "valence")
    if valences:
        entries = [(v, *valences[v]) for v in sorted(valences, key=_valence_key)]
        # Every valence up to the top one printed, offered or not, as the
        # pool chart does: a valence the pool has none of is an empty column.
        numbered = [int(v) for v, _, _ in entries if str(v).isdigit()]
        top = max([MAX_VALENCE, *numbered])
        shown = {str(v): (v, vp, vo) for v, vp, vo in entries}
        p = sum(e[1] for e in entries)
        o = sum(e[2] for e in entries)
        average = p / o if o else 0
        columns = []
        for key in ["none"] * ("none" in shown) + [str(v) for v in range(1, top + 1)]:
            if key not in shown:
                columns.append({"label": key, "value": None})
                continue
            v, vp, vo = shown[key]
            rest_p, rest_o = p - vp, o - vo
            flag = (rate_flag(vp, vo, rest_p / rest_o)
                    if vo >= MIN_OFFERS and rest_o >= MIN_OFFERS else None)
            style = _flag_style(flag)
            columns.append({"label": _valence_name(v).removesuffix("v"), "value": vp / vo,
                            # Offers only: "70/190" under twelve columns ran together.
                            "count_text": f"{vo:,}",
                            "faded": vo < MIN_OFFERS, "marker": style["marker"],
                            "marker_fill": style["marker_fill"]})
        card.add(sc.SectionHeader("By valence", f"{round(100 * average)}% overall · offers under each"))
        card.add(sc.RateColumns(columns, fill=sc.NEUTRAL, baseline=average))
        card.add(sc.Legend([("Overall pick rate", sc.REFERENCE, "line")], indent=0))
        card.add(sc.Spacer(4))

    bare = _buckets(rows, "embellished").get("bare")
    kinds = _buckets(rows, "kind")
    if bare and kinds:
        section("By embellishment",
                [(KIND_LABELS.get(k, k), *kinds[k]) for k in KIND_LABELS if k in kinds],
                baseline=bare,
                detail=f"against bare cards: {round(100 * bare[0] / bare[1])}%")
    return card



def _item_label(row: dict) -> str:
    return str(row.get("item_name") or "—")


ITEM_TYPES = ("card", "aspect", "rite")


def draft_items_card(rows: list, population: str = "") -> sc.Card:
    """The five most and five least picked cards, aspects and rites, one group
    per type, each item read against its OWN type's pick rate and flagged
    against the rest of its type.

    Split by type 2026-09-29 (Turner): ranked together, one kind can fill both
    lists, and what a designer tunes is a card against other cards. A type with
    nothing ranked draws nothing; a type with fewer than ten ranked items
    draws a shorter Least picked, never an item twice.
    """
    parts = [population, f"version ≥ {CUTOFF_VERSION}", f"items offered {MIN_ITEM_OFFERS}+ times"]
    card = sc.Card("Draft items", " · ".join(p for p in parts if p))

    def rate(r):
        return int(r.get("picked") or 0) / int(r.get("offered"))

    kinds = [k for k in ITEM_TYPES if any(r.get("item_type") == k for r in rows)]
    kinds += sorted({str(r.get("item_type")) for r in rows} - set(ITEM_TYPES) - {"None"})
    drawn = False
    for kind in kinds:
        group = [r for r in rows if r.get("item_type") == kind]
        offered = sum(int(r.get("offered") or 0) for r in group)
        picked = sum(int(r.get("picked") or 0) for r in group)
        average = picked / offered if offered else 0
        ranked = [r for r in group if int(r.get("offered") or 0) >= MIN_ITEM_OFFERS]
        if not ranked:
            continue
        most = sorted(ranked, key=lambda r: (-rate(r), -int(r["offered"]), _item_label(r)))[:ITEMS_PER_SECTION]
        rest = [r for r in ranked if r not in most]
        # Least picked leads with the lowest rate; drawn in that order.
        least = sorted(rest, key=lambda r: (rate(r), -int(r["offered"]), _item_label(r)))[:ITEMS_PER_SECTION]

        if drawn:
            card.add(sc.Rule(14))
        drawn = True
        name = TYPE_LABELS.get(kind, kind.capitalize())
        detail = f"all {name.lower()} {round(average * 100)}% · {picked}/{offered}"
        for title, items in ((f"Most picked {name.lower()}", most),
                             (f"Least picked {name.lower()}", least)):
            if not items:
                continue
            card.add(sc.SectionHeader(title, detail))
            for r in items:
                p, o = int(r.get("picked") or 0), int(r.get("offered"))
                flag = rate_flag(p, o, (picked - p) / (offered - o)) if offered - o else None
                card.add(sc.BarRow(_item_label(r), p / o, value_text=f"{round(100 * p / o)}%",
                                   count_text=f"{p}/{o}", delta=sc.signed((p / o - average) * 100),
                                   reference=average, label_w=130, **_flag_style(flag)))
            card.add(sc.Spacer(6))
    return card



# The highest valence a card is printed at: the pool chart runs to here even
# when nothing in the pool does, so an empty top end reads as empty.
MAX_VALENCE = 10


def draft_pool_card(row: dict) -> sc.Card:
    """What the draft pool holds: content, not play, so no cohort."""
    from azoth_logic import stats_format as sf
    cards_n, aspects_n = int(row.get("cards") or 0), int(row.get("aspects") or 0)
    card = sc.Card("Draft pool", "Shipped draft decks · content, not play")
    tiles = [("Cards", str(cards_n)), ("Aspects", str(aspects_n))]
    templates = int(row.get("rite_templates") or 0)
    if templates:
        # Rites are templates drawn WITH replacement into injected slots, not
        # pool members: counted beside the pool, never added into it.
        tiles += [("Rite templates", str(templates)),
                  ("Rite slots / run", f"~{sf.injected_slots(cards_n + aspects_n)}")]
    card.add(*sc.tile_rows(tiles))
    card.add(sc.Spacer(4))

    elements, _ = sf._element_buckets(row)
    if elements:
        top = max(n for _, n in elements)
        card.add(sc.SectionHeader("Cards by element"))
        for name, n in elements:
            card.add(sc.BarRow(str(name).capitalize(), n / top, value_text=str(n), count_w=0,
                               fill=ELEMENT_COLOURS.get(name, sc.NEUTRAL)))
        card.add(sc.Spacer(4))
    # A histogram over 1-10 with every valence drawn, empty ones included, so
    # a hole in the pool (no 7s, no 10s) shows as a hole (2026-10-03).
    # Cards with no valence lead, apart from the scale.
    counts = row.get("valence_counts")
    if isinstance(counts, dict) and counts:
        valued = {int(k): int(v or 0) for k, v in counts.items() if str(k).isdigit()}
        top = max([MAX_VALENCE, *valued])
        card.add(sc.SectionHeader("Cards by valence"))
        card.add(sc.Histogram([int(counts.get("none") or 0)] + [valued.get(v, 0) for v in range(1, top + 1)],
                              labels=["—"] + [str(v) for v in range(1, top + 1)],
                              show="count", label_all=True))
    else:
        valences, _ = sf._valence_buckets(row)
        if valences:
            top = max(n for _, n in valences)
            card.add(sc.SectionHeader("Cards by valence"))
            for label, n in valences:
                card.add(sc.BarRow(label, n / top, value_text=str(n), count_w=0, fill=sc.NEUTRAL))
    return card


# ---------------------------------------------------------------------------
# /stats item
# ---------------------------------------------------------------------------
# One piece of content alone, split by version (or ritual, or hero), so a
# change shows up as a change (Turner, 2026-09-29): Veln's hp was halved in
# 0.9.11, and /stats bosses, pooling 0.9.10 with 0.9.11, still ranked it the
# hardest boss.
#
# Each group is read against the REST of its kind in the SAME group: Veln in
# 0.9.11 against act 3's other bosses in 0.9.11, a card against every other
# card offered in that version. A version where everything got easier (a new
# player wave, a global change) then does not read as a fix to this item.
#
# Content is live from the database, not from the build, so a version is when
# players ran a build, not when the item changed. Turner's call: tracking each
# change's date is too much; the numbers moving is what matters.

# What each kind of content is measured by: (heading, unit, rest, minimum).
# `rest` names the baseline; `minimum` is the sample below which a row is grey
# and never flagged, the same floors as the reports these come from.
ITEM_MEASURES = {
    "boss": ("Win rate", "fights", "act {act}'s other bosses", MIN_BOSS_FIGHTS),
    "card": ("Pick rate", "offers", "other cards", MIN_OFFERS),
    "aspect": ("Pick rate", "offers", "other aspects", MIN_OFFERS),
    "rite": ("Pick rate", "offers", "other rites", MIN_OFFERS),
    "hero": ("Beat act 3", "runs", "other heroes", MIN_RUNS),
}
ITEM_KIND_LABELS = {"boss": "Boss", "card": "Card", "aspect": "Aspect", "rite": "Rite",
                    "hero": "Hero"}
# The baseline's tile label: short, since the tiles share a row with the thumb.
ITEM_TILE_REST = {"boss": "Rest of act {act}", "card": "Other cards", "aspect": "Other aspects",
                  "rite": "Other rites", "hero": "Other heroes"}
ITEM_BY_TITLES = {"version": "by version", "ritual": "by ritual", "hero": "by hero"}
# Ordered axes draw as columns; heroes, unordered and long-named, as rows.
ITEM_COLUMN_AXES = ("version", "ritual")
# What fits across the card before the columns get too thin; the latest shown.
MAX_ITEM_COLUMNS = 10


# Where each kind's split lives (game repo 2026-09-29_item_split_views.sql):
# (view, id column, (item hits, item n, kind hits, kind n)). The kind's totals
# ride on every row and include the item; item_groups subtracts it.
ITEM_SPLITS = {
    "boss": ("boss_split_view", "boss_id", ("wins", "finished", "act_wins", "act_finished")),
    "card": ("draft_item_split_view", "item_id", ("picked", "offered", "type_picked", "type_offered")),
    "aspect": ("draft_item_split_view", "item_id", ("picked", "offered", "type_picked", "type_offered")),
    "rite": ("draft_item_split_view", "item_id", ("picked", "offered", "type_picked", "type_offered")),
    "hero": ("hero_split_view", "hero_id", ("cleared", "runs", "all_cleared", "all_runs")),
}
# A hero split by hero is itself.
ITEM_AXES = {"boss": ("version", "ritual", "hero"), "card": ("version", "ritual", "hero"),
             "aspect": ("version", "ritual", "hero"), "rite": ("version", "ritual", "hero"),
             "hero": ("version", "ritual")}


def item_groups(rows: list, kind: str) -> list:
    """`[{group, hits, n, rest_hits, rest_n}]` from one item's split rows, the
    cohorts already summed (select_cohort on `grp`)."""
    hits_col, n_col, kind_hits_col, kind_n_col = ITEM_SPLITS[kind][2]
    groups = []
    for r in rows:
        hits, n = int(r.get(hits_col) or 0), int(r.get(n_col) or 0)
        groups.append({"group": r.get("grp"), "hits": hits, "n": n,
                       "rest_hits": int(r.get(kind_hits_col) or 0) - hits,
                       "rest_n": int(r.get(kind_n_col) or 0) - n})
    return groups


def _group_order(groups: list, by: str) -> list:
    if by == "version":
        return sorted(groups, key=lambda g: _version_key(g["group"]))
    if by == "ritual":
        return sorted(groups, key=lambda g: int(g["group"]) if str(g["group"]).isdigit() else 99)
    return sorted(groups, key=lambda g: (-g["n"], str(g["group"])))


def item_colour(kind: str, row: dict | None, act: int | None = None) -> str:
    """The colour the game gives this item, for its columns (Turner,
    2026-09-29: a report about one thing should look like that thing). A boss
    in its act's colour, a card in its element's, an aspect in its art's accent
    (`primary_color`: aspect colours are reversed, docs/CARD_RENDERING.md), a
    rite in its palette's `primary_color`, a hero in its `color`. Anything
    missing falls back to the house accent."""
    row = row or {}
    data = row.get("image_data") or {}

    def hex_of(c):
        if isinstance(c, str) and c.startswith("#") and len(c) >= 7:
            return c[:7].lower()
        if isinstance(c, (list, tuple)) and len(c) >= 3:
            return "#" + "".join(f"{int(v):02x}" for v in c[:3])
        if isinstance(c, dict) and {"r", "g", "b"} <= set(c):
            return "#" + "".join(f"{int(c[k]):02x}" for k in "rgb")
        return None

    if kind == "boss" and act:
        return sc.ACT_COLOURS[min(max(int(act), 1), len(sc.ACT_COLOURS)) - 1]
    if kind == "card":
        return ELEMENT_COLOURS.get(str(row.get("element") or "catalyst").lower(), sc.ACCENT)
    if kind in ("aspect", "rite"):
        return hex_of(data.get("primary_color")) or sc.ACCENT
    if kind == "hero":
        return hex_of(row.get("color")) or sc.ACCENT
    return sc.ACCENT


def item_card(name: str, kind: str, groups: list, by: str = "version",
              population: str = "", act: int | None = None,
              colour: str | None = None, thumb=None) -> sc.Card:
    """One item's rate per group, each against the rest of its kind there.

    `groups`: `[{group, hits, n, rest_hits, rest_n}]`, where hits / n is the
    item (wins over finished fights, picks over offers, act-3 clears over runs)
    and rest_hits / rest_n the same pooled over the rest of its kind in that
    group. Groups with no sample are left out: a version before the item
    existed is not a version where it did badly.

    `colour` is the item's own (item_colour); `thumb` its face or art, drawn
    top right (stats_thumbs, which does the I/O this module does not).
    """
    heading, unit, rest_label, minimum = ITEM_MEASURES[kind]
    rest_label = rest_label.format(act=act)
    colour = colour or sc.ACCENT
    groups = _group_order([g for g in groups if g["n"]], by)
    hits, n = sum(g["hits"] for g in groups), sum(g["n"] for g in groups)
    rest_hits, rest_n = sum(g["rest_hits"] for g in groups), sum(g["rest_n"] for g in groups)

    kind_text = ITEM_KIND_LABELS[kind] + (f" · act {act}" if act else "")
    who = " · ".join(p for p in [population, f"version ≥ {CUTOFF_VERSION}"] if p)
    card = sc.Card(name, f"{kind_text}\n{who}", thumb=thumb)
    thumb_w = thumb.width / sc.SCALE + 14 if thumb is not None else 0
    tiles = sc.StatTiles([
        (unit.capitalize(), f"{n:,}"),
        (heading, f"{round(100 * hits / n)}%" if n else "—"),
        (ITEM_TILE_REST[kind].format(act=act), f"{round(100 * rest_hits / rest_n)}%" if rest_n else "—"),
    ], columns=3, inset_right=thumb_w)
    card.add(tiles)
    # Full-width blocks start below the thumb.
    below = thumb.height / sc.SCALE - card.head() - tiles.height if thumb is not None else 0
    card.add(sc.Spacer(max(below, 0) + 8))
    if not groups:
        return card

    def flag_of(g):
        base = g["rest_hits"] / g["rest_n"] if g["rest_n"] else None
        flag = (rate_flag(g["hits"], g["n"], base)
                if base is not None and g["n"] >= minimum and g["rest_n"] >= minimum else None)
        return base, _flag_style(flag)

    if by in ITEM_COLUMN_AXES:
        # An ordered axis reads as a trend left to right (Turner, 2026-09-29):
        # a column per group in the item's colour, a tick at the rest of its
        # kind in that group. Flags are the marker; the colour stays the item's.
        card.add(sc.SectionHeader(f"{heading} {ITEM_BY_TITLES[by]}"))
        columns = []
        for g in groups[-MAX_ITEM_COLUMNS:]:
            base, style = flag_of(g)
            columns.append({"label": _label({"grp": g["group"]}, by).replace("Ritual ", "R"),
                            "value": g["hits"] / g["n"], "rest": base,
                            "count_text": f"{g['hits']}/{g['n']}", "faded": g["n"] < minimum,
                            "marker": style["marker"], "marker_fill": style["marker_fill"]})
        card.add(sc.RateColumns(columns, fill=colour))
        card.add(sc.Legend([(rest_label[0].upper() + rest_label[1:], sc.REFERENCE, "line")], indent=0))
        return card

    card.add(sc.SectionHeader(f"{heading} {ITEM_BY_TITLES[by]}", f"against {rest_label}"))
    for g in groups:
        rate = g["hits"] / g["n"]
        base, style = flag_of(g)
        card.add(sc.BarRow(_label({"grp": g["group"]}, by), rate,
                           value_text=f"{round(rate * 100)}%", count_text=f"{g['hits']}/{g['n']}",
                           delta=sc.signed((rate - base) * 100) if base is not None else "",
                           faded=g["n"] < minimum, reference=base, label_w=100, count_w=66,
                           fill=colour, marker=style["marker"], marker_fill=style["marker_fill"]))
    return card



# ---------------------------------------------------------------------------
# The daily report
# ---------------------------------------------------------------------------
# Redrawn as one image 2026-09-29 from daily_update._fetch_daily_stats. A
# digest of what happened yesterday, not a verdict: one day is too little to
# flag anything, so the same MIN_ rules as the other reports dim nearly every
# row and flag none. /stats bosses and /stats draft give the verdicts.
#
# It counts everyone, developers included, as the text report always did: a
# day of only testing reads as that day's activity, not as "no one played".
# The links-per-turn chart it used to carry is dropped with the other per-turn
# habits (Turner's review of the breakdown).

DAILY_MOST, DAILY_LEAST = 3, 2


def daily_card(stats: dict, day: str) -> sc.Card:
    # Who it counts goes in the header, as every report's does: everyone,
    # developers included, and no version cutoff (yesterday's builds are
    # current by definition).
    card = sc.Card("Daily Report", f"{day} · Central time · {COHORT_LABELS['all']}")

    tiles = [("Players", str(stats.get("unique_players") or 0)),
             ("New", str(stats.get("new_players") or 0)),
             # Solo only: co-op has its own tile below, counted by session.
             ("Solo runs", str(stats.get("solo_runs", stats.get("total_games")) or 0))]
    if stats.get("total_playtime_sec"):
        tiles.append(("Played", duration(stats["total_playtime_sec"])))
    card.add(*sc.tile_rows(tiles))
    # Co-op and legacy runs, always shown, 0 included (Turner, 2026-09-29): not
    # balance data yet, but whether anyone played them is itself the answer.
    # Co-op counts SESSIONS, not the one row per participant.
    more = [("Co-op runs", str(stats.get("coop_sessions") or 0)),
            ("Legacy runs", str(stats.get("legacy_runs") or 0))]
    if stats.get("tutorial_games"):
        more.append(("Tutorial", str(stats["tutorial_games"])))
    card.add(*sc.tile_rows(more))
    card.add(sc.Spacer(4))

    acts = {int(a): int(n) for a, n in (stats.get("act_distribution") or {}).items() if n}
    if acts:
        right = sc.WIDTH - sc.PAD
        card.add(sc.SectionHeader("How far runs got"))
        card.add(sc.ColumnHeads([("Furthest act", sc.PAD + sc.LABEL_W, "lm"),
                                 ("Beat act 3", right - sc.ActStripRow.COUNT_W, "rm"),
                                 ("Runs", right, "rm")]))
        # Reaching act 4 means the act 3 boss fell.
        card.add(sc.ActStripRow("Solo runs", acts, sum(n for a, n in acts.items() if a >= 4)))
        card.add(sc.act_legend(acts))
        card.add(sc.Spacer(4))

    tg = stats.get("turn_grain") or {}
    if tg.get("error"):
        card.add(sc.Note(f"Boss fights and level-ups unavailable: {tg['error']}"[:90]))
        card.add(sc.Spacer(4))

    record = tg.get("boss_record") or {}
    if record:
        wins = sum(w for w, _ in record.values())
        fights = sum(f for _, f in record.values())
        card.add(sc.SectionHeader("Boss fights", f"won {wins} of {fights}"))
        average = wins / fights if fights else 0
        for name in sorted(record, key=lambda n: (-record[n][1], n)):
            w, f = record[name]
            card.add(sc.BarRow(name, w / f, value_text=f"{round(100 * w / f)}%",
                               count_text=f"{w}/{f}", delta=sc.signed((w / f - average) * 100),
                               faded=f < MIN_BOSS_FIGHTS, reference=average, fill=sc.NEUTRAL))
        card.add(sc.Spacer(4))

    rewards = tg.get("top_rewards") or []
    if rewards:
        card.add(sc.SectionHeader("Level-up picks", f"{tg.get('levelup_packs', 0)} packs"))
        for name, r in rewards:
            card.add(sc.BarRow(name, r["taken"] / r["offered"],
                               value_text=f"{round(100 * r['taken'] / r['offered'])}%",
                               count_text=f"{r['taken']}/{r['offered']}", fill=sc.NEUTRAL))
        card.add(sc.Spacer(4))

    items = (stats.get("draft") or {}).get("item_rates") or []
    if items:
        rate = lambda i: i["picked"] / i["offered"]
        most = sorted(items, key=lambda i: (-rate(i), -i["offered"], i["item_name"]))[:DAILY_MOST]
        least = sorted([i for i in items if i not in most],
                       key=lambda i: (rate(i), -i["offered"], i["item_name"]))[:DAILY_LEAST]
        card.add(sc.SectionHeader("Draft", "most and least picked"))
        for i in most + least:
            card.add(sc.BarRow(i["item_name"], rate(i), value_text=f"{round(100 * rate(i))}%",
                               count_text=f"{i['picked']}/{i['offered']}", fill=sc.NEUTRAL,
                               label_w=130, tag="" if i["item_type"] == "card" else i["item_type"]))
    return card


# ---------------------------------------------------------------------------
# /stats leaderboard
# ---------------------------------------------------------------------------
# Redrawn 2026-09-29 as a ranked table of PLAYERS by their best run, from
# leaderboard_best_view (best run per player and hero). The fun, community
# board, so it counts everyone by default. A table, not bars: combos grow
# exponentially, and the number is the point.

LEADERBOARD_PODIUM = 3


def _combo_key(row: dict) -> Decimal:
    """combo_numeric can pass float's range (2^2048), so compare as Decimal."""
    try:
        return Decimal(str(row.get("combo_numeric")))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(-1)


def leaderboard_rows(rows: list, hero: str | None = None) -> list:
    """Each player's single best run, best first: across heroes, or with
    `hero` only. Ties go to the earlier run, as the view breaks them."""
    best: dict = {}
    for row in rows:
        if hero and row.get("hero") != hero:
            continue
        key = row.get("player")
        mine = best.get(key)
        if mine is None or (_combo_key(row), -_ts(row)) > (_combo_key(mine), -_ts(mine)):
            best[key] = row
    return sorted(best.values(), key=lambda r: (-_combo_key(r), _ts(r), str(r.get("player"))))


def _ts(row: dict) -> float:
    """started_at as a sortable number; missing sorts last."""
    text = str(row.get("started_at") or "")
    digits = "".join(ch for ch in text if ch.isdigit())[:14]
    return float(digits) if digits else float("inf")


def leaderboard_card(rows: list, population: str = "", hero: str | None = None,
                     limit: int = 10) -> sc.Card:
    from azoth_logic import stats_format as sf
    ranked = leaderboard_rows(rows, hero)[:limit]
    parts = [population, hero, f"version ≥ {CUTOFF_VERSION}", "best run per player"]
    card = sc.Card("Top combos", " · ".join(p for p in parts if p))
    x_rank, x_name = sc.PAD, sc.PAD + 28
    x_combo, x_hero, x_act = 330, 344, sc.WIDTH - sc.PAD
    card.add(sc.ColumnHeads([("Player", x_name, "lm"), ("Combo", x_combo, "rm"),
                             ("Hero", x_hero, "lm"), ("Act", x_act, "rm")]))
    for i, row in enumerate(ranked, start=1):
        style = "strong" if i <= LEADERBOARD_PODIUM else "normal"
        ritual = row.get("ritual")
        hero_text = str(row.get("hero") or "—") + (f" R{ritual}" if ritual is not None else "")
        card.add(sc.TableRow([
            (str(i), x_rank, "lm", "rank"),
            (str(row.get("player") or "—"), x_name, "lm", style),
            (sf.value("combo", row.get("combo_numeric", row.get("combo"))), x_combo, "rm", style),
            (hero_text, x_hero, "lm", "muted"),
            (str(row.get("act") or "—"), x_act, "rm", "muted"),
        ]))
        # The podium, set apart without medals.
        if i == LEADERBOARD_PODIUM and len(ranked) > LEADERBOARD_PODIUM:
            card.add(sc.Rule(6))
    return card




# ---------------------------------------------------------------------------
# /stats links
# ---------------------------------------------------------------------------
# 2026-10-02. How many links a turn holds, which types they are, and how long.
# Reads the game repo's 2026-10-02_link_views.sql: link_turn_view (turns per
# links played, zero-link turns included) and link_type_view (links per type
# key and size). Both are counts per cohort, so select_cohort sums them.
#
# A one-card link satisfies all three types (docs/LINK_VALIDATION.md), so it
# is its own row: counted under "all three" it would make that the biggest
# type by far and say nothing about what players link. A longer link valid as
# all three is folded into it (2026-10-03, Turner): Group (equal valences) and
# Sequence (consecutive ones) cannot both hold for two cards that count, so
# with shipped content such a link is one card plus Inert or valence-less
# cards (catalysts). Only Transmutable, or Inert scoped to one link type, could
# make it otherwise, and no shipped card has either.
#
# A link with no type at all is Circumvent's: "ignores link requirements"
# replaces `valid` but not the types validation found, so a link of cards
# matching nothing resolves with none. Real play, not a dev setting (a run
# with ignore_link_requirements on never uploads). A Circumvent link that
# happens to match a type is counted under that type.

LINK_TURN_COUNTS = ("turns",)
LINK_TYPE_COUNTS = ("links",)
LINK_TYPE_ORDER = ("group", "set", "sequence")
LINK_TYPE_NAMES = {"group": "Group", "set": "Set", "sequence": "Sequence"}
# Below this many links a type's average length is grey.
MIN_TYPE_LINKS = 10
# Wide enough for "Group + Sequence".
LINK_LABEL_W = 150


ONE_CARD = "All"
NO_TYPE = "None"


def link_type_label(key: str, size: int) -> str:
    types = [t for t in (key or "").split("+") if t]
    if size == 1 or len(types) == len(LINK_TYPE_ORDER):
        return ONE_CARD
    if not types:
        return NO_TYPE
    return " + ".join(LINK_TYPE_NAMES.get(t, t) for t in types)


def _type_order(label: str) -> tuple:
    """Single types first, then pairs, each in the game's order (group, set,
    sequence), then links that ignored the requirements, then one-card links."""
    if label in (NO_TYPE, ONE_CARD):
        return (9, label == ONE_CARD, label)
    names = [LINK_TYPE_NAMES[t] for t in LINK_TYPE_ORDER]
    parts = label.split(" + ")
    index = tuple(names.index(p) if p in names else len(names) for p in parts)
    return (len(parts), index, label)


def link_types(rows: list) -> list:
    """`[(label, links, cards)]` from link_type_view rows, merged over turn
    type, in display order. `cards` is the sum of sizes, for averages."""
    merged: dict = {}
    for r in rows:
        size = int(r.get("link_size") or 0)
        label = link_type_label(r.get("link_types") or "", size)
        n = int(r.get("links") or 0)
        entry = merged.setdefault(label, [0, 0])
        entry[0] += n
        entry[1] += n * size
    return sorted(((k, n, c) for k, (n, c) in merged.items() if n),
                  key=lambda t: _type_order(t[0]))


def links_per_turn(rows: list, turn_type: str) -> dict:
    """`{links: turns}` for one turn type, from link_turn_view rows."""
    out: dict = {}
    for r in rows:
        if r.get("turn_type") == turn_type and int(r.get("turns") or 0):
            k = int(r.get("links") or 0)
            out[k] = out.get(k, 0) + int(r["turns"])
    return out


def _share(n: int, total: int) -> str:
    """A whole percent, but never 0% for something that happened."""
    pct = round(100 * n / total)
    return "<1%" if n and not pct else f"{pct}%"


def _mean(dist: dict):
    turns = sum(dist.values())
    return sum(k * t for k, t in dist.items()) / turns if turns else None


def links_card(turn_rows: list, type_rows: list, population: str = "") -> sc.Card:
    regular = links_per_turn(turn_rows, "regular")
    boss = links_per_turn(turn_rows, "boss")
    types = link_types(type_rows)
    links = sum(n for _, n, _ in types)
    # Length is read over links of more than one card that counts: a
    # one-card link's length is padding, not a choice of how long to go. The
    # tile and the length section's baseline are the same number.
    longer = [(label, n, c) for label, n, c in types if label != ONE_CARD]
    n_longer = sum(n for _, n, _ in longer)
    average = sum(c for _, _, c in longer) / n_longer if n_longer else None

    parts = [population, f"version ≥ {CUTOFF_VERSION}"]
    card = sc.Card("Links", " · ".join(p for p in parts if p))

    def per(dist):
        m = _mean(dist)
        return "—" if m is None else f"{m:.1f}"
    card.add(*sc.tile_rows([("Per regular turn", per(regular)),
                            ("Per boss turn", per(boss)),
                            ("Average length", "—" if average is None else f"{average:.1f}"),
                            ("Links", f"{links:,}")]))
    card.add(sc.Spacer(4))

    for title, dist in (("Links per regular turn", regular), ("Links per boss turn", boss)):
        if not dist:
            continue
        turns = sum(dist.values())
        card.add(sc.SectionHeader(title, f"{turns:,} turns"))
        card.add(sc.Histogram([dist.get(k, 0) for k in range(max(dist) + 1)], _mean(dist)))
        card.add(sc.Spacer(4))

    if types:
        card.add(sc.SectionHeader("Link types", "share of links"))
        for label, n, _ in types:
            card.add(sc.BarRow(label, n / links, value_text=_share(n, links),
                               count_text=f"{n:,}", label_w=LINK_LABEL_W, fill=sc.NEUTRAL))
        card.add(sc.Spacer(4))

        # Length per type, against the average above. The scale is set by
        # the types with enough links to trust: 3 Circumvent links averaging
        # 10.7 cards squeezed every other bar into the first quarter
        # (2026-10-03). A greyed row past the scale runs to the full track.
        if average is not None:
            trusted = [c / n for _, n, c in longer if n >= MIN_TYPE_LINKS] \
                or [c / n for _, n, c in longer]
            scale = max(trusted) * 1.25
            card.add(sc.SectionHeader("Length by type", f"{average:.1f} cards on average"))
            for label, n, c in longer:
                mean = c / n
                card.add(sc.BarRow(label, mean / scale, value_text=f"{mean:.1f}",
                                   count_text=f"{n:,}", delta=f"{mean - average:+.1f}",
                                   reference=average / scale, faded=n < MIN_TYPE_LINKS,
                                   label_w=LINK_LABEL_W, fill=sc.NEUTRAL))
    return card


# ---------------------------------------------------------------------------
# Surveys: /stats surveys, and the daily report's section
# ---------------------------------------------------------------------------
# The game's one-click survey questions (game repo docs/SURVEYS.md). One row
# per survey SHOWN, with its outcome: answered, skipped, or ignored (shown and
# never touched). /stats surveys reads survey_answer_view, counts per cohort;
# /daily_reports' feedback post tallies the raw rows since its last post,
# everyone included.

SURVEY_COUNTS = ("responses", "comments")
SURVEY_MERGE = ("question_id", "moment", "run_slot", "outcome", "answer")
# Below this many answers a question's bars are grey.
MIN_SURVEY_ANSWERS = 5
SURVEY_LABEL_W = 110
# The feedback post's question column: wide enough for "Fight difficulty" in bold.
DAILY_SURVEY_LABEL_W = 142
OUTCOME_COLOURS = {"answered": sc.ACCENT, "skipped": sc.NEUTRAL, "ignored": sc.FADED}
OUTCOME_NAMES = {"answered": "Answered", "skipped": "Skipped", "ignored": "Ignored"}


def survey_tally(raw: list) -> list:
    """survey_answer_view-shaped rows from raw survey_responses rows: one per
    (question, moment, run slot, outcome, answer), with `responses` and
    `comments` counted."""
    merged: dict = {}
    for r in raw:
        slot = r.get("run_number") if r.get("run_number") in (1, 3) else None
        key = (r.get("question_id"), r.get("moment"), slot, r.get("outcome"), r.get("answer"))
        entry = merged.setdefault(key, dict(zip(SURVEY_MERGE, key), responses=0, comments=0))
        entry["responses"] += 1
        entry["comments"] += 1 if r.get("has_comment") else 0
    return list(merged.values())


def _outcomes(rows: list) -> dict:
    out = {"answered": 0, "skipped": 0, "ignored": 0}
    for r in rows:
        out[r.get("outcome")] = out.get(r.get("outcome"), 0) + int(r.get("responses") or 0)
    return out


def _answer_counts(rows: list) -> dict:
    """`{answer_key: answers}` over answered rows."""
    out: dict = {}
    for r in rows:
        if r.get("outcome") == "answered" and r.get("answer") is not None:
            out[str(r["answer"])] = out.get(str(r["answer"]), 0) + int(r.get("responses") or 0)
    return out


def _answer_keys(question_id: str, counts: dict) -> list:
    """The answers to draw, in order: a scale's 1-5 always, a choice's own
    answers in the game's order, then anything unknown."""
    if sl.is_scale(question_id):
        known = [str(k) for k in range(1, 6)]
    else:
        known = list(sl.CHOICES.get(question_id, []))
    return known + sorted(k for k in counts if k not in known)


def _scale_mean(counts: dict):
    total = sum(n for k, n in counts.items() if k.isdigit())
    return sum(int(k) * n for k, n in counts.items() if k.isdigit()) / total if total else None


def _answer_rows(question_id: str, counts: dict, tag: str = "") -> list:
    total = sum(counts.values())
    faded = total < MIN_SURVEY_ANSWERS
    ends = sl.SCALE_ENDS.get(question_id, {})
    blocks = []
    for key in _answer_keys(question_id, counts):
        n = counts.get(key, 0)
        if sl.is_scale(question_id):
            label, row_tag = key, ends.get(int(key), "") if key.isdigit() else ""
        else:
            label, row_tag = sl.ANSWERS.get(key, key), ""
        if tag:
            row_tag = f"{tag} · {row_tag}" if row_tag else tag
        blocks.append(sc.BarRow(label, n / total if total else 0,
                                value_text=_share(n, total) if total else "—",
                                count_text=f"{n:,}", faded=faded, tag=row_tag,
                                label_w=SURVEY_LABEL_W, fill=sc.NEUTRAL))
    return blocks


def surveys_card(rows: list, population: str = "") -> sc.Card:
    outcomes = _outcomes(rows)
    shown = sum(outcomes.values())
    comments = sum(int(r.get("comments") or 0) for r in rows)
    card = sc.Card("Surveys", population)
    rate = f"{round(100 * outcomes['answered'] / shown)}%" if shown else "—"
    card.add(*sc.tile_rows([("Shown", f"{shown:,}"), ("Answered", rate),
                            ("Comments", f"{comments:,}")]))
    card.add(sc.Spacer(4))

    # Where it was shown, and what players did with it: the bar's length is
    # how often that screen asked, split by outcome.
    by_moment: dict = {}
    for r in rows:
        by_moment.setdefault(r.get("moment"), []).append(r)
    moments = sorted(by_moment, key=lambda m: (sl.MOMENT_ORDER.index(m)
                                               if m in sl.MOMENT_ORDER else 99, str(m)))
    counts = {m: _outcomes(by_moment[m]) for m in moments}
    scale = max((sum(c.values()) for c in counts.values()), default=0)
    if scale:
        card.add(sc.SectionHeader("Where it was shown", "answered of shown"))
        for m in moments:
            c = counts[m]
            n = sum(c.values())
            card.add(sc.TimeRow(sl.MOMENTS.get(m, str(m)),
                                [(c[o], OUTCOME_COLOURS[o]) for o in OUTCOME_COLOURS],
                                scale, total_text=f"{round(100 * c['answered'] / n)}%" if n else "—",
                                extra=(f"{c['answered']}/{n}",)))
        present = {o for c in counts.values() for o, n in c.items() if n}
        card.add(sc.Legend([(OUTCOME_NAMES[o], OUTCOME_COLOURS[o])
                            for o in OUTCOME_COLOURS if o in present]))
        card.add(sc.Spacer(4))

    by_question: dict = {}
    for r in rows:
        by_question.setdefault(r.get("question_id"), []).append(r)
    for qid in sorted(by_question, key=sl.order_key):
        q_rows = by_question[qid]
        counts_all = _answer_counts(q_rows)
        answered = sum(counts_all.values())
        if not answered:
            continue
        detail = [f"{answered:,} answer" + ("" if answered == 1 else "s")]
        mean = _scale_mean(counts_all) if sl.is_scale(qid) else None
        if mean is not None:
            detail.append(f"avg {mean:.1f}")
        q_comments = sum(int(r.get("comments") or 0) for r in q_rows)
        if q_comments:
            detail.append(f"{q_comments} comment" + ("" if q_comments == 1 else "s"))
        card.add(sc.SectionHeader(sl.question(qid), " · ".join(detail)))
        # The first-run question is asked in run 1 and again in run 3: the
        # point is to compare them, so each run gets its own bars.
        slots = sorted({r.get("run_slot") for r in q_rows if r.get("run_slot") in (1, 3)})
        if qid == "understood" and slots:
            for slot in slots:
                slot_counts = _answer_counts([r for r in q_rows if r.get("run_slot") == slot])
                if sum(slot_counts.values()):
                    card.add(*_answer_rows(qid, slot_counts, tag=f"run {slot}"))
        else:
            card.add(*_answer_rows(qid, counts_all))
        card.add(sc.Spacer(4))
    return card


def _answer_summary(question_id: str, counts: dict) -> str:
    """One line of a day's answers: "Too hard 2 · Just right 1", or for a
    1-5 question "avg 3.5 (4, 3)"."""
    if sl.is_scale(question_id):
        mean = _scale_mean(counts)
        scores = sorted((int(k) for k, n in counts.items() if k.isdigit() for _ in range(n)),
                        reverse=True)
        return f"avg {mean:.1f}  ({', '.join(map(str, scores))})" if mean is not None else ""
    keys = [k for k in _answer_keys(question_id, counts) if counts.get(k)]
    return " · ".join(f"{sl.ANSWERS.get(k, k)} {counts[k]}" for k in keys)


def survey_answers_card(tally: list):
    """/daily_reports' survey answers: how many were shown and answered since
    the last post, then a line per question asked. None when none were shown.
    Everyone, developers included, like the rest of that post."""
    outcomes = _outcomes(tally)
    shown = sum(outcomes.values())
    if not shown:
        return None
    card = sc.Card("Survey answers", f"Since the last post · {COHORT_LABELS['all']}")
    blocks = [sc.SectionHeader("Answered", f"{outcomes['answered']} of {shown} shown")]
    by_question: dict = {}
    for r in tally:
        by_question.setdefault(r.get("question_id"), []).append(r)
    right = sc.WIDTH - sc.PAD
    for qid in sorted(by_question, key=sl.order_key):
        q_rows = by_question[qid]
        counts = _answer_counts(q_rows)
        n = sum(int(r.get("responses") or 0) for r in q_rows)
        blocks.append(sc.TableRow([
            (sl.short(qid), sc.PAD, "lm", "strong"),
            (_answer_summary(qid, counts) or "no answers", sc.PAD + DAILY_SURVEY_LABEL_W, "lm",
             "normal" if counts else "muted"),
            (f"{sum(counts.values())}/{n}", right, "rm", "muted"),
        ], height=26))
    return card.add(*blocks)
