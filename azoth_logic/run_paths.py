"""New players' paths, run by run: the graph behind /stats paths.

Caleb's design, 2026-10-08, from the game repo's player-paths prototype
(docs/USER_BEHAVIOR_VISUALS_PLAN.md there). One row per step of a player's
history. Since 2026-11-12 (Turner: "we care about the first 5 rows quite a
bit") the first five are the onboarding, and the runs follow:

  * Row 0 is the tutorial, by the furthest any of the player's tutorial runs
    got: BEAT the act 1 boss, LOST to it (an act 1 boss turn lost, Retry or
    not), or LEFT before it. A player with no tutorial run starts at "No
    tutorial".
  * Rows 1-4 are lessons 2-5 (`games.format` `lesson_2` ..), by its furthest
    run: DONE when it beat the act 1 boss and the player moved on to the next
    lesson there (`result = 'lesson_complete'`); PLAYED_ON_INTO when they kept
    playing instead (Lesson Complete's other button), named by the act the run
    ended in ("Act 2", "Act 3", "Won"; Turner, 2026-11-12); LEFT when it never
    beat the act 1 boss. A path visits only the lessons
    the player played, so one that went on to regular runs without them (Full
    Game after the tutorial, or Skip Tutorial in Settings) jumps from its last
    onboarding row straight to Run 1. Practice runs are onboarding too, and
    are on no row.
  * Every row after them is one regular run: IMPROVED when it beat more acts than any
    earlier run on the same hero and ritual, SAME when it ended in the same
    act as that best, SHORT when it ended earlier (a death, a restart, a run
    left unfinished). A hero and ritual not played yet has beaten no acts, so
    a first run on one that dies in act 1 is SAME (split 2026-11-03, Turner:
    "improved or died at the same spot"; until then SAME and SHORT were one
    LOST).
  * A path ends in STOPPED once the player has gone 3 days without a run, or
    PLAYED ON past the last run shown. A player who ran in the last 3 days has
    no end node: their path stops at their latest run until their next run, or
    3 idle days, shows up in a later report.

Pure: game rows in, a `Paths` out. The drawing is stats_cards.paths_card.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from azoth_logic.stats_format import CUTOFF_VERSION

ACTIVE = timedelta(days=3)       # a player with a run this recent has not stopped
IN_PROGRESS = timedelta(hours=2) # a resultless last run this recent may still be going
WINS = {"victory", "no_boss_key"}
WON = 99                         # acts beaten by a win: more than any act

# Node kinds, in the order a row lists them.
(BEAT, LOST, LEFT, NO_TUTORIAL, DONE, PLAYED_ON_INTO, IMPROVED, SAME, SHORT, STOPPED,
 PLAYED_ON) = (
    "beat", "lost", "left", "no_tutorial", "done", "played_on_into", "improved", "same",
    "short", "stopped", "played_on")
_KIND_ORDER = {BEAT: 0, DONE: 0, LOST: 1, PLAYED_ON_INTO: 1, LEFT: 2, NO_TUTORIAL: 3,
               IMPROVED: 0, SAME: 1, SHORT: 2, STOPPED: 3, PLAYED_ON: 4}
# The result a lesson run ends with when the player moves on to the next one.
LESSON_COMPLETE = "lesson_complete"
NODE_LABELS = {BEAT: "Beat the boss", LOST: "Lost to the boss", LEFT: "Left early",
               NO_TUTORIAL: "No tutorial", DONE: "Done", PLAYED_ON: "Played on"}

TUTORIAL_FORMAT = "tutorial"
PRACTICE_FORMAT = "practice"
# The lessons after the tutorial (lesson 1), one row each after it.
LESSONS = (2, 3, 4, 5)
TUTORIAL_ROW = 0
FIRST_RUN_ROW = 1 + len(LESSONS)


def lesson_of(game: dict) -> int:
    """The lesson a `lesson_N` run plays, or 0."""
    fmt = str(game.get("format") or "")
    return int(fmt[7:]) if fmt.startswith("lesson_") and fmt[7:].isdigit() else 0


def is_onboarding(game: dict) -> bool:
    return game.get("format") in (TUTORIAL_FORMAT, PRACTICE_FORMAT) or lesson_of(game) > 0


@dataclass(eq=False)
class Node:
    row: int
    kind: str
    label: str = ""          # the tutorial row's names, and "Played on"
    players: int = 0
    order: float = 0


@dataclass
class Paths:
    """`rows[r]` lists row r's nodes in display order; `links` maps a
    (from, to) pair of nodes to the players who took that step."""
    players: int
    rows: list = field(default_factory=list)
    links: dict = field(default_factory=dict)


def _version_key(version) -> list:
    return [int(p) if p.isdigit() else -1 for p in str(version or "").split(".")]


def parse_time(value) -> datetime:
    """A `started_at` as a datetime. PostgREST trims trailing zeros from the
    fraction (`.12+00:00`), which Python 3.10's fromisoformat refuses, so the
    fraction is dropped: a second is finer than anything here measures."""
    if isinstance(value, datetime):
        return value
    text = re.sub(r"\.\d+", "", str(value)).replace("Z", "+00:00")
    when = datetime.fromisoformat(text)
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def acts_beaten(game: dict) -> int:
    """A win beats every act; otherwise every act before the one the run ended
    in, or every act up to the last boss it beat, whichever is further.

    `act_reached` lags a boss win. It is only written when the run's outcome is
    sent, so a run the player left and never came back to has it NULL, and a
    run restarted between a boss win and the next act reads the act it just
    beat. A tutorial left after its act 1 boss is the common case: the prompts
    end there and many new players close the game. `boss_won_act` is the
    highest act whose boss turn the run won, from `turns`
    (`stats.new_player_paths`); without it, a NULL `act_reached` is act 1."""
    if game.get("result") in WINS:
        return WON
    return max(int(game.get("act_reached") or 1) - 1, int(game.get("boss_won_act") or 0))


def tutorial_kind(tutorials: list) -> str:
    """The tutorial row's node for a player's tutorial runs: BEAT if one beat
    the act 1 boss, LOST if one lost an act 1 boss turn (`boss_lost_act`,
    from `turns`), LEFT otherwise."""
    if any(acts_beaten(g) >= 1 for g in tutorials):
        return BEAT
    if any(int(g.get("boss_lost_act") or 0) == 1 for g in tutorials):
        return LOST
    return LEFT


def lesson_node(played: list) -> tuple:
    """`(kind, label)` for a lesson's row from its runs, by the furthest: LEFT
    if none beat the act 1 boss, DONE if the furthest moved on to the next
    lesson there, else PLAYED_ON_INTO labelled by the act it ended in."""
    best = max(played, key=lambda g: (acts_beaten(g), g.get("result") == LESSON_COMPLETE))
    beaten = acts_beaten(best)
    if beaten < 1:
        return LEFT, ""
    if best.get("result") == LESSON_COMPLETE:
        return DONE, ""
    return PLAYED_ON_INTO, "Won" if beaten == WON else f"Act {beaten + 1}"


def steps(runs: list, until: datetime) -> list:
    """One player's steps, top row first: `(row, kind, label)`.

    `runs` are the player's games in start order. A last run with no result
    that started within IN_PROGRESS of `until` may still be going, and is left
    out: it would read as Lost before it ends. An older resultless run was
    abandoned and counts.

    The onboarding comes first whatever order it was played in: the tutorial
    row, then a row for each lesson the player played. Then each regular run
    in the order played, from FIRST_RUN_ROW.
    """
    if runs and runs[-1].get("result") is None and until - parse_time(runs[-1]["started_at"]) < IN_PROGRESS:
        runs = runs[:-1]
    if not runs:
        return []
    tutorials = [g for g in runs if g.get("format") == TUTORIAL_FORMAT]
    out = [(TUTORIAL_ROW, tutorial_kind(tutorials) if tutorials else NO_TUTORIAL, "")]
    for row, lesson in enumerate(LESSONS, start=1):
        played = [g for g in runs if lesson_of(g) == lesson]
        if played:
            out.append((row, *lesson_node(played)))
    best: dict = {}
    for i, g in enumerate([g for g in runs if not is_onboarding(g)]):
        key = (g.get("starting_hero"), g.get("ritual"))
        b, before = acts_beaten(g), best.get(key, 0)
        if b > before:
            best[key] = b
            kind = IMPROVED
        else:
            kind = SAME if b == before else SHORT
        out.append((FIRST_RUN_ROW + i, kind, ""))
    return out


def new_arrivals(games: list, earlier: set, cohorts: dict) -> dict:
    """player_uuid -> their solo runs at the cutoff, oldest first, for the
    players who ARRIVED in the window `games` covers.

    `games` are the solo runs started in the window; `earlier` the players with
    any run before it (they arrived earlier); `cohorts` player_uuid -> cohort
    from player_cohort_view, where a player missing from the view is new (as
    survey_answer_view counts them). Developers and veterans are left out: a
    veteran's path starts partway through.
    """
    by_player: dict = {}
    for g in games:
        p = g.get("player_uuid")
        if not p or p in earlier or cohorts.get(p, "new") != "new" or not g.get("started_at"):
            continue
        if _version_key(g.get("version")) < _version_key(CUTOFF_VERSION):
            continue
        by_player.setdefault(p, []).append(g)
    for runs in by_player.values():
        runs.sort(key=lambda g: parse_time(g["started_at"]))
    return by_player


def build(by_player: dict, until: datetime, runs_shown: int = 5) -> Paths:
    """The graph: the tutorial row, a row per lesson, then `runs_shown` run
    rows, then a row for the paths that go on past the last run shown. A step
    can skip rows (a path with no lessons goes from the tutorial to Run 1), and
    a path's end node sits in the row after its last step."""
    nodes: dict = {}
    links: dict = {}

    def node(row, kind, label=""):
        key = (row, kind, label)
        if key not in nodes:
            # A played-on lesson's acts in order, Won last.
            order = 99 if label == "Won" else int(label[4:]) if label.startswith("Act ") else 0
            nodes[key] = Node(row, kind, label or NODE_LABELS.get(kind, ""), order=order)
        return nodes[key]

    last_row = FIRST_RUN_ROW + runs_shown - 1
    players = 0
    for runs in by_player.values():
        path = steps(runs, until)
        if not path:
            continue
        players += 1
        shown = [s for s in path if s[0] <= last_row]
        prev = None
        for row, kind, label in shown:
            n = node(row, kind, label)
            n.players += 1
            if prev is not None:
                links[(prev, n)] = links.get((prev, n), 0) + 1
            prev = n
        end_row = shown[-1][0] + 1
        if len(path) > len(shown):
            end = node(end_row, PLAYED_ON)
        elif until - parse_time(runs[-1]["started_at"]) >= ACTIVE:
            end = node(end_row, STOPPED)
        else:
            continue     # ran in the last 3 days: no end node yet
        end.players += 1
        links[(prev, end)] = links.get((prev, end), 0) + 1

    depth = max((n.row for n in nodes.values()), default=-1) + 1
    rows = [sorted((n for n in nodes.values() if n.row == r),
                   key=lambda n: (_KIND_ORDER[n.kind], n.order)) for r in range(depth)]
    return Paths(players, rows, links)
