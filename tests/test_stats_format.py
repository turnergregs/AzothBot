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
    is its whole job. Claiming `>= 0.8.2` on a table visibly showing 0.7.0 rows
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
    out = sf.reached(PLAYER)
    assert "Act **3** (avg 2.3)" in out
    assert "Deck size" in out


# ---------------------------------------------------------------------------
# Per-act links, and pattern clearing
# ---------------------------------------------------------------------------

ACTS = [{"act": 1, "avg_links_regular": 4.1, "regular_turns": 6,
         "avg_links_boss": 6.0, "boss_turns": 2},
        {"act": 2, "avg_links_regular": 4.6, "regular_turns": 7,
         "avg_links_boss": 9.5, "boss_turns": 2},
        {"act": 3, "avg_links_regular": 4.9, "regular_turns": 3,
         "avg_links_boss": None, "boss_turns": 0}]


def test_the_act_table_shows_the_turn_count_behind_each_average():
    """"4.9 in act 3" can rest on three turns. A difference between acts is only
    a difference if the samples are real."""
    out = sf.act_table(ACTS)
    assert "4.9" in out and "3" in out
    assert "Reg" in out and "Boss" in out


def test_acts_are_ordered_even_when_the_rows_are_not():
    out = sf.act_table(list(reversed(ACTS)))
    body = [l for l in out.splitlines() if l and l[0].isdigit()]
    assert [l[0] for l in body] == ["1", "2", "3"]


def test_an_act_with_no_boss_turns_shows_a_dash_not_a_zero():
    """Act 3 has no boss turns recorded. `0.0` would claim they fought a boss
    and played no links."""
    out = sf.act_table([ACTS[2]])
    assert "—" in out
    assert "0.0" not in out


def test_no_act_data_says_so():
    assert "no act data" in sf.act_table([])


def test_a_missing_view_is_named_rather_than_shown_as_no_data():
    """`None` means the fetch failed — most likely the migration has not run.
    "No act data" would blame the dataset for a deployment problem."""
    assert "not migrated" in sf.act_table(None)


CLEAR = {"avg_links_before_clear": 3.2, "avg_links_after_clear": 1.1,
         "avg_seconds_before_clear": 41.0, "avg_seconds_after_clear": 12.0,
         "cleared_turns": 7, "clearable_turns": 9}


def test_clearing_reports_both_sides():
    out = sf.clearing(CLEAR)
    assert "Before" in out and "After" in out
    assert "3.2" in out and "1.1" in out


def test_clearing_is_reported_with_its_censoring():
    """Right-censored: turns that never clear contribute no numerator, so the
    mean is biased optimistic exactly where difficulty is highest. "3.2 links"
    alone is not the honest statement."""
    out = sf.clearing(CLEAR)
    assert "7 of 9 turns" in out and "78%" in out


def test_never_clearing_is_not_reported_as_an_average():
    """Zero cleared turns means every average is over an empty set."""
    out = sf.clearing({**CLEAR, "cleared_turns": 0})
    assert "Never cleared" in out
    assert "3.2" not in out


def test_no_clearable_turns_says_so():
    assert "no turn-level data" in sf.clearing({"clearable_turns": 0})


@pytest.mark.parametrize("seconds, expected", [(41.0, "41s"), (60.0, "1m 00s"),
                                               (185.0, "3m 05s"), (None, "—")])
def test_durations_read_as_time(seconds, expected):
    assert sf._seconds(seconds) == expected
