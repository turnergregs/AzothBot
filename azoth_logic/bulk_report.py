"""Summaries for `/bulk_insert` and `/bulk_update`.

The commands used to reply with a count -- "Updated 5 record(s) in `cards`" --
which tells you the write landed but not what it did. These build a report of
the actual change, so a bulk edit can be checked without opening the database.

Pure functions over dicts. The commands do the I/O.
"""
from __future__ import annotations

# Tables whose rows can be drawn, and the kind each maps to.
# `events` is the Rite table on a database the rename migration has not reached.
RENDERABLE = {"cards": "card", "aspects": "aspect", "rites": "rite", "events": "rite"}

# The jsonb blobs that define the MECHANIC. Never diffed field by field.
#
# They used to print their shape -- `properties`: 1 entry → 0 entries -- which
# is three words that say nothing anyone can act on, on every record, for every
# blob. A nine-record update ran to 36 lines of which 27 were shape.
#
# What actually changed is visible in the RULES TEXT, which the author edits in
# the same payload. So these are collapsed into one note, and that note is only
# worth printing when the text did NOT move -- see `diff`.
_QUIET = ("actions", "triggers", "properties", "upgrades", "image_data",
          # A boss's attack timelines, one column per player count (2026-09-24).
          "timeline_1p", "timeline_2p", "timeline_3p", "timeline_4p")

# Written by the bot or the database, not by the author -- noise in a diff.
# `visuals` is a boss's pre-2026-09-24 shape, now derived by a DB trigger from
# the stat and timeline columns, so it moves whenever they do.
_IGNORED = ("updated_at", "created_at", "created_by", "id", "visuals")

MAX_VALUE_CHARS = 60


def _short(value) -> str:
    text = "∅" if value in (None, "", [], {}) else str(value)
    return text if len(text) <= MAX_VALUE_CHARS else text[:MAX_VALUE_CHARS - 1] + "…"


def _face(value) -> str:
    """A `split` payload as the second face reads on the card.

    `split` is the one jsonb column that is player-facing -- it IS an element
    and a valence -- so it is diffed rather than counted. `/show` renders it the
    same way.
    """
    if not isinstance(value, dict):
        return _short(value)
    element = value.get("element")
    return (f"{str(element).capitalize() if element else 'Colourless'} "
            f"valence {value.get('valence', '?')}")


def describe_change(field: str, old, new) -> str | None:
    """One line of diff, or None when nothing changed.

    Truncated, because one long rules text should not push every other change
    out of the reply. `_QUIET` fields never reach here; `diff` collapses them.
    """
    if old == new:
        return None
    if field == "split":
        return f"`{field}`: {_face(old)} → {_face(new)}"
    return f"`{field}`: {_short(old)} → {_short(new)}"


def diff(before: dict, after: dict) -> list:
    """What the update altered, as a reader needs it.

    Two tiers, because the old flat diff buried the readable changes under the
    unreadable ones:

      * **Player-facing fields** -- `text`, `name`, `element`, `valence`,
        `subtypes`, `split`, everything that is not a mechanic blob -- print
        `old → new`. Anything not explicitly quieted lands here, so a column
        nobody anticipated is shown rather than silently dropped. That direction
        matters: a write report that hides a change is worse than a noisy one.

      * **Mechanic blobs** (`_QUIET`) collapse into a single trailing note, and
        only when the rules text did NOT change. An edit to `actions` shows up
        as an edit to `text`, and saying so twice is what made the report long.

    ⚠️ The corollary is deliberate: when the text DID change, a blob edit is not
    reported at all. A payload that rewrites the text and clears `properties` by
    accident reads as a text edit. That is the cost of the collapse.
    """
    lines, quiet = [], []
    for field in after:
        if field in _IGNORED:
            continue
        old, new = before.get(field), after.get(field)
        if old == new:
            continue
        if field in _QUIET:
            quiet.append(field)
            continue
        line = describe_change(field, old, new)
        if line:
            lines.append(line)

    text_changed = "text" in after and before.get("text") != after.get("text")
    if quiet and not text_changed:
        # Italic, so it reads as a note about the record rather than as another
        # `field: old → new` line.
        lines.append(f"*{', '.join(quiet)} updated*")
    return lines


def summarize_new(table: str, row: dict) -> str:
    """One line describing a newly inserted row.

    Names the attributes that identify the thing, so a bulk insert can be
    eyeballed for a wrong element or a missing valence without rendering
    anything -- which matters because art is usually uploaded AFTER the insert.
    """
    bits = []
    element = row.get("element")
    if "element" in row:
        bits.append(str(element).capitalize() if element else "Colourless")
    if row.get("valence") is not None:
        bits.append(f"v{row['valence']}")
    if row.get("subtypes"):
        bits.append(", ".join(str(s) for s in row["subtypes"]))
    # Rites have no art to be missing: their face is a pattern coloured from
    # `image_data`, so "no art" on every one would be noise.
    if not row.get("image") and table not in ("rites", "events"):
        bits.append("no art")

    name = row.get("name") or "(unnamed)"
    ident = f" `#{row['id']}`" if row.get("id") is not None else ""
    detail = f" — {' · '.join(bits)}" if bits else ""
    return f"• **{name}**{ident}{detail}"


def fit(lines, limit: int = 1024) -> str:
    """Join lines into one Discord field value, trimming with a count.

    Silent truncation in a write report is the worst case: it reads as "that is
    everything that changed" when it is not.
    """
    if not lines:
        return "—"
    out, used = [], 0
    for i, line in enumerate(lines):
        tail = f"\n… and {len(lines) - i} more"
        if used + len(line) + 1 + len(tail) > limit:
            out.append(f"… and {len(lines) - i} more")
            break
        out.append(line)
        used += len(line) + 1
    return "\n".join(out)


def _tables(groups) -> set:
    """The tables a bulk_update report covers. Empty for a bulk_insert, whose
    groups are keyed by table with no record name."""
    return {table for table, name in groups if name}


def report_fields(groups, empty_note: str = "no detail") -> list:
    """`groups` -> the embed fields that show it, as (label, value, inline).

    `groups` maps (table, name) -> lines: one entry per RECORD for an update,
    one per TABLE for an insert.

    A record label names its table only when the payload spans more than one.
    Names collide across types -- `deck_contents` exists because of it -- so the
    table cannot simply be dropped; but `· cards` repeated down a nine-card
    update disambiguates nothing, and the report is read as a column of names.
    Single-table payloads say it once, in the footer (`table_note`).
    """
    multi = len(_tables(groups)) > 1
    fields = []
    for (table, name), lines in groups.items():
        if not name:
            label = f"{table} ({len(lines)})"
        else:
            label = f"{name} · {table}" if multi else name
        fields.append((label, fit(lines) if lines else f"*{empty_note}*", False))
    return fields


def table_note(groups) -> str | None:
    """Where a single-table report's rows live, said once. None otherwise --
    a multi-table report carries the table on every label instead, and an insert
    already has it in each field's own label.
    """
    tables = _tables(groups)
    return f"All records are in {next(iter(tables))}." if len(tables) == 1 else None
