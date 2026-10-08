"""New players' paths, run by run: the graph behind /stats paths.

Caleb's design, 2026-10-08, from the game repo's player-paths prototype
(docs/USER_BEHAVIOR_VISUALS_PLAN.md there). One row per step of a player's
history:

  * Row 0 is the tutorial, by how far the player's opening tutorial runs got:
    the last act beaten ("Act 2"), "Died" for none, "Won". A player whose first
    run was a regular one starts at "No tutorial".
  * Every row after it is one run: IMPROVED when it beat more acts than any
    earlier run on the same hero and ritual, LOST otherwise (a death, a
    restart, a run left unfinished).
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
TUTORIAL, NO_TUTORIAL, IMPROVED, LOST, STOPPED, PLAYED_ON = (
    "tutorial", "no_tutorial", "improved", "lost", "stopped", "played_on")
_KIND_ORDER = {TUTORIAL: 0, NO_TUTORIAL: 1, IMPROVED: 0, LOST: 1, STOPPED: 2, PLAYED_ON: 3}


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
    in. `act_reached` is NULL on a run closed before its first save: act 1."""
    if game.get("result") in WINS:
        return WON
    return int(game.get("act_reached") or 1) - 1


def tutorial_label(beaten: int) -> str:
    return "Won" if beaten == WON else "Died" if beaten == 0 else f"Act {beaten}"


def steps(runs: list, until: datetime) -> list:
    """One player's steps, oldest first: `(kind, label, order)`.

    `runs` are the player's games in start order. A last run with no result
    that started within IN_PROGRESS of `until` may still be going, and is left
    out: it would read as Lost before it ends. An older resultless run was
    abandoned and counts.
    """
    if runs and runs[-1].get("result") is None and until - parse_time(runs[-1]["started_at"]) < IN_PROGRESS:
        runs = runs[:-1]
    if not runs:
        return []
    out, best = [], {}
    i = 0
    while i < len(runs) and runs[i].get("format") == "tutorial":
        i += 1
    if i:
        b = max(acts_beaten(g) for g in runs[:i])
        best["tutorial"] = b
        out.append((TUTORIAL, tutorial_label(b), -b))
    else:
        out.append((NO_TUTORIAL, "No tutorial", 1000))
    # The tutorial is its own line, so it sets no best for the hero it plays;
    # a tutorial replayed later is compared with the best tutorial so far.
    for g in runs[i:]:
        key = "tutorial" if g.get("format") == "tutorial" else (g.get("starting_hero"), g.get("ritual"))
        b = acts_beaten(g)
        if b > best.get(key, 0):
            best[key] = b
            out.append((IMPROVED, "", 0))
        else:
            out.append((LOST, "", 1))
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
    """The graph: the tutorial row, then `runs_shown` run rows, then a row for
    the paths that go on or stop after the last run shown."""
    nodes: dict = {}
    links: dict = {}

    def node(row, kind, label, order):
        key = (row, kind, label)
        if key not in nodes:
            nodes[key] = Node(row, kind, label, order=order)
        return nodes[key]

    players = 0
    for runs in by_player.values():
        path = steps(runs, until)
        if not path:
            continue
        players += 1
        shown = path[:runs_shown + 1]
        prev = None
        for row, (kind, label, order) in enumerate(shown):
            n = node(row, kind, label, order)
            n.players += 1
            if prev is not None:
                links[(prev, n)] = links.get((prev, n), 0) + 1
            prev = n
        if len(path) > len(shown):
            end = node(len(shown), PLAYED_ON, "Played on", 0)
        elif until - parse_time(runs[-1]["started_at"]) >= ACTIVE:
            end = node(len(shown), STOPPED, "", 0)
        else:
            continue     # ran in the last 3 days: no end node yet
        end.players += 1
        links[(prev, end)] = links.get((prev, end), 0) + 1

    depth = max((n.row for n in nodes.values()), default=-1) + 1
    rows = [sorted((n for n in nodes.values() if n.row == r),
                   key=lambda n: (_KIND_ORDER[n.kind], n.order)) for r in range(depth)]
    return Paths(players, rows, links)
