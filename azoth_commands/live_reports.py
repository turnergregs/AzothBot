"""/live_reports -- post new player reports, survey comments and crashes every 10 minutes.

Players file reports from inside the game (`report_popup.gd` in the game repo:
bugs, feature requests, accessibility requests, content ideas), can type a
comment on the one-click survey (`survey_responses`), and the game files a
crash report by itself when something breaks (`ReportManager`). This posts each
new one to the channels it is enabled in, within ten minutes of it landing: an
embed per kind, oldest first.

  - PLAYER REPORTS: every `report_type` except `crash`.
  - SURVEY COMMENTS: survey rows with a comment. The one-click answers are
    counted in `/daily_reports`' daily image instead.
  - CRASHES: `report_type = 'crash'`, EXCEPT those from developer accounts
    (`players.developer`). Measured 2026-10-07: of ~700 crash reports in two
    weeks, all but ~9 came from Turner's and Caleb's own sessions -- GUT runs,
    dev probes, scratch scripts -- which would bury the few that matter. A crash
    with no player (sent before the install registered) IS posted: that is a
    new player. Crashes are their own kind, on their own watermark, so a burst
    of them never delays a report a player wrote.

Per channel, one watermark per kind (`last_report_id`, `last_survey_id`,
`last_crash_id`), each advanced only AFTER a message is out and only past the
rows it carried. A failed send is retried on the next cycle, ten minutes later;
the worst case is a row posted twice, which beats one never posted. The kinds
are independent: a failure fetching or sending one never holds another back.

The crash watermark also moves past developer crashes without a message, once
they have been read, so they are not scanned again every cycle.

No day is claimed, unlike daily_reports.py: posting is the point of every
cycle, so a cycle with nothing new simply sends nothing.

A backlog (the bot was down) drains at MESSAGES_PER_CYCLE messages per kind per
cycle, each up to BATCH_LIMIT rows, oldest first.

A channel enabled for the first time starts where `/daily_reports` stopped
posting reports and comments (it carried them until 2026-10-07), so the switch
neither repeats nor drops anything. A watermark with nothing to start from --
crashes, which the daily post never carried, or a bot with no daily state --
starts at the newest row, never id 0, so it never dumps the history.
"""
import asyncio
import json
import os
import traceback
from datetime import datetime

import nextcord
from nextcord import Interaction, SlashOption
from nextcord.ext import tasks

from azoth_commands import daily_reports
from azoth_commands.daily_update import _atomic_write_json
from azoth_commands.helpers import safe_interaction
from azoth_logic import survey_labels
from constants import DEV_GUILD_ID
from supabase_client import supabase
from supabase_helpers import SupabaseQueryError, SupabaseUnreadableError, _assert_readable

# State file stores per-channel config:
# {
#   "channels": {
#     "<channel_id>": {
#       "last_report_id": 2054,
#       "last_survey_id": 8,
#       "last_crash_id": 2061
#     }
#   }
# }
STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "live_reports_state.json")

INTERVAL_MINUTES = 10
BATCH_LIMIT = 10           # rows per message
MESSAGES_PER_CYCLE = 3     # per kind per channel, so a backlog drains without flooding
SUMMARY_LIMIT = 200        # characters of the player's text, before escaping
CRASH_SUMMARY_LIMIT = 300  # the error line and where it happened
META_LIMIT = 40            # category, player name, version, contact

# Crash rows read per fetch, developer ones included. Developer crashes run
# 15-100 a day, so this reaches past a day of them in one read.
CRASH_SCAN_LIMIT = 200

# Discord's cap on one embed's total text. Ten summaries fit comfortably; ten
# worst-case ones (every character a markdown character, so escaping doubles
# it) do not, and the rows that don't fit wait for the next message.
EMBED_CHAR_LIMIT = 6000

CRASH_TYPE = "crash"
REPORT_COLUMNS = ["id", "player_uuid", "report_type", "category", "description",
                  "contact_info", "game_version", "created_at"]
CRASH_COLUMNS = ["id", "player_uuid", "error_message", "game_state", "os_info",
                 "game_version", "created_at"]
SURVEY_TABLE = daily_reports.SURVEY_TABLE
SURVEY_COLUMNS = ["id", "player_uuid", "question_id", "answer", "comment",
                  "version", "created_at"]

REPORT_COLOR = 0x3498DB
SURVEY_COLOR = 0x9B59B6
CRASH_COLOR = 0xE74C3C

# The loop and the startup catch-up both run as soon as the bot is ready, each
# with its own copy of the state. A send is an await, so without this the second
# pass can load the state while the first is mid-send and post the same rows
# again. Held across load -> fetch -> send -> save.
_SEND_LOCK = asyncio.Lock()


def _load_state() -> dict:
    data = _read_json_state(STATE_FILE)
    data.setdefault("channels", {})
    return data


def _read_json_state(path: str) -> dict:
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    return data if isinstance(data, dict) else {}


def _save_state(state: dict):
    _atomic_write_json(STATE_FILE, state)


# ---------------------------------------------------------------------------
# Supabase data fetching
#
# Each fetch returns (rows to post, how many are waiting in total, the highest
# id it read). The last is how far the watermark may move once every row to
# post has gone out -- past rows read and deliberately not posted (developer
# crashes) too. Each raises on failure rather than returning [] -- an empty
# list means "nothing new", and must never stand in for "could not read".
# ---------------------------------------------------------------------------

def _fetch_player_reports(after_id: int, limit: int = BATCH_LIMIT) -> tuple[list[dict], int, int]:
    """The oldest `limit` non-crash reports with id > after_id.

    `neq` also drops rows whose report_type is NULL (SQL three-valued logic).
    The game always sets it, and this postgrest version has no `or_` to say
    otherwise.
    """
    _assert_readable("reports")
    try:
        response = (
            supabase.table("reports")
            .select(",".join(REPORT_COLUMNS), count="exact")
            .gt("id", after_id)
            .neq("report_type", CRASH_TYPE)
            .order("id")
            .limit(limit)
            .execute()
        )
    except Exception as e:
        raise SupabaseQueryError(f"select on `reports` failed: {e}") from e
    return _with_total(response, after_id)


def _fetch_survey_comments(after_id: int, limit: int = BATCH_LIMIT) -> tuple[list[dict], int, int]:
    """The oldest `limit` survey responses with a comment and id > after_id."""
    _assert_readable(SURVEY_TABLE)
    try:
        response = (
            supabase.table(SURVEY_TABLE)
            .select(",".join(SURVEY_COLUMNS), count="exact")
            .gt("id", after_id)
            .eq("has_comment", True)
            .order("id")
            .limit(limit)
            .execute()
        )
    except Exception as e:
        raise SupabaseQueryError(f"select on `{SURVEY_TABLE}` failed: {e}") from e
    return _with_total(response, after_id)


def _with_total(response, after_id: int) -> tuple[list[dict], int, int]:
    rows = response.data or []
    total = response.count if response.count is not None else len(rows)
    return rows, total, max([after_id] + [r["id"] for r in rows])


def _fetch_crashes(after_id: int, limit: int = BATCH_LIMIT) -> tuple[list[dict], int, int]:
    """Crash reports with id > after_id that did not come from a developer.

    Reads up to CRASH_SCAN_LIMIT crashes, oldest first, and drops the
    developers' in Python: PostgREST cannot join `players`, and this postgrest
    version has no `or_` to keep the rows with no player. The total is what
    this read found, so a backlog past CRASH_SCAN_LIMIT is counted short.
    """
    _assert_readable("reports")
    developers = _fetch_developer_uuids()
    try:
        response = (
            supabase.table("reports")
            .select(",".join(CRASH_COLUMNS))
            .gt("id", after_id)
            .eq("report_type", CRASH_TYPE)
            .order("id")
            .limit(CRASH_SCAN_LIMIT)
            .execute()
        )
    except Exception as e:
        raise SupabaseQueryError(f"select on `reports` failed: {e}") from e
    scanned = response.data or []
    rows = [r for r in scanned if r.get("player_uuid") not in developers]
    read_through = max([after_id] + [r["id"] for r in scanned])
    if len(rows) > limit:
        # Only the first `limit` go out, so the watermark may not pass them.
        read_through = rows[limit]["id"] - 1
    return rows[:limit], len(rows), read_through


def _fetch_developer_uuids() -> set[str]:
    try:
        response = supabase.table("players").select("uuid").eq("developer", True).execute()
    except Exception as e:
        raise SupabaseQueryError(f"select on `players.developer` failed: {e}") from e
    return {row["uuid"] for row in (response.data or []) if row.get("uuid")}


def _fetch_latest_id(table: str) -> int:
    """The highest id in `table`, or 0 when it is empty. Raises on failure."""
    _assert_readable(table)
    try:
        response = supabase.table(table).select("id").order("id", desc=True).limit(1).execute()
    except Exception as e:
        raise SupabaseQueryError(f"select on `{table}` failed: {e}") from e
    rows = response.data or []
    return int(rows[0]["id"]) if rows else 0


def _fetch_player_names(player_uuids: list[str]) -> dict[str, str]:
    uuids = sorted({u for u in player_uuids if u})
    if not uuids:
        return {}
    try:
        response = supabase.table("players").select("uuid,name").in_("uuid", uuids).execute()
    except Exception as e:
        raise SupabaseQueryError(f"select on `players` failed: {e}") from e
    return {row["uuid"]: row.get("name") for row in (response.data or []) if row.get("uuid")}


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def _clean(text, limit: int) -> str:
    """Player text, truncated and made inert.

    Truncated BEFORE escaping so the cut can't split an escape sequence, and
    escaped so a report can't restyle the embed or render a mention. Nothing
    here would ping anyway -- mentions inside embeds never notify, and every
    send passes AllowedMentions.none() -- but a rendered `@everyone` still
    reads like one.
    """
    text = str(text).strip()
    if len(text) > limit:
        text = text[:limit - 1].rstrip() + "…"
    return nextcord.utils.escape_mentions(nextcord.utils.escape_markdown(text))


def _type_label(report_type) -> str:
    return str(report_type or "report").replace("_", " ").title()


def _parse_timestamp(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _meta_line(row: dict, player_names: dict[str, str], version_key: str,
               leading=(), trailing=()) -> str:
    """`answer · player · v0.9.12 · <time> · contact`, the parts that are present."""
    meta = list(leading)
    player = player_names.get(row.get("player_uuid"))
    if player:
        meta.append(_clean(player, META_LIMIT))
    if row.get(version_key):
        meta.append(f"v{_clean(row[version_key], META_LIMIT)}")
    created = _parse_timestamp(row.get("created_at"))
    if created:
        meta.append(f"<t:{int(created.timestamp())}:f>")
    meta.extend(trailing)
    return " · ".join(meta)


def _report_field(report: dict, player_names: dict[str, str]) -> tuple[str, str]:
    """(name, value) for one player report.

    Name: `Feature Request #12`, plus the category when it says something the
    type doesn't (`Bug #7 · Visual`). Value: the player's text, then a line of
    who / which version / when / how to reach them.
    """
    label = _type_label(report.get("report_type"))
    name = f"{label} #{report['id']}"
    category = report.get("category")
    if category and str(category).strip() and str(category).strip().lower() not in (
            label.lower(), f"{label.lower()} report"):
        name += f" · {_clean(category, META_LIMIT)}"

    description = report.get("description")
    body = (_clean(description, SUMMARY_LIMIT) if description and str(description).strip()
            else "*(no description)*")
    contact = report.get("contact_info")
    trailing = [f"contact: {_clean(contact, META_LIMIT)}"] if contact and str(contact).strip() else []
    meta = _meta_line(report, player_names, "game_version", trailing=trailing)
    if meta:
        body += "\n" + meta
    return name, body


def _survey_field(row: dict, player_names: dict[str, str]) -> tuple[str, str]:
    """(name, value) for one survey comment.

    Name: the question and the row's id. Value: the comment, then a line of the
    answer / who / which version / when. Only the comment is player text; the
    question and answer are the game's own words.
    """
    name = f"{_clean(survey_labels.question(row.get('question_id')), META_LIMIT * 2)} #{row['id']}"
    body = _clean(row.get("comment") or "", SUMMARY_LIMIT) or "*(no comment)*"

    answer = row.get("answer")
    leading = ([_clean(survey_labels.answer(row.get("question_id"), answer), META_LIMIT)]
               if answer is not None and str(answer).strip() else [])
    meta = _meta_line(row, player_names, "version", leading=leading)
    if meta:
        body += "\n" + meta
    return name, body


UNCLEAN_EXIT = "Previous session ended without shutting down cleanly."
WHAT_WENT_WRONG = "--- What went wrong ---"


def _crash_summary(error_message) -> str:
    """The line that says what broke, and where, out of a crash's error_message.

    Three shapes reach the table (`ReportManager` in the game repo):
      - a runtime error: `SCRIPT ERROR: ...` then `  at: res://...`;
      - an unclean exit with the error found in the log: the same two lines
        under `--- What went wrong ---`, then the log's tail;
      - an unclean exit with nothing found: only the log's tail, which is
        mostly startup noise, so it is not shown.
    """
    text = str(error_message or "").strip()
    if not text:
        return "(no error message)"
    unclean = text.startswith(UNCLEAN_EXIT)
    if WHAT_WENT_WRONG in text:
        text = text.split(WHAT_WENT_WRONG, 1)[1].strip()
    elif unclean:
        return "Game closed without shutting down cleanly (no error found in the log)"
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    summary = lines[0] if lines else "(no error message)"
    if len(lines) > 1 and lines[1].startswith("at:"):
        summary += "\n" + lines[1]
    if unclean:
        summary = "Unclean exit: " + summary
    return summary


def _crash_field(crash: dict, player_names: dict[str, str]) -> tuple[str, str]:
    """(name, value) for one crash: `Crash #12 · RESOLVING_CARDS`, then what
    broke and where, then who / which version / when / which OS."""
    name = f"Crash #{crash['id']}"
    if crash.get("game_state"):
        name += f" · {_clean(crash['game_state'], META_LIMIT)}"
    body = _clean(_crash_summary(crash.get("error_message")), CRASH_SUMMARY_LIMIT)
    trailing = [_clean(crash["os_info"], META_LIMIT)] if crash.get("os_info") else []
    meta = _meta_line(crash, player_names, "game_version", trailing=trailing)
    if meta:
        body += "\n" + meta
    return name, body


def _build_embed(rows: list[dict], fields: list[tuple[str, str]], total: int,
                 noun: str, color: int) -> tuple[nextcord.Embed, int]:
    """One embed of `fields`, and how many of them it carries.

    Fields are added oldest first until the next would break EMBED_CHAR_LIMIT;
    the rest wait for the next message.
    """
    embed = nextcord.Embed(color=color)
    # The title and footer depend on how many fit, so size them for the worst
    # case (as long as they can get) before choosing.
    reserve = len(_title(len(rows), noun)) + len(_footer(len(rows), total) or "") + 10
    used, shown = reserve, 0
    for name, value in fields:
        if used + len(name) + len(value) > EMBED_CHAR_LIMIT:
            break
        embed.add_field(name=name, value=value, inline=False)
        used += len(name) + len(value)
        shown += 1

    embed.title = _title(shown, noun)
    footer = _footer(shown, total)
    if footer:
        embed.set_footer(text=footer)
    return embed, shown


def _plural(noun: str) -> str:
    return noun + ("es" if noun.endswith("sh") else "s")


def _title(shown: int, noun: str) -> str:
    return f"{shown} new {noun if shown == 1 else _plural(noun)}"


def _footer(shown: int, total: int):
    waiting = total - shown
    if waiting <= 0:
        return None
    return f"{waiting} more waiting -- they'll follow shortly"


# Each kind: (watermark key, table it starts from, fetch, field builder, title
# noun, colour). Player reports first, crashes last: a crash flood waits its turn.
KINDS = (
    ("last_report_id", "reports", _fetch_player_reports, _report_field, "player report", REPORT_COLOR),
    ("last_survey_id", SURVEY_TABLE, _fetch_survey_comments, _survey_field, "survey comment", SURVEY_COLOR),
    ("last_crash_id", "reports", _fetch_crashes, _crash_field, "crash", CRASH_COLOR),
)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------

async def _post_kind(channel, state: dict, channel_id: str, config: dict, kind) -> int:
    """Post one kind's new rows to `channel`, up to MESSAGES_PER_CYCLE messages.
    Caller must hold _SEND_LOCK. Returns how many rows were posted.

    A fetch error raises before anything moves. A failed send stops this kind
    for the cycle with its watermark where the last good send left it.
    """
    watermark, _, fetch, field_for, noun, color = kind
    posted = 0
    for _ in range(MESSAGES_PER_CYCLE):
        after_id = int(config.get(watermark) or 0)
        rows, total, read_through = fetch(after_id)
        if not rows:
            # Nothing to post, but rows may have been read and passed over
            # (developer crashes): move past them so they are not read again.
            if read_through > after_id:
                config[watermark] = read_through
                state["channels"][channel_id] = config
                _save_state(state)
            break
        names = _fetch_player_names([r.get("player_uuid") for r in rows])
        embed, shown = _build_embed(rows, [field_for(r, names) for r in rows], total, noun, color)
        if not shown:
            print(f"Live reports: {noun} #{rows[0]['id']} does not fit in an embed; "
                  f"channel {channel_id} is stuck on it")
            break
        try:
            await channel.send(embed=embed, allowed_mentions=nextcord.AllowedMentions.none())
        except Exception as e:
            print(f"Live reports send FAILED for channel {channel_id}; {_plural(noun)} after "
                  f"#{after_id} will be retried next cycle: {e}")
            break

        # Advance past exactly what the message carried -- and past what was
        # read and passed over, only when nothing to post was left behind.
        config[watermark] = max(after_id, read_through if shown == len(rows) else rows[shown - 1]["id"])
        state["channels"][channel_id] = config
        _save_state(state)
        posted += shown
        print(f"Live reports: posted {shown} of {total} new {_plural(noun)} to channel {channel_id}")
        if shown >= total:
            break
    return posted


async def _post_channel(bot, state: dict, channel_id: str, config: dict) -> int | None:
    """Post every kind's new rows to one channel. Caller must hold _SEND_LOCK.

    Returns the rows posted, or None when the channel could not be resolved.
    Each kind is tried on its own: one failing is printed and the others still
    post.
    """
    channel = bot.get_channel(int(channel_id))
    if not channel:
        print(f"Live reports: channel {channel_id} not found; will retry next cycle")
        return None

    if _start_missing_watermarks(config):
        state["channels"][channel_id] = config
        _save_state(state)

    posted = 0
    for kind in KINDS:
        try:
            posted += await _post_kind(channel, state, channel_id, config, kind)
        except (SupabaseQueryError, SupabaseUnreadableError) as e:
            print(f"Live reports: {_plural(kind[4])} skipped for channel {channel_id} ({e}); "
                  f"retrying next cycle")
    return posted


async def _post_all_channels(bot, source: str) -> None:
    """Post new rows to every enabled channel.

    ⚠️ NOTHING may propagate out of here -- an exception reaching tasks.Loop
    stops it for the life of the process. Same contract as
    daily_reports._send_due_channels.
    """
    try:
        async with _SEND_LOCK:
            state = _load_state()
            for channel_id, config in list(state["channels"].items()):
                try:
                    if not isinstance(config, dict):
                        print(f"Live reports [{source}]: channel {channel_id} has a "
                              f"malformed state entry ({type(config).__name__}); skipping")
                        continue
                    if config.get("disabled"):
                        continue
                    await _post_channel(bot, state, channel_id, config)
                except Exception as e:
                    traceback.print_exc()
                    print(f"Live reports [{source}]: channel {channel_id} failed with "
                          f"{type(e).__name__}: {e}. The next cycle will retry it.")
    except Exception as e:
        traceback.print_exc()
        print(f"Live reports [{source}]: cycle aborted with {type(e).__name__}: {e}. "
              f"The next cycle will retry.")


def _start_missing_watermarks(config: dict) -> bool:
    """Give every kind without a watermark its starting point: past what
    `/daily_reports` already posted, or else past the newest row, so it never
    dumps the history. Returns whether anything was set.

    The daily post carried reports and comments until 2026-10-07 under the same
    key names, so its watermarks are exactly "already posted". It never carried
    crashes, so those always start at the newest row. A table that cannot be
    read leaves its watermark unset, to be tried again next cycle -- starting
    it at 0 would post the whole history.
    """
    daily = _read_json_state(daily_reports.STATE_FILE).get("channels", {})
    changed = False
    for watermark, table, *_ in KINDS:
        if watermark in config:
            continue
        posted = [int(c.get(watermark) or 0) for c in daily.values() if isinstance(c, dict)]
        if watermark != "last_crash_id" and any(posted):
            config[watermark] = max(posted)
            changed = True
            continue
        try:
            config[watermark] = _fetch_latest_id(table)
            changed = True
        except (SupabaseQueryError, SupabaseUnreadableError) as e:
            print(f"Live reports: could not find the newest row in `{table}` ({e}); "
                  f"`{watermark}` will be started next cycle")
    return changed


# ---------------------------------------------------------------------------
# Commands and background task
# ---------------------------------------------------------------------------

def add_live_reports_commands(cls):

    @nextcord.slash_command(name="live_reports",
                            description="Toggle posting new reports, survey comments and crashes every 10 minutes",
                            guild_ids=[DEV_GUILD_ID])
    @safe_interaction(timeout=30, error_message="Failed to update live reports setting.", require_authorized=True)
    async def live_reports_cmd(
        self,
        interaction: Interaction,
        enabled: bool = SlashOption(description="Enable or disable live posts in this channel", required=True),
    ):
        channel_id = str(interaction.channel_id)

        async with _SEND_LOCK:
            state = _load_state()
            config = state["channels"].get(channel_id)
            config = config if isinstance(config, dict) else {}

            if not enabled:
                # Keep the watermarks, so re-enabling picks up where it stopped
                # instead of re-posting.
                config["disabled"] = True
                state["channels"][channel_id] = config
                _save_state(state)
                return "Live reports **disabled** for this channel."

            config.pop("disabled", None)
            state["channels"][channel_id] = config
            _save_state(state)

            posted = await _post_channel(self.bot, state, channel_id, config)

        detail = (f" Posted {posted} waiting now." if posted else "")
        return (f"Live reports **enabled** for this channel. New player reports, survey comments "
                f"and crashes (not developers') will be posted every {INTERVAL_MINUTES} minutes.{detail}")

    # See _post_all_channels: the body swallows everything, because an exception
    # reaching tasks.Loop stops it for good.
    @tasks.loop(minutes=INTERVAL_MINUTES)
    async def live_reports_task(self):
        await _post_all_channels(self.bot, "loop")

    async def _check_live_reports_on_start(self):
        await self.bot.wait_until_ready()
        await _post_all_channels(self.bot, "startup")

    original_init = cls.__init__

    def new_init(self, bot):
        original_init(self, bot)
        self._live_reports_task = live_reports_task
        self._live_reports_task.start(self)
        bot.loop.create_task(_check_live_reports_on_start(self))

    cls.__init__ = new_init

    cls.live_reports_cmd = live_reports_cmd
    cls._live_reports_task_func = live_reports_task
    cls._check_live_reports_on_start = _check_live_reports_on_start
