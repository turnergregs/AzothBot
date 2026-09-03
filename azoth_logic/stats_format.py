"""Turning a `/stats` view into something readable.

Every `/stats` subcommand used to reply with `json.dumps(records, indent=2)` in a
code block -- the raw view, column names and all. It is complete and almost
unreadable, and it hid the two things that matter most about these numbers:

  * `avg_combo_log10` is an ORDER OF MAGNITUDE, not an average. Printed as
    `4.78` beside `max_combo: 652298` it reads as a tiny average combo. It is
    rendered here as `10^4.8`, which is what it means.
  * The dataset is small enough that any single number is noise. Every reply
    carries a footer saying how many games are behind it and where the cutoff
    is, so nobody quotes a mean of two runs.

Pure functions over dicts. The commands do the I/O.
"""
from __future__ import annotations

# Discord's limits, and what a phone can read without horizontal scrolling.
MAX_DESCRIPTION = 4096
MAX_FIELD = 1024
MAX_TABLE_WIDTH = 56

# What actually fits an embed code block on a PHONE, measured from a wrapped
# screenshot: a 24-character header survived, a 36-character one wrapped. A
# wrapped monospace table is worse than no table -- the columns stop lining up
# and every row breaks in a different place.
MOBILE_TABLE_WIDTH = 24

# The analytics cutoff, mirrored from `analytics_cutoff()` for the footer.
# Display only -- the DB function is what actually filters. Bump both together
# or the footer will state a threshold the views are not enforcing.
CUTOFF_VERSION = "0.9.0"

# Below this, a win RATE is theatre: one win in two runs is not "50%".
MIN_RUNS_FOR_A_RATE = 5

# Column name -> heading. Anything absent is title-cased with underscores
# stripped, which is right for `game_count` and wrong for the two below.
HEADINGS = {
    "avg_combo_log10": "Combo",
    "max_combo": "Best combo",
    "highest_combo": "Best combo",
    "combo_numeric": "Combo",
    "game_count": "Games",
    "hours_played": "Played",
    "most_picked_hero": "Top hero",
    "most_drafted": "Top pick",
    "last_played_at": "Last played",
    "hero_name": "Hero",
    "deck_size": "Deck",
    # draft_rates_view. Short on purpose: the default headings ("Times
    # offered", "Times picked") are wider than the numbers under them, and this
    # table has five columns to fit.
    "item_name": "Item",
    "item_type": "Type",
    "pick_rate": "Pick",
    "reserve_rate": "Held",
    "times_picked": "Took",
    "times_offered": "Seen",
    "times_reserved": "Kept",
}

# Columns that are internal or redundant in a rendered table.
HIDDEN = {"combo_numeric"}


def heading(column: str) -> str:
    return HEADINGS.get(column, column.replace("_", " ").capitalize())


def _compact(number: float) -> str:
    """A big number at a glance. Combos reach 10^30; nobody counts digits."""
    for limit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(number) >= limit:
            trimmed = f"{number / limit:.1f}".rstrip("0").rstrip(".")
            return f"{trimmed}{suffix}"
    return f"{number:g}"


def value(column: str, raw) -> str:
    """One cell, formatted for what the column actually means."""
    if raw is None or raw == "":
        return "—"

    if column == "avg_combo_log10":
        # An order of magnitude. `4.78` alone reads as an average combo of five.
        try:
            return f"10^{float(raw):.1f}"
        except (TypeError, ValueError):
            return str(raw)

    if column == "best_combo":
        # The full BigNum, grouped. This is one run's actual score, not a
        # summary, so it is not compacted -- and log space has nothing to fix
        # about a maximum. Grouping is what makes 30 digits readable.
        try:
            return f"{int(str(raw)):,}"
        except (TypeError, ValueError):
            return str(raw)

    if column == "max_ritual":
        return str(raw)

    if column in ("max_combo", "highest_combo", "combo", "combo_numeric"):
        try:
            return _compact(float(raw))
        except (TypeError, ValueError):
            return str(raw)

    if column in ("pick_rate", "reserve_rate"):
        # A proportion, stored 0-1. Left alone it renders as `0.7`, which reads
        # as a count of something. Whole percents: the extra decimal costs a
        # column of width and the sample is nowhere near precise enough to
        # earn it.
        try:
            return f"{float(raw) * 100:.0f}%"
        except (TypeError, ValueError):
            return str(raw)

    if column == "hours_played":
        try:
            hours = float(raw)
        except (TypeError, ValueError):
            return str(raw)
        return f"{round(hours * 60)}m" if hours < 1 else f"{hours:.1f}h"

    if column.endswith("_at"):
        return str(raw)[:10]          # the date; the time is never the question

    if isinstance(raw, float):
        return f"{raw:.1f}".rstrip("0").rstrip(".")
    if isinstance(raw, bool):
        return "yes" if raw else "no"
    return str(raw)


def columns_of(rows: list, exclude=()) -> list:
    """Every column present, in the view's own order, minus the noise."""
    seen = []
    for row in rows:
        for column in row:
            if column not in seen and column not in HIDDEN and column not in exclude:
                seen.append(column)
    return seen


def table(rows: list, columns=None, rank: bool = False,
          limit_width: int = MAX_TABLE_WIDTH):
    """Rows as an aligned monospace block. Returns `(text, dropped_columns)`.

    Callers should pass the columns they want. Left to itself this keeps the
    view's own order, and the view puts `avg_turns` before `avg_combo_log10` --
    so the width trim below would throw away the combo, which is the column
    anyone actually came for.

    That trim is the backstop, not the plan: columns come off the RIGHT until
    the table fits a phone, because a table that wraps is unreadable in a way
    that a table missing a column is not. **What came off is returned**, never
    dropped silently -- same rule `/search` follows when it truncates.
    """
    if not rows:
        return "", []
    columns = list(columns or columns_of(rows))
    dropped = []

    body = [[value(c, row.get(c)) for c in columns] for row in rows]
    heads = [heading(c) for c in columns]
    widths = [max(len(heads[i]), *(len(r[i]) for r in body)) for i in range(len(columns))]

    if rank:
        heads.insert(0, "#")
        widths.insert(0, max(1, len(str(len(rows)))))
        for index, row in enumerate(body, start=1):
            row.insert(0, str(index))

    # Trim from the right until it fits.
    while len(columns) > 1 and sum(widths) + 2 * (len(widths) - 1) > limit_width:
        widths.pop()
        heads.pop()
        for row in body:
            row.pop()
        dropped.insert(0, columns.pop())

    def line(cells):
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()

    text = "\n".join([line(heads), "  ".join("-" * w for w in widths)]
                     + [line(r) for r in body])
    return text, dropped


def fields(row: dict, columns=None, inline: bool = True, exclude=()) -> list:
    """A single record as embed `(name, value, inline)` triples.

    `exclude` drops columns that the embed's title already carries -- repeating
    the player's name inside their own card is noise.
    """
    columns = columns or columns_of([row], exclude=exclude)
    return [(heading(c), value(c, row.get(c)), inline) for c in columns]


def record(row: dict) -> str:
    """Runs that CLEARED, out of runs that finished.

    "Cleared" is beating the act 3 boss -- the milestone the game itself rewards
    with the next ritual (main.gd:1464). Acts 4 and 5 are bonus content, so a run
    that cleared act 3 and then died to the act 4 boss is a cleared run, and
    `games.result` still correctly says `death`. `public.run_cleared()` holds
    that definition; nothing here re-derives it.

    A full clear (the act 5 boss) is called out separately when there is one --
    it is a different achievement, not a bigger version of the same one.

    `finished` excludes NULL results: those are abandoned or in progress, and
    counting them as losses would invent defeats.

    Reported as TWO NUMBERS, never a bare percentage: "50%" over two runs is one
    clear wearing a decimal point.
    """
    cleared, finished = row.get("cleared"), row.get("finished")
    if not finished:
        return f"{row.get('game_count') or 0} played, none finished"

    line = f"**{cleared or 0}** of {finished} cleared act 3"
    if finished >= MIN_RUNS_FOR_A_RATE:
        line += f" ({round(100 * (cleared or 0) / finished)}%)"

    full = row.get("full_clears") or 0
    if full:
        line += f"\n**{full}** full clear{'' if full == 1 else 's'} *(act 5)*"
    return line


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def links(row: dict) -> str:
    """Average links per turn, regular and boss kept apart.

    A boss turn is a different activity -- docs/DB_SCHEMA.md caveat 8 says the
    two are not comparable, so they are never averaged together.

    Both carry their SAMPLE SIZE. The link average is over finished runs only
    (an abandoned run's last turn is mid-flight), so it covers a smaller
    population than `game_count`, and an average with no denominator over a
    handful of turns is the thing this document keeps warning about.
    """
    regular, boss = row.get("avg_links_regular"), row.get("avg_links_boss")
    if regular is None and boss is None:
        return "*no turn-level data yet*"

    parts = []
    if regular is not None:
        parts.append(f"**{value('avg_links_regular', regular)}** regular "
                     f"*({_plural(row.get('regular_turns_sampled') or 0, 'turn')})*")
    if boss is not None:
        parts.append(f"**{value('avg_links_boss', boss)}** boss "
                     f"*({_plural(row.get('boss_turns_sampled') or 0, 'turn')})*")
    return "\n".join(parts)


def _rows(title: str, heads: list, body: list) -> str:
    """An aligned table with a rule under the header, as plain text."""
    widths = [max(len(heads[i]), *(len(row[i]) for row in body)) for i in range(len(heads))]

    def line(cells):
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells)).rstrip()

    out = [line(heads), "  ".join("-" * w for w in widths)] + [line(row) for row in body]
    return (f"{title}\n" if title else "") + "\n".join(out)


def _grid(heads: list, body: list) -> str:
    """A code-fenced table with a rule under the header."""
    widths = [max(len(heads[i]), *(len(row[i]) for row in body)) for i in range(len(heads))]

    def line(cells):
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells)).rstrip()

    return block("\n".join([line(heads), "  ".join("-" * w for w in widths)]
                           + [line(row) for row in body]))


def _sorted_acts(rows: list) -> list:
    return sorted(rows, key=lambda r: r.get("act") or 0)


def links_table(acts: list, row: dict) -> str:
    """Links per turn, per act, with the overall figure as a final `All` row.

    One table rather than a number and a table beside it -- the overall average
    IS the bottom of this column, and separating them invited reading the act
    rows as a decomposition of something else.

    `All` comes from `player_info_view`, not from averaging the act rows: the
    acts have different turn counts, so a mean of means would be wrong.

    Turn counts are IN the table. "4.9 in act 3" can rest on four turns, and a
    difference between acts is only a difference if the samples are real.
    """
    if acts is None:
        return "*unavailable — `player_act_view` is not migrated*"

    body = [[str(r.get("act")),
             value("x", r.get("avg_links_regular")),
             str(r.get("regular_turns") or 0),
             value("x", r.get("avg_links_boss")),
             str(r.get("boss_turns") or 0)]
            for r in _sorted_acts(acts or [])]

    if row.get("avg_links_regular") is not None or row.get("avg_links_boss") is not None:
        body.append(["All",
                     value("x", row.get("avg_links_regular")),
                     str(row.get("regular_turns_sampled") or 0),
                     value("x", row.get("avg_links_boss")),
                     str(row.get("boss_turns_sampled") or 0)])

    if not body:
        return "*no turn-level data yet*"
    return _grid(["Act", "Reg", "num", "Boss", "num"], body)


def clearing_table(acts: list, row: dict) -> str:
    """Pattern clearing per act, with an `All` row.

    Links and seconds share a cell -- "4.3, 8m21s" -- because they answer one
    question together and six columns will not fit a phone.

    REGULAR TURNS ONLY, and only turns that had patterns to solve; the view
    decides both. `Cleared` is the censoring, carried on every row: turns that
    never clear contribute no numerator, so an average without it is biased
    optimistic exactly where difficulty is highest.
    """
    if acts is None:
        return "*unavailable — `player_act_view` is not migrated*"

    def rows_for(r, label):
        cleared, clearable = r.get("cleared_turns"), r.get("clearable_turns")
        if not clearable:
            return None
        if not cleared:
            return ([label, "—", "—", f"0/{clearable}"], [label, "—", "—"])
        return (
            [label,
             value("x", r.get("avg_links_before_clear")),
             value("x", r.get("avg_links_after_clear")),
             f"{cleared}/{clearable}"],
            [label,
             _seconds(r.get("avg_seconds_before_clear")),
             _seconds(r.get("avg_seconds_after_clear"))],
        )

    links, times = [], []
    for r in _sorted_acts(acts or []):
        made = rows_for(r, str(r.get("act")))
        if made:
            links.append(made[0])
            times.append(made[1])

    overall = rows_for(row, "All")
    if overall:
        links.append(overall[0])
        times.append(overall[1])

    if not links:
        return "*no turn-level data yet*"

    # TWO narrow tables rather than one wide one. Links, seconds and the clear
    # ratio in a single row came to 36 characters, which wraps on a phone and
    # takes the column alignment with it. Split, each fits inside
    # MOBILE_TABLE_WIDTH and the alignment survives -- which is the only reason
    # to use a monospace table at all.
    return (block(_rows("links", ["Act", "Bef", "Aft", "Cleared"], links))
            + "\n"
            + block(_rows("seconds", ["Act", "Bef", "Aft"], times)))


# The bonus axes, in the order turn_bonus.gd declares them -- which is also its
# tie-break order, so two columns read side by side are being compared the way
# the game compares them. Labels are four characters because three tables share
# one width and `precision`/`overdraw`/`overload` do not fit a phone.
SCOREBOARD_AXES = [("precision", "Prec"), ("overdraw", "Draw"), ("overload", "Load")]


def _scoreboard_index(rows: list) -> dict:
    """{act -> {axis -> row}}. The view's rollup row has act NULL, so it keys
    under None and needs no special case anywhere below."""
    by_act = {}
    for row in rows or []:
        by_act.setdefault(row.get("act"), {})[row.get("axis")] = row
    return by_act


def _scoreboard_acts(by_act: dict) -> list:
    """Act keys in order, rollup last. `All` belongs at the bottom of the column
    it summarises, exactly as links_table puts it."""
    acts = sorted(a for a in by_act if a is not None)
    return acts + ([None] if None in by_act else [])


def scoreboard_sample(rows: list) -> int:
    """Turns behind the whole table.

    NOT `sum(turns_sampled)`. Every scored turn produces one row PER AXIS and
    contributes to the rollup as well, so summing the column counts each turn
    six times over. The rollup row of any single axis is the honest total --
    all three carry the same number, because every scored turn scores all three.
    """
    rollup = _scoreboard_index(rows).get(None) or {}
    for key, _ in SCOREBOARD_AXES:
        row = rollup.get(key)
        if row and row.get("turns_sampled") is not None:
            return int(row["turns_sampled"])
    return 0


def _scoreboard_grid(rows: list, cell, tail_head: str = None, tail_cell=None):
    """Acts down the side, bonus axes across the top.

    One shape reused for every scoreboard question, because they ARE one shape --
    three axes measured identically. Three hand-built tables would drift apart
    the first time an axis is added or renamed, which is a thing this data is
    explicitly expected to survive.

    Returns None when there is nothing to draw, so callers can say "no data yet"
    in their own words rather than rendering an empty frame.
    """
    by_act = _scoreboard_index(rows)
    acts = _scoreboard_acts(by_act)
    if not acts:
        return None

    heads = ["Act"] + [label for _, label in SCOREBOARD_AXES]
    if tail_head:
        heads.append(tail_head)

    body = []
    for act in acts:
        axes = by_act[act]
        line = ["All" if act is None else str(act)]
        line += [cell(axes.get(key)) for key, _ in SCOREBOARD_AXES]
        if tail_head:
            line.append(tail_cell(axes))
        body.append(line)

    return _grid(heads, body)


def _rate_cell(column: str):
    def cell(row):
        if not row or row.get(column) is None:
            return "—"
        try:
            return f"{float(row[column]):.0f}%"
        except (TypeError, ValueError):
            return "—"
    return cell


def _count_cell(row) -> str:
    if not row or row.get("avg_count") is None:
        return "—"
    return f"{float(row['avg_count']):g}"


def _threshold_caption(rows: list) -> str:
    """The thresholds those counts were measured against, as one line under the
    table rather than a `12.4/10` in every cell -- which pushed the table past
    the width a phone renders without wrapping, for a number that is the same
    down the whole column.

    Read off the ROLLUP row, and averaged there rather than picked, so a run
    that actually raised a threshold surfaces as a fractional value instead of
    hiding behind today's default. Precision is absent on purpose: its threshold
    is structurally zero.
    """
    rollup = _scoreboard_index(rows).get(None) or {}
    parts = []
    for key, label in SCOREBOARD_AXES:
        row = rollup.get(key)
        if not row or row.get("avg_threshold") is None:
            continue
        try:
            threshold = float(row["avg_threshold"])
        except (TypeError, ValueError):
            continue
        if threshold == 0:
            continue
        parts.append(f"{label} {threshold:g}")
    return "*thresholds — " + " · ".join(parts) + "*" if parts else ""


def _act_turns(axes: dict) -> int:
    """Turns behind one act's row. Identical across the three axes -- every
    scored turn scores all three -- so it is one number, not three."""
    for key, _ in SCOREBOARD_AXES:
        row = axes.get(key)
        if row and row.get("turns_sampled") is not None:
            return int(row["turns_sampled"])
    return 0


def _sample_caption(rows: list) -> str:
    """Turns behind each act, as a line under the table.

    The denominator is not optional -- "62% in act 3" can rest on four turns,
    and every other table in this module carries its counts for that reason.
    It sits in a caption rather than a fifth column only because the column put
    the table one character past what a phone renders without wrapping, and a
    wrapped monospace table loses its alignment entirely.
    """
    by_act = _scoreboard_index(rows)
    parts = []
    for act in _scoreboard_acts(by_act):
        turns = _act_turns(by_act[act])
        parts.append(f"{'all' if act is None else act}: {turns}")
    return "*turns — " + " · ".join(parts) + "*" if parts else ""


def scoreboard_hits(rows: list) -> str:
    """How often each axis crossed its threshold, per act.

    The question the view exists for. The turn counts behind each row are in the
    caption underneath -- see _sample_caption for why they are not a column.

    The hit test is `count > threshold`, not `>=` -- turn_bonus.gd pays
    `max(0, count - threshold)`, so landing exactly on the number scores nothing.
    The view applies it; this only renders what it returns.
    """
    grid = _scoreboard_grid(rows, _rate_cell("hit_rate"))
    if not grid:
        return "*no scoreboard data yet*"
    caption = _sample_caption(rows)
    return grid + ("\n" + caption if caption else "")


def scoreboard_counts(rows: list) -> str:
    """What each axis measured, over the threshold in force.

    The tuning number, and the one a hit rate cannot give you: a rate says
    whether the threshold is being met, this says by how far it is being missed,
    which is what tells you where to move it.

    The threshold comes off the turn rather than from today's default, because
    content can raise it mid-run and because the whole point of storing it was
    that a retune leaves history readable. It sits in a caption under the table;
    see _threshold_caption.
    """
    grid = _scoreboard_grid(rows, _count_cell)
    if not grid:
        return "*no scoreboard data yet*"
    caption = _threshold_caption(rows)
    return grid + ("\n" + caption if caption else "")


def scoreboard_paid(rows: list) -> str:
    """Which axis actually paid, per act.

    Only the winner pays, never the sum, so a row adds to 100% bar rounding. A
    high hit rate on an axis that never pays means it is being crowded out by
    another -- which reads as a healthy axis in the hits table alone.
    """
    return _scoreboard_grid(rows, _rate_cell("won_rate")) or "*no scoreboard data yet*"


def clearing(row: dict) -> str:
    """Links and seconds either side of clearing the turn's patterns.

    RIGHT-CENSORED, and reported as two numbers because of it: turns that never
    clear contribute no numerator, so the mean is biased optimistic exactly
    where difficulty is highest. "3.2 links" alone is not the honest statement --
    "3.2 links, on the 78% of turns that cleared" is.

    Turns that began with no patterns are already excluded by the view; they
    would "clear" at node one having done nothing.
    """
    cleared = row.get("cleared_turns")
    clearable = row.get("clearable_turns")
    if not clearable:
        return "*no turn-level data yet*"
    if not cleared:
        return f"*Never cleared* — 0 of {_plural(clearable, 'turn')} with patterns to solve"

    before = (f"**{value('x', row.get('avg_links_before_clear'))}** links, "
              f"{_seconds(row.get('avg_seconds_before_clear'))}")
    after = (f"**{value('x', row.get('avg_links_after_clear'))}** links, "
             f"{_seconds(row.get('avg_seconds_after_clear'))}")
    share = f"{round(100 * cleared / clearable)}%" if clearable else "?"
    return (f"Before: {before}\nAfter: {after}\n"
            f"*cleared on {cleared} of {_plural(clearable, 'turn')} ({share})*")


def _seconds(raw) -> str:
    if raw is None:
        return "—"
    try:
        total = float(raw)
    except (TypeError, ValueError):
        return str(raw)
    if total < 60:
        return f"{total:.0f}s"
    return f"{int(total // 60)}m {round(total % 60):02d}s"


def reached(row: dict) -> str:
    """Furthest reached, with the average in parentheses.

    Max leads because it is the unambiguous number -- "act 3" is a fact about a
    run that happened. The average needs its label to be readable at all, which
    is why it is the one wearing `avg`.
    """
    return (f"Act **{row.get('max_act')}** (avg {value('avg_act', row.get('avg_act'))})\n"
            f"Level **{row.get('max_level')}** (avg {value('avg_level', row.get('avg_level'))})\n"
            f"Deck size **{row.get('max_deck_size')}** "
            f"(avg {value('avg_deck_size', row.get('avg_deck_size'))})")


def most_drafted(row: dict) -> str:
    """The player's most-picked items, or WHY there are none.

    The count matters more than the names when it is 2: it says "everything is
    tied near the floor", which is what a two-run sample looks like.

    An empty answer has two causes and they are not the same news:

      * picks exist, none repeated -- expected on a small sample, and the pick
        count says how small;
      * no picks at all -- draft rows are missing for those runs, which is a
        recording problem wearing the same blank space.

    `draft_picks` is what tells them apart. Without it (a view older than the
    2026-08-27 migration) neither claim can be made, so neither is made.
    """
    names = row.get("most_drafted")
    if names:
        count = row.get("most_drafted_count")
        if not count:
            return str(names)
        # "each" only when there is more than one name to be each of.
        suffix = " each" if "," in str(names) else ""
        return f"{names} — picked **{count}×**{suffix}"

    # Nothing to show -> no field at all. The caller drops it rather than
    # printing a placeholder.
    #
    # NOTE this also hides the `draft_picks == 0` case, which is not the same
    # news: no picks AT ALL means draft rows are missing for those runs, a
    # recording fault rather than a small sample. Nothing has that shape today
    # (every player has picks), so it is hidden with the rest -- but that is the
    # one case worth un-hiding if draft capture ever breaks.
    return ""


def last_played(row: dict) -> str:
    when = row.get("last_played")
    return f"last played {str(when)[:10]}" if when else None


def footer(rows: list, count_column: str = "game_count", note: str = None,
           dropped=(), cutoff: bool = True) -> str:
    """What the numbers rest on.

    Never omitted. The trustworthy dataset is a couple of runs deep, so a mean
    here is one or two games wearing a decimal point. See docs/ANALYTICS.md.

    `cutoff` is NOT always true. Seven of the eight views filter on
    `analytics_cutoff()`, but `version_info_view` deliberately does not -- its
    whole job is comparing versions, and filtering to `>= 0.8.2` would leave it
    with one row. Claiming a cutoff on a table visibly showing 0.7.0 rows is
    worse than claiming nothing.
    """
    games = 0
    for row in rows:
        try:
            games += int(row.get(count_column) or 0)
        except (TypeError, ValueError):
            pass

    parts = [f"version >= {CUTOFF_VERSION}"] if cutoff else ["all versions"]
    if games:
        parts.append(f"{games} game{'' if games == 1 else 's'}")
    if note:
        parts.append(note)
    if dropped:
        parts.append("not shown: " + ", ".join(heading(c).lower() for c in dropped))
    return " · ".join(parts)


def block(text: str) -> str:
    """A code fence, which is the only way Discord renders aligned columns."""
    return f"```\n{text}\n```" if text else "*no rows*"


# ---------------------------------------------------------------------------
# The draft pool
# ---------------------------------------------------------------------------
# Three fields of bare "label N · label N" runs until 2026-09-03. Two things
# were wrong with that beyond it being plain:
#
#   * Four counts on one line is a list, not a distribution. Whether 5v is half
#     of 2v takes arithmetic to see, which nobody does while reading Discord.
#   * The valence line was SHORT BY 28 CARDS and did not say so -- see
#     _valence_buckets. A missing bucket is invisible in prose; in a chart the
#     labels are the axis, so a bucket either has a row or it visibly does not.

# Discord renders exactly eight foreground colours inside an ```ansi fence and
# NONE OF THEM IS PURPLE, so each element takes the nearest of them to the
# colour the GAME draws it in (GlobalVars.ELEMENTS, mirrored in
# card_layout.ELEMENT_COLORS). Blood's red -> red and sol's orange -> yellow are
# obvious; anima is not, and was pink until 2026-09-03.
#
# Anima is #8769E9, a blue-violet at hue 254. Against Discord's palette (which
# is Solarized) blue #268bd2 is nearer than pink #d33682 on both measures --
# 105 vs 138 in RGB distance, and 49 degrees of hue against 77. Pink lands on
# the magenta side of purple and reads as a different colour family; blue lands
# on the side anima actually sits on.
#
# Catalysts are WHITE, which is also the default -- the absence of a colour for
# the absence of an element. Grey (30) was the first choice for that reason and
# is wrong in practice: 30 renders #4f545c against a #2b2d31 code block, and a
# row nobody reads is the same failure as a bucket nobody counts.
ANSI_ELEMENT = {"anima": 34, "blood": 31, "sol": 33, "catalyst": 37}
# The escape character, spelled out: a literal 0x1b in a source file is
# invisible in a diff and does not survive a careless copy-paste.
ESC = "\u001b"

# Listing order, mirroring taxonomy.CARD_ELEMENTS -- which is itself the game's
# GlobalVars.ELEMENTS order, not alphabetical. NOT imported from taxonomy: this
# module is pure functions over dicts and taxonomy reaches the database on a
# cache miss. Anything the view returns that is not named here is appended
# rather than dropped, following taxonomy.values(): a value present in the data
# must never be invisible for want of a list entry.
#
# `catalyst` is the bucket of cards with NO ELEMENT, named for what 23 of those
# 24 cards are; the odd one out is Waxix, an elementless spell. The view decides
# the name -- see 2026-09-03_draft_pool_histograms.sql, which carries the same
# caveat where the bucket is defined.
ELEMENT_ORDER = ["anima", "blood", "sol", "catalyst"]

# The label for a card carrying no valence. It leads the chart rather than
# trailing it: these are not a valence above the others, and reading down from
# 1v the eye takes a final row as the end of the scale.
NO_VALENCE = "—"

BAR = "█"

MIGRATION_NOTE = ("*⚠️ incomplete — `draft_deck_view` predates "
                  "`2026-09-03_draft_pool_histograms.sql`*")


def ansi_block(text: str) -> str:
    """A code fence Discord will colour. Same contract as `block`.

    A client too old to know the `ansi` language shows the escape codes as
    literal text rather than colour; every current desktop and mobile build
    handles it. Only the element chart uses this -- the tables stay plain,
    because colour there would be decoration rather than an extra dimension.
    """
    return f"```ansi\n{text}\n```" if text else "*no rows*"


def _histogram(buckets: list, width: int = MOBILE_TABLE_WIDTH,
               colours: dict = None) -> str:
    """`(label, count)` pairs as one bar per row, scaled to the largest.

    **A non-zero count always gets at least one cell.** That is the whole point.
    Scaled against a peak of 26 the single 7v card rounds to 0.7 of a cell, and
    a bar that renders empty says "none" -- which is precisely the false reading
    the missing `7v` column produced for as long as it was missing. Rows are
    never dropped or merged for being small either.

    Sized to MOBILE_TABLE_WIDTH for the same reason the tables are: a line that
    wraps in a monospace fence loses its alignment, and an unaligned bar chart
    is not a chart. The escape codes are outside the padded text, so colouring
    a row cannot shift its columns.
    """
    rows = [(str(label), int(count or 0)) for label, count in buckets]
    if not rows:
        return ""

    label_width = max(len(label) for label, _ in rows)
    count_width = max(len(str(count)) for _, count in rows)
    cells = max(1, width - label_width - count_width - 2)
    peak = max(count for _, count in rows)

    lines = []
    for label, count in rows:
        filled = max(1, round(cells * count / peak)) if count > 0 and peak else 0
        line = f"{label.ljust(label_width)} {str(count).rjust(count_width)} {BAR * filled}"
        colour = (colours or {}).get(label)
        lines.append(f"{ESC}[0;{colour}m{line}{ESC}[0m" if colour else line)
    return "\n".join(lines)


def _element_order(keys) -> list:
    """Element bucket keys in display order.

    Shared by the composition chart and the breakdown table so the two line up
    row for row and can be read against each other -- "27% of the pool, 38% of
    picks" is only legible if anima is the same row in both.
    """
    known = [name for name in ELEMENT_ORDER if name in keys]
    return known + sorted(str(k) for k in keys if k not in ELEMENT_ORDER)


def _valence_order(keys) -> list:
    """Valence bucket keys in display order: valence-less first, then 1, 2, 3...

    Non-numeric keys are NOT merged. `none` is the only one the view emits, but
    folding an unrecognised key into it would be the same move -- a value
    disappearing into a bucket that does not name it -- that this whole view was
    rebuilt to stop. An unexpected key gets its own row under its own name, at
    the end where it is obvious.
    """
    keys = [str(k) for k in keys]
    numeric = sorted(int(k) for k in keys if k.isdigit())
    other = sorted(k for k in keys if not k.isdigit() and k != "none")
    return (["none"] if "none" in keys else []) + [str(n) for n in numeric] + other


def _valence_label(key) -> str:
    """`3` -> `3v`, `none` -> the no-valence dash, anything else as itself."""
    key = str(key)
    if key.isdigit():
        return f"{key}v"
    return NO_VALENCE if key == "none" else key


def _element_buckets(row: dict):
    """`([(element, count)], complete)` for the element chart.

    `element_counts` (jsonb, 2026-09-03) is keyed by the element itself, so an
    element added to the game lands in a bucket without a migration. The older
    shape had a column each for anima/blood/sol plus `combo` for the colourless
    ones -- a name that means the exponential run score in every other view in
    this schema, for a column counting cards with no element.
    """
    counts = row.get("element_counts")
    complete = isinstance(counts, dict)
    if not complete:
        counts = {"anima": row.get("anima"), "blood": row.get("blood"),
                  "sol": row.get("sol"), "catalyst": row.get("combo")}

    counts = {name: count for name, count in counts.items() if count}
    return [(name, counts[name]) for name in _element_order(counts)], complete


def _valence_buckets(row: dict):
    """`([(label, count)], complete)` for the valence chart.

    THE BUG THIS EXISTS FOR. `draft_deck_view` used to carry a column per
    valence and only `1v`..`6v` of them -- 2026-08-26_rebuild_analytics_views
    dropped `7v`..`10v` as "permanently zero", on the grounds that valence is
    1-6. The pool holds Circumvent at 7 and three cards at 9, and 24 cards with
    no valence at all. So the field summed to 108 of 136 cards and read as a
    complete distribution that stops at 6, rather than as one missing 28 rows.

    `valence_counts` is keyed by the valence, so nothing can fall outside it,
    and valence-less cards key under `none` instead of vanishing. The old
    columns are still read when the histogram is absent -- the bot is
    hand-started on a machine that may be running either side of the migration
    -- and `complete` is False there so the caller can say the numbers are
    short rather than presenting them as whole.
    """
    counts = row.get("valence_counts")
    complete = isinstance(counts, dict)
    if not complete:
        counts = {key[:-1]: value for key, value in row.items()
                  if len(key) > 1 and key.endswith("v") and key[:-1].isdigit()}

    # The valence-less bucket leads -- see _valence_order. Having no valence is
    # not having more of it than 9, and a row under the scale reads as the far
    # end of it.
    counts = {str(key): count for key, count in counts.items() if count}
    return [(_valence_label(key), counts[key])
            for key in _valence_order(counts)], complete


def draft_pool_contents(row: dict) -> str:
    """What is in the pool, and how much of it there is.

    The total is stated rather than left to be added up: it is the denominator
    every other number in this embed is a share of.

    NO RITES IN THIS COUNT, and not because they are not drafted -- they are.
    They are TEMPLATES injected into extra slots by a weighted draw with
    replacement, not pool members present once each
    (CardLogic._shuffle_in_injected_pools), so the two are different quantities
    and adding them produces a number that is neither. They get their own field;
    see draft_pool_rites, which the total below points at.

    `events` is not read even when an older view still returns it: surfacing
    rites or not depending on which side of the migration the database is on
    would be worse than either answer on its own.
    """
    cards = int(row.get("cards") or 0)
    aspects = int(row.get("aspects") or 0)
    total = cards + aspects
    tail = " before rites are injected" if row.get("rite_templates") else ""
    return (f"**{cards}** cards · **{aspects}** aspects\n"
            f"*{total} item{'' if total == 1 else 's'} in the pool{tail}*")


def draft_pool_elements(row: dict) -> str:
    """The element split, in the game's own colours."""
    buckets, complete = _element_buckets(row)
    if not buckets:
        return "*no cards in the draft pool*"

    chart = ansi_block(_histogram(buckets, colours=ANSI_ELEMENT))
    return chart if complete else chart + "\n" + MIGRATION_NOTE


def draft_pool_valence(row: dict) -> str:
    """The valence spread, every occupied bucket, none of them rounded away.

    Not coloured: valence is orthogonal to element, and a second colour scheme
    in the same embed would read as though the two were related.
    """
    buckets, complete = _valence_buckets(row)
    if not buckets:
        return "*no cards in the draft pool*"

    # The `—` row carries no caption. It sat at the bottom of the chart and
    # needed one to explain why; at the top, ahead of 1v, it reads as the
    # off-scale bucket it is.
    parts = [block(_histogram(buckets))]
    if not complete:
        parts.append(MIGRATION_NOTE)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Pick rate by element and valence
# ---------------------------------------------------------------------------
# `draft_dimension_rates_view`, one row per (dimension, bucket). It answers what
# neither of its neighbours can: `draft_deck_view` says what the pool is made
# of, `draft_rates_view` says how often one item is taken, and only this says
# whether a whole class of card is being ignored.
#
# Rendered as a TABLE, not the bar chart the composition uses. A bar is a share
# of a whole and these are not shares of anything -- four independent rates that
# do not sum to 100 -- so bars would invite reading them as parts of one pie.
# The bucket order is shared with the charts (_element_order / _valence_order)
# so the two fields line up row for row and can be read against each other.

# Kept off the table and stated once underneath. `n` is the same word in both
# tables and a "Seen"/"Offered" column costs width the counts will want as the
# sample grows.
OFFERS_CAPTION = "*n = times offered*"


# The order the contents line names them in, so the two read the same way.
TYPE_ORDER = ["card", "aspect", "rite"]


def _type_order(keys) -> list:
    known = [name for name in TYPE_ORDER if name in keys]
    return known + sorted(str(k) for k in keys if k not in TYPE_ORDER)


def _dimension_rows(rows: list, dimension: str) -> list:
    """`(label, bucket_key, row)` for one dimension, in display order."""
    by_bucket = {str(r.get("bucket")): r for r in rows or []
                 if r.get("dimension") == dimension}
    if not by_bucket:
        return []

    order, label = {
        "type": (_type_order, str),
        "element": (_element_order, str),
        "valence": (_valence_order, _valence_label),
    }[dimension]
    return [(label(key), key, by_bucket[key]) for key in order(by_bucket)]


def _rate_table(entries: list, head: str, colours: dict = None) -> str:
    """Bucket, pick rate and denominator, aligned, optionally coloured per row.

    The denominator is a column rather than a footnote because a rate without
    one is the thing docs/ANALYTICS.md exists to warn about -- and at bucket
    grain the counts differ enough between rows that a single caption could not
    carry them.
    """
    body = [[label, value("pick_rate", row.get("pick_rate")),
             str(int(row.get("times_offered") or 0))]
            for label, _, row in entries]
    heads = [head, "Pick", "n"]

    widths = [max(len(heads[i]), *(len(r[i]) for r in body)) for i in range(3)]

    def line(cells, colour=None):
        text = "  ".join(cells[i].rjust(widths[i]) if i else cells[i].ljust(widths[i])
                         for i in range(3)).rstrip()
        return f"{ESC}[0;{colour}m{text}{ESC}[0m" if colour else text

    out = [line(heads), "  ".join("-" * w for w in widths)]
    out += [line(row, (colours or {}).get(key))
            for row, (_, key, _r) in zip(body, entries)]
    text = "\n".join(out)
    return ansi_block(text) if colours else block(text)


def draft_rate_by_type(rows: list) -> str:
    """Pick rate for cards, aspects and rites side by side.

    THIS COMPARISON IS FAIR, and an earlier version of this module said it was
    not. A pick rate is conditional on the item being OFFERED, so the injection
    budget -- which governs how often a rite appears in a pack and nothing else
    -- divides straight back out. What is not fair is a raw pick COUNT, which
    measures the injection rate instead of the player's choice; that is what
    2026-08-27_most_drafted_excludes_rites.sql excluded rites from, and the two
    cases were conflated here.
    """
    entries = _dimension_rows(rows, "type")
    if not entries:
        return "*no draft data yet*"
    return _rate_table(entries, "Type") + "\n" + OFFERS_CAPTION


def draft_rate_by_element(rows: list) -> str:
    """Pick rate per element, in the same colours and order as the pool chart."""
    entries = _dimension_rows(rows, "element")
    if not entries:
        return "*no card draft data yet*"
    return _rate_table(entries, "Element", ANSI_ELEMENT) + "\n" + OFFERS_CAPTION


def draft_rate_by_valence(rows: list) -> str:
    """Pick rate per valence. Uncoloured, exactly as the composition chart is."""
    entries = _dimension_rows(rows, "valence")
    if not entries:
        return "*no card draft data yet*"
    return _rate_table(entries, "Val") + "\n" + OFFERS_CAPTION


def draft_offers_sampled(rows: list) -> int:
    """Card offers behind the breakdown.

    Read off ONE dimension, not summed across the view: every offer is counted
    once under each dimension it has, so the whole view totals well over the
    real number -- the same trap scoreboard_sample documents.

    `type` first, because it is the only dimension covering ALL offers. The
    other two are cards only, so falling back to them reports the card offers
    alone -- correct for the field they footnote, and a visible undercount
    beside a `type` table that has aspects and rites in it.
    """
    for dimension in ("type", "element", "valence"):
        entries = _dimension_rows(rows, dimension)
        if entries:
            return sum(int(row.get("times_offered") or 0) for _, _, row in entries)
    return 0


# ---------------------------------------------------------------------------
# Rites
# ---------------------------------------------------------------------------
# A rite IS drafted -- picked from a pack, held in the events zone, spent later
# (docs/EVENTS.md in the game repo). What differs is how it reaches the pack,
# and therefore what "composition" means for it:
#
#   card / aspect   a POOL MEMBER. The three `draft` decks load 190 items into
#                   the draft zone, each present exactly once.
#   rite            a TEMPLATE. After the pool loads,
#                   CardLogic._shuffle_in_injected_pools() adds
#                   floor(p * pool / (7 - p)) further slots and fills each by an
#                   independent weighted draw WITH REPLACEMENT. A template can
#                   be drawn twice or not at all, and the deck is never consumed.
#
# So they are not the same kind of quantity and must not share a count -- "22
# rites" beside "136 cards" reads as 22 pool slots. They get their own field, in
# their own units.

# GlobalVars.stats.reactant_pool_percent in the game repo (the stat keeps its
# reactant-era name). Mirrored here for the same reason CUTOFF_VERSION is: this
# is display arithmetic over a game constant, and a constant in a SQL view is
# harder to find and impossible to test. If the game moves it, the estimate
# below goes stale silently -- which is why every number derived from it is
# labelled "at the default rate" rather than stated flat.
INJECTED_POOL_PERCENT = 0.7


def injected_slots(pool_size: int) -> int:
    """How many injected slots a pool of this size gets.

    `floor(p * pool_size / (7 - p))` -- CardLogic._shuffle_in_injected_pools.
    At the default p and today's 190-item pool that is 21.
    """
    p = INJECTED_POOL_PERCENT
    return int(p * pool_size / (7 - p)) if pool_size > 0 else 0


def _weight_summary(weights: dict) -> str:
    """How the templates are weighted, in a phrase.

    One bucket means every template has the same share of every draw, which is
    the whole of what the composition question asks at this grain. More than one
    and the shares differ, so they are named -- `deck_contents.weight` is a real
    column and AzothBot has simply never set it.
    """
    buckets = {k: v for k, v in (weights or {}).items() if v}
    if not buckets:
        return ""
    if len(buckets) == 1:
        return "equal weight"
    named = ", ".join(f"{k}×{v}" for k, v in sorted(buckets.items()))
    return f"{len(buckets)} weight tiers ({named})"


def draft_pool_rites(row: dict) -> str:
    """The rite half of the pool, in template units rather than slot units.

    Returns "" when the view does not carry rites -- the caller drops the field
    rather than printing a placeholder, exactly as `most_drafted` does.
    """
    templates = int(row.get("rite_templates") or 0)
    if not templates:
        return ""

    weights = row.get("rite_weight_counts")
    uniform = isinstance(weights, dict) and len([v for v in weights.values() if v]) == 1

    line = f"**{templates}** rite{'' if templates == 1 else 's'}"
    summary = _weight_summary(weights)
    if summary:
        line += f" · {summary}"

    pool = int(row.get("cards") or 0) + int(row.get("aspects") or 0)
    slots = injected_slots(pool)
    if not slots:
        return line

    share = round(100 * slots / (pool + slots))
    note = (f"*≈{slots} injected slots (~{share}% of the pool) at the default "
            f"rate, drawn with replacement")
    if uniform:
        # P(a given template is drawn none of the `slots` times), which only has
        # this closed form while every template has the same share.
        absent = round(100 * (1 - 1 / templates) ** slots)
        note += f" — so ~{absent}% of templates miss a given run*"
    else:
        note += "*"
    return f"{line}\n{note}"
