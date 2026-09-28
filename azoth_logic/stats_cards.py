"""Each /stats report's rows, laid out as a stats_charts.Card.

One function per report. The views do the aggregation; these decide what the
reader sees first, what is grey, and what every number is read against. Pure:
rows in, Card out.
"""
from __future__ import annotations

import math

from azoth_logic import stats_charts as sc
from azoth_logic.stats_format import CUTOFF_VERSION

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
    card.add(sc.ActLegend())
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
