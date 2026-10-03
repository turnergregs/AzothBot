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

from decimal import Decimal, InvalidOperation


# What actually fits an embed code block on a PHONE, measured from a wrapped
# screenshot: a 24-character header survived, a 36-character one wrapped. A
# wrapped monospace table is worse than no table -- the columns stop lining up
# and every row breaks in a different place.
MOBILE_TABLE_WIDTH = 24

# The analytics cutoff, mirrored from `analytics_cutoff()` for the footer.
# Display only -- the DB function is what actually filters. Bump both together
# or the footer will state a threshold the views are not enforcing.
CUTOFF_VERSION = "0.9.10"


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
    "max_ritual": "Ritual",
    "player_count": "Players",
    # The draft rate columns. Short on purpose: the default headings ("Times
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



def heading(column: str) -> str:
    return HEADINGS.get(column, column.replace("_", " ").capitalize())


# Where a combo stops being compacted ("53.3T") and is shown as a power of
# two -- the way the game talks about combos (levels sit on 2^14, 2^62, ...).
_MAGNITUDE_FROM = Decimal(2) ** 50
_LOG10_2 = Decimal(2).log10()


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
        # Not float(raw): `combo_numeric()` returns `numeric`, PostgREST sends
        # it as a bare JSON number, and a combo past ~1.8e308 arrives as a
        # Python int that float() refuses with OverflowError -- which took
        # down all of /stats active_players over one player's run. Past the
        # "T" suffix the compact form stops being readable anyway, so a combo
        # that large is shown as a power of two: `2^1328.8`, or `2^62` when
        # it lands exactly on one.
        try:
            number = Decimal(str(raw))
        except (TypeError, ValueError, InvalidOperation):
            return str(raw)
        if not number.is_finite():
            return str(raw)
        if abs(number) >= _MAGNITUDE_FROM:
            exponent = f"{abs(number).log10() / _LOG10_2:.1f}".removesuffix(".0")
            return f"2^{exponent}"
        return _compact(float(number))

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


def _grid(heads: list, body: list) -> str:
    """A code-fenced table with a rule under the header."""
    widths = [max(len(heads[i]), *(len(row[i]) for row in body)) for i in range(len(heads))]

    def line(cells):
        return "  ".join(c.ljust(widths[i]) for i, c in enumerate(cells)).rstrip()

    return block("\n".join([line(heads), "  ".join("-" * w for w in widths)]
                           + [line(row) for row in body]))


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


# The draft pick-rate tables (by element, valence, type and embellishment)
# were replaced 2026-09-29 by the image /stats draft picks: see stats_cards.

# ---------------------------------------------------------------------------
# Rites
# ---------------------------------------------------------------------------
# A rite IS drafted -- picked from a pack, held in the rites zone, spent later
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
# rites" beside "136 cards" reads as 22 pool slots. The pool card
# (stats_cards.draft_pool_card) gives them their own tiles, in their own units.

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


# The text bar charts (histogram, act_chart, link_chart and the ritual and
# player charts built on them) were retired 2026-09-29, once the daily report,
# its last user, was drawn as an image. Charts are stats_charts now.
