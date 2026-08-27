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

# The analytics cutoff, mirrored from `analytics_cutoff()` for the footer.
CUTOFF_VERSION = "0.8.2"

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
        return f"{raw} *(highest reached)*"

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
    """Wins out of finished runs, with the rate only when it means anything.

    `no_boss_key` counts as a win alongside `victory` (docs/DB_SCHEMA.md), and
    `finished` excludes NULL results -- those are abandoned or in progress, and
    counting them as losses would invent defeats.

    Reported as TWO NUMBERS, never a bare percentage: "50%" over two runs is one
    win wearing a decimal point.
    """
    wins, finished = row.get("wins"), row.get("finished")
    if not finished:
        return f"{row.get('game_count') or 0} played, none finished"
    line = f"**{wins or 0}** of {finished} won"
    if finished >= MIN_RUNS_FOR_A_RATE:
        line += f" ({round(100 * (wins or 0) / finished)}%)"
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


def reached(row: dict) -> str:
    return (f"act **{value('avg_act', row.get('avg_act'))}** (max {row.get('max_act')})\n"
            f"level **{value('avg_level', row.get('avg_level'))}** (max {row.get('max_level')})")


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

    picks = row.get("draft_picks")
    if picks is None:
        return "*Not available*"
    if picks == 0:
        return "*No draft picks recorded for these runs*"

    runs = row.get("game_count") or 0
    if picks == 1:
        return "*Nothing picked twice yet* — only 1 pick so far"
    return (f"*Nothing picked twice yet* — {_plural(picks, 'pick')} across "
            f"{_plural(runs, 'run')}, every one different")


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
