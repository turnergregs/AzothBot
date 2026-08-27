"""Summaries for `/bulk_insert` and `/bulk_update`.

The commands used to reply with a count -- "Updated 5 record(s) in `cards`" --
which tells you the write landed but not what it did. These build a report of
the actual change, so a bulk edit can be checked without opening the database.

Pure functions over dicts. The commands do the I/O.
"""
from __future__ import annotations

# Tables whose rows can be drawn, and the kind each maps to.
RENDERABLE = {"cards": "card", "aspects": "aspect", "events": "rite"}

# Fields that are jsonb blobs. A diff prints their SHAPE rather than their
# contents: an actions array runs to hundreds of characters and would bury every
# other change in the report.
_STRUCTURED = ("actions", "triggers", "properties", "upgrades", "image_data", "split")

# Written by the bot or the database, not by the author -- noise in a diff.
_IGNORED = ("updated_at", "created_at", "created_by", "id")

MAX_VALUE_CHARS = 60


def _short(value) -> str:
    text = "∅" if value in (None, "", [], {}) else str(value)
    return text if len(text) <= MAX_VALUE_CHARS else text[:MAX_VALUE_CHARS - 1] + "…"


def _count(value) -> str:
    if isinstance(value, (list, tuple)):
        return f"{len(value)} entr{'y' if len(value) == 1 else 'ies'}"
    if isinstance(value, dict):
        return f"{len(value)} key{'' if len(value) == 1 else 's'}"
    return "unset" if value in (None, "") else "set"


def describe_change(field: str, old, new) -> str | None:
    """One line of diff, or None when nothing changed.

    Structured fields report their shape; scalars report old → new. Both are
    truncated, because one long rules text should not push every other change
    out of the reply.
    """
    if old == new:
        return None
    if field in _STRUCTURED:
        return f"`{field}`: {_count(old)} → {_count(new)}"
    return f"`{field}`: {_short(old)} → {_short(new)}"


def diff(before: dict, after: dict) -> list:
    """Every field the update actually altered, ignoring bookkeeping columns."""
    lines = []
    for field in after:
        if field in _IGNORED:
            continue
        line = describe_change(field, before.get(field), after.get(field))
        if line:
            lines.append(line)
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
    if row.get("attunement") is not None:
        bits.append(f"attune {row['attunement']}")
    if row.get("foresight") is not None:
        bits.append(f"foresight {row['foresight']}")
    if row.get("subtypes"):
        bits.append(", ".join(str(s) for s in row["subtypes"]))
    if not row.get("image"):
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
