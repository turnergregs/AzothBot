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
    "1v": "1v", "2v": "2v", "3v": "3v", "4v": "4v", "5v": "5v", "6v": "6v",
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
