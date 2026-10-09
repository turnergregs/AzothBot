"""The image reports: the drawing layer (stats_charts) and what each report
decides (stats_cards). Appearance is judged by eye in Discord; these guard the
decisions behind it -- what is compared with what, what is grey, what comes
first -- and that a card renders at all."""
import io

import pytest
from PIL import Image

from azoth_logic import stats_cards as cards
from azoth_logic import stats_charts as sc


def _boss(name, act, wins, fights, unfinished=0):
    return {"boss": name, "act": act, "wins": wins, "losses": fights - wins,
            "unfinished": unfinished}


BOSSES = [
    _boss("Marrox", 1, 14, 19), _boss("Sylvex", 1, 9, 14),
    _boss("Veln", 2, 2, 11, unfinished=1), _boss("Griv", 2, 5, 7), _boss("Neth", 2, 0, 2),
    _boss("Caerul", 4, 1, 1), _boss("Omnex", 4, 0, 0),
    _boss("Umbru", 5, 0, 0),
]


# --- The drawing layer ------------------------------------------------------

def test_a_card_renders_at_double_width():
    """Drawn at 2x so it stays sharp at Discord's ~500px and on a phone."""
    img = Image.open(io.BytesIO(sc.Card("Title", "sub").add(sc.Note("x")).png()))
    assert img.width == sc.WIDTH * sc.SCALE


def test_a_card_is_as_tall_as_its_blocks():
    """Measured before it is drawn, so nothing is ever clipped."""
    short = sc.Card("T").add(sc.Spacer(10)).render()
    tall = sc.Card("T").add(sc.Spacer(10), sc.Spacer(40)).render()
    assert tall.height - short.height == sc.px(40)


@pytest.mark.parametrize("points, text", [(12.4, "+12"), (-33, "−33"), (0.3, "±0")])
def test_a_difference_is_signed_with_a_real_minus(points, text):
    assert sc.signed(points) == text


# --- /stats bosses -----------------------------------------------------------

def test_the_act_rate_is_pooled_not_a_mean_of_boss_rates():
    """Act 2: 7 wins in 20 finished fights is 35%. The mean of the three
    bosses' rates (18%, 71%, 0%) would be 30% -- Neth's two fights weighing
    as much as Veln's eleven."""
    act2 = [r for r in BOSSES if r["act"] == 2]
    assert cards.act_win_rate(act2) == (7, 20)


def test_the_hardest_reliable_boss_comes_first():
    """A grey 0% from two fights must not outrank a real 18% from eleven."""
    act2 = [r for r in BOSSES if r["act"] == 2]
    assert [r["boss"] for r in cards.boss_order(act2)] == ["Veln", "Griv", "Neth"]


def test_unfought_bosses_come_last_but_are_listed():
    act4 = [r for r in BOSSES if r["act"] == 4]
    assert [r["boss"] for r in cards.boss_order(act4)] == ["Caerul", "Omnex"]


def _rows_of(card):
    return [b for b in card.blocks if isinstance(b, sc.BarRow)]


def test_each_boss_is_read_against_its_act():
    veln = next(r for r in _rows_of(cards.bosses_card(BOSSES)) if r.label == "Veln")
    assert veln.reference == pytest.approx(7 / 20)
    assert veln.delta.endswith("−17")   # 18% against the act's 35%


def test_a_boss_alone_in_its_act_has_no_baseline():
    """A baseline of one boss is that boss: every delta would read ±0."""
    rows = _rows_of(cards.bosses_card([_boss("Umbru", 5, 1, 3)]))
    assert rows[0].reference is None and rows[0].delta == ""


def test_few_fights_are_grey():
    rows = {r.label: r for r in _rows_of(cards.bosses_card(BOSSES))}
    assert rows["Neth"].faded and rows["Caerul"].faded
    assert not rows["Veln"].faded


def test_unfinished_fights_are_left_out_of_the_rate():
    card = cards.bosses_card(BOSSES)
    assert next(r for r in _rows_of(card) if r.label == "Veln").count_text == "2/11"


def _notes(card):
    return [b.text for b in card.blocks if isinstance(b, sc.Note)]


def _headers(card):
    return {b.label: b.detail for b in card.blocks if isinstance(b, sc.SectionHeader)}


def test_unfought_bosses_are_named_not_drawn():
    """Knowing they exist is the information; an empty track is not."""
    card = cards.bosses_card(BOSSES)
    assert "Omnex" not in [r.label for r in _rows_of(card)]
    assert "Not yet fought: Omnex" in _notes(card)


def test_an_act_nobody_reached_is_its_header_alone():
    card = cards.bosses_card(BOSSES)
    assert _headers(card)["Act 5"] == "no fights yet"
    assert "Umbru" not in [r.label for r in _rows_of(card)]


def test_the_bosses_card_renders():
    img = Image.open(io.BytesIO(cards.bosses_card(BOSSES).png()))
    assert img.width == sc.WIDTH * sc.SCALE and img.height > img.width / 2


# --- Outlier flags -------------------------------------------------------------

def test_the_wilson_range_is_wide_on_few_fights():
    """At 0/n and n/n a plain rate ± SE collapses to a point; Wilson does not."""
    low, high = cards.wilson_interval(1, 1)
    assert low > 0.1 and high == pytest.approx(1.0)
    low, high = cards.wilson_interval(2, 11)
    assert (round(low, 2), round(high, 2)) == (0.05, 0.48)


ACT = [_boss("Veln", 2, 2, 11), _boss("Moloch", 2, 6, 9), _boss("Xethis", 2, 4, 6),
       _boss("Griv", 2, 5, 7)]


def test_a_boss_clearly_below_the_rest_of_its_act_is_flagged():
    """Veln: plausibly 5-48%, against the other bosses' 15/22 = 68%."""
    assert cards.boss_flag(ACT[0], ACT) == "below"


def test_a_low_rate_within_chance_is_not_flagged():
    """Sylvex 9/14 looks low beside act 1's other bosses, but 14 fights cannot
    rule out their 78%."""
    act1 = [_boss("Sylvex", 1, 9, 14), _boss("Marrox", 1, 14, 19),
            _boss("Oshe", 1, 12, 15), _boss("Vorhn", 1, 13, 16)]
    assert cards.boss_flag(act1[0], act1) is None


def test_a_boss_clearly_above_the_rest_is_flagged():
    act = [_boss("Pushover", 1, 19, 20), _boss("A", 1, 5, 12), _boss("B", 1, 6, 12)]
    assert cards.boss_flag(act[0], act) == "above"


def test_the_baseline_leaves_the_boss_out():
    """Against the whole act, Veln would pull its own baseline toward itself.
    Two bosses at 2/11 and 9/11: the whole act is 50%, which 2/11's range
    (5-48%) clears only barely; the other boss alone is 82%."""
    act = [_boss("Low", 1, 2, 11), _boss("High", 1, 9, 11)]
    assert cards.boss_flag(act[0], act) == "below"


def test_few_fights_are_never_flagged():
    """A dim row saying "not enough fights" and a red one saying "clearly too
    hard" cannot both be true."""
    act = [_boss("Neth", 3, 0, 4), _boss("A", 3, 20, 22)]
    assert cards.boss_flag(act[0], act) is None


def test_a_boss_alone_in_its_act_is_never_flagged():
    assert cards.boss_flag(_boss("Umbru", 5, 0, 9), [_boss("Umbru", 5, 0, 9)]) is None


def test_a_flag_is_carried_in_text_as_well_as_colour():
    veln = next(r for r in _rows_of(cards.bosses_card(ACT)) if r.label == "Veln")
    assert veln.fill == sc.BELOW
    assert veln.marker == "▼" and veln.marker_fill == sc.BELOW


def test_unflagged_bars_recede():
    griv = next(r for r in _rows_of(cards.bosses_card(ACT)) if r.label == "Griv")
    assert griv.fill == sc.NEUTRAL and griv.marker == ""


def test_a_zero_rate_still_draws_a_mark():
    """A flagged 0% was invisible: no bar length, so no red. It is a dot now."""
    img = sc.Card("T").add(sc.BarRow("Veln", 0.0, fill=sc.BELOW)).render()
    x = sc.px(sc.PAD + sc.BarRow.LABEL_W) + sc.px(4)
    y = sc.px(sc.PAD + sc.Card.HEAD) + sc.px(14)
    assert img.getpixel((x, y))[:3] == tuple(int(sc.BELOW[i:i + 2], 16) for i in (1, 3, 5))


# --- Cohorts -------------------------------------------------------------------

COHORT_ROWS = [
    {"boss": "Veln", "act": 2, "cohort": "new", "fights": 5, "wins": 1, "losses": 4, "unfinished": 0},
    {"boss": "Veln", "act": 2, "cohort": "developer", "fights": 6, "wins": 5, "losses": 1, "unfinished": 0},
    {"boss": "Veln", "act": 2, "cohort": "veteran", "fights": 2, "wins": 2, "losses": 0, "unfinished": 0},
    {"boss": "Griv", "act": 2, "cohort": "developer", "fights": 3, "wins": 3, "losses": 0, "unfinished": 0},
    {"boss": "Umbru", "act": 5, "cohort": None, "fights": 0, "wins": 0, "losses": 0, "unfinished": 0},
]


def _by(rows):
    return {r["boss"]: r for r in rows}


def test_new_playtesters_only_counts_their_fights():
    rows, filtered = cards.select_cohort(COHORT_ROWS, "new", "boss", cards.BOSS_COUNTS)
    assert filtered and (_by(rows)["Veln"]["wins"], _by(rows)["Veln"]["losses"]) == (1, 4)


def test_everyone_but_us_adds_the_veterans():
    rows, _ = cards.select_cohort(COHORT_ROWS, "players", "boss", cards.BOSS_COUNTS)
    assert _by(rows)["Veln"]["wins"] == 3


def test_everyone_counts_every_cohort():
    rows, _ = cards.select_cohort(COHORT_ROWS, "all", "boss", cards.BOSS_COUNTS)
    assert _by(rows)["Veln"]["fights"] == 13


def test_a_boss_only_developers_fought_is_unfought_for_new_players():
    """Not missing: Griv exists, the new players just have not fought it."""
    rows, _ = cards.select_cohort(COHORT_ROWS, "new", "boss", cards.BOSS_COUNTS)
    assert _by(rows)["Griv"]["fights"] == 0 and _by(rows)["Griv"]["act"] == 2


def test_a_view_without_cohorts_counts_everyone_and_says_so():
    """Before the cohort migration: label it Everyone, never New playtesters."""
    old = [{k: v for k, v in r.items() if k != "cohort"} for r in COHORT_ROWS[:1]]
    rows, filtered = cards.select_cohort(old, "new", "boss", cards.BOSS_COUNTS)
    assert not filtered and rows[0]["wins"] == 1


def test_the_card_names_whose_fights_it_shows():
    assert cards.bosses_card(BOSSES, "New playtesters").subtitle.startswith("New playtesters")


# --- /stats breakdown ----------------------------------------------------------

def _run(dim, grp, act, runs, cleared=0, cohort="new", **turns):
    row = {"dimension": dim, "grp": grp, "cohort": cohort, "furthest_act": act,
           "runs": runs, "cleared": cleared}
    row.update({c: 0 for c in cards.BREAKDOWN_COUNTS if c not in ("runs", "cleared")})
    row.update(turns)
    return row


HERO_ROWS = [
    _run("hero", "Lumis", 1, 4), _run("hero", "Lumis", 2, 6), _run("hero", "Lumis", 3, 6),
    _run("hero", "Lumis", 4, 1, cleared=1, regular_turns=100, regular_activations=110,
         boss_turns=30, boss_activations=57),
    _run("hero", "Eith", 4, 3, cleared=3), _run("hero", "Eith", 2, 3),
    _run("hero", "Ignis", 3, 2, cohort="developer"),
]


def _select(rows, key="new"):
    merged, _ = cards.select_cohort(rows, key, ("dimension", "grp", "furthest_act"),
                                    cards.BREAKDOWN_COUNTS)
    return merged


def test_groups_sum_their_acts_into_one_bar():
    groups = {g["grp"]: g for g in cards.breakdown_groups(_select(HERO_ROWS), "hero")}
    assert groups["Lumis"]["acts"] == {1: 4, 2: 6, 3: 6, 4: 1}
    assert (groups["Lumis"]["runs"], groups["Lumis"]["cleared"]) == (17, 1)


def test_a_group_nobody_in_the_cohort_played_is_dropped():
    """Unlike an unfought boss: a hero no new player picked is not an answer."""
    grps = [g["grp"] for g in cards.breakdown_groups(_select(HERO_ROWS), "hero")]
    assert "Ignis" not in grps
    grps = [g["grp"] for g in cards.breakdown_groups(_select(HERO_ROWS, "all"), "hero")]
    assert "Ignis" in grps


def test_heroes_come_most_played_first():
    assert [g["grp"] for g in cards.breakdown_groups(_select(HERO_ROWS), "hero")] == ["Lumis", "Eith"]


def test_rituals_are_in_level_order():
    rows = [_run("ritual", "10", 1, 1), _run("ritual", "2", 1, 1), _run("ritual", "0", 1, 1)]
    assert [g["grp"] for g in cards.breakdown_groups(rows, "ritual")] == ["0", "2", "10"]


def test_versions_are_in_release_order_not_text_order():
    """As text, 0.9.9 sorts after 0.9.10; as a release it comes before."""
    rows = [_run("version", "0.9.10", 1, 1), _run("version", "0.9.9", 1, 1),
            _run("version", "0.9.11", 1, 1)]
    assert [g["grp"] for g in cards.breakdown_groups(rows, "version")] == ["0.9.9", "0.9.10", "0.9.11"]


def test_a_group_clearly_behind_the_rest_is_flagged():
    """Lumis 1/17 against Eith's 3/6."""
    groups = cards.breakdown_groups(_select(HERO_ROWS), "hero")
    assert cards.group_flag(groups[0], groups) == "below"


def test_a_group_is_not_judged_against_a_tiny_rest():
    """26 runs at ritual 0 against two at ritual 1 is not a comparison."""
    rows = [_run("ritual", "0", 2, 24), _run("ritual", "0", 4, 2, cleared=2),
            _run("ritual", "1", 2, 2)]
    groups = cards.breakdown_groups(rows, "ritual")
    assert cards.group_flag(groups[0], groups) is None


def _strip(card):
    return [b for b in card.blocks if isinstance(b, sc.ActStripRow)]


def _metrics(card):
    return [b for b in card.blocks if isinstance(b, sc.MetricRow)]


def test_the_hero_card_shows_activations_not_skips_or_links():
    """Skips and links work the same on every hero (Turner's review)."""
    card = cards.breakdown_card(_select(HERO_ROWS), "hero")
    heads = [h[0] for b in card.blocks if isinstance(b, sc.ColumnHeads) for h in b.heads]
    assert "Per regular turn" in heads and "Per boss fight" in heads
    assert "Skips" not in heads and "Links" not in heads


def test_activations_are_divided_from_totals():
    lumis = _metrics(cards.breakdown_card(_select(HERO_ROWS), "hero"))[0]
    assert lumis.values[0][0] == pytest.approx(1.1)
    assert lumis.values[1][0] == pytest.approx(1.9)


def test_ritual_and_version_show_all_three_per_turn_averages_links_against_the_budget():
    rows = [_run("ritual", "0", 2, 6, regular_turns=40, regular_skips=16,
                 regular_activations=44, regular_links=140)]
    row = _metrics(cards.breakdown_card(rows, "ritual"))[0]
    assert [round(v, 1) for v, _ in row.values] == [0.4, 1.1, 3.5]
    assert row.values[2][1] == cards.LINKS_PER_TURN


def test_a_group_with_no_regular_turns_shows_a_dash_not_zero():
    row = _metrics(cards.breakdown_card([_run("ritual", "1", 1, 1)], "ritual"))[0]
    assert row.values[0][0] is None


def test_rituals_are_labelled_and_versions_are_not_repeated_in_the_subtitle():
    assert _strip(cards.breakdown_card([_run("ritual", "3", 1, 1)], "ritual"))[0].label == "Ritual 3"
    assert "version ≥" not in cards.breakdown_card([_run("version", "0.9.11", 1, 1)], "version").subtitle
    assert "version ≥" in cards.breakdown_card([_run("hero", "Lumis", 1, 1)], "hero").subtitle


def test_few_runs_are_dimmed():
    strips = {s.label: s for s in _strip(cards.breakdown_card(_select(HERO_ROWS), "hero"))}
    assert not strips["Lumis"].faded and strips["Eith"].faded is False
    assert _strip(cards.breakdown_card([_run("hero", "Essra", 4, 1)], "hero"))[0].faded


@pytest.mark.parametrize("by", ["hero", "ritual", "version"])
def test_every_breakdown_renders(by):
    rows = [_run(by, "0.9.11" if by == "version" else "0" if by == "ritual" else "Lumis", a, 3,
                 regular_turns=10, regular_links=30) for a in (1, 2, 3, 4, 5)]
    img = Image.open(io.BytesIO(cards.breakdown_card(rows, by, "New playtesters").png()))
    assert img.width == sc.WIDTH * sc.SCALE


def test_an_all_runs_row_gives_the_rates_a_baseline():
    """A ▼ on Lumis means little without the rate it is below."""
    strips = _strip(cards.breakdown_card(_select(HERO_ROWS), "hero"))
    total = strips[-1]
    assert total.label == "All runs" and total.strong
    assert (sum(total.acts.values()), total.cleared) == (23, 4)
    assert total.acts[4] == 4 and total.marker == ""


def test_a_single_group_has_no_all_runs_row():
    """It would repeat the one row above it."""
    strips = _strip(cards.breakdown_card([_run("version", "0.9.11", 2, 3)], "version"))
    assert [s.label for s in strips] == ["0.9.11"]


# --- The legend rule ------------------------------------------------------------

def _legends(card):
    return [b for b in card.blocks if isinstance(b, sc.Legend)]


def test_an_act_legend_lists_only_the_acts_drawn():
    """Nobody reached act 5, so act 5 is not a key to anything (Turner)."""
    card = cards.breakdown_card(_select(HERO_ROWS), "hero")
    assert [label for label, _ in _legends(card)[0].items] == ["Act 1", "Act 2", "Act 3", "Act 4"]


# --- /stats players --------------------------------------------------------------

def _player(name, runs=0, ritual=0, cohort="new", actions=None, **secs):
    row = {"player": name, "cohort": cohort, "runs": runs, "max_ritual": ritual,
           "run_sec": None, "custom_run_sec": None, "codex_sec": None, "art_sec": None,
           "actions": actions or {}}
    row.update(secs)
    return row


PLAYERS = [
    _player("Max", 12, run_sec=27700),
    _player("Ratpunzel", 6, run_sec=6800, custom_run_sec=1400, codex_sec=2200,
            actions={"created:card": 4, "edited:card": 2, "exported:deck": 1}),
    _player("Jay B", 1),                         # not on a tracked build yet
]


def _rows(card, kind):
    return [b for b in card.blocks if isinstance(b, kind)]


def test_the_most_active_player_leads_and_untracked_players_follow():
    rows = _rows(cards.players_card(PLAYERS), sc.TimeRow)
    assert [r.label for r in rows] == ["Max", "Ratpunzel", "Jay B"]


def test_an_untracked_player_is_listed_with_a_dash():
    """Their runs are history; their time starts with a tracked build."""
    jay = _rows(cards.players_card(PLAYERS), sc.TimeRow)[-1]
    assert jay.total_text == "—" and jay.extra == ("1", "R0")


def test_time_bars_share_one_scale():
    """Length is total active time, so it says how much as well as how split."""
    rows = _rows(cards.players_card(PLAYERS), sc.TimeRow)
    assert {r.scale for r in rows} == {27700}


def test_the_time_legend_lists_only_surfaces_someone_used():
    labels = [label for label, _ in _legends(cards.players_card(PLAYERS))[0].items]
    assert labels == ["Runs", "Custom runs", "Codex"]


def test_no_tracked_time_means_no_legend_and_no_time_tiles():
    card = cards.players_card([_player("Jay B", 1), _player("zz", 1)])
    assert not _legends(card)
    tiles = [label for t in _rows(card, sc.StatTiles) for label, _ in t.tiles]
    assert tiles == ["Players", "Runs"]


def test_made_in_the_codex_is_hidden_until_something_is_made():
    card = cards.players_card([_player("Max", 12, run_sec=100)])
    assert "Made in the Codex" not in [b.label for b in card.blocks if isinstance(b, sc.SectionHeader)]


def test_made_in_the_codex_shows_only_the_kinds_that_happened_most_first():
    assert cards.codex_tiles(PLAYERS) == [("New cards", 4), ("Card edits", 2), ("Exports", 1)]


@pytest.mark.parametrize("key, label", [
    ("created:hero", "New heroes"), ("edited:deck", "Deck edits"),
    ("design_saved:deck_tile", "Designs saved"), ("drafted:card", "Shipped edits"),
])
def test_action_labels(key, label):
    assert cards.action_label(key) == label


def test_a_long_roster_is_capped_and_says_how_many_more():
    many = [_player(f"P{i}", 1, run_sec=100 + i) for i in range(cards.MAX_ROSTER + 3)]
    card = cards.players_card(many)
    assert len(_rows(card, sc.TimeRow)) == cards.MAX_ROSTER
    assert "+ 3 more players with less time" in [b.text for b in card.blocks if isinstance(b, sc.Note)]


def test_a_single_kind_of_time_needs_no_legend():
    assert not _legends(cards.players_card([_player("Max", 12, run_sec=27700)]))


def test_run_time_needs_no_tracker():
    """Max's run time is games.elapsed_sec: a bar, no Codex tile."""
    card = cards.players_card([_player("Max", 12, run_sec=27700)])
    assert _rows(card, sc.TimeRow)[0].total_text == "7.7h"
    tiles = [label for t in _rows(card, sc.StatTiles) for label, _ in t.tiles]
    assert "Time in runs" in tiles and "Time in the Codex" not in tiles


def test_the_players_card_renders():
    img = Image.open(io.BytesIO(cards.players_card(PLAYERS, "New playtesters").png()))
    assert img.width == sc.WIDTH * sc.SCALE


def test_before_any_tracked_time_the_roster_has_no_bars():
    """Ten empty tracks and a column of dashes is the empty legend again."""
    card = cards.players_card([_player("Jay B", 1), _player("zz", 1)])
    assert not any(r.bar for r in _rows(card, sc.TimeRow))
    heads = [h[0] for b in _rows(card, sc.ColumnHeads) for h in b.heads]
    assert "Active time" not in heads
    assert "Who is playing" in [b.label for b in _rows(card, sc.SectionHeader)]


# --- /stats player ---------------------------------------------------------------

def _prun(hero, act, ritual=0, cleared=False, when="2026-09-27T10:00:00+00:00"):
    return {"hero": hero, "ritual": ritual, "furthest_act": act, "cleared": cleared,
            "started_at": when}


MAX_RUNS = ([_prun("Lumis", a) for a in (1, 1, 1, 2, 2, 2, 2, 3, 3, 3, 3)]
            + [_prun("Eith", 4, cleared=True, when="2026-09-28T09:00:00+00:00")])


def test_a_hero_row_per_hero_with_its_top_ritual_most_played_first():
    rows = cards.hero_rows(MAX_RUNS + [_prun("Eith", 2, ritual=2)])
    assert [(h, sum(a.values()), won, r) for h, a, won, r in rows] == \
        [("Lumis", 11, 0, 0), ("Eith", 2, 1, 2)]


def test_the_player_card_shows_heroes_as_act_bars_labelled_with_ritual():
    strips = _rows(cards.player_card("Max", MAX_RUNS, None, None), sc.ActStripRow)
    assert [s.label for s in strips] == ["Lumis R0", "Eith R0"]
    assert strips[0].acts == {1: 3, 2: 4, 3: 4}


def test_the_player_tiles_count_runs_and_act_3_wins():
    card = cards.player_card("Max", MAX_RUNS, None, {"best_combo": "65500"})
    tiles = dict(t for row in _rows(card, sc.StatTiles) for t in row.tiles)
    assert (tiles["Runs"], tiles["Beat act 3"], tiles["Best combo"]) == ("12", "1", "65.5K")


def test_time_tiles_only_when_there_is_time():
    card = cards.player_card("Max", MAX_RUNS, {"cohort": "new", "run_sec": 27700}, None)
    labels = [label for row in _rows(card, sc.StatTiles) for label, _ in row.tiles]
    assert "Time in runs" in labels and "Time in the Codex" not in labels


def test_the_subtitle_names_the_cohort_and_last_played():
    card = cards.player_card("Max", MAX_RUNS, {"cohort": "new"}, None)
    assert card.subtitle.startswith("New playtester") and "last played 2026-09-28" in card.subtitle


def test_sections_with_nothing_in_them_are_not_drawn():
    heads = [b.label for b in cards.player_card("Max", MAX_RUNS, None, None).blocks
             if isinstance(b, sc.SectionHeader)]
    assert heads == ["Heroes"]


def test_most_drafted_and_codex_sections_appear_when_there_is_something():
    card = cards.player_card("Rat", MAX_RUNS, {"cohort": "new", "actions": {"created:card": 4}},
                             {"most_drafted": "Echo, Bloom", "most_drafted_count": 3})
    heads = [b.label for b in card.blocks if isinstance(b, sc.SectionHeader)]
    assert heads == ["Heroes", "Most drafted", "Made in the Codex"]
    assert "Echo, Bloom · 3 times each" in [b.text for b in card.blocks if isinstance(b, sc.Note)]


def test_the_player_card_renders():
    img = Image.open(io.BytesIO(cards.player_card("Max", MAX_RUNS, None, None).png()))
    assert img.width == sc.WIDTH * sc.SCALE


# --- /stats draft picks, items, pool ----------------------------------------------

def _offer(dim, bucket, offered, picked, cohort="new"):
    return {"dimension": dim, "bucket": bucket, "cohort": cohort, "offered": offered, "picked": picked}


OFFERS = [
    _offer("type", "card", 900, 300), _offer("type", "aspect", 280, 95), _offer("type", "rite", 56, 17),
    _offer("element", "anima", 280, 110), _offer("element", "blood", 250, 70),
    _offer("element", "sol", 240, 80), _offer("element", "catalyst", 130, 40),
    _offer("valence", "none", 34, 8), _offer("valence", "10", 6, 2), _offer("valence", "2", 190, 70),
    _offer("embellished", "bare", 610, 180), _offer("embellished", "embellished", 290, 120),
    _offer("kind", "upgrade", 130, 60), _offer("kind", "attribute", 110, 40),
    _offer("kind", "enhancement", 90, 30),
]


def _bar_rows(card):
    return [b for b in card.blocks if isinstance(b, sc.BarRow)]


def _section_rows(card, title):
    out, inside = [], False
    for b in card.blocks:
        if isinstance(b, sc.SectionHeader):
            inside = b.label == title
        elif inside and isinstance(b, sc.BarRow):
            out.append(b)
    return out


def test_draft_picks_has_no_bare_vs_embellished_section():
    """Embellished cards are expected to be picked more (Turner's review)."""
    heads = [b.label for b in cards.draft_picks_card(OFFERS).blocks if isinstance(b, sc.SectionHeader)]
    assert heads == ["By type", "By element", "By valence", "By embellishment"]


def test_each_embellishment_kind_is_read_against_bare_cards():
    rows = _section_rows(cards.draft_picks_card(OFFERS), "By embellishment")
    assert [r.label for r in rows] == ["Upgraded", "Attribute", "Enhanced"]
    assert all(r.reference == pytest.approx(180 / 610) for r in rows)
    assert rows[0].marker == "▲"          # 60/130 is clearly above bare 30%


def test_an_element_is_flagged_against_the_rest_of_its_section():
    rows = {r.label: r for r in _section_rows(cards.draft_picks_card(OFFERS), "By element")}
    assert rows["Anima"].marker == "▲" and rows["Blood"].marker == "▼"
    assert rows["Sol"].marker == ""


def _blocks(card, kind):
    return [b for b in card.blocks if isinstance(b, kind)]


def test_valence_is_in_numeric_order_with_none_first_and_gaps_kept():
    """Columns since 2026-10-03: every valence to 10, one never offered empty."""
    groups = _blocks(cards.draft_picks_card(OFFERS), sc.RateColumns)[0].groups
    assert [g["label"] for g in groups] == ["—"] + [str(v) for v in range(1, 11)]
    by = {g["label"]: g for g in groups}
    assert by["5"]["value"] is None and by["10"]["faded"]     # 6 offers


def test_valence_columns_share_one_baseline():
    columns = _blocks(cards.draft_picks_card(OFFERS), sc.RateColumns)[0]
    assert columns.baseline is not None and all("rest" not in g for g in columns.groups)


def test_the_picks_tiles_total_every_offer_once():
    tiles = dict(cards.draft_picks_card(OFFERS).blocks[0].tiles)
    assert (tiles["Offers"], tiles["Picked"], tiles["Pick rate"]) == ("1,236", "412", "33%")


PACK_OFFERS = OFFERS + [
    _offer("type", "pack", 60, 24),
    _offer("pack_type", "card", 30, 14), _offer("pack_type", "aspect", 18, 6),
    _offer("pack_type", "rite", 12, 4),
    _offer("in_pack", "in_pack", 72, 20), _offer("in_pack", "outer", 1236, 412),
]


def test_packs_are_a_type_and_get_a_section_of_their_own():
    card = cards.draft_picks_card(PACK_OFFERS)
    heads = [b.label for b in card.blocks if isinstance(b, sc.SectionHeader)]
    assert heads == ["By type", "Packs", "By element", "By valence", "By embellishment"]
    assert _section_rows(card, "By type")[-1].label == "Packs"
    rows = _section_rows(card, "Packs")
    assert [r.label for r in rows] == ["Atoms", "Aspects", "Rites"]
    assert rows[0].count_text == "14/30"


def test_the_pack_section_says_how_many_opened_packs_gave_a_card():
    """24 opened, 20 cards taken from them: the other 4 were Skipped."""
    detail = next(b.detail for b in cards.draft_picks_card(PACK_OFFERS).blocks
                  if isinstance(b, sc.SectionHeader) and b.label == "Packs")
    assert detail == "40% opened · 83% of opened packs gave a pick"


def test_a_view_from_before_packs_shows_no_pack_section():
    assert "Packs" not in [b.label for b in cards.draft_picks_card(OFFERS).blocks
                           if isinstance(b, sc.SectionHeader)]


def test_cohorts_are_summed_per_bucket():
    rows, _ = cards.select_cohort(OFFERS + [_offer("type", "card", 100, 90, cohort="developer")],
                                  "all", ("dimension", "bucket"), cards.DRAFT_COUNTS)
    card = next(r for r in rows if r["bucket"] == "card")
    assert (card["offered"], card["picked"]) == (1000, 390)


def _item(name, kind, offered, picked, item_id=None):
    return {"item_type": kind, "item_id": item_id or hash(name) % 1000, "item_name": name,
            "offered": offered, "picked": picked}


ITEMS = [_item("Circumvent", "card", 18, 14), _item("Veil", "aspect", 14, 9),
         _item("Pyre", "rite", 11, 6), _item("Echo", "card", 31, 20), _item("Bloom", "card", 19, 11),
         _item("Kindle", "card", 24, 12), _item("Thorn", "card", 14, 1), _item("Ledger", "aspect", 13, 2),
         _item("Hush", "card", 15, 3), _item("Toll", "rite", 8, 2), _item("Waxix", "card", 12, 3),
         _item("Fluke", "card", 1, 1)]


def test_items_are_ranked_within_their_own_type_five_each():
    card = cards.draft_items_card(ITEMS)
    most = [r.label for r in _section_rows(card, "Most picked cards")]
    least = [r.label for r in _section_rows(card, "Least picked cards")]
    assert most == ["Circumvent", "Echo", "Bloom", "Kindle", "Waxix"]
    assert least == ["Thorn", "Hush"]          # never an item twice
    assert [r.label for r in _section_rows(card, "Most picked aspects")] == ["Veil", "Ledger"]
    assert _section_rows(card, "Least picked aspects") == []
    assert [r.label for r in _section_rows(card, "Most picked rites")] == ["Pyre", "Toll"]


def test_each_item_is_read_against_its_own_type():
    card = cards.draft_items_card(ITEMS)
    veil = _section_rows(card, "Most picked aspects")[0]
    echo = _section_rows(card, "Most picked cards")[1]
    assert veil.reference == pytest.approx(11 / 27)
    assert echo.reference == pytest.approx(65 / 134)       # Fluke's offer counts in the rate
    assert veil.tag == "" and echo.tag == ""


def test_an_item_offered_too_rarely_is_not_ranked():
    """Fluke's one offer and one pick would otherwise top the list at 100%."""
    labels = [r.label for r in _bar_rows(cards.draft_items_card(ITEMS))]
    assert "Fluke" not in labels


def test_a_type_with_nothing_ranked_is_not_drawn():
    rows = [r for r in ITEMS if r["item_type"] != "rite"] + [_item("Rare", "rite", 2, 1)]
    headers = [b.label for b in cards.draft_items_card(rows).blocks if isinstance(b, sc.SectionHeader)]
    assert not any("rites" in h for h in headers)


POOL = {"cards": 136, "aspects": 22, "rite_templates": 18,
        "element_counts": {"anima": 37, "blood": 36, "sol": 39, "catalyst": 24},
        "valence_counts": {"none": 24, "2": 26, "9": 3}}


def test_the_pool_uses_the_game_element_colours():
    rows = {r.label: r for r in _section_rows(cards.draft_pool_card(POOL), "Cards by element")}
    assert rows["Anima"].fill == cards.ELEMENT_COLOURS["anima"]
    assert rows["Sol"].value == 1.0


def test_rites_are_counted_beside_the_pool_never_in_it():
    tiles = dict(t for b in cards.draft_pool_card(POOL).blocks if isinstance(b, sc.StatTiles) for t in b.tiles)
    assert tiles["Cards"] == "136" and tiles["Rite templates"] == "18"
    assert tiles["Rite slots / run"].startswith("~")


def test_the_pool_shows_every_valence_so_holes_show():
    hist = _blocks(cards.draft_pool_card(POOL), sc.Histogram)[0]
    assert hist.labels == ["—"] + [str(v) for v in range(1, 11)]
    assert hist.counts == [24, 0, 26, 0, 0, 0, 0, 0, 0, 3, 0]


@pytest.mark.parametrize("build", [lambda: cards.draft_picks_card(OFFERS),
                                   lambda: cards.draft_picks_card(PACK_OFFERS),
                                   lambda: cards.draft_items_card(ITEMS),
                                   lambda: cards.draft_pool_card(POOL)])
def test_every_draft_card_renders(build):
    assert Image.open(io.BytesIO(build().png())).width == sc.WIDTH * sc.SCALE


# --- /stats item -------------------------------------------------------------------

def _split(grp, wins, finished, act_wins, act_finished, cohort="new"):
    """A boss_split_view row: the act's totals include this boss."""
    return {"boss_id": 8, "boss": "Veln", "act": 2, "dimension": "version", "grp": grp,
            "cohort": cohort, "wins": wins, "finished": finished,
            "act_wins": act_wins, "act_finished": act_finished}


VELN = [_split("0.9.10", 3, 19, 33, 77), _split("0.9.11", 9, 21, 36, 81),
        _split("0.9.9", 2, 4, 8, 17)]


def _veln_groups():
    rows, _ = cards.select_cohort(VELN, "new", "grp", cards.ITEM_SPLITS["boss"][2])
    return cards.item_groups(rows, "boss")


def _columns(card):
    return next(b for b in card.blocks if isinstance(b, sc.RateColumns)).groups


def test_the_rest_is_the_kind_without_the_item():
    """The view's totals include the item; the rest must not, or Veln would
    pull its own baseline toward itself."""
    g = {x["group"]: x for x in _veln_groups()}["0.9.10"]
    assert (g["hits"], g["n"], g["rest_hits"], g["rest_n"]) == (3, 19, 30, 58)


def test_cohorts_are_summed_per_group_totals_included():
    rows = VELN + [_split("0.9.10", 5, 5, 20, 30, cohort="developer")]
    merged, _ = cards.select_cohort(rows, "all", "grp", cards.ITEM_SPLITS["boss"][2])
    g = {x["group"]: x for x in cards.item_groups(merged, "boss")}["0.9.10"]
    assert (g["hits"], g["n"], g["rest_hits"], g["rest_n"]) == (8, 24, 45, 83)


def test_versions_run_in_release_order_as_columns():
    columns = _columns(cards.item_card("Veln", "boss", _veln_groups(), "version", act=2))
    assert [c["label"] for c in columns] == ["0.9.9", "0.9.10", "0.9.11"]


def test_each_version_is_read_against_the_rest_that_version():
    columns = {c["label"]: c for c in _columns(cards.item_card("Veln", "boss", _veln_groups(), act=2))}
    assert columns["0.9.10"]["rest"] == pytest.approx(30 / 58)
    assert columns["0.9.11"]["rest"] == pytest.approx(27 / 60)
    assert columns["0.9.10"]["marker"] == "▼"      # 3/19 is clearly under 30/58
    assert columns["0.9.11"]["marker"] == ""       # 9/21 against 27/60 is not
    assert columns["0.9.9"]["faded"] and columns["0.9.9"]["marker"] == ""   # 4 fights


def test_the_columns_wear_the_item_colour_not_the_flag():
    """A flag is the marker: a blood card's red column is not a warning."""
    colour = cards.item_colour("boss", {"act": 2}, 2)
    card = cards.item_card("Veln", "boss", _veln_groups(), act=2, colour=colour)
    chart = next(b for b in card.blocks if isinstance(b, sc.RateColumns))
    assert chart.fill == sc.ACT_COLOURS[1]
    assert "fill" not in chart.groups[1]


def test_a_hero_split_is_rows_and_a_hero_is_not_split_by_hero():
    groups = [{"group": "Lumis", "hits": 6, "n": 20, "rest_hits": 25, "rest_n": 50},
              {"group": "Eith", "hits": 2, "n": 11, "rest_hits": 15, "rest_n": 35}]
    card = cards.item_card("Veln", "boss", groups, "hero", act=2)
    assert [r.label for r in _bar_rows(card)] == ["Lumis", "Eith"]
    assert "hero" not in cards.ITEM_AXES["hero"]


def test_only_the_latest_versions_are_drawn():
    groups = [{"group": f"0.9.{v}", "hits": 1, "n": 5, "rest_hits": 5, "rest_n": 20}
              for v in range(10, 10 + cards.MAX_ITEM_COLUMNS + 2)]
    labels = [c["label"] for c in _columns(cards.item_card("Veln", "boss", groups, act=2))]
    assert len(labels) == cards.MAX_ITEM_COLUMNS and labels[-1] == "0.9.21"


@pytest.mark.parametrize("kind, row, act, expected", [
    ("boss", {}, 4, sc.ACT_COLOURS[3]),
    ("card", {"element": "Blood"}, None, cards.ELEMENT_COLOURS["blood"]),
    ("card", {"element": None}, None, cards.ELEMENT_COLOURS["catalyst"]),
    ("aspect", {"image_data": {"primary_color": [236, 166, 76]}}, None, "#eca64c"),
    ("rite", {"image_data": {"primary_color": "#C8C4B8"}}, None, "#c8c4b8"),
    ("hero", {"color": {"r": 255, "g": 198, "b": 49}}, None, "#ffc631"),
    ("hero", {}, None, sc.ACCENT),
])
def test_an_item_is_drawn_in_the_colour_the_game_gives_it(kind, row, act, expected):
    assert cards.item_colour(kind, row, act) == expected


def test_full_width_blocks_start_below_the_thumb():
    thumb = Image.new("RGBA", (sc.px(100), sc.px(200)), (255, 0, 0, 255))
    card = cards.item_card("Veln", "boss", _veln_groups(), act=2, thumb=thumb)
    tiles = next(b for b in card.blocks if isinstance(b, sc.StatTiles))
    assert tiles.inset_right >= 100
    above_chart = card.head() + sum(b.height for b in card.blocks[:card.blocks.index(
        next(b for b in card.blocks if isinstance(b, sc.SectionHeader)))])
    assert above_chart >= 200


@pytest.mark.parametrize("by", ["version", "hero"])
def test_the_item_card_renders(by):
    thumb = Image.new("RGBA", (sc.px(80), sc.px(140)), (0, 0, 0, 0))
    groups = _veln_groups() if by == "version" else [
        {"group": "Lumis", "hits": 6, "n": 20, "rest_hits": 25, "rest_n": 50}]
    png = cards.item_card("Veln", "boss", groups, by, act=2, thumb=thumb).png()
    assert Image.open(io.BytesIO(png)).width == sc.WIDTH * sc.SCALE


def test_the_column_scale_is_the_next_quarter_above_the_tallest_mark():
    assert sc.RateColumns([{"value": 0.16, "rest": 0.52}]).scale() == 0.75
    assert sc.RateColumns([{"value": 0.2, "rest": 0.3}]).scale() == 0.5
    assert sc.RateColumns([{"value": 1.0, "rest": 0.4}]).scale() == 1.0


# --- /stats leaderboard ------------------------------------------------------------

def _best(player, hero, combo, ritual=0, act=3, when="2026-09-20T10:00:00+00:00", cohort="new"):
    return {"player": player, "hero": hero, "combo": str(combo), "combo_numeric": combo,
            "ritual": ritual, "act": act, "started_at": when, "cohort": cohort}


BOARD = [
    _best("Bram", "Lumis", 2 ** 2048, ritual=1, act=5, cohort="veteran"),
    _best("Ratpunzel", "Lumis", 4_300_000),
    _best("Ratpunzel", "Ignis", 9_000),
    _best("Max", "Eith", 65_500, act=4),
    _best("Max", "Lumis", 40_000),
    _best("Mike", "Lumis", 365),
]


def test_each_player_appears_once_with_their_best_run():
    """Ranking runs let one player hold half the board."""
    rows = cards.leaderboard_rows(BOARD)
    assert [r["player"] for r in rows] == ["Bram", "Ratpunzel", "Max", "Mike"]
    assert rows[2]["hero"] == "Eith"


def test_a_combo_past_float_range_ranks_first():
    """2^2048 overflows a float; compared as Decimal it simply wins."""
    assert cards.leaderboard_rows(BOARD)[0]["player"] == "Bram"


def test_a_hero_board_takes_each_players_best_with_that_hero():
    rows = cards.leaderboard_rows(BOARD, hero="Lumis")
    assert [(r["player"], r["combo_numeric"]) for r in rows][2] == ("Max", 40_000)


def test_ties_go_to_the_earlier_run():
    rows = cards.leaderboard_rows([_best("Late", "Lumis", 100, when="2026-09-21T00:00:00+00:00"),
                                   _best("Early", "Lumis", 100, when="2026-09-20T00:00:00+00:00")])
    assert [r["player"] for r in rows] == ["Early", "Late"]


def _table(card):
    return [b for b in card.blocks if isinstance(b, sc.TableRow)]


def test_the_podium_is_bold_and_set_apart():
    card = cards.leaderboard_card(BOARD)
    rows = _table(card)
    assert rows[2].cells[1][3] == "strong" and rows[3].cells[1][3] == "normal"
    assert any(isinstance(b, sc.Rule) for b in card.blocks)


def test_each_row_shows_hero_and_ritual_and_act():
    first = _table(cards.leaderboard_card(BOARD))[0].cells
    assert first[3][0] == "Lumis R1" and first[4][0] == "5"
    assert first[2][0].startswith("2^")


def test_the_limit_caps_the_board():
    assert len(_table(cards.leaderboard_card(BOARD, limit=2))) == 2


def test_the_leaderboard_renders():
    assert Image.open(io.BytesIO(cards.leaderboard_card(BOARD, "Everyone").png())).width \
        == sc.WIDTH * sc.SCALE


# --- /stats links -------------------------------------------------------------

def _turns(turn_type, links, turns, cohort="new"):
    return {"cohort": cohort, "turn_type": turn_type, "links": links, "turns": turns}


def _links(key, size, links, turn_type="regular", cohort="new"):
    return {"cohort": cohort, "turn_type": turn_type, "link_types": key,
            "link_size": size, "links": links}


LINK_TURNS = [_turns("regular", 0, 4), _turns("regular", 2, 10), _turns("regular", 3, 6),
              _turns("boss", 1, 5), _turns("boss", 4, 3)]
LINK_TYPES = [_links("group", 3, 12), _links("set", 4, 8), _links("group+sequence", 5, 6),
              _links("group+set+sequence", 1, 9), _links("group+set+sequence", 3, 2),
              _links("sequence", 2, 4, turn_type="boss")]


def test_a_link_valid_as_all_three_is_a_one_card_link():
    """Group and Sequence cannot both hold for two cards that count, so a
    longer all-three link is one card plus catalysts or Inert cards."""
    labels = {label: n for label, n, _ in cards.link_types(LINK_TYPES)}
    assert labels[cards.ONE_CARD] == 11 and "All three" not in labels
    assert list(labels)[-1] == cards.ONE_CARD


def test_a_link_with_no_type_ignored_the_requirements():
    labels = [label for label, _, _ in cards.link_types(LINK_TYPES + [_links("", 11, 3)])]
    assert labels[-2:] == [cards.NO_TYPE, cards.ONE_CARD]


def test_one_card_links_are_left_out_of_length():
    card = cards.links_card(LINK_TURNS, LINK_TYPES)
    length = [b.label for b in card.blocks[card.blocks.index(next(
        b for b in card.blocks if isinstance(b, sc.SectionHeader) and b.label == "Length by type")):]
        if isinstance(b, sc.BarRow)]
    assert cards.ONE_CARD not in length and length


def test_a_rare_long_type_does_not_set_the_length_scale():
    """3 links averaging 11 cards once squeezed every other bar to a sliver."""
    rows = [_links("group", 3, 40), _links("set", 4, 20), _links("", 11, 3)]
    bars = {b.label: b for b in cards.links_card(LINK_TURNS, rows).blocks
            if isinstance(b, sc.BarRow) and b.delta}
    assert bars["Set"].value == pytest.approx(1 / 1.25)
    assert bars[cards.NO_TYPE].faded and bars[cards.NO_TYPE].value > 1


def test_a_share_that_rounds_to_nothing_is_not_zero():
    assert cards._share(3, 2774) == "<1%" and cards._share(0, 10) == "0%"


def test_link_types_merge_regular_and_boss_turns():
    merged = {label: n for label, n, _ in cards.link_types(
        LINK_TYPES + [_links("group", 3, 5, turn_type="boss")])}
    assert merged["Group"] == 17


def test_zero_link_turns_count_toward_links_per_turn():
    regular = cards.links_per_turn(LINK_TURNS, "regular")
    assert regular[0] == 4 and sum(regular.values()) == 20


def test_link_cohorts_are_summed_per_bucket():
    rows, filtered = cards.select_cohort(
        LINK_TURNS + [_turns("regular", 2, 7, cohort="developer")],
        "new", ("turn_type", "links"), cards.LINK_TURN_COUNTS)
    two = next(r for r in rows if r["turn_type"] == "regular" and r["links"] == 2)
    assert filtered and two["turns"] == 10


def test_the_links_card_renders():
    assert Image.open(io.BytesIO(cards.links_card(LINK_TURNS, LINK_TYPES, "New playtesters").png())).width \
        == sc.WIDTH * sc.SCALE


def test_every_link_count_is_its_own_column_gaps_kept():
    """Boss turns were bucketed in fives as rows; as columns they need not be."""
    turns = [_turns("boss", 0, 2), _turns("boss", 7, 5), _turns("boss", 22, 1)]
    hist = [b for b in cards.links_card(turns, LINK_TYPES).blocks if isinstance(b, sc.Histogram)]
    assert len(hist) == 1 and len(hist[0].counts) == 23
    assert hist[0].counts[7] == 5 and hist[0].counts[8] == 0
    assert hist[0].mean == pytest.approx((7 * 5 + 22) / 8)


def test_a_long_histogram_scales_to_its_tallest_column_and_renders():
    hist = sc.Histogram([1] * 30 + [5])
    assert hist.scale() >= 5 / 35
    sc.Card("t").add(hist).png()


# --- Surveys ------------------------------------------------------------------

def _survey(qid, outcome="answered", answer=None, moment="loss", run=7, comment=False):
    return {"question_id": qid, "moment": moment, "outcome": outcome, "answer": answer,
            "run_number": run, "has_comment": comment}


def test_a_tally_counts_each_response_and_its_comments():
    tally = cards.survey_tally([_survey("loss", answer="unfair", comment=True),
                                _survey("loss", answer="unfair"),
                                _survey("loss", "skipped")])
    unfair = next(r for r in tally if r["answer"] == "unfair")
    assert unfair["responses"] == 2 and unfair["comments"] == 1
    assert sum(r["responses"] for r in tally) == 3


def test_only_runs_1_and_3_keep_their_run_number():
    tally = cards.survey_tally([_survey("understood", answer="yes", run=1),
                                _survey("understood", answer="yes", run=3),
                                _survey("fun", answer="4", run=12)])
    assert {r["run_slot"] for r in tally} == {1, 3, None}


def test_understood_is_split_by_run_so_the_two_can_be_compared():
    tally = cards.survey_tally([_survey("understood", answer="no", run=1),
                                _survey("understood", answer="yes", run=3)])
    tags = [b.tag for b in cards.surveys_card(tally).blocks if isinstance(b, sc.BarRow)]
    assert tags.count("run 1") == 3 and tags.count("run 3") == 3


def test_understood_asked_again_after_run_3_gets_its_own_bars():
    tally = cards.survey_tally([_survey("understood", answer="no", run=1),
                                _survey("understood", answer="mostly", run=5)])
    tags = [b.tag for b in cards.surveys_card(tally).blocks if isinstance(b, sc.BarRow)]
    assert tags.count("run 1") == 3 and tags.count("later") == 3


def test_a_score_question_shows_every_score_even_unpicked():
    tally = cards.survey_tally([_survey("fun", answer="4")])
    labels = [b.label for b in cards.surveys_card(tally).blocks if isinstance(b, sc.BarRow)]
    assert labels == ["1", "2", "3", "4", "5"]


def test_a_question_with_few_answers_is_grey():
    tally = cards.survey_tally([_survey("loss", answer="unfair")] * (cards.MIN_SURVEY_ANSWERS - 1))
    assert all(b.faded for b in cards.surveys_card(tally).blocks if isinstance(b, sc.BarRow))


def test_survey_cohorts_are_summed_per_answer():
    rows = [dict(question_id="loss", moment="loss", run_slot=None, outcome="answered",
                 answer="unfair", responses=n, comments=0, cohort=c)
            for n, c in ((2, "new"), (3, "veteran"), (5, "developer"))]
    merged, filtered = cards.select_cohort(rows, "players", cards.SURVEY_MERGE, cards.SURVEY_COUNTS)
    assert filtered and merged[0]["responses"] == 5


def test_the_answers_card_is_drawn_only_when_something_was_shown():
    assert cards.survey_answers_card([]) is None
    card = cards.survey_answers_card(cards.survey_tally([_survey("difficulty", "ignored")]))
    assert card is not None and card.png()


def test_the_surveys_card_renders():
    tally = cards.survey_tally([_survey("fight_fun", answer="5", moment="boss_win"),
                                _survey("loss", answer="my_fault", comment=True),
                                _survey("difficulty", "skipped", moment="victory")])
    assert Image.open(io.BytesIO(cards.surveys_card(tally, "Everyone but us").png())).width \
        == sc.WIDTH * sc.SCALE


def test_a_short_bar_a_little_wider_than_round_draws():
    """PIL raised for a filled bar one to two pixels wider than its height,
    which a 1/17 share's fractional end landed in (2026-10-04)."""
    card = sc.Card("t")
    for n in range(1, 40):
        card.add(sc.BarRow("x", 1 / n, label_w=110))
    card.png()


def _player_survey(qid, answer="yes", comment=None, day=1, outcome="answered"):
    return {"question_id": qid, "moment": "loss", "outcome": outcome, "answer": answer,
            "run_number": 9, "has_comment": bool(comment), "comment": comment,
            "created_at": f"2026-10-0{day}T12:00:00+00:00"}


def test_a_player_never_shown_a_survey_has_no_surveys_section():
    assert cards.player_survey_blocks([]) == []
    heads = [b.label for b in cards.player_card("Max", MAX_RUNS, None, None, []).blocks
             if isinstance(b, sc.SectionHeader)]
    assert "Surveys" not in heads


def test_a_players_newest_comments_come_first_and_are_capped():
    rows = [_player_survey("loss", "unfair", comment=f"comment {d}", day=d) for d in range(1, 6)]
    texts = [b.cells[0][0] for b in cards.player_survey_blocks(rows)
             if isinstance(b, sc.TableRow) and b.cells[0][0].startswith("“")]
    assert texts == ["“comment 5”", "“comment 4”", "“comment 3”"]


def test_a_long_comment_wraps_inside_the_card_and_is_shown_whole():
    lines = cards._wrap("word " * 200, sc.WIDTH - 2 * sc.PAD, cards.COMMENT_SIZE)
    f = sc.font(cards.COMMENT_SIZE)
    assert " ".join(lines).split() == ["word"] * 200 and not lines[-1].endswith("…")
    assert all(f.getlength(line) <= sc.px(sc.WIDTH - 2 * sc.PAD) for line in lines)


def test_the_player_card_shows_every_word_of_a_long_comment():
    comment = " ".join(f"w{i}" for i in range(120))
    rows = [_player_survey("loss", "unfair", comment=comment)]
    joined = " ".join(b.cells[0][0] for b in cards.player_survey_blocks(rows)
                      if isinstance(b, sc.TableRow))
    quoted = joined[joined.index("“") + 1:joined.index("”")]
    assert quoted.split() == comment.split()


def test_the_player_card_renders_with_surveys():
    rows = [_player_survey("understood", "mostly", comment="x " * 80),
            _player_survey("difficulty", outcome="skipped", answer=None)]
    cards.player_card("Max", MAX_RUNS, None, None, rows).png()


# --- /stats sessions ----------------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402

NOW = datetime(2026, 10, 9, 18, tzinfo=timezone.utc)


def _launch(player, version, hours_ago, new_install=False):
    return {"player_uuid": player, "version": version, "new_install": new_install,
            "started_at": (NOW - timedelta(hours=hours_ago)).isoformat()}


def test_a_player_counts_under_the_version_of_their_new_install_launch():
    """Their later launches on newer builds do not move them."""
    rows = [_launch("a", "0.10.5", 60, new_install=True), _launch("a", "0.10.7", 5)]
    [group] = cards.session_groups(rows, {}, NOW)
    assert (group["version"], group["players"], group["again"]) == ("0.10.5", 1, 1)


def test_any_second_launch_counts_however_soon():
    """Turner: a return on the same day is a return."""
    rows = [_launch("a", "0.10.6", 50, new_install=True), _launch("a", "0.10.6", 49.9),
            _launch("b", "0.10.6", 48, new_install=True)]
    [group] = cards.session_groups(rows, {}, NOW)
    assert (group["players"], group["again"]) == (2, 1)


def test_developers_and_veterans_are_left_out_and_a_missing_cohort_is_new():
    rows = [_launch(p, "0.10.6", 50, new_install=True) for p in ("dev", "vet", "new", "unknown")]
    cohorts = {"dev": "developer", "vet": "veteran", "new": "new"}
    [group] = cards.session_groups(rows, cohorts, NOW)
    assert group["players"] == 2


def test_a_player_with_no_new_install_launch_is_not_new():
    """Launch rows began at 0.10.5: an earlier player's first recorded launch
    is not their first launch."""
    assert cards.session_groups([_launch("a", "0.10.5", 50), _launch("a", "0.10.6", 10)],
                                {}, NOW) == []


def test_versions_sort_by_number_and_open_means_a_player_under_a_day_old():
    rows = [_launch("a", "0.10.10", 2, new_install=True),
            _launch("b", "0.10.9", 30, new_install=True),
            _launch("c", "0.10.9", 23, new_install=True)]
    groups = cards.session_groups(rows, {}, NOW)
    assert [(g["version"], g["open"]) for g in groups] == [("0.10.9", True), ("0.10.10", True)]
    rows[2]["started_at"] = (NOW - timedelta(hours=25)).isoformat()
    assert [g["open"] for g in cards.session_groups(rows, {}, NOW)] == [False, True]


def test_the_sessions_card_shows_the_newest_versions_and_its_line_is_theirs():
    groups = [{"version": f"0.10.{i}", "players": 2, "again": i % 2, "open": False}
              for i in range(cards.MAX_SESSION_VERSIONS + 3)]
    card = cards.sessions_card(groups)
    chart = next(b for b in card.blocks if isinstance(b, sc.RateColumns))
    shown = groups[-cards.MAX_SESSION_VERSIONS:]
    assert [g["label"] for g in chart.groups] == [g["version"] for g in shown]
    assert chart.baseline == sum(g["again"] for g in shown) / sum(g["players"] for g in shown)
    assert chart.percent
    card.png()
