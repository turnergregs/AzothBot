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
