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


def test_unfinished_fights_are_left_out_of_the_rate_and_said_so():
    card = cards.bosses_card(BOSSES)
    assert next(r for r in _rows_of(card) if r.label == "Veln").count_text == "2/11"
    assert "1 unfinished not counted" in cards.bosses_footer(BOSSES)


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
