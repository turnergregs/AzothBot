"""The checks and the wording behind `/announce`.

An announcement is news a hero says in a speech bubble on the game's title
screen (an update, new content, an event): one row of `announcements`, read by
every player's next launch. The game repo's docs/ANNOUNCEMENTS.md is the
authority on what each column does there.

Nothing here touches Discord or Supabase, so the tests can reach all of it:
command bodies are never executed by the suite.

What is refused, and why each one is worth refusing here rather than left to
the database or the game:

  * A body over the table's CHECK, so the reply says which limit, not `23514`.
  * `[term]` tags that do not pair up. The game turns them into colour tags by
    plain text replacement, so an unclosed one colours the rest of the line and
    a mistyped one is shown to players as the literal tag.
  * A version that is not dotted numbers. The game reads each part with
    `int()`, so `v0.11` or `0.11-beta` compares as a zero and the announcement
    quietly reaches the wrong builds.
  * An end that is not after the start, or is already past: it would never be
    shown.
"""
import re
from datetime import datetime, timezone

TABLE = "announcements"

# The table's CHECK on char_length(btrim(body)).
MAX_BODY = 400
# About three lines of the bubble. A longer body is allowed, and grows the box
# downward over the title's logo.
BUBBLE_CHARS = 110
# Who speaks when no speaker is picked: Lumis, the first hero of the game's
# unlock chain (`UnlockManager.HERO_ORDER[0]`), the one every player has. The
# game falls back to the same hero for a NULL speaker; writing the id makes the
# row say so.
DEFAULT_SPEAKER_ID = 7

_TERM_TAG = re.compile(r"\[(/?)term\]", re.IGNORECASE)
_BBCODE_TAG = re.compile(r"\[/?[a-zA-Z][^\[\]]*\]")
_VERSION = re.compile(r"\d+(\.\d+)*")


class AnnouncementError(ValueError):
    """Input the command refuses, worded for the reply."""


# ---------------------------------------------------------------------------
# The parts
# ---------------------------------------------------------------------------

def clean_body(body: str) -> str:
    """The body as it is stored: trimmed, inside the limit, its terms paired."""
    body = (body or "").strip()
    if not body:
        raise AnnouncementError("The body is empty.")
    if len(body) > MAX_BODY:
        raise AnnouncementError(
            f"The body is {len(body)} characters; the table allows {MAX_BODY}.")

    open_term = False
    for tag in _TERM_TAG.finditer(body):
        if tag.group(0) != tag.group(0).lower():
            raise AnnouncementError(
                f"Write `{tag.group(0).lower()}` in lower case: the game matches the tag exactly, "
                f"so `{tag.group(0)}` would be shown to players as text.")
        closing = bool(tag.group(1))
        if closing != open_term:
            raise AnnouncementError(
                "A `[/term]` has no `[term]` before it." if closing
                else "A `[term]` opens inside another one.")
        open_term = not closing
    if open_term:
        raise AnnouncementError("A `[term]` is never closed with `[/term]`.")
    return body


def visible_length(body: str) -> int:
    """The characters a player reads: the body without its BBCode tags."""
    return len(_BBCODE_TAG.sub("", body))


def parse_time(text: str | None, what: str) -> datetime | None:
    """A typed date or date and time as an aware UTC datetime; None for blank.

    A time with no offset is read as UTC, which is why the reply prints every
    time as a Discord timestamp: each reader sees it in their own zone, so a
    time typed as local shows up wrong before any player is shown it.
    """
    if text is None or not text.strip():
        return None
    try:
        when = datetime.fromisoformat(text.strip())
    except ValueError:
        raise AnnouncementError(
            f"`{what}` is not a time I can read: `{text}`. Use `2026-10-20` or "
            f"`2026-10-20 18:00` (UTC), or give an offset: `2026-10-20 18:00-05:00`.") from None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc)


def clean_version(text: str | None, what: str) -> str | None:
    """A build version as dotted numbers; None for blank."""
    if text is None or not text.strip():
        return None
    version = text.strip()
    if not _VERSION.fullmatch(version):
        raise AnnouncementError(
            f"`{what}` must be numbers and dots, like `0.11.0`. Got `{text}`.")
    return version


def version_key(version: str) -> tuple:
    """Orders versions as the game does: by number, so 0.10.0 is above 0.9.9,
    and a missing part is a zero, so 0.11 is 0.11.0."""
    parts = [int(p) for p in version.split(".")]
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def find_hero(name: str, heroes: list[dict]) -> dict:
    """The hero row a typed or picked speaker names, whatever its case."""
    wanted = (name or "").strip().lower()
    for hero in heroes:
        if str(hero.get("name", "")).lower() == wanted:
            return hero
    known = ", ".join(sorted(str(h.get("name")) for h in heroes)) or "none found"
    raise AnnouncementError(f"No hero named `{name}`. Pick one of: {known}.")


def default_hero(heroes: list[dict]) -> dict | None:
    """The hero who speaks when none is picked, or None when that hero is not
    among `heroes` (archived, say): the speaker is then left to the game."""
    for hero in heroes:
        if hero.get("id") == DEFAULT_SPEAKER_ID:
            return hero
    return None


# ---------------------------------------------------------------------------
# The row
# ---------------------------------------------------------------------------

def build_row(body: str, speaker_hero_id: int | None = None,
              starts: str | None = None, ends: str | None = None,
              min_version: str | None = None, max_version: str | None = None,
              now: datetime | None = None) -> dict:
    """The row to insert, or AnnouncementError saying what is wrong with it.

    Only what was given is in it. A column left out takes the table's default,
    which is what makes an announcement with no `starts` go live at the
    database's `now()` rather than at this machine's clock.
    """
    now = now or datetime.now(timezone.utc)
    row: dict = {"body": clean_body(body)}

    if speaker_hero_id is not None:
        row["speaker_hero_id"] = int(speaker_hero_id)

    starts_at = parse_time(starts, "starts")
    ends_at = parse_time(ends, "ends")
    if starts_at is not None:
        row["starts_at"] = starts_at.isoformat()
    if ends_at is not None:
        if starts_at is not None and ends_at <= starts_at:
            raise AnnouncementError("`ends` must be after `starts`.")
        if ends_at <= now:
            raise AnnouncementError("`ends` is already past, so this would never be shown.")
        row["ends_at"] = ends_at.isoformat()

    lowest = clean_version(min_version, "min_version")
    highest = clean_version(max_version, "max_version")
    if lowest and highest and version_key(lowest) > version_key(highest):
        raise AnnouncementError(
            f"`min_version` {lowest} is above `max_version` {highest}: no build is in that range.")
    if lowest:
        row["min_version"] = lowest
    if highest:
        row["max_version"] = highest
    return row


# ---------------------------------------------------------------------------
# The reply
# ---------------------------------------------------------------------------

def _stamp(iso: str) -> tuple[datetime, str]:
    when = datetime.fromisoformat(iso)
    unix = int(when.timestamp())
    return when, f"<t:{unix}:F> (<t:{unix}:R>)"


def describe(row: dict, speaker_name: str | None = None, row_id: int | None = None,
             now: datetime | None = None) -> str:
    """What was posted, or with no `row_id` what would be: the reply's text."""
    now = now or datetime.now(timezone.utc)
    body = row["body"]

    if row_id is None:
        lines = ["👀 **Preview.** Nothing was written."]
    else:
        lines = [f"📣 Announcement **#{row_id}** posted."]

    # As near as Discord gets to the bubble: the gold terms in bold.
    shown = _TERM_TAG.sub("**", body)
    lines += [f"> {line}" for line in shown.splitlines()]

    lines.append(f"**Speaker:** {speaker_name or 'left to the game (the first hero of its unlock chain)'}")

    if "starts_at" not in row:
        lines.append("**Live:** now")
    else:
        starts_at, stamp = _stamp(row["starts_at"])
        if starts_at > now:
            lines.append(f"**Live:** from {stamp}")
        else:
            # A player is never shown news dated before they installed.
            lines.append(f"**Live:** now, dated {stamp}. Anyone who installed after that will not see it.")

    lines.append(f"**Until:** {_stamp(row['ends_at'])[1] if 'ends_at' in row else 'it is pulled'}")

    lowest, highest = row.get("min_version"), row.get("max_version")
    if lowest and highest:
        builds = f"{lowest} to {highest}"
    elif lowest:
        builds = f"{lowest} and up"
    elif highest:
        builds = f"up to {highest}"
    else:
        builds = "every build"
    lines.append(f"**Builds:** {builds}")

    length = visible_length(body)
    if length > BUBBLE_CHARS:
        lines.append(f"⚠️ {length} characters. About {BUBBLE_CHARS} fit three lines of the bubble; "
                     f"more grows the box down over the logo.")

    if row_id is not None:
        lines.append(f"-# Wrong? Set `archived_at` on row {row_id} in Supabase to pull it, then post a "
                     f"new one. An edited row is not shown again to anyone who already confirmed it.")
    return "\n".join(lines)
