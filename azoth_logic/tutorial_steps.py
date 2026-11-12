"""New players' first tutorial, prompt by prompt: the graph behind the daily
report's tutorial steps card.

Turner, 2026-11-03: where in the tutorial do new players leave, and do they
come back? The game writes one `tutorial_steps` row per prompt a tutorial run
shows (game repo docs/DB_SCHEMA.md § tutorial_steps), from 0.12.0. Each new
player whose first run was a tutorial on such a build is followed through that
run:

  * They REACHED every step that run showed.
  * The run ended in one of three ways. The act 1 boss was won or lost
    (`turns`, the run's act 1 boss turn), or the player left at a step: the
    one still on screen when they left (`closed = false`), or, left between
    prompts, the last one shown.
  * Every ending is split by what the player did next, as /stats paths ends a
    path: a later run was a tutorial (Turner, 2026-11-03: did they come back
    to the tutorial, or not at all?), split again by whether one of those beat
    the act 1 boss (REPLAYED_WON) or none did (REPLAYED: left it again or lost
    to the boss; Turner, 2026-11-03: did they finish it the second time?);
    PLAYED_AGAIN (later runs, none a tutorial); STOPPED (3 days without a
    run); or RECENT (none of those yet).

Only the tutorial's own steps count. Since game 2026-11-12 a run's
`tutorial_steps` also hold each look at the Tutorial So Far window, and a
lesson's prompts, both with the run's format as their `tutorial` (`tutorial`,
`lesson_3`) where a step has the tutorial's name (`link_basics`); is_aside
leaves those out.

Steps are added and renamed as the tutorial changes, so their order is read
from the rows (mean `step_index`), not fixed here, and each is filed under the
turn most of its rows were shown in.

Pure: rows in, a `Funnel` out. The drawing is stats_cards.tutorial_steps_card.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from azoth_logic import run_paths as rp

# The first build that writes tutorial_steps. A first tutorial on an older one
# has no rows, which would read as a player who never saw a prompt.
STEPS_FROM = "0.12.0"

REPLAYED_WON, REPLAYED = "replayed_won", "replayed"
PLAYED_AGAIN, STOPPED, RECENT = "played_again", "stopped", "recent"
ENDS = (STOPPED, REPLAYED, REPLAYED_WON, PLAYED_AGAIN, RECENT)
WON, LOST = "won", "lost"


@dataclass
class Step:
    key: str
    group: str               # "turn 1" ... or "boss"
    reached: int = 0
    left: dict = field(default_factory=lambda: dict.fromkeys(ENDS, 0))


@dataclass
class Funnel:
    """`steps` in tutorial order; `boss` the act 1 boss's result ("won" /
    "lost") -> {end: players}. `players` is every player counted."""
    players: int
    steps: list = field(default_factory=list)
    boss: dict = field(default_factory=dict)


def is_aside(row: dict, run_format: str) -> bool:
    """A tutorial_steps row that is not one of the tutorial's steps: its
    `tutorial` is the run's format, not a tutorial's name."""
    return row.get("tutorial") == run_format


def first_tutorials(by_player: dict, until: datetime) -> dict:
    """player_uuid -> their first run, for the players whose first run was a
    tutorial on a build that records its steps. A resultless one that started
    within run_paths.IN_PROGRESS of `until` may still be going, and is left
    out, as /stats paths leaves it out."""
    out = {}
    for p, runs in by_player.items():
        g = runs[0] if runs else None
        if not g or g.get("format") != "tutorial":
            continue
        if rp._version_key(g.get("version")) < rp._version_key(STEPS_FROM):
            continue
        if g.get("result") is None and until - rp.parse_time(g["started_at"]) < rp.IN_PROGRESS:
            continue
        out[p] = g
    return out


def later_tutorials(by_player: dict, firsts: dict) -> list:
    """The uuids of every tutorial each player in `firsts` played after their
    first run: whose act 1 boss turns say whether a replay was won."""
    return [g["uuid"] for p in firsts for g in by_player[p][1:] if g.get("format") == "tutorial"]


def beat_the_boss(turns: list) -> set:
    """The game uuids among `turns` rows whose act 1 boss was won."""
    return {t["game_uuid"] for t in turns if t.get("boss_id") is not None
            and int(t.get("act") or 0) <= 1 and t.get("boss_result") == "win"}


def after(runs: list, until: datetime, won: set = frozenset()) -> str:
    """What a player did after their first run: replayed the tutorial (and
    beat its boss, a game in `won`, or not), played other runs only, went 3
    days without a run, or none of those yet."""
    replays = [g for g in runs[1:] if g.get("format") == "tutorial"]
    if replays:
        return REPLAYED_WON if any(g["uuid"] in won for g in replays) else REPLAYED
    if len(runs) > 1:
        return PLAYED_AGAIN
    return STOPPED if until - rp.parse_time(runs[-1]["started_at"]) >= rp.ACTIVE else RECENT


def build(by_player: dict, steps: list, turns: list, until: datetime) -> Funnel:
    """The funnel of each new player's first tutorial.

    `steps` are the tutorial_steps rows of those runs (game_uuid, tutorial,
    step, step_index, turn_index, closed); `turns` the `turns` rows (game_uuid,
    turn_index, act, boss_id, boss_result) of those runs and of the players'
    later tutorials (later_tutorials), which say whether a replay was won. A run with no step rows at all is
    left out: it showed no prompt the game could send (a crash, or a quit
    flush that ran out of time), and counting it nowhere is better than
    counting it as a quit at the first prompt.
    """
    firsts = first_tutorials(by_player, until)
    games = {g["uuid"]: p for p, g in firsts.items()}
    rows_by_game: dict = {}
    for r in steps:
        if r.get("game_uuid") in games and r.get("step") \
                and not is_aside(r, firsts[games[r["game_uuid"]]].get("format")):
            rows_by_game.setdefault(r["game_uuid"], []).append(r)
    boss_turns = {(t["game_uuid"], t.get("turn_index")) for t in turns
                  if t.get("game_uuid") in games and t.get("boss_id") is not None}
    won = beat_the_boss(turns)
    result = {}
    for t in turns:
        if (t.get("game_uuid") in games and t.get("boss_id") is not None
                and int(t.get("act") or 0) <= 1 and t.get("boss_result") in ("win", "loss")):
            result[t["game_uuid"]] = WON if t["boss_result"] == "win" else LOST

    # Order and group from every row: mean position, and the turn most were in.
    index, turn_of = {}, {}
    for rows in rows_by_game.values():
        for r in rows:
            index.setdefault(r["step"], []).append(int(r.get("step_index") or 0))
            boss = (r["game_uuid"], r.get("turn_index")) in boss_turns
            # The opening prompts show before turn 1 starts, at turn_count 0.
            turn = f"turn {max(int(r.get('turn_index') or 0), 1)}"
            turn_of.setdefault(r["step"], Counter())["boss" if boss else turn] += 1
    order = sorted(index, key=lambda k: (sum(index[k]) / len(index[k]), k))
    by_key = {k: Step(k, turn_of[k].most_common(1)[0][0]) for k in order}

    funnel = Funnel(0, [by_key[k] for k in order])
    for game_uuid, rows in rows_by_game.items():
        p = games[game_uuid]
        funnel.players += 1
        rows.sort(key=lambda r: int(r.get("step_index") or 0))
        for k in {r["step"] for r in rows}:
            by_key[k].reached += 1
        end = after(by_player[p], until, won)
        if game_uuid in result:
            funnel.boss.setdefault(result[game_uuid], dict.fromkeys(ENDS, 0))[end] += 1
            continue
        open_rows = [r for r in rows if r.get("closed") is False]
        where = (open_rows or rows)[-1]["step"]
        by_key[where].left[end] += 1
    return funnel
