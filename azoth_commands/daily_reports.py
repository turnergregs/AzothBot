"""/daily_reports -- post the day's survey answers to a channel once a day.

The in-game one-click survey writes `survey_responses` (the game repo's
docs/SURVEYS.md). Once a day this posts one image
(stats_cards.survey_answers_card): every survey shown since the last post,
everyone included, a line per question.

Until 2026-10-07 this was the daily FEEDBACK post, and also carried the new
player reports and survey comments, ten a day each. Those moved to
live_reports.py, which posts bug reports and survey comments every 10 minutes.
The old watermarks `last_report_id` / `last_survey_id` stay in the state file:
nothing here reads them any more, but `/live_reports` starts a new channel from
them, so the switch neither repeats nor drops anything.

Two pieces of per-channel state, and they fail in opposite directions on
purpose:

  - `last_sent_date` is claimed BEFORE sending, exactly as in daily_update.py,
    so a failed or partial send can never re-fire every 10 minutes.
  - `last_survey_tally_id` advances only AFTER the image is out, and covers
    every row the image counted. A failed send therefore counts those surveys
    in tomorrow's image instead of losing them; so does a day the bot was down.

The schedule (send time, UTC offset, CST day boundary) is shared with
daily_update.py; the state file is not, so the two can be pointed at different
channels and toggled independently.
"""
import asyncio
import io
import json
import os
import traceback

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
from azoth_logic import stats_cards
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
#       "last_survey_tally_id": 41
#     }
#   }
# }
STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "daily_reports_state.json")

SURVEY_EMBED_COLOR = 0x9B59B6

SURVEY_TABLE = "survey_responses"
SURVEY_TALLY_COLUMNS = ["id", "question_id", "moment", "outcome", "answer",
                        "run_number", "has_comment"]
# Far past a real day's surveys. A backlog past it is counted in the next post.
SURVEY_TALLY_LIMIT = 5000

# The loop and the startup catch-up both run as soon as the bot is ready, each
# with its own copy of the state. A send is an await, so without this the second
# pass can load the state while the first is mid-send and post the same image
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

def _fetch_survey_tally_rows(after_id: int) -> list[dict]:
    """Every survey shown with id > after_id, oldest first, up to
    SURVEY_TALLY_LIMIT.

    Raises on failure rather than returning [] -- an empty list here means
    "nothing to send" and would claim the day.
    """
    _assert_readable(SURVEY_TABLE)
    try:
        response = (
            supabase.table(SURVEY_TABLE)
            .select(",".join(SURVEY_TALLY_COLUMNS))
            .gt("id", after_id)
            .order("id")
            .limit(SURVEY_TALLY_LIMIT)
            .execute()
        )
    except Exception as e:
        raise SupabaseQueryError(f"select on `{SURVEY_TABLE}` failed: {e}") from e
    return response.data or []


# ---------------------------------------------------------------------------
# Sending helper
# ---------------------------------------------------------------------------

async def _claim_and_send(bot, state: dict, channel_id: str, config: dict, today: str) -> int | None:
    """Post the survey answers since the last post. Caller must hold _SEND_LOCK.

    Returns the number of surveys counted (0 when there were none -- the day is
    still claimed, so a quiet day is checked once, not every 10 minutes), or
    None when the channel could not be resolved (nothing claimed, retried next
    cycle). Raises on a data error BEFORE claiming, so that day stays retryable.
    """
    channel = bot.get_channel(int(channel_id))
    if not channel:
        print(f"Daily reports: channel {channel_id} not found; will retry next cycle")
        return None

    tally_after_id = int(config.get("last_survey_tally_id") or 0)
    tally_rows = _fetch_survey_tally_rows(tally_after_id)

    # Drawn before the day is claimed, so a drawing failure never uses up the day.
    message = None
    card = stats_cards.survey_answers_card(stats_cards.survey_tally(tally_rows))
    if card is not None:
        data = await asyncio.to_thread(card.png)
        embed = nextcord.Embed(color=SURVEY_EMBED_COLOR)
        embed.set_image(url="attachment://survey_answers.png")
        message = {"embed": embed,
                   "file": nextcord.File(io.BytesIO(data), filename="survey_answers.png")}

    # Claim the day before sending anything (see the module docstring).
    config["last_sent_date"] = today
    state["channels"][channel_id] = config
    _save_state(state)

    if message is None:
        return 0
    try:
        await channel.send(**message, allowed_mentions=nextcord.AllowedMentions.none())
    except Exception as e:
        print(f"Daily reports send FAILED for channel {channel_id} after claiming {today}; "
              f"surveys after #{tally_after_id} will be counted in the next update: {e}")
        return 0

    config["last_survey_tally_id"] = max(tally_after_id, max(r["id"] for r in tally_rows))
    _save_state(state)
    print(f"Daily reports: posted {len(tally_rows)} surveys shown, as one image, to channel {channel_id}")
    return len(tally_rows)


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

    @nextcord.slash_command(name="daily_reports", description="Toggle the daily post of survey answers",
                            guild_ids=[DEV_GUILD_ID])
    @safe_interaction(timeout=30, error_message="Failed to update daily reports setting.", require_authorized=True)
    async def daily_reports_cmd(
        self,
        interaction: Interaction,
        enabled: bool = SlashOption(description="Enable or disable the daily survey answers post", required=True),
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
                # re-counts old surveys nor sends twice in one day.
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
            # A new channel starts at id 0, i.e. its first image counts every
            # survey ever shown. An existing one keeps its watermark.
            config.setdefault("last_survey_tally_id", 0)
            config["send_hour_utc"] = hour_utc
            config["send_minute_utc"] = minute_utc
            config.pop("disabled", None)
            state["channels"][channel_id] = config
            _save_state(state)

            today = _today_cst_str()
            if config.get("last_sent_date") != today and _is_past_send_time_utc(hour_utc, minute_utc):
                posted = await _claim_and_send(self.bot, state, channel_id, config, today)
                if posted is not None:
                    detail = (f"Posted {posted} survey(s) shown now." if posted
                              else "No new surveys right now.")
                    return f"Daily reports **enabled** for this channel. {detail}"

        local_time = _format_utc_to_local(hour_utc, minute_utc, utc_offset)
        return (f"Daily reports **enabled** for this channel. Survey answers will be posted daily "
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
