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


def select_cohort(rows: list, key: str, merge_on: str, counts: tuple):
    """`(merged_rows, filtered)`: one row per `merge_on`, with the `counts`
    columns summed over the cohorts `key` names.

    Every entity that appears in ANY row is kept, zeroed if none of its rows
    are in the cohort: a boss fought only by developers is still a boss the new
    playtesters have not fought, not a boss that does not exist.

    `filtered` is False when the view predates the cohort migration (no
    `cohort` column): every row is then counted, and the caller should say the
    filter did not apply rather than label everyone as new playtesters.
    """
    filtered = not rows or "cohort" in rows[0]
    wanted = COHORTS[key]
    merged: dict = {}
    for row in rows:
        entry = merged.get(row.get(merge_on))
        if entry is None:
            entry = {k: v for k, v in row.items() if k != "cohort" and k not in counts}
            entry.update({c: 0 for c in counts})
            merged[row.get(merge_on)] = entry
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
    baseline = rest_wins / rest_finished
    low, high = wilson_interval(int(row.get("wins") or 0), finished)
    if high < baseline:
        return "below"
    if low > baseline:
        return "above"
    return None


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
