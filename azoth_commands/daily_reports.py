"""/daily_reports -- post new player-submitted reports to a channel once a day.

Players file bug reports, feature requests, content ideas and accessibility
feedback from inside the game (`report_popup.gd` in the game repo). They land
in `reports`, where nobody sees them unless they go looking. This posts up to
REPORT_LIMIT unsent reports per channel per day, oldest first, as a short
summary per report in ONE embed, and says how many are still waiting.

`report_type = 'crash'` is excluded: those are written automatically by
`ReportManager` and there are far too many of them to post one by one.

Two pieces of per-channel state, and they fail in opposite directions on
purpose:

  - `last_sent_date` is claimed BEFORE sending, exactly as in daily_update.py,
    so a failed or partial send can never re-fire every 10 minutes.
  - `last_report_id` advances only AFTER the message is out, and only past
    the reports it actually carried. A failed send therefore retries those
    reports tomorrow instead of skipping them. The worst case is
    a report posted twice, which beats one never posted.

The schedule (send time, UTC offset, CST day boundary) is shared with
daily_update.py; the state file is not, so the two can be pointed at different
channels and toggled independently.
"""
import asyncio
import json
import os
import traceback
from datetime import datetime

import nextcord
from nextcord import Interaction, SlashOption
from nextcord.ext import tasks

from azoth_commands.daily_update import (
    _atomic_write_json,
    _format_utc_to_local,
    _is_past_send_time_utc,
    _parse_send_time,
    _today_cst_str,
)
from azoth_commands.helpers import safe_interaction
from constants import DEV_GUILD_ID
from supabase_client import supabase
from supabase_helpers import SupabaseQueryError, _assert_readable

# State file stores per-channel config:
# {
#   "channels": {
#     "<channel_id>": {
#       "send_hour_utc": 18,
#       "send_minute_utc": 0,
#       "last_sent_date": "2026-09-25",
#       "last_report_id": 41
#     }
#   }
# }
STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "daily_reports_state.json")

REPORT_LIMIT = 10          # reports posted per channel per day
SUMMARY_LIMIT = 200        # characters of the player's text, before escaping
META_LIMIT = 40            # category, player name, version, contact

# Discord's cap on one embed's total text. Ten summaries fit comfortably; ten
# worst-case ones (every character a markdown character, so escaping doubles
# it) do not, and the reports that don't fit wait for the next update.
EMBED_CHAR_LIMIT = 6000

REPORT_COLUMNS = ["id", "player_uuid", "report_type", "category", "description",
                  "contact_info", "game_version", "created_at"]

EMBED_COLOR = 0x3498DB

# The loop and the startup catch-up both run as soon as the bot is ready, each
# with its own copy of the state. A send is an await, so without this the second
# pass can load the state while the first is mid-send and post the same reports
# again. Held across load -> fetch -> claim -> send -> save.
_SEND_LOCK = asyncio.Lock()


def _load_state() -> dict:
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("channels", {})
    return data


def _save_state(state: dict):
    _atomic_write_json(STATE_FILE, state)


# ---------------------------------------------------------------------------
# Supabase data fetching
# ---------------------------------------------------------------------------

def _fetch_unsent_reports(after_id: int, limit: int = REPORT_LIMIT) -> tuple[list[dict], int]:
    """The oldest `limit` non-crash reports with id > after_id, and how many exist in total.

    Raises on failure rather than returning [] -- an empty list here means
    "nothing to send" and would claim the day.

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
            .neq("report_type", "crash")
            .order("id")
            .limit(limit)
            .execute()
        )
    except Exception as e:
        raise SupabaseQueryError(f"select on `reports` failed: {e}") from e
    rows = response.data or []
    total = response.count if response.count is not None else len(rows)
    return rows, total


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


def _summary_field(report: dict, player_names: dict[str, str]) -> tuple[str, str]:
    """(name, value) for one report's field.

    Name: `Feature Request #12`, plus the category when it says something the
    type doesn't (`Bug #7 · Vision`). Value: the player's text, then a line of
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

    meta = []
    player = player_names.get(report.get("player_uuid"))
    if player:
        meta.append(_clean(player, META_LIMIT))
    if report.get("game_version"):
        meta.append(f"v{_clean(report['game_version'], META_LIMIT)}")
    created = _parse_timestamp(report.get("created_at"))
    if created:
        meta.append(f"<t:{int(created.timestamp())}:d>")
    if report.get("contact_info") and str(report["contact_info"]).strip():
        meta.append(f"contact: {_clean(report['contact_info'], META_LIMIT)}")
    if meta:
        body += "\n" + " · ".join(meta)
    return name, body


def _build_reports_embed(reports: list[dict], total: int,
                         player_names: dict[str, str]) -> tuple[nextcord.Embed, int]:
    """One embed summarising `reports`, and the highest id it actually carries.

    Fields are added oldest first until the next would break EMBED_CHAR_LIMIT;
    the rest are left for the next update. The returned id is how far the
    watermark may move once this is sent -- never past a report left out.
    """
    embed = nextcord.Embed(color=EMBED_COLOR)
    fields = [_summary_field(r, player_names) for r in reports]

    # The title and footer depend on how many fit, so size them for the worst
    # case (as long as they can get) before choosing.
    reserve = len(_title(len(reports))) + len(_footer(len(reports), total) or "") + 10
    used, shown = reserve, 0
    for name, value in fields:
        if used + len(name) + len(value) > EMBED_CHAR_LIMIT:
            break
        embed.add_field(name=name, value=value, inline=False)
        used += len(name) + len(value)
        shown += 1

    embed.title = _title(shown)
    footer = _footer(shown, total)
    if footer:
        embed.set_footer(text=footer)
    max_id = max(r["id"] for r in reports[:shown]) if shown else None
    return embed, max_id


def _title(shown: int) -> str:
    return f"{shown} new player report" + ("" if shown == 1 else "s")


def _footer(shown: int, total: int):
    waiting = total - shown
    if waiting <= 0:
        return None
    return f"{waiting} more waiting -- they'll follow in the next updates"


# ---------------------------------------------------------------------------
# Sending helper
# ---------------------------------------------------------------------------

async def _claim_and_send(bot, state: dict, channel_id: str, config: dict, today: str) -> int | None:
    """Post this channel's unsent reports. Caller must hold _SEND_LOCK.

    Returns the number of reports posted (0 when there were none -- the day is
    still claimed, so a quiet day is checked once, not every 10 minutes), or
    None when the channel could not be resolved (nothing claimed, retried next
    cycle). Raises on a data error BEFORE claiming, so that day stays retryable.
    """
    channel = bot.get_channel(int(channel_id))
    if not channel:
        print(f"Daily reports: channel {channel_id} not found; will retry next cycle")
        return None

    after_id = int(config.get("last_report_id") or 0)
    reports, total = _fetch_unsent_reports(after_id)
    if reports:
        names = _fetch_player_names([r.get("player_uuid") for r in reports])
        embed, max_id = _build_reports_embed(reports, total, names)

    # Claim the day before sending anything (see the module docstring).
    config["last_sent_date"] = today
    state["channels"][channel_id] = config
    _save_state(state)

    if not reports:
        return 0

    try:
        await channel.send(embed=embed, allowed_mentions=nextcord.AllowedMentions.none())
    except Exception as e:
        print(f"Daily reports send FAILED for channel {channel_id} after claiming {today}; "
              f"reports after #{after_id} will be retried with the next update: {e}")
        return 0

    # Advance past exactly what the message carried -- not past reports that
    # did not fit in it.
    config["last_report_id"] = max(after_id, max_id)
    _save_state(state)
    posted = len(embed.fields)
    print(f"Daily reports: posted {posted} of {total} unsent to channel {channel_id}")
    return posted


async def _send_due_channels(bot, source: str) -> None:
    """Post to every channel whose send time has passed today.

    ⚠️ NOTHING may propagate out of here -- an exception reaching tasks.Loop
    stops it for the life of the process. Same contract, and same reasons, as
    daily_update._send_due_channels.
    """
    try:
        async with _SEND_LOCK:
            state = _load_state()
            today = _today_cst_str()
            for channel_id, config in list(state["channels"].items()):
                try:
                    if not isinstance(config, dict):
                        print(f"Daily reports [{source}]: channel {channel_id} has a "
                              f"malformed state entry ({type(config).__name__}); skipping")
                        continue
                    if config.get("disabled") or config.get("last_sent_date") == today:
                        continue
                    if not _is_past_send_time_utc(config.get("send_hour_utc", 18),
                                                  config.get("send_minute_utc", 0)):
                        continue
                    await _claim_and_send(bot, state, channel_id, config, today)
                except Exception as e:
                    traceback.print_exc()
                    print(f"Daily reports [{source}]: channel {channel_id} failed with "
                          f"{type(e).__name__}: {e}. The day was NOT claimed; the next "
                          f"cycle will retry it.")
    except Exception as e:
        traceback.print_exc()
        print(f"Daily reports [{source}]: cycle aborted with {type(e).__name__}: {e}. "
              f"The schedule is intact; the next cycle will retry.")


# ---------------------------------------------------------------------------
# Commands and background task
# ---------------------------------------------------------------------------

def add_daily_reports_commands(cls):

    @nextcord.slash_command(name="daily_reports", description="Toggle daily posts of new player reports",
                            guild_ids=[DEV_GUILD_ID])
    @safe_interaction(timeout=30, error_message="Failed to update daily reports setting.", require_authorized=True)
    async def daily_reports_cmd(
        self,
        interaction: Interaction,
        enabled: bool = SlashOption(description="Enable or disable daily report posts", required=True),
        send_time: str = SlashOption(
            description="Time to post (HH:MM), default 12:00",
            required=False,
            default="12:00",
        ),
        utc_offset: int = SlashOption(
            description="Your UTC offset (e.g. -6 for CST, +8 for China), default -6",
            required=False,
            default=-6,
            min_value=-12,
            max_value=14,
        ),
    ):
        channel_id = str(interaction.channel_id)

        if not enabled:
            async with _SEND_LOCK:
                state = _load_state()
                config = state["channels"].get(channel_id)
                config = config if isinstance(config, dict) else {}
                # Keep the watermark and the claim, so re-enabling neither
                # re-posts old reports nor sends twice in one day.
                config["disabled"] = True
                state["channels"][channel_id] = config
                _save_state(state)
            return "Daily reports **disabled** for this channel."

        try:
            hour_utc, minute_utc = _parse_send_time(send_time, utc_offset)
        except (ValueError, IndexError):
            return "Invalid time format. Use HH:MM (e.g. 12:00, 14:30)."

        async with _SEND_LOCK:
            state = _load_state()
            config = state["channels"].get(channel_id)
            config = config if isinstance(config, dict) else {}
            # A new channel starts at id 0, i.e. it works through the whole
            # history REPORT_LIMIT a day. An existing one keeps its watermark.
            config.setdefault("last_report_id", 0)
            config["send_hour_utc"] = hour_utc
            config["send_minute_utc"] = minute_utc
            config.pop("disabled", None)
            state["channels"][channel_id] = config
            _save_state(state)

            today = _today_cst_str()
            if config.get("last_sent_date") != today and _is_past_send_time_utc(hour_utc, minute_utc):
                posted = await _claim_and_send(self.bot, state, channel_id, config, today)
                if posted is not None:
                    detail = f"Posted {posted} report(s) now." if posted else "No unsent reports right now."
                    return f"Daily reports **enabled** for this channel. {detail}"

        local_time = _format_utc_to_local(hour_utc, minute_utc, utc_offset)
        return (f"Daily reports **enabled** for this channel. New reports will be posted daily "
                f"at {local_time} (UTC{utc_offset:+d}).")

    # See _send_due_channels: the body swallows everything, because an exception
    # reaching tasks.Loop stops it for good.
    @tasks.loop(minutes=10)
    async def daily_reports_task(self):
        await _send_due_channels(self.bot, "loop")

    async def _check_missed_reports(self):
        await self.bot.wait_until_ready()
        await _send_due_channels(self.bot, "startup")

    original_init = cls.__init__

    def new_init(self, bot):
        original_init(self, bot)
        self._daily_reports_task = daily_reports_task
        self._daily_reports_task.start(self)
        bot.loop.create_task(_check_missed_reports(self))

    cls.__init__ = new_init

    cls.daily_reports_cmd = daily_reports_cmd
    cls._daily_reports_task_func = daily_reports_task
    cls._check_missed_reports = _check_missed_reports
