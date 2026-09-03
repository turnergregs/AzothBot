"""`azoth_logic/stats_format.py` — `/stats` as embeds instead of raw JSON.

Every subcommand used to reply with `json.dumps(records, indent=2)` in a code
fence. That was complete and nearly unreadable, and it actively misled on two
points that these tests pin:

  * `avg_combo_log10` is an ORDER OF MAGNITUDE. Printed raw as `4.78` next to
    `max_combo: 652298` it reads as an average combo of about five.
  * The dataset is tiny. A footer stating what the numbers rest on is not
    decoration — `docs/ANALYTICS.md` opens by saying not to quote these as fact.
"""
import re

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

def test_columns_are_aligned():
    text, _ = sf.table(ROWS, ["player", "game_count"])
    header, rule, *body = text.splitlines()
    assert len(header) == len(rule)
    assert all(line.startswith("Turner  ") or line.startswith("Bram    ") for line in body)


def test_rank_adds_a_numbered_column():
    text, _ = sf.table(ROWS, ["player"], rank=True)
    assert text.splitlines()[2].startswith("1")
    assert text.splitlines()[3].startswith("2")


def test_headings_are_renamed_where_the_column_name_would_mislead():
    text, _ = sf.table(ROWS, ["highest_combo"])
    assert "Best combo" in text.splitlines()[0]


def test_an_empty_result_is_not_a_table():
    assert sf.table([], ["player"]) == ("", [])
    assert sf.block("") == "*no rows*"


def test_a_wide_table_drops_columns_from_the_right():
    wide = [{f"col_{i}": "xxxxxxxx" for i in range(12)}]
    text, dropped = sf.table(wide)
    assert dropped, "something had to give"
    assert max(len(line) for line in text.splitlines()) <= sf.MAX_TABLE_WIDTH


def test_what_was_dropped_is_reported_never_silent():
    """Same rule `/search` follows: truncation is always announced. A table
    quietly missing a column reads as a column that does not exist."""
    wide = [{f"col_{i}": "xxxxxxxx" for i in range(12)}]
    _, dropped = sf.table(wide)
    assert "not shown" in sf.footer(wide, dropped=dropped)


def test_the_leftmost_column_always_survives():
    """It is the identifying one — a table of numbers with no names is useless."""
    wide = [{"player": "Turner", **{f"col_{i}": "xxxxxxxx" for i in range(12)}}]
    text, _ = sf.table(wide)
    assert "Player" in text.splitlines()[0]


def test_hidden_columns_never_appear():
    """`combo_numeric` exists so the view can sort; it duplicates `combo`."""
    rows = [{"combo": "652298", "combo_numeric": 652298}]
    assert "combo_numeric" not in sf.columns_of(rows)


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------

def test_fields_are_name_value_inline_triples():
    assert sf.fields(ROWS[0], ["game_count"]) == [("Games", "2", True)]


def test_excluded_columns_are_dropped():
    """The player's name titles the embed; repeating it inside is noise."""
    names = [name for name, _, _ in sf.fields(ROWS[0], exclude=("player",))]
    assert "Player" not in names


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
# The player card (player_info_view v2, 2026-08-27)
# ---------------------------------------------------------------------------

# The row `player_info_view` (2026-08-27 rebuild) actually produces, taken from
# a scratch-Postgres run of the migration.
PLAYER = {"player": "Turner", "game_count": 3,
          "avg_act": 2.33, "max_act": 3, "avg_level": 8.33, "max_level": 13,
          "max_ritual": 10, "avg_deck_size": 18.3,
          "avg_links_regular": 1.67, "avg_links_boss": 5.0,
          "regular_turns_sampled": 3, "boss_turns_sampled": 1,
          "cleared": 1, "full_clears": 0, "finished": 2, "best_combo": "652298",
          "most_drafted": "Ablution, Bind", "most_drafted_count": 2,
          "last_played": "2026-08-27T16:38:39+00:00"}


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


def test_a_clear_rate_is_withheld_on_a_tiny_sample():
    """One clear in two runs is not "50%". Two numbers, per docs/DB_SCHEMA.md."""
    assert "%" not in sf.record(PLAYER)
    assert "1** of 2 cleared act 3" in sf.record(PLAYER)


def test_a_rate_appears_once_there_are_enough_runs():
    assert "%" in sf.record({**PLAYER, "cleared": 3, "finished": 10})


def test_a_full_clear_is_called_out_separately():
    """Act 5 is a different achievement, not a bigger act 3. Folding it into the
    cleared count would hide it entirely."""
    out = sf.record({**PLAYER, "cleared": 2, "full_clears": 1})
    assert "2** of 2 cleared act 3" in out
    assert "1** full clear" in out
    assert "act 5" in out


def test_no_full_clears_says_nothing_about_them():
    assert "full clear" not in sf.record(PLAYER)


def test_clearing_act_3_counts_even_when_the_run_ended_in_death():
    """The case this exists for: beat the act 3 boss, then died to the act 4
    boss. `result` is `death` and the run still cleared — the view decides that
    via run_cleared(), and nothing here second-guesses it."""
    out = sf.record({**PLAYER, "cleared": 1, "finished": 1, "full_clears": 0})
    assert "1** of 1 cleared act 3" in out


def test_unfinished_runs_are_not_counted_as_losses():
    """A NULL result is abandoned or in progress (docs/DB_SCHEMA.md caveat 2).
    Dividing by it would invent defeats."""
    assert sf.record({**PLAYER, "wins": 0, "finished": 0}) == "3 played, none finished"


def test_regular_and_boss_links_are_never_merged():
    """A boss turn is a different activity — docs/DB_SCHEMA.md caveat 8. One
    combined "links per turn" would average two populations together."""
    out = sf.links(PLAYER)
    assert "regular" in out and "boss" in out


def test_each_link_average_carries_its_sample_size():
    """The link sample covers finished runs only, so it is a smaller population
    than `game_count`. An average of a handful of turns with no denominator is
    what caveat 6 exists to stop."""
    out = sf.links(PLAYER)
    assert "3 turns" in out and "1 turn" in out


def test_a_single_turn_is_not_pluralised():
    assert "1 turns" not in sf.links(PLAYER)


def test_no_turn_rows_says_so_rather_than_showing_zero():
    """"No links recorded" and "zero links per turn" are different claims, and
    0.0 links per turn would be a striking and completely false statistic."""
    out = sf.links({**PLAYER, "avg_links_regular": None, "avg_links_boss": None})
    assert "no turn-level data yet" in out
    assert "0" not in out


def test_a_run_with_no_boss_turns_shows_only_the_regular_average():
    out = sf.links({**PLAYER, "avg_links_boss": None})
    assert "regular" in out and "boss" not in out


def test_most_drafted_carries_the_count_that_made_it_the_most():
    """At a count of 2 the number is the story: everything is tied near the
    floor, which is what a two-run sample looks like."""
    assert "2×" in sf.most_drafted(PLAYER)


def test_each_is_only_used_when_there_is_more_than_one():
    """Live data has both shapes: Turner's is `Ablution`, Caleb's is
    `Conjunction, Echo`. "Ablution — picked 2x each" reads as a mistake."""
    assert sf.most_drafted({**PLAYER, "most_drafted": "Ablution"}).endswith("2×**")
    assert sf.most_drafted({**PLAYER, "most_drafted": "Conjunction, Echo"}).endswith("each")


def test_an_empty_most_drafted_renders_nothing():
    """The card drops the field rather than printing a placeholder.

    ⚠️ This also swallows `draft_picks == 0`, which is a DIFFERENT thing: no
    picks at all means draft rows are missing, a recording fault rather than a
    small sample. Nothing has that shape today. If draft capture ever breaks,
    this is where the silence would come from.
    """
    assert sf.most_drafted({**PLAYER, "most_drafted": None, "draft_picks": 6}) == ""
    assert sf.most_drafted({**PLAYER, "most_drafted": None, "draft_picks": 0}) == ""


def test_the_ritual_value_is_bare():
    """The field is named "Highest Ritual", so the value does not repeat it."""
    assert sf.value("max_ritual", 10) == "10"


def test_max_leads_and_the_average_is_labelled():
    """`act 3 (max 3)` gave no clue which number was which."""
    out = sf.reached({**PLAYER, "max_deck_size": 25})
    assert "Act **3** (avg 2.3)" in out


def test_deck_size_reads_like_act_and_level():
    """It was the odd one out: an average with no max beside it."""
    out = sf.reached({**PLAYER, "max_deck_size": 25})
    assert "Deck size **25** (avg 18.3)" in out


# ---------------------------------------------------------------------------
# Per-act links, and pattern clearing
# ---------------------------------------------------------------------------

ACTS = [
    {"act": 1, "avg_links_regular": 3.2, "regular_turns": 6,
     "avg_links_boss": 10.5, "boss_turns": 2,
     "avg_links_before_clear": 3.1, "avg_seconds_before_clear": 302.0,
     "avg_links_after_clear": 1.0, "avg_seconds_after_clear": 6.0,
     "cleared_turns": 4, "clearable_turns": 6},
    {"act": 2, "avg_links_regular": 5.5, "regular_turns": 6,
     "avg_links_boss": 10.0, "boss_turns": 2,
     "avg_links_before_clear": 4.8, "avg_seconds_before_clear": 551.0,
     "avg_links_after_clear": 1.3, "avg_seconds_after_clear": 8.0,
     "cleared_turns": 3, "clearable_turns": 6},
    # Act 3: links recorded, but nothing ever cleared.
    {"act": 3, "avg_links_regular": 4.8, "regular_turns": 4,
     "avg_links_boss": None, "boss_turns": 0,
     "avg_links_before_clear": None, "avg_seconds_before_clear": None,
     "avg_links_after_clear": None, "avg_seconds_after_clear": None,
     "cleared_turns": 0, "clearable_turns": 4},
]


OVERALL = {"avg_links_regular": 4.4, "avg_links_boss": 11.0,
           "regular_turns_sampled": 16, "boss_turns_sampled": 5,
           "avg_links_before_clear": 4.3, "avg_seconds_before_clear": 501.0,
           "avg_links_after_clear": 1.2, "avg_seconds_after_clear": 7.0,
           "cleared_turns": 9, "clearable_turns": 16}


def test_the_links_table_shows_the_turn_count_behind_each_average():
    """"4.8 in act 3" can rest on four turns. A difference between acts is only
    a difference if the samples are real."""
    out = sf.links_table(ACTS, OVERALL)
    assert "num" in out and "Reg" in out and "Boss" in out
    assert "4.9" in out or "4.8" in out


def test_the_overall_row_comes_from_the_player_view_not_a_mean_of_means():
    """The acts have different turn counts, so averaging the act rows would be
    wrong. `All` is the figure the view computed over every turn."""
    out = sf.links_table(ACTS, OVERALL)
    assert "All" in out
    assert "4.4" in out and "16" in out


def test_acts_are_ordered_even_when_the_rows_are_not():
    out = sf.links_table(list(reversed(ACTS)), OVERALL)
    body = [l for l in out.splitlines() if l and l[0].isdigit()]
    assert [l[0] for l in body] == ["1", "2", "3"]


def test_an_act_with_no_boss_turns_shows_a_dash_not_a_zero():
    """`0.0` would claim they fought a boss and played no links."""
    out = sf.links_table([ACTS[2]], {})
    assert "—" in out
    assert "0.0" not in out


def test_no_act_data_says_so():
    assert "no turn-level data" in sf.links_table([], {})


def test_a_missing_view_is_named_rather_than_shown_as_no_data():
    """`None` means the fetch failed — most likely the migration has not run.
    "No act data" would blame the dataset for a deployment problem."""
    assert "not migrated" in sf.links_table(None, {})
    assert "not migrated" in sf.clearing_table(None, {})


CLEAR = {"avg_links_before_clear": 3.2, "avg_links_after_clear": 1.1,
         "avg_seconds_before_clear": 41.0, "avg_seconds_after_clear": 12.0,
         "cleared_turns": 7, "clearable_turns": 9}


def test_clearing_reports_both_sides_per_act():
    out = sf.clearing_table(ACTS, OVERALL)
    assert "Bef" in out and "Aft" in out
    assert "3.1" in out and "4.8" in out


def test_clearing_splits_links_and_seconds_into_two_tables():
    """One table carrying links, seconds and the ratio came to 36 characters,
    which wraps on a phone and takes the column alignment with it — the only
    reason to use a monospace table at all."""
    out = sf.clearing_table(ACTS, OVERALL)
    assert "links" in out and "seconds" in out
    assert "5m 02s" in out and "3.1" in out


@pytest.mark.parametrize("render", ["links_table", "clearing_table"])
def test_the_player_tables_fit_a_phone(render):
    """REGRESSION (2026-08-28): the clearing table wrapped on mobile.

    Measured from the wrapped screenshot: a 24-character header survived, a
    36-character one did not. A wrapped monospace table is worse than no table —
    the columns stop lining up and every row breaks somewhere different.
    """
    out = getattr(sf, render)(ACTS, OVERALL)
    widest = max(len(l) for l in out.replace("```", "").splitlines() if l.strip())
    assert widest <= sf.MOBILE_TABLE_WIDTH, f"{widest} chars wraps on a phone"


def test_clearing_carries_its_censoring_on_every_row():
    """Right-censored: turns that never clear contribute no numerator, so the
    mean is biased optimistic exactly where difficulty is highest. The ratio
    rides on each row, not in a footnote under the table."""
    out = sf.clearing_table(ACTS, OVERALL)
    assert "4/6" in out and "9/16" in out


def test_an_act_that_never_cleared_shows_no_average():
    """Act 3 in the fixture cleared nothing. An average over an empty set would
    be blank or zero; both read as a measurement."""
    out = sf.clearing_table([ACTS[2]], {})
    lines = [l for l in out.splitlines() if l.startswith("3")]
    assert lines and "0/4" in lines[0] and "—" in lines[0]


def test_no_clearable_turns_says_so():
    assert "no turn-level data" in sf.clearing_table([], {})


@pytest.mark.parametrize("seconds, expected", [(41.0, "41s"), (60.0, "1m 00s"),
                                               (185.0, "3m 05s"), (None, "—")])
def test_durations_read_as_time(seconds, expected):
    assert sf._seconds(seconds) == expected


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
LEGACY_POOL = {
    "deck_name": "Total Draft Pool", "cards": 136, "events": 22, "aspects": 54,
    "anima": 37, "blood": 38, "sol": 37, "combo": 24,
    "1v": 23, "2v": 26, "3v": 20, "4v": 20, "5v": 12, "6v": 7,
}


def _chart(text):
    """The fenced block out of a rendered field, as Discord will draw it.

    The ANSI sequences go entirely, not just their ESC byte: Discord consumes
    `\x1b[0;35m` and prints nothing for it, so leaving the six visible
    characters behind would measure a width no reader ever sees.
    """
    fence = text.split("```")[1].replace("ansi", "", 1).strip("\n")
    return re.sub(re.escape(sf.ESC) + r"\[[0-9;]*m", "", fence)


def test_valence_above_six_is_shown():
    """THE BUG. Four cards in the pool sit above valence 6 and the field had no
    row for any of them."""
    chart = _chart(sf.draft_pool_valence(POOL))
    assert "7v" in chart and "9v" in chart


def test_a_rare_valence_still_gets_a_bar():
    """A non-zero count always gets at least one cell.

    Today's spread does not need the floor -- one card against a peak of 26
    rounds up to a cell on its own -- but the pool only grows, and a bar that
    rounds to nothing renders as an empty row, which says NONE. That is the
    same false statement the missing `7v` column made, so the sharper the
    distribution gets the more the guard is load-bearing. Pinned against a
    spread wide enough to need it: 3 against a peak of 400 is 0.13 of a cell.
    """
    wide = dict(POOL, cards=406, valence_counts={"2": 400, "3": 3, "9": 1})
    bars = [line.split()[-1] for line
            in _chart(sf.draft_pool_valence(wide)).splitlines() if line.strip()]
    assert all(bar.startswith(sf.BAR) for bar in bars), bars

    # And the case in front of us: the four cards above valence 6.
    for label in ("7v", "9v"):
        row = [l for l in _chart(sf.draft_pool_valence(POOL)).splitlines()
               if l.startswith(label)][0]
        assert row.rstrip().endswith(sf.BAR)


def test_every_card_in_the_pool_lands_in_a_bucket():
    """The check the old field would have failed: the valence rows sum to the
    card count. 108 of 136 was the whole incident."""
    buckets, _ = sf._valence_buckets(POOL)
    assert sum(count for _, count in buckets) == POOL["cards"]


def test_cards_with_no_valence_get_their_own_row():
    """24 of them, and they are not a valence of zero. Dropping them is how the
    distribution came to describe 108 cards while being read as 136."""
    chart = _chart(sf.draft_pool_valence(POOL))
    assert f"{sf.NO_VALENCE}  24" in chart


def test_the_no_valence_row_leads_the_chart():
    """Ahead of 1v, not after 9v. Having no valence is not having more of it
    than 9, and a row under the scale reads as the far end of it."""
    chart = _chart(sf.draft_pool_valence(POOL))
    assert chart.splitlines()[0].startswith(sf.NO_VALENCE)


def test_the_no_valence_row_needs_no_caption():
    """It carried one explaining that those were the colourless cards, which was
    the price of putting it at the bottom. At the top it explains itself."""
    assert "no valence" not in sf.draft_pool_valence(POOL)


def test_the_elements_are_coloured():
    """The ask this rendering answers. Discord colours a fence tagged `ansi`
    and only that, so the block tag is part of the assertion."""
    out = sf.draft_pool_elements(POOL)
    assert out.startswith("```ansi")
    for element, code in sf.ANSI_ELEMENT.items():
        assert f"{sf.ESC}[0;{code}m" in out


def test_colouring_a_row_does_not_shift_its_columns():
    """The escape codes sit outside the padded text. Inside it they would count
    toward ljust and stagger every row by five characters."""
    chart = _chart(sf.draft_pool_elements(POOL))
    assert len({line.index(sf.BAR) for line in chart.splitlines() if line.strip()}) == 1


def test_the_elements_are_listed_in_the_games_order():
    """taxonomy.CARD_ELEMENTS order, which is GlobalVars.ELEMENTS order, not
    alphabetical -- and catalyst last, since it is the absence of an element."""
    buckets, _ = sf._element_buckets(POOL)
    assert [name for name, _ in buckets] == ["anima", "blood", "sol", "catalyst"]


def test_anima_is_blue_rather_than_pink():
    """Discord's eight ANSI colours contain no purple. Anima is #8769E9, a
    blue-violet at hue 254; blue #268bd2 is nearer than pink #d33682 on RGB
    distance (105 vs 138) and on hue (49 degrees against 77). Pink reads as a
    different colour family."""
    assert sf.ANSI_ELEMENT["anima"] == 34


def test_the_elementless_bucket_is_called_catalyst():
    """23 of those 24 cards are catalysts. The bucket is still defined as NULL
    ELEMENT though -- Waxix is an elementless spell counted here -- which is why
    the view carries that caveat where the name is chosen."""
    assert "catalyst" in _chart(sf.draft_pool_elements(POOL))


def test_an_unknown_element_is_appended_rather_than_dropped():
    """The `rite` usage type and every valence above 6 were invisible for
    exactly this reason: a value with no entry in a hardcoded list. A new
    element shows up unstyled rather than not at all."""
    grown = dict(POOL, element_counts=dict(POOL["element_counts"], void=9))
    buckets, _ = sf._element_buckets(grown)
    assert buckets[-1] == ("void", 9)


def test_the_contents_line_carries_the_total():
    """It is the denominator every other number in the embed is a share of."""
    assert "190 items in the pool" in sf.draft_pool_contents(POOL)


def test_the_contents_total_points_at_the_rites_field():
    """190 is cards and aspects. Rites are drafted too, so a bare "190 items"
    beside a Rites field reads as though rites were in it."""
    assert "before rites are injected" in sf.draft_pool_contents(POOL)
    # ...and says no such thing when the view carries no rites to point at.
    assert "before rites" not in sf.draft_pool_contents(LEGACY_POOL)


def test_rites_are_never_added_into_the_item_count():
    """Not because they are undrafted -- they are drafted. A card is a pool
    member present once; a rite is a template drawn with replacement into an
    injected slot. Adding the two gives a number that is neither.

    Read off LEGACY_POOL, which still carries the old `events` column."""
    assert "22" not in sf.draft_pool_contents(LEGACY_POOL)
    assert "212" not in sf.draft_pool_contents(POOL)


@pytest.mark.parametrize("render", ["draft_pool_elements", "draft_pool_valence"])
def test_the_pool_charts_fit_a_phone(render):
    """Same measurement as the tables. A wrapped bar chart is not a chart."""
    chart = _chart(getattr(sf, render)(POOL))
    widest = max(len(line) for line in chart.splitlines() if line.strip())
    assert widest <= sf.MOBILE_TABLE_WIDTH, f"{widest} chars wraps on a phone"


@pytest.mark.parametrize("render", ["draft_pool_elements", "draft_pool_valence"])
def test_an_unmigrated_view_still_renders(render):
    """The bot is hand-started on a machine that may be running either side of
    the migration, so the old columns are still read."""
    assert sf.BAR in getattr(sf, render)(LEGACY_POOL)


@pytest.mark.parametrize("render", ["draft_pool_elements", "draft_pool_valence"])
def test_an_unmigrated_view_says_the_numbers_are_short(render):
    """And it must SAY so. Those columns cannot count valence 7 or 9, so
    rendering them as a finished distribution repeats the original bug with a
    nicer chart on top."""
    assert "incomplete" in getattr(sf, render)(LEGACY_POOL)


def test_a_migrated_view_makes_no_such_claim():
    assert "incomplete" not in sf.draft_pool_valence(POOL)


@pytest.mark.parametrize("render", ["draft_pool_elements", "draft_pool_valence"])
def test_an_empty_pool_says_so(render):
    """jsonb_object_agg over no rows is NULL; the view coalesces it to `{}` and
    this says "no cards" rather than drawing an empty frame."""
    empty = {"cards": 0, "aspects": 0,
             "element_counts": {}, "valence_counts": {}}
    assert getattr(sf, render)(empty) == "*no cards in the draft pool*"


# ---------------------------------------------------------------------------
# Pick rate by element and valence
# ---------------------------------------------------------------------------
# `draft_dimension_rates_view`, added 2026-09-03 for the question neither
# neighbouring view answers: not what the pool holds and not how one item does,
# but whether a whole class of card is being ignored.

BREAKDOWN = [
    {"dimension": "element", "bucket": "anima", "times_offered": 88, "times_picked": 33, "pick_rate": 0.375},
    {"dimension": "element", "bucket": "blood", "times_offered": 95, "times_picked": 29, "pick_rate": 0.3053},
    {"dimension": "element", "bucket": "sol", "times_offered": 87, "times_picked": 21, "pick_rate": 0.2414},
    {"dimension": "element", "bucket": "catalyst", "times_offered": 61, "times_picked": 5, "pick_rate": 0.082},
    {"dimension": "valence", "bucket": "none", "times_offered": 61, "times_picked": 5, "pick_rate": 0.082},
    {"dimension": "valence", "bucket": "1", "times_offered": 66, "times_picked": 17, "pick_rate": 0.2576},
    {"dimension": "valence", "bucket": "3", "times_offered": 48, "times_picked": 19, "pick_rate": 0.3958},
    {"dimension": "valence", "bucket": "9", "times_offered": 5, "times_picked": 0, "pick_rate": 0.0},
]


def test_a_pick_rate_reads_as_a_percentage():
    """Stored 0-1. Left to the generic float branch it renders as `0.4`, which
    reads as a count of something rather than a share."""
    assert sf.value("pick_rate", 0.3958) == "40%"
    assert sf.value("reserve_rate", 0.0) == "0%"


def test_every_bucket_carries_its_denominator():
    """A rate with no denominator is what docs/ANALYTICS.md opens by warning
    about. `9v` here is 0% off five offers, which is not evidence of anything."""
    chart = _chart(sf.draft_rate_by_valence(BREAKDOWN))
    assert "9v     0%   5" in chart


def test_the_breakdown_orders_buckets_like_the_composition_chart():
    """Shared ordering (_element_order / _valence_order) is the whole reason the
    two fields can be read against each other -- "27% of the pool, 38% of picks"
    only works if anima is the same row in both."""
    pool = [label for label, _ in sf._element_buckets(POOL)[0]]
    rates = [label for label, _, _ in sf._dimension_rows(BREAKDOWN, "element")]
    assert pool == rates == ["anima", "blood", "sol", "catalyst"]


def test_the_valence_breakdown_leads_with_the_no_valence_row():
    """Same rule as the composition chart, from the same function."""
    chart = _chart(sf.draft_rate_by_valence(BREAKDOWN))
    assert chart.splitlines()[2].startswith(sf.NO_VALENCE)


def test_the_element_breakdown_is_coloured_like_the_composition_chart():
    out = sf.draft_rate_by_element(BREAKDOWN)
    assert out.startswith("```ansi")
    assert f"{sf.ESC}[0;{sf.ANSI_ELEMENT['anima']}m" in out


def test_the_valence_breakdown_is_not_coloured():
    """Valence is orthogonal to element; a second colour scheme in one embed
    would read as though the two were related."""
    assert sf.ESC not in sf.draft_rate_by_valence(BREAKDOWN)


def test_the_offer_count_is_not_summed_across_the_view():
    """Every offer is counted once under its element and again under its
    valence, so a sum over the view is exactly double. Same trap as
    scoreboard_sample, and the same fix -- read one dimension."""
    assert sf.draft_offers_sampled(BREAKDOWN) == 88 + 95 + 87 + 61


@pytest.mark.parametrize("render", ["draft_rate_by_element", "draft_rate_by_valence"])
def test_the_breakdown_tables_fit_a_phone(render):
    chart = _chart(getattr(sf, render)(BREAKDOWN))
    widest = max(len(line) for line in chart.splitlines() if line.strip())
    assert widest <= sf.MOBILE_TABLE_WIDTH, f"{widest} chars wraps on a phone"


@pytest.mark.parametrize("render", ["draft_rate_by_element", "draft_rate_by_valence"])
def test_no_draft_data_says_so(render):
    assert getattr(sf, render)([]) == "*no card draft data yet*"


def test_an_unexpected_valence_bucket_gets_its_own_row():
    """It is NOT folded into the no-valence row. Folding is a value disappearing
    into a bucket that does not name it, which is the failure the whole draft
    view was rebuilt to end."""
    odd = BREAKDOWN + [{"dimension": "valence", "bucket": "unknown",
                        "times_offered": 4, "times_picked": 1, "pick_rate": 0.25}]
    labels = [label for label, _, _ in sf._dimension_rows(odd, "valence")]
    assert labels == [sf.NO_VALENCE, "1v", "3v", "9v", "unknown"]


def test_the_per_item_rate_table_keeps_its_rate_columns():
    """REGRESSION. `draft_rates` had no entry in stats.COLUMNS, so `table()`
    kept draft_rates_view's own column order -- item_type, item_id, item_name,
    element, valence, then the five rate columns -- and trimmed from the right
    until it fitted. Every number came off. The reply was a ranked list of item
    names with no visible reason for the ranking, which is the exact failure the
    COLUMNS dict was introduced to prevent for `avg_combo_log10`."""
    from azoth_commands.stats import COLUMNS

    rows = [{"item_type": "card", "item_id": 157, "item_name": "Torrent",
             "element": "anima", "valence": 5, "times_offered": 6,
             "times_picked": 6, "times_reserved": 0, "pick_rate": 1.0,
             "reserve_rate": 0.0}]
    text, dropped = sf.table(rows, COLUMNS["draft_rates"], rank=True)
    assert "100%" in text
    assert not dropped
    assert "157" not in text          # item_id is a join key, not information


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

TYPE_RATES = [
    {"dimension": "type", "bucket": "card", "times_offered": 590, "times_picked": 151, "pick_rate": 0.2559},
    {"dimension": "type", "bucket": "aspect", "times_offered": 212, "times_picked": 58, "pick_rate": 0.2736},
    {"dimension": "type", "bucket": "rite", "times_offered": 126, "times_picked": 31, "pick_rate": 0.246},
]


def test_rites_are_counted_in_templates_not_slots():
    """22 templates against 21 injected slots. `22` in the contents line would
    read as 22 pool slots, which is the thing this field exists to prevent."""
    out = sf.draft_pool_rites(POOL)
    assert "**22** rites" in out
    assert "21 injected slots" in out


def test_the_injected_slot_count_follows_the_games_formula():
    """floor(p * pool / (7 - p)), CardLogic._shuffle_in_injected_pools. At the
    default p over today's 190-item pool that is 21."""
    assert sf.injected_slots(190) == 21
    assert sf.injected_slots(0) == 0


def test_the_estimate_says_it_rests_on_the_default_rate():
    """INJECTED_POOL_PERCENT mirrors a game stat that content can move. A number
    derived from a mirrored constant has to wear that condition, or it silently
    describes a game that is no longer being played."""
    assert "at the default rate" in sf.draft_pool_rites(POOL)


def test_uniform_weights_report_the_share_that_misses_a_run():
    """Drawn WITH REPLACEMENT, so 21 draws over 22 templates does not cover
    them: (1 - 1/22)^21 is about 38%. That is the fact a flat count of 22
    hides -- more than a third of the rite pool is absent from any given run."""
    assert "~38% of templates miss a given run" in sf.draft_pool_rites(POOL)


def test_that_share_is_withheld_when_the_weights_differ():
    """The closed form only holds while every template has the same share of
    every draw. With tiers it would be a plausible-looking wrong number."""
    tiered = dict(POOL, rite_weight_counts={"0.25": 18, "1.0": 4})
    out = sf.draft_pool_rites(tiered)
    assert "2 weight tiers" in out
    assert "miss a given run" not in out


def test_a_view_with_no_rites_drops_the_field():
    """Rather than reporting zero rites, which is a claim about the pool."""
    assert sf.draft_pool_rites(LEGACY_POOL) == ""


def test_the_three_item_types_are_compared_by_rate():
    """The correction. A rate is conditional on being offered, so the injection
    budget divides out -- and the answer is that rites are taken at about the
    same rate as everything else, which the old framing could not have said."""
    chart = _chart(sf.draft_rate_by_type(TYPE_RATES))
    assert "card     26%  590" in chart
    assert "rite     25%  126" in chart


def test_the_types_read_in_the_same_order_as_the_contents_line():
    """"cards · aspects" then rites, top to bottom, in both places."""
    labels = [label for label, _, _ in sf._dimension_rows(TYPE_RATES, "type")]
    assert labels == ["card", "aspect", "rite"]


def test_the_offer_count_prefers_the_type_dimension():
    """It is the only dimension covering every offer. `element` and `valence`
    are cards only, so footing the embed with one of them would undercount by
    every aspect and rite -- beside a table that lists them."""
    assert sf.draft_offers_sampled(TYPE_RATES) == 590 + 212 + 126
    assert sf.draft_offers_sampled(TYPE_RATES + BREAKDOWN) == 928


def test_the_offer_caption_does_not_say_cards():
    """The type table has aspects and rites in it."""
    assert "card" not in sf.OFFERS_CAPTION
