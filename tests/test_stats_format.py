"""`azoth_logic/stats_format.py` — `/stats` as embeds instead of raw JSON.

Every subcommand used to reply with `json.dumps(records, indent=2)` in a code
fence. That was complete and nearly unreadable, and it actively misled on two
points that these tests pin:

  * `avg_combo_log10` is an ORDER OF MAGNITUDE. Printed raw as `4.78` next to
    `max_combo: 652298` it reads as an average combo of about five.
  * The dataset is tiny. A footer stating what the numbers rest on is not
    decoration — `docs/ANALYTICS.md` opens by saying not to quote these as fact.
"""

import pytest

from azoth_logic import stats_format as sf


ROWS = [
    {"player": "Turner", "game_count": 2, "hours_played": 2.1, "highest_combo": 652298},
    {"player": "Bram", "game_count": 1, "hours_played": 0.58, "highest_combo": 32},
]


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------

def test_a_log_combo_is_shown_as_a_power():
    """The whole reason this module exists."""
    assert sf.value("avg_combo_log10", 4.78) == "10^4.8"


def test_a_big_combo_is_compacted():
    """Combos reach 10^30. Nobody counts digits."""
    assert sf.value("max_combo", 652298) == "652.3K"
    assert sf.value("max_combo", 53_300_000_000_000) == "53.3T"


def test_a_combo_past_float_range_does_not_crash_the_table():
    """2026-09-26: /stats active_players failed with "int too large to convert
    to float". `highest_combo` is `numeric`, arrives as a JSON number, and a
    combo past ~1.8e308 is a Python int that float() refuses with
    OverflowError -- which the old except clause did not catch."""
    assert sf.value("highest_combo", 10 ** 400) == "2^1328.8"
    assert sf.value("max_combo", str(10 ** 400)) == "2^1328.8"


def test_a_combo_past_trillions_is_shown_as_a_power_of_two():
    """`_compact` has no suffix past T; 10^30 used to print as 19 digits of T.
    Powers of two because that is how the game states combos."""
    assert sf.value("combo", 2 * 10 ** 30) == "2^100.7"
    assert sf.value("combo", 2 ** 62) == "2^62"


def test_a_small_combo_is_left_alone():
    assert sf.value("max_combo", 32) == "32"


def test_a_combo_that_is_not_a_number_survives():
    """`combo` is a text column holding a BigNum; one malformed row must not
    take the whole table down."""
    assert sf.value("combo", "not-a-number") == "not-a-number"


@pytest.mark.parametrize("hours, expected", [(0.21, "13m"), (0.58, "35m"), (2.1, "2.1h")])
def test_playtime_reads_as_time(hours, expected):
    assert sf.value("hours_played", hours) == expected


def test_a_timestamp_is_a_date():
    assert sf.value("last_played_at", "2026-08-27T16:38:39.742377+00:00") == "2026-08-27"


@pytest.mark.parametrize("empty", [None, ""])
def test_missing_values_are_a_dash_not_none(empty):
    """`None` in a table reads as a value called None."""
    assert sf.value("most_drafted", empty) == "—"


def test_trailing_zeros_are_trimmed():
    assert sf.value("avg_act", 3.0) == "3"
    assert sf.value("avg_turns", 10.5) == "10.5"


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------

def test_the_footer_counts_the_games_behind_the_numbers():
    assert "3 games" in sf.footer(ROWS)


def test_one_game_is_singular():
    assert "1 game" in sf.footer([ROWS[1]]) and "1 games" not in sf.footer([ROWS[1]])


def test_the_cutoff_is_stated_by_default():
    assert f"version >= {sf.CUTOFF_VERSION}" in sf.footer(ROWS)


def test_the_cutoff_can_be_denied():
    """`version_info_view` is the one view with no cutoff — comparing versions
    is its whole job. Claiming the cutoff on a table visibly showing 0.7.0 rows
    is worse than claiming nothing."""
    text = sf.footer(ROWS, cutoff=False)
    assert sf.CUTOFF_VERSION not in text
    assert "all versions" in text


def test_a_missing_count_column_does_not_break_the_footer():
    assert sf.footer([{"deck_name": "Total Draft Pool"}])


def test_a_non_numeric_count_is_skipped_rather_than_raising():
    assert sf.footer([{"game_count": "lots"}, {"game_count": 2}]).endswith("2 games")


# ---------------------------------------------------------------------------
# Combo and ritual values (value(), which formats every table cell)
# ---------------------------------------------------------------------------

def test_the_best_combo_is_the_full_number_not_a_summary():
    """It is one run's actual score. `max_combo` was shown beside
    `avg_combo_log10`, which put two representations of one number side by side;
    and log space has nothing to fix about a MAXIMUM."""
    assert sf.value("best_combo", "652298") == "652,298"


def test_a_thirty_digit_combo_is_grouped_not_compacted():
    """Combos reach 10^30. Grouping is what makes that readable; compacting it
    to `2.6N` would throw away the score the player actually got."""
    out = sf.value("best_combo", "2596148429267413814265248164610048")
    assert out.startswith("2,596,148") and out.endswith("610,048")


def test_the_ritual_value_is_bare():
    """The field is named "Highest Ritual", so the value does not repeat it."""
    assert sf.value("max_ritual", 10) == "10"


# ---------------------------------------------------------------------------
# Turn scoreboard
# ---------------------------------------------------------------------------
# `turn_scoreboard_view` returns one row per (act, axis) plus an `act IS NULL`
# rollup row per axis. See db/migrations/2026-08-31_turn_scoreboard.sql.


def _sb(act, axis, turns, count, threshold, hit_rate, won_rate):
    return {"act": act, "axis": axis, "turns_sampled": turns,
            "avg_count": count, "avg_threshold": threshold,
            "hit_rate": hit_rate, "won_rate": won_rate}


SCOREBOARD = [
    _sb(1, "precision", 22, 1.8, 0, 61.5, 54.2),
    _sb(1, "overdraw", 22, 12.4, 10, 38.1, 31.0),
    _sb(1, "overload", 22, 9.1, 18, 4.5, 14.8),
    _sb(3, "precision", 9, 0.6, 0, 22.2, 18.0),
    _sb(3, "overdraw", 9, 15.0, 10, 66.7, 55.0),
    _sb(3, "overload", 9, 20.2, 18, 55.6, 27.0),
    _sb(None, "precision", 31, 1.4, 0, 51.6, 43.0),
    _sb(None, "overdraw", 31, 13.2, 10, 46.1, 38.0),
    _sb(None, "overload", 31, 12.3, 18, 19.4, 19.0),
]


def test_the_scoreboard_shows_every_axis_per_act():
    """The question the view exists for: is a threshold met more in act 3 than
    act 1. Both acts and all three axes have to be on one table to see it."""
    out = sf.scoreboard_hits(SCOREBOARD)
    assert "Prec" in out and "Draw" in out and "Load" in out
    assert "62%" in out and "67%" in out


def test_the_scoreboard_rollup_row_is_labelled_all_and_comes_last():
    """`act IS NULL` is the view's GROUPING SETS rollup, not a missing act. It
    belongs at the bottom of the column it summarises, as links_table does."""
    lines = [l for l in sf.scoreboard_hits(SCOREBOARD).splitlines() if l.strip()]
    table = [l for l in lines if not l.startswith("`") and not l.startswith("*")]
    assert table[-1].startswith("All")
    assert "52%" in table[-1]


def test_the_scoreboard_overall_is_not_a_mean_of_the_act_rows():
    """Acts have different turn counts, so averaging their rates would be a mean
    of means. The rollup comes from the view; 51.6 is not the mean of 61.5 and
    22.2 (41.9), which is what a client-side average would have produced."""
    assert "52%" in sf.scoreboard_hits(SCOREBOARD)
    assert "42%" not in sf.scoreboard_hits(SCOREBOARD)


def test_the_scoreboard_carries_its_denominator():
    """"62% in act 3" can rest on four turns. Every other table in this module
    carries its counts; this one does it in the caption, for width."""
    out = sf.scoreboard_hits(SCOREBOARD)
    assert "1: 22" in out and "3: 9" in out and "all: 31" in out


def test_the_scoreboard_sample_is_not_a_sum_of_the_column():
    """Every scored turn produces one row PER AXIS and one more in the rollup,
    so summing `turns_sampled` counts each turn six times. 31, not 186."""
    assert sf.scoreboard_sample(SCOREBOARD) == 31


def test_the_scoreboard_sample_is_zero_without_a_rollup_row():
    assert sf.scoreboard_sample([_sb(1, "precision", 5, 1.0, 0, 20.0, 20.0)]) == 0


def test_the_thresholds_are_shown_as_measured_not_as_defaults():
    """Content can raise a threshold mid-run, which is why the column is stored
    per turn at all. A raised one has to be visible or the counts above it
    cannot be read."""
    raised = [dict(r) for r in SCOREBOARD]
    for row in raised:
        if row["axis"] == "overdraw":
            row["avg_threshold"] = 13.5
    assert "Draw 13.5" in sf.scoreboard_counts(raised)


def test_precision_has_no_threshold_in_the_caption():
    """Its threshold is structurally zero — any unspent node pays — so listing
    it reads as a real number the player could miss."""
    caption = sf.scoreboard_counts(SCOREBOARD).splitlines()[-1]
    assert "Prec" not in caption
    assert "Draw 10" in caption and "Load 18" in caption


def test_the_paid_shares_are_separate_from_the_hit_rates():
    """An axis can clear its threshold constantly and never pay, because only
    the winner pays. That reads as a healthy axis in the hits table alone."""
    hits = sf.scoreboard_hits(SCOREBOARD)
    paid = sf.scoreboard_paid(SCOREBOARD)
    assert "56%" in hits          # overload hit rate, act 3
    assert "27%" in paid          # ...but it only paid on 27%


def test_a_missing_axis_row_is_a_dash_not_a_zero():
    """A zero would say the axis never scored; the truth is it was not returned."""
    out = sf.scoreboard_hits([_sb(1, "precision", 5, 1.0, 0, 20.0, 20.0)])
    assert "—" in out


@pytest.mark.parametrize("render",
                         ["scoreboard_hits", "scoreboard_counts", "scoreboard_paid"])
def test_no_scoreboard_data_says_so(render):
    assert getattr(sf, render)([]) == "*no scoreboard data yet*"


@pytest.mark.parametrize("render",
                         ["scoreboard_hits", "scoreboard_counts", "scoreboard_paid"])
def test_the_scoreboard_tables_fit_a_phone(render):
    """Same measurement as test_the_player_tables_fit_a_phone. Only the fenced
    table has to fit — the captions are prose and wrap harmlessly."""
    out = getattr(sf, render)(SCOREBOARD)
    table = out.split("```")[1]
    widest = max(len(l) for l in table.splitlines() if l.strip())
    assert widest <= sf.MOBILE_TABLE_WIDTH, f"{widest} chars wraps on a phone"


# ---------------------------------------------------------------------------
# The draft pool
# ---------------------------------------------------------------------------
# The incident: `/stats draft_pool` reported a valence distribution running 1v
# to 6v that summed to 108 of the pool's 136 cards, and said nothing about the
# other 28. `draft_deck_view` had a COLUMN PER VALENCE and only six of them --
# 2026-08-26_rebuild_analytics_views dropped "7v".."10v" as "permanently zero,
# valence is 1-6" -- so Circumvent (7), Ouroboros, Trifold and Apex (9) were
# counted by nothing, and the 24 valence-less colourless cards by nothing
# either. It did not read as a distribution with 28 cards missing. It read as a
# complete one that stops at 6.
#
# 2026-09-03_draft_pool_histograms.sql replaces those columns with jsonb keyed
# by the value, so a bucket cannot go missing for want of a column. These tests
# pin the rendering half: every occupied bucket gets a row, and a small one
# still gets a visible bar.

# The live pool, 2026-09-03, in the post-migration shape. Rites are counted
# separately and in their own units -- `rite_templates`, not `events` -- because
# they are drawn with replacement into injected slots rather than being pool
# members present once each.
POOL = {
    "deck_name": "Total Draft Pool", "cards": 136, "aspects": 54,
    "rite_templates": 22, "rite_weight_counts": {"default": 22},
    "element_counts": {"anima": 37, "blood": 38, "sol": 37, "catalyst": 24},
    "valence_counts": {"1": 23, "2": 26, "3": 20, "4": 20, "5": 12, "6": 7,
                       "7": 1, "9": 3, "none": 24},
}

# The same pool as the OLD view reported it: eight columns, no histograms.


def test_every_card_in_the_pool_lands_in_a_bucket():
    """The check the old field would have failed: the valence rows sum to the
    card count. 108 of 136 was the whole incident."""
    buckets, _ = sf._valence_buckets(POOL)
    assert sum(count for _, count in buckets) == POOL["cards"]


def test_the_elements_are_listed_in_the_games_order():
    """taxonomy.CARD_ELEMENTS order, which is GlobalVars.ELEMENTS order, not
    alphabetical -- and catalyst last, since it is the absence of an element."""
    buckets, _ = sf._element_buckets(POOL)
    assert [name for name, _ in buckets] == ["anima", "blood", "sol", "catalyst"]


def test_an_unknown_element_is_appended_rather_than_dropped():
    """The `rite` usage type and every valence above 6 were invisible for
    exactly this reason: a value with no entry in a hardcoded list. A new
    element shows up unstyled rather than not at all."""
    grown = dict(POOL, element_counts=dict(POOL["element_counts"], void=9))
    buckets, _ = sf._element_buckets(grown)
    assert buckets[-1] == ("void", 9)


# ---------------------------------------------------------------------------
# Pick rate by element and valence
# ---------------------------------------------------------------------------
# `draft_dimension_rates_view`, added 2026-09-03 for the question neither
# neighbouring view answers: not what the pool holds and not how one item does,
# but whether a whole class of card is being ignored.


def test_a_pick_rate_reads_as_a_percentage():
    """Stored 0-1. Left to the generic float branch it renders as `0.4`, which
    reads as a count of something rather than a share."""
    assert sf.value("pick_rate", 0.3958) == "40%"
    assert sf.value("reserve_rate", 0.0) == "0%"


# ---------------------------------------------------------------------------
# Rites
# ---------------------------------------------------------------------------
# A rite IS drafted. An earlier version of this module said otherwise and drew
# two conclusions from it, one right and one wrong:
#
#   right   a rite must not be added into the pool item count -- it is a
#           TEMPLATE drawn with replacement into an injected slot, not a pool
#           member present once, so the two do not sum to anything;
#   wrong   a rite pick RATE is not comparable to a card's. It is: a rate is
#           conditional on the item being offered, so the injection budget --
#           which governs how often a rite is offered and nothing else --
#           divides straight back out.
#
# Both halves are pinned here.


def test_the_injected_slot_count_follows_the_games_formula():
    """floor(p * pool / (7 - p)), CardLogic._shuffle_in_injected_pools. At the
    default p over today's 190-item pool that is 21."""
    assert sf.injected_slots(190) == 21
    assert sf.injected_slots(0) == 0


# ---------------------------------------------------------------------------
# Draft embellishments (draft_embellishment_rates_view)
# ---------------------------------------------------------------------------

EMBELLISHMENTS = [
    {"dimension": "embellished", "bucket": "bare", "times_offered": 512, "times_picked": 128, "pick_rate": 0.25},
    {"dimension": "embellished", "bucket": "embellished", "times_offered": 78, "times_picked": 29, "pick_rate": 0.3718},
    {"dimension": "kind", "bucket": "upgrade", "times_offered": 31, "times_picked": 11, "pick_rate": 0.3548},
    {"dimension": "kind", "bucket": "attribute", "times_offered": 27, "times_picked": 9, "pick_rate": 0.3333},
    {"dimension": "kind", "bucket": "enhancement", "times_offered": 29, "times_picked": 13, "pick_rate": 0.4483},
    {"dimension": "enhancement", "bucket": "Glass", "times_offered": 9, "times_picked": 2, "pick_rate": 0.2222},
    {"dimension": "enhancement", "bucket": "Sealed", "times_offered": 8, "times_picked": 4, "pick_rate": 0.5},
    {"dimension": "enhancement", "bucket": "Lucky", "times_offered": 7, "times_picked": 5, "pick_rate": 0.7143},
    {"dimension": "enhancement", "bucket": "Etched", "times_offered": 5, "times_picked": 2, "pick_rate": 0.4},
    {"dimension": "attribute", "bucket": "Ritualistic", "times_offered": 6, "times_picked": 3, "pick_rate": 0.5},
    {"dimension": "attribute", "bucket": "Augment", "times_offered": 12, "times_picked": 4, "pick_rate": 0.3333},
]


def test_the_kind_denominators_do_not_sum_to_the_embellished_bucket():
    """The three roll INDEPENDENTLY, so a card can carry two and be counted in
    both. A reader who adds them up should not land on 78 by coincidence."""
    kinds = sum(int(r["times_offered"]) for r in EMBELLISHMENTS if r["dimension"] == "kind")
    assert kinds != 78
