"""The daily report's tutorial steps funnel (2026-11-03): who is counted,
where a first tutorial ended, and what the player did next. Appearance is
judged by eye in Discord."""
from datetime import datetime, timedelta, timezone

from azoth_logic import tutorial_steps as ts

NOW = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)


def game(uuid, hours_ago, *, fmt="tutorial", version="0.12.0", result="death"):
    return {"uuid": uuid, "started_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
            "format": fmt, "version": version, "result": result}


def step(game_uuid, key, index, turn=1, closed=True):
    return {"game_uuid": game_uuid, "step": key, "step_index": index, "turn_index": turn,
            "closed": closed}


def three_steps(g, **last):
    return [step(g, "intro", 1, turn=0), step(g, "links", 2), step(g, "patterns", 3, turn=2, **last)]


def by_key(funnel):
    return {s.key: s for s in funnel.steps}


def test_a_player_left_at_the_prompt_still_on_screen():
    f = ts.build({"p": [game("g", 100)]},
                 [step("g", "intro", 1), step("g", "links", 2, closed=False), step("g", "patterns", 3)],
                 [], NOW)
    assert by_key(f)["links"].left[ts.STOPPED] == 1
    assert by_key(f)["patterns"].left[ts.STOPPED] == 0


def test_left_between_prompts_is_the_last_prompt_shown():
    f = ts.build({"p": [game("g", 100)]}, three_steps("g"), [], NOW)
    assert by_key(f)["patterns"].left[ts.STOPPED] == 1
    assert [s.reached for s in f.steps] == [1, 1, 1]


def test_what_they_did_next_splits_every_ending():
    """2026-11-03: a player who came back is split by whether any later run
    was a tutorial, however many regular runs came first."""
    players = {"again": [game("a", 100), game("a2", 50, fmt="standard")],
               "replayed": [game("r", 100), game("r2", 60, fmt="standard"), game("r3", 50)],
               "gone": [game("b", 100)], "recent": [game("c", 30)]}
    rows = three_steps("a") + three_steps("r") + three_steps("b") + three_steps("c")
    left = by_key(ts.build(players, rows, [], NOW))["patterns"].left
    assert left == {ts.PLAYED_AGAIN: 1, ts.REPLAYED: 1, ts.REPLAYED_WON: 0,
                    ts.STOPPED: 1, ts.RECENT: 1}


def test_a_replay_is_split_by_whether_it_beat_the_boss():
    """2026-11-03: did they finish it the second time? Losing to the boss on
    the replay is not finishing it."""
    win = lambda g: {"game_uuid": g, "turn_index": 9, "act": 1, "boss_id": 7, "boss_result": "win"}
    loss = lambda g: {**win(g), "boss_result": "loss"}
    players = {"won": [game("a", 100), game("a2", 90), game("a3", 80)],
               "lost": [game("b", 100), game("b2", 90)],
               "left": [game("c", 100), game("c2", 90, result=None)]}
    rows = three_steps("a") + three_steps("b") + three_steps("c")
    firsts = ts.first_tutorials(players, NOW)
    assert sorted(ts.later_tutorials(players, firsts)) == ["a2", "a3", "b2", "c2"]
    left = by_key(ts.build(players, rows, [win("a3"), loss("b2")], NOW))["patterns"].left
    assert (left[ts.REPLAYED_WON], left[ts.REPLAYED]) == (1, 2)


def test_a_boss_result_ends_the_tutorial_not_a_prompt():
    """Even with a prompt still up when the boss fight ended."""
    turns = [{"game_uuid": "g", "turn_index": 4, "act": 1, "boss_id": 7, "boss_result": "loss"}]
    rows = [step("g", "intro", 1), step("g", "boss", 2, turn=4, closed=False)]
    f = ts.build({"p": [game("g", 100)]}, rows, turns, NOW)
    assert f.boss == {ts.LOST: dict.fromkeys(ts.ENDS, 0) | {ts.STOPPED: 1}}
    assert sum(sum(s.left.values()) for s in f.steps) == 0
    assert by_key(f)["boss"].group == "boss"


def test_only_a_first_run_that_was_a_tutorial_on_a_build_with_steps_counts():
    players = {"regular": [game("r", 100, fmt="standard"), game("r2", 90)],
               "old": [game("o", 100, version="0.11.6")],
               "going": [game("n", 1, result=None)],
               "in": [game("g", 100)]}
    rows = three_steps("r2") + three_steps("o") + three_steps("n") + three_steps("g")
    assert ts.build(players, rows, [], NOW).players == 1


def test_a_run_with_no_prompt_rows_is_counted_nowhere():
    """Not as a quit at the first prompt: it showed none the game could send."""
    f = ts.build({"p": [game("g", 100)], "q": [game("h", 100)]}, three_steps("h"), [], NOW)
    assert f.players == 1


def test_steps_are_in_shown_order_and_filed_under_their_turn():
    """The opening prompts show at turn_count 0, before turn 1 starts."""
    f = ts.build({"p": [game("g", 100)]}, list(reversed(three_steps("g"))), [], NOW)
    assert [(s.key, s.group) for s in f.steps] == [
        ("intro", "turn 1"), ("links", "turn 1"), ("patterns", "turn 2")]


def test_tutorial_so_far_and_lesson_rows_are_not_steps():
    """Game 2026-11-12: a run's tutorial_steps also hold each look at Tutorial
    So Far and a lesson's prompts, under the run's format rather than the
    tutorial's name. They are not steps of the tutorial, and a help window left
    open is not where the player left it."""
    rows = [{**step("g", "intro", 1), "tutorial": "link_basics"},
            {**step("g", "links", 2), "tutorial": "link_basics"},
            {**step("g", "tutorial_so_far", 3, closed=False), "tutorial": "tutorial"}]
    f = ts.build({"p": [game("g", 100)]}, rows, [], NOW)
    assert [s.key for s in f.steps] == ["intro", "links"]
    assert by_key(f)["links"].left[ts.STOPPED] == 1
