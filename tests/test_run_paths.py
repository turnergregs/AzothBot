"""New player paths (/stats paths, and the daily report's second image).

The rules behind each node: what the tutorial row says, when a run counts as
Improved, when a path ends, and who counts as a new arrival. Appearance is
judged by eye in Discord; the card is only checked to render.
"""
import io
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

import supabase_helpers as h
from azoth_commands import stats
from azoth_logic import run_paths as rp
from azoth_logic import stats_cards
from azoth_logic import stats_charts as sc

NOW = datetime(2026, 10, 8, 20, 0, tzinfo=timezone.utc)


def run(hours_ago, *, fmt="standard", hero=1, ritual=0, act=1, result="death", version="0.10.3"):
    return {"player_uuid": "p", "started_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
            "starting_hero": hero, "ritual": ritual, "format": fmt, "act_reached": act,
            "result": result, "version": version}


def kinds(runs):
    return [k for k, _, _ in rp.steps(runs, NOW)]


# --- What each step says ----------------------------------------------------

def test_acts_beaten_is_the_act_before_the_one_a_run_ended_in():
    assert rp.acts_beaten({"act_reached": 3, "result": "death"}) == 2
    assert rp.acts_beaten({"act_reached": 4, "result": "victory"}) == rp.WON
    assert rp.acts_beaten({"act_reached": 4, "result": "no_boss_key"}) == rp.WON


def test_a_run_closed_before_its_first_save_beat_nothing():
    """act_reached is NULL until the first save: that run is in act 1."""
    assert rp.acts_beaten({"act_reached": None, "result": None}) == 0


def test_the_tutorial_row_is_the_best_opening_tutorial():
    path = rp.steps([run(100, fmt="tutorial", act=1, result="restart"),
                     run(99, fmt="tutorial", act=3), run(98)], NOW)
    assert path[0][:2] == (rp.TUTORIAL, "Act 2")


def test_a_tutorial_that_beat_nothing_is_died():
    assert rp.steps([run(100, fmt="tutorial", act=1)], NOW)[0][1] == "Died"


def test_a_player_who_started_with_a_regular_run_has_no_tutorial():
    assert kinds([run(100, act=2)]) == [rp.NO_TUTORIAL, rp.IMPROVED]


def test_a_run_improves_only_on_its_own_hero_and_ritual():
    path = kinds([run(100, fmt="tutorial", act=3),
                  run(99, hero=1, act=3),             # first Lumis R0 run: beat 2 > 0
                  run(98, hero=1, act=2),             # worse than Lumis's best
                  run(97, hero=2, act=2),             # first run on hero 2: beat 1
                  run(96, hero=1, ritual=1, act=2),   # a new ritual is a new line
                  run(95, hero=1, act=4, result="victory")])
    assert path == [rp.TUTORIAL, rp.IMPROVED, rp.LOST, rp.IMPROVED, rp.IMPROVED, rp.IMPROVED]


def test_a_first_run_on_a_hero_must_beat_act_1_to_improve():
    """Otherwise every first try on a new hero would read as Improved."""
    assert kinds([run(100, fmt="tutorial"), run(99, hero=5, act=1)]) == [rp.TUTORIAL, rp.LOST]


def test_the_tutorial_sets_no_best_for_the_hero_it_plays():
    """A tutorial that beat act 2, then a regular run that beat act 1: still
    an improvement, since the tutorial is its own line."""
    assert kinds([run(100, fmt="tutorial", act=3), run(99, act=2)])[1] == rp.IMPROVED


def test_a_tutorial_replayed_later_is_compared_with_the_first():
    path = kinds([run(100, fmt="tutorial", act=2), run(99, act=2),
                  run(98, fmt="tutorial", act=2), run(97, fmt="tutorial", act=3)])
    assert path == [rp.TUTORIAL, rp.IMPROVED, rp.LOST, rp.IMPROVED]


def test_a_restart_that_got_no_further_is_lost():
    assert kinds([run(100, fmt="tutorial"), run(99, act=2), run(98, act=2, result="restart")])[-1] == rp.LOST


def test_a_resultless_run_from_the_last_two_hours_is_left_out():
    """It may still be going, and would read as Lost before it ends."""
    assert kinds([run(100, fmt="tutorial"), run(1, act=None, result=None)]) == [rp.TUTORIAL]


def test_an_older_resultless_run_was_abandoned_and_counts():
    """REGRESSION (2026-10-08): the prototype hid every resultless last run by
    a player active in the last 3 days, which hid four post-tutorial runs 23
    to 44 hours old."""
    assert kinds([run(100, fmt="tutorial"), run(24, act=None, result=None)]) == [rp.TUTORIAL, rp.LOST]


# --- Where a path ends ------------------------------------------------------

def _graph(players, runs_shown=5):
    return rp.build({f"p{i}": runs for i, runs in enumerate(players)}, NOW, runs_shown)


def _ends(graph):
    return {n.kind: n.players for row in graph.rows for n in row
            if n.kind in (rp.STOPPED, rp.PLAYED_ON)}


def test_a_player_idle_for_three_days_stopped():
    assert _ends(_graph([[run(80, fmt="tutorial")]])) == {rp.STOPPED: 1}


def test_a_player_who_ran_in_the_last_three_days_has_no_end_yet():
    """Their next run, or three idle days, shows up in a later report."""
    graph = _graph([[run(30, fmt="tutorial")]])
    assert _ends(graph) == {} and graph.links == {}


def test_a_path_longer_than_the_rows_shown_played_on():
    long = [run(200, fmt="tutorial")] + [run(199 - i) for i in range(7)]
    graph = _graph([long], runs_shown=3)
    assert len(graph.rows) == 5 and _ends(graph) == {rp.PLAYED_ON: 1}


def test_every_bar_holds_what_flows_into_it():
    graph = _graph([[run(100, fmt="tutorial"), run(99, act=2), run(98)],
                    [run(100, fmt="tutorial", act=2), run(99, act=3)],
                    [run(90, fmt="tutorial")]])
    for row in graph.rows[1:]:
        for n in row:
            assert n.players == sum(c for (a, b), c in graph.links.items() if b is n)
    assert graph.players == 3


def test_the_tutorial_row_lists_the_furthest_first():
    graph = _graph([[run(90, fmt="tutorial", act=1)], [run(90, fmt="tutorial", act=3)],
                    [run(90, act=2)]])
    assert [n.label for n in graph.rows[0]] == ["Act 2", "Died", "No tutorial"]


# --- Who arrived ------------------------------------------------------------

def test_only_new_players_who_arrived_in_the_window_count():
    games = [dict(run(50), player_uuid=p) for p in ("new", "earlier", "dev", "vet", "unknown")]
    arrived = rp.new_arrivals(games, earlier={"earlier"},
                              cohorts={"new": "new", "dev": "developer", "vet": "veteran"})
    assert set(arrived) == {"new", "unknown"}, "a player missing from the cohort view is new"


def test_runs_below_the_cutoff_are_left_out():
    arrived = rp.new_arrivals([run(50, version="0.9.9"), run(40, version="0.10.0")], set(), {})
    assert len(arrived["p"]) == 1


def test_a_players_runs_are_put_in_start_order():
    arrived = rp.new_arrivals([run(10, act=3), run(50, act=1)], set(), {})
    assert [g["act_reached"] for g in arrived["p"]] == [1, 3]


@pytest.mark.parametrize("text", ["2026-10-08T12:00:00.12+00:00", "2026-10-08T12:00:00Z",
                                  "2026-10-08T12:00:00.123456+00:00", "2026-10-08T12:00:00"])
def test_postgrest_timestamps_parse(text):
    """PostgREST trims the fraction's trailing zeros, which Python 3.10's
    fromisoformat refuses."""
    assert rp.parse_time(text) == datetime(2026, 10, 8, 12, tzinfo=timezone.utc)


# --- The fetch --------------------------------------------------------------

def test_the_fetch_reads_only_the_window(monkeypatch, fake_supabase):
    """A window on started_at, not all 7,000 solo runs; and a player with an
    earlier run arrived earlier, whatever the window shows of them."""
    since = NOW - timedelta(days=14)
    fs = fake_supabase({
        "games": [dict(run(24 * 20), player_uuid="old", game_type="solo"),
                  dict(run(50), player_uuid="old", game_type="solo"),
                  dict(run(50, fmt="tutorial"), player_uuid="fresh", game_type="solo")],
        "player_cohort_view": [{"player_uuid": "old", "cohort": "new"},
                               {"player_uuid": "fresh", "cohort": "new"}],
    })
    monkeypatch.setattr(h, "supabase", fs)
    paths, got_since = stats.new_player_paths(NOW, 14, 5)
    assert got_since == since
    assert ("gte", "started_at", since.isoformat()) in fs.log["games"]["filters"]
    assert paths.players == 1


# --- The card ---------------------------------------------------------------

def test_the_card_renders():
    graph = _graph([[run(100, fmt="tutorial"), run(99, act=2), run(98)],
                    [run(100, fmt="tutorial", act=2)], [run(90, act=3)]])
    png = stats_cards.paths_card(graph, NOW - timedelta(days=14), NOW, 5).png()
    assert Image.open(io.BytesIO(png)).width == sc.WIDTH * sc.SCALE


def test_the_header_names_the_last_day_inside_the_window():
    """The daily report's window ends at the midnight its day ends at: the
    report for Oct 7 must not say Oct 8."""
    until = datetime(2026, 10, 8, tzinfo=timezone.utc)
    card = stats_cards.paths_card(_graph([[run(100, fmt="tutorial")]]), until - timedelta(days=14), until, 5)
    assert "Sep 24 – Oct 7" in card.subtitle


def test_the_legend_lists_only_what_is_drawn():
    """House rule (Turner, 2026-09-28): a legend is not a list of what might be."""
    card = stats_cards.paths_card(_graph([[run(100, fmt="tutorial"), run(99, act=2)]]),
                                  NOW - timedelta(days=14), NOW, 5)
    legend = next(b for b in card.blocks if isinstance(b, sc.Legend))
    assert [label for label, _ in legend.items] == ["Beat their best on that hero", "Stopped (3 days idle)"]


def test_an_image_block_is_drawn_through_draw_on():
    """ImageBlocks composite onto the image; plain draw is refused rather than
    silently drawing nothing."""
    with pytest.raises(TypeError):
        sc.Flow([[sc.FlowNode(1, "#ffffff")]], {}, ["A"]).draw(None, 0)
