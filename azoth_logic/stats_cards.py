"""Each /stats report's rows, laid out as a stats_charts.Card.

One function per report. The views do the aggregation; these decide what the
reader sees first, what is grey, and what every number is read against. Pure:
rows in, Card out.
"""
from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

from azoth_logic import stats_charts as sc
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
    # What it cannot show -- unfinished fights -- is in bosses_footer instead.
    return card


def bosses_footer(rows: list) -> str:
    """The embed footer: what the card rests on, as copyable text, including
    the unfinished fights the win rates leave out."""
    finished = sum(_finished(r) for r in rows)
    unfinished = sum(int(r.get("unfinished") or 0) for r in rows)
    footer = f"version >= {CUTOFF_VERSION} · {finished} finished fights · solo"
    if unfinished:
        footer += f" · {unfinished} unfinished not counted"
    return footer


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


def breakdown_footer(rows: list, by: str) -> str:
    runs = sum(g["runs"] for g in breakdown_groups(rows, by))
    return f"version >= {CUTOFF_VERSION} · {runs} solo runs · grouped by {by}"


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
# low; the footer says so.

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


def _on_tracker(row: dict) -> bool:
    """Has this player run a build carrying EngagementTracker? The view's
    tracker columns are NULL until they have."""
    return any(row.get(col) is not None for col in ("custom_run_sec", "codex_sec", "art_sec"))


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


def players_footer(rows: list) -> str:
    listed = [r for r in rows if int(r.get("runs") or 0) or _tracked(r)]
    on_tracker = sum(1 for r in listed if _on_tracker(r))
    return (f"version >= {CUTOFF_VERSION} · {len(listed)} player{'' if len(listed) == 1 else 's'}"
            f" · run time includes idle; Codex time is active, from tracked builds"
            f" ({on_tracker} of {len(listed)} players so far)")


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


def player_footer(runs: list) -> str:
    return f"version >= {CUTOFF_VERSION} · {len(runs)} solo run{'' if len(runs) == 1 else 's'}"


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
PACK_LABELS = {"card": "Atoms", "catalyst": "Catalysts", "aspect": "Aspects", "rite": "Rites"}
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

    valences = _buckets(rows, "valence")
    section("By valence", [(_valence_name(v), *valences[v]) for v in sorted(valences, key=_valence_key)])

    bare = _buckets(rows, "embellished").get("bare")
    kinds = _buckets(rows, "kind")
    if bare and kinds:
        section("By embellishment",
                [(KIND_LABELS.get(k, k), *kinds[k]) for k in KIND_LABELS if k in kinds],
                baseline=bare,
                detail=f"against bare cards: {round(100 * bare[0] / bare[1])}%")
    return card


def draft_picks_footer(rows: list) -> str:
    offered = sum(o for _, o in _buckets(rows, "type").values())
    text = f"version >= {CUTOFF_VERSION} · {offered:,} offers · solo · element and valence are cards only"
    if _buckets(rows, "pack_type"):
        text += " · a pack is picked when opened; what is offered inside it counts as offered"
    return text


def _item_label(row: dict) -> str:
    return str(row.get("item_name") or "—")


def draft_items_card(rows: list, population: str = "") -> sc.Card:
    """The five most and five least picked items, cards, aspects and rites
    ranked together, each read against the pick rate over ALL offers."""
    offered = sum(int(r.get("offered") or 0) for r in rows)
    picked = sum(int(r.get("picked") or 0) for r in rows)
    average = picked / offered if offered else 0
    ranked = [r for r in rows if int(r.get("offered") or 0) >= MIN_ITEM_OFFERS]

    def rate(r):
        return int(r.get("picked") or 0) / int(r.get("offered"))

    most = sorted(ranked, key=lambda r: (-rate(r), -int(r["offered"]), _item_label(r)))[:ITEMS_PER_SECTION]
    rest = [r for r in ranked if r not in most]
    least = sorted(rest, key=lambda r: (rate(r), -int(r["offered"]), _item_label(r)))[:ITEMS_PER_SECTION]

    parts = [population, f"version ≥ {CUTOFF_VERSION}", f"items offered {MIN_ITEM_OFFERS}+ times"]
    card = sc.Card("Draft items", " · ".join(p for p in parts if p))

    def section(title, items):
        if not items:
            return
        card.add(sc.SectionHeader(title, f"all items {round(average * 100)}%"))
        for r in items:
            p, o = int(r.get("picked") or 0), int(r.get("offered"))
            flag = rate_flag(p, o, (picked - p) / (offered - o)) if offered - o else None
            kind = r.get("item_type")
            card.add(sc.BarRow(_item_label(r), p / o, value_text=f"{round(100 * p / o)}%",
                               count_text=f"{p}/{o}", delta=sc.signed((p / o - average) * 100),
                               reference=average, label_w=130,
                               tag="" if kind == "card" else str(kind or ""),
                               **_flag_style(flag)))
        card.add(sc.Spacer(6))

    section("Most picked", most)
    # Least picked leads with the lowest rate; drawn in that order.
    section("Least picked", least)
    return card


def draft_items_footer(rows: list) -> str:
    unranked = sum(1 for r in rows if int(r.get("offered") or 0) < MIN_ITEM_OFFERS)
    footer = f"version >= {CUTOFF_VERSION} · {len(rows)} items offered · solo"
    if unranked:
        footer += f" · {unranked} offered fewer than {MIN_ITEM_OFFERS} times, not ranked"
    return footer


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
    valences, _ = sf._valence_buckets(row)
    if valences:
        top = max(n for _, n in valences)
        card.add(sc.SectionHeader("Cards by valence"))
        for label, n in valences:
            card.add(sc.BarRow(label, n / top, value_text=str(n), count_w=0, fill=sc.NEUTRAL))
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
    card = sc.Card("Yesterday", f"{day} · Central time")

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


def leaderboard_footer(rows: list, hero: str | None = None) -> str:
    players = len(leaderboard_rows(rows, hero))
    return f"version >= {CUTOFF_VERSION} · {players} player{'' if players == 1 else 's'} ranked · solo"
