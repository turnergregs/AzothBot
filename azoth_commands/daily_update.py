import json
import os
import nextcord
from datetime import datetime, time, timedelta, timezone
from nextcord import Interaction, SlashOption
from nextcord.ext import tasks
from azoth_commands.helpers import safe_interaction, AUTHORIZED_USER_IDS
from constants import DEV_GUILD_ID
from supabase_client import supabase

# State file stores per-channel config:
# {
#   "channels": {
#     "<channel_id>": {
#       "send_hour_utc": 18,
#       "send_minute_utc": 0,
#       "last_sent_date": "2026-03-19"
#     }
#   }
# }
STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "daily_update_state.json")

# Default send time: 12:00 PM CST = 18:00 UTC
DEFAULT_SEND_HOUR = 12
DEFAULT_UTC_OFFSET = -6


def _load_state() -> dict:
    try:
        with open(STATE_FILE, "r") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data.setdefault("channels", {})
    return data


def _save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)


def _yesterday_range_utc():
    """Return (start, end) ISO strings for yesterday in CST, converted to UTC."""
    now_utc = datetime.now(timezone.utc)
    cst = timezone(timedelta(hours=-6))
    now_cst = now_utc.astimezone(cst)
    yesterday_cst = now_cst.date() - timedelta(days=1)
    start = datetime(yesterday_cst.year, yesterday_cst.month, yesterday_cst.day, tzinfo=cst)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def _today_cst_str():
    cst = timezone(timedelta(hours=-6))
    return datetime.now(timezone.utc).astimezone(cst).strftime("%Y-%m-%d")


def _yesterday_cst_str():
    cst = timezone(timedelta(hours=-6))
    return (datetime.now(timezone.utc).astimezone(cst) - timedelta(days=1)).strftime("%Y-%m-%d")


def _is_past_send_time_utc(hour_utc: int, minute_utc: int) -> bool:
    """Check if current UTC time is past the given hour:minute."""
    now = datetime.now(timezone.utc).time()
    return now >= time(hour=hour_utc, minute=minute_utc)


def _parse_send_time(send_time: str, utc_offset: int) -> tuple[int, int]:
    """Parse a 'HH:MM' local time + UTC offset into (hour_utc, minute_utc)."""
    parts = send_time.strip().split(":")
    local_hour = int(parts[0])
    local_minute = int(parts[1]) if len(parts) > 1 else 0

    if not (0 <= local_hour <= 23 and 0 <= local_minute <= 59):
        raise ValueError("Time must be HH:MM with hour 0-23 and minute 0-59.")

    utc_hour = (local_hour - utc_offset) % 24
    return utc_hour, local_minute


def _format_utc_to_local(hour_utc: int, minute_utc: int, utc_offset: int) -> str:
    """Format a UTC hour:minute back to local time string for display."""
    local_hour = (hour_utc + utc_offset) % 24
    return f"{local_hour:02d}:{minute_utc:02d}"


# ---------------------------------------------------------------------------
# Supabase data fetching
# ---------------------------------------------------------------------------

def _resolve_item_names(items: list[dict]) -> dict[tuple[str, int], str]:
    """Given draft_items rows, resolve (item_type, item_id) -> display name."""
    grouped = {}
    for item in items:
        grouped.setdefault(item["item_type"], set()).add(item["item_id"])

    name_map = {}
    for item_type, ids in grouped.items():
        table = f"{item_type}s"
        name_col = "challenge_name" if item_type == "ritual" else "name"
        records = (
            supabase.table(table)
            .select(f"id, {name_col}")
            .in_("id", list(ids))
            .execute()
        ).data or []
        for r in records:
            name_map[(item_type, r["id"])] = r.get(name_col, f"Unknown {item_type}")

    return name_map


def _fetch_draft_stats(game_uuids: list[str], game_by_uuid: dict) -> dict:
    """Compute draft pick analytics for a set of games."""
    if not game_uuids:
        return {}

    # Fetch all drafts for these games (batch in chunks to avoid URL length issues)
    all_drafts = []
    for i in range(0, len(game_uuids), 50):
        chunk = game_uuids[i:i + 50]
        rows = (
            supabase.table("drafts")
            .select("uuid, game_uuid")
            .in_("game_uuid", chunk)
            .execute()
        ).data or []
        all_drafts.extend(rows)

    if not all_drafts:
        return {}

    draft_uuids = [d["uuid"] for d in all_drafts]
    draft_to_game = {d["uuid"]: d["game_uuid"] for d in all_drafts}

    # Fetch all draft items
    all_items = []
    for i in range(0, len(draft_uuids), 50):
        chunk = draft_uuids[i:i + 50]
        rows = (
            supabase.table("draft_items")
            .select("id, draft_uuid, item_type, item_id, picked")
            .in_("draft_uuid", chunk)
            .execute()
        ).data or []
        all_items.extend(rows)

    if not all_items:
        return {}

    # Resolve names
    name_map = _resolve_item_names(all_items)

    # Pick rate: how often each item was picked when offered
    offer_count = {}   # name -> times offered
    pick_count = {}    # name -> times picked
    for item in all_items:
        name = name_map.get((item["item_type"], item["item_id"]), f"{item['item_type']}#{item['item_id']}")
        offer_count[name] = offer_count.get(name, 0) + 1
        if item.get("picked"):
            pick_count[name] = pick_count.get(name, 0) + 1

    pick_rates = {}
    for name, offered in offer_count.items():
        picked = pick_count.get(name, 0)
        if offered >= 2:  # only include items offered at least twice for meaningful rates
            pick_rates[name] = {"picked": picked, "offered": offered, "rate": picked / offered}

    # Sort for most/least picked
    sorted_by_rate = sorted(pick_rates.items(), key=lambda x: (-x[1]["rate"], -x[1]["offered"]))
    most_picked = sorted_by_rate[:5]
    least_picked = sorted(
        [(n, s) for n, s in pick_rates.items() if s["offered"] >= 3],
        key=lambda x: (x[1]["rate"], -x[1]["offered"])
    )[:5]

    # Performance correlation: for each picked item, compute average game score
    # Score = level_reached + highest_combo (simple composite metric)
    item_game_scores = {}  # name -> list of scores
    for item in all_items:
        if not item.get("picked"):
            continue
        name = name_map.get((item["item_type"], item["item_id"]))
        if not name:
            continue
        game_uuid = draft_to_game.get(item["draft_uuid"])
        if not game_uuid:
            continue
        game = game_by_uuid.get(game_uuid)
        if not game:
            continue
        score = (game.get("level_reached") or 0) + (game.get("highest_combo") or 0)
        item_game_scores.setdefault(name, []).append(score)

    # Items that appear in at least 2 games for meaningful averages
    performance = {}
    for name, scores in item_game_scores.items():
        if len(scores) >= 2:
            performance[name] = {"avg_score": sum(scores) / len(scores), "games": len(scores)}

    top_performers = sorted(performance.items(), key=lambda x: -x[1]["avg_score"])[:5]

    return {
        "total_drafts": len(all_drafts),
        "total_picks": sum(pick_count.values()),
        "most_picked": most_picked,
        "least_picked": least_picked,
        "top_performers": top_performers,
    }


def _fetch_daily_stats():
    """Query supabase for yesterday's game activity stats."""
    start, end = _yesterday_range_utc()

    # Games finished yesterday
    games = (
        supabase.table("games")
        .select("id, uuid, player_uuid, level_reached, highest_combo, turns_played, elapsed_sec, result, act_reached")
        .gte("finished_at", start)
        .lt("finished_at", end)
        .execute()
    ).data or []

    # Players created yesterday (new players)
    new_players = (
        supabase.table("players")
        .select("id")
        .gte("created_at", start)
        .lt("created_at", end)
        .execute()
    ).data or []

    # Boss fights from yesterday
    boss_fights = (
        supabase.table("boss_fights")
        .select("id, result, damage_dealt, damage_received")
        .gte("created_at", start)
        .lt("created_at", end)
        .execute()
    ).data or []

    # Draft data for yesterday's games
    game_uuids = [g["uuid"] for g in games if g.get("uuid")]
    game_by_uuid = {g["uuid"]: g for g in games if g.get("uuid")}
    draft_stats = _fetch_draft_stats(game_uuids, game_by_uuid)

    total_games = len(games)
    unique_players = len({g["player_uuid"] for g in games})
    new_player_count = len(new_players)

    max_level = max((g.get("level_reached") or 0 for g in games), default=0)
    max_combo = max((g.get("highest_combo") or 0 for g in games), default=0)
    max_act = max((g.get("act_reached") or 0 for g in games), default=0)

    durations = [g["elapsed_sec"] for g in games if g.get("elapsed_sec")]
    avg_duration = sum(durations) / len(durations) if durations else 0
    total_playtime = sum(durations)

    turns = [g["turns_played"] for g in games if g.get("turns_played")]
    avg_turns = sum(turns) / len(turns) if turns else 0

    results = {}
    for g in games:
        r = g.get("result") or "unknown"
        results[r] = results.get(r, 0) + 1

    total_boss_fights = len(boss_fights)
    boss_wins = sum(1 for b in boss_fights if b.get("result") == "win")
    boss_losses = sum(1 for b in boss_fights if b.get("result") == "loss")

    return {
        "total_games": total_games,
        "unique_players": unique_players,
        "new_players": new_player_count,
        "max_level": max_level,
        "max_combo": max_combo,
        "max_act": max_act,
        "avg_duration_sec": avg_duration,
        "total_playtime_sec": total_playtime,
        "avg_turns": avg_turns,
        "game_results": results,
        "total_boss_fights": total_boss_fights,
        "boss_wins": boss_wins,
        "boss_losses": boss_losses,
        "draft": draft_stats,
    }


# ---------------------------------------------------------------------------
# Embed building
# ---------------------------------------------------------------------------

def _format_duration(seconds):
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


def _embed_char_count(embed: nextcord.Embed) -> int:
    """Calculate total character count of an embed (Discord limit: 6000)."""
    total = len(embed.title or "")
    total += len(embed.description or "")
    for field in embed.fields:
        total += len(field.name or "")
        total += len(field.value or "")
    if embed.footer:
        total += len(embed.footer.text or "")
    if embed.author:
        total += len(embed.author.name or "")
    return total


def _build_update_embeds(stats: dict) -> list[nextcord.Embed]:
    """Build one or more embeds for the daily report, splitting if needed."""
    yesterday = _yesterday_cst_str()
    color = 0x7B2D8E

    if stats["total_games"] == 0:
        embed = nextcord.Embed(
            title=f"Daily Activity Report — {yesterday}",
            description="No games were played yesterday.",
            color=color,
        )
        return [embed]

    # Collect all fields as (name, value, inline) tuples
    fields = []

    # Player activity
    player_lines = [
        f"**{stats['unique_players']}** unique players",
        f"**{stats['new_players']}** new players",
        f"**{stats['total_games']}** games played",
    ]
    fields.append(("Players & Games", "\n".join(player_lines), False))

    # Game highlights
    highlight_lines = [
        f"Highest level reached: **{stats['max_level']}**",
        f"Highest act reached: **{stats['max_act']}**",
        f"Highest combo: **{stats['max_combo']}**",
    ]
    fields.append(("Highlights", "\n".join(highlight_lines), False))

    # Time stats
    time_lines = [
        f"Avg game duration: **{_format_duration(stats['avg_duration_sec'])}**",
        f"Avg turns per game: **{stats['avg_turns']:.1f}**",
        f"Total playtime: **{_format_duration(stats['total_playtime_sec'])}**",
    ]
    fields.append(("Session Stats", "\n".join(time_lines), False))

    # Game results breakdown
    if stats["game_results"]:
        result_lines = [f"{k}: **{v}**" for k, v in sorted(stats["game_results"].items())]
        fields.append(("Game Results", "\n".join(result_lines), True))

    # Boss fights
    if stats["total_boss_fights"] > 0:
        boss_lines = [
            f"**{stats['total_boss_fights']}** total fights",
            f"**{stats['boss_wins']}** wins / **{stats['boss_losses']}** losses",
        ]
        fields.append(("Boss Fights", "\n".join(boss_lines), True))

    # Draft analytics
    draft = stats.get("draft")
    if draft and draft.get("total_drafts"):
        draft_summary = f"**{draft['total_drafts']}** drafts, **{draft['total_picks']}** cards picked"
        fields.append(("Draft Activity", draft_summary, False))

        if draft.get("most_picked"):
            lines = []
            for name, s in draft["most_picked"]:
                pct = s["rate"] * 100
                lines.append(f"**{name}** — {pct:.0f}% ({s['picked']}/{s['offered']})")
            fields.append(("Most Drafted", "\n".join(lines), True))

        if draft.get("least_picked"):
            lines = []
            for name, s in draft["least_picked"]:
                pct = s["rate"] * 100
                lines.append(f"**{name}** — {pct:.0f}% ({s['picked']}/{s['offered']})")
            fields.append(("Least Drafted", "\n".join(lines), True))

        if draft.get("top_performers"):
            lines = []
            for name, s in draft["top_performers"]:
                lines.append(f"**{name}** — avg score {s['avg_score']:.1f} ({s['games']} games)")
            fields.append(("Top Performing Picks", "\n".join(lines), False))

    # Pack fields into embeds, splitting at 5800 chars (buffer under 6000 limit)
    MAX_EMBED_CHARS = 5800
    MAX_FIELD_CHARS = 1024
    embeds = []
    current = nextcord.Embed(
        title=f"Daily Activity Report — {yesterday}",
        color=color,
    )

    for name, value, inline in fields:
        # Truncate field value if it exceeds Discord's 1024 char field limit
        if len(value) > MAX_FIELD_CHARS:
            value = value[:MAX_FIELD_CHARS - 4] + "\n..."

        field_size = len(name) + len(value)
        current_size = _embed_char_count(current)

        if current_size + field_size > MAX_EMBED_CHARS and current.fields:
            # Current embed is full, start a new one
            embeds.append(current)
            current = nextcord.Embed(
                title=f"Daily Activity Report — {yesterday} (cont.)",
                color=color,
            )

        current.add_field(name=name, value=value, inline=inline)

    embeds.append(current)
    return embeds


# ---------------------------------------------------------------------------
# Sending helper
# ---------------------------------------------------------------------------

async def _send_update_to_channel(bot, channel_id: int) -> bool:
    """Fetch stats, build embeds, and send to a channel. Returns True on success."""
    channel = bot.get_channel(channel_id)
    if not channel:
        print(f"Daily update: channel {channel_id} not found")
        return False

    stats = _fetch_daily_stats()
    embeds = _build_update_embeds(stats)
    for embed in embeds:
        await channel.send(embed=embed)
    return True


# ---------------------------------------------------------------------------
# Commands and background task
# ---------------------------------------------------------------------------

def add_daily_update_commands(cls):

    @nextcord.slash_command(name="daily_update", description="Toggle daily activity reports", guild_ids=[DEV_GUILD_ID])
    @safe_interaction(timeout=30, error_message="Failed to update daily report setting.", require_authorized=True)
    async def daily_update_cmd(
        self,
        interaction: Interaction,
        enabled: bool = SlashOption(description="Enable or disable daily updates", required=True),
        send_time: str = SlashOption(
            description="Time to send the update (HH:MM), default 12:00",
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
        state = _load_state()

        if enabled:
            # Parse and validate time
            try:
                hour_utc, minute_utc = _parse_send_time(send_time, utc_offset)
            except (ValueError, IndexError):
                return "Invalid time format. Use HH:MM (e.g. 12:00, 14:30)."

            # Register this channel (preserve last_sent_date if re-enabling)
            channel_config = state["channels"].get(channel_id, {})
            channel_config["send_hour_utc"] = hour_utc
            channel_config["send_minute_utc"] = minute_utc
            channel_config.pop("disabled", None)
            state["channels"][channel_id] = channel_config
            _save_state(state)

            # Check if we missed today's update for this channel
            today = _today_cst_str()
            already_sent = channel_config.get("last_sent_date") == today

            if not already_sent and _is_past_send_time_utc(hour_utc, minute_utc):
                try:
                    await _send_update_to_channel(self.bot, interaction.channel_id)
                    state["channels"][channel_id]["last_sent_date"] = today
                    _save_state(state)
                    local_time = _format_utc_to_local(hour_utc, minute_utc, utc_offset)
                    return f"Daily updates **enabled** for this channel. Sent missed update for {_yesterday_cst_str()}."
                except Exception as e:
                    return f"Daily updates **enabled**, but failed to send catch-up update: {e}"

            local_time = _format_utc_to_local(hour_utc, minute_utc, utc_offset)
            return f"Daily updates **enabled** for this channel. Reports will be sent daily at {local_time} (UTC{utc_offset:+d})."
        else:
            # Mark channel as disabled but preserve last_sent_date to prevent
            # re-sending if toggled back on the same day
            config = state["channels"].get(channel_id, {})
            state["channels"][channel_id] = {
                "disabled": True,
                "last_sent_date": config.get("last_sent_date"),
            }
            _save_state(state)
            return "Daily updates **disabled** for this channel."

    # Background task — runs every 10 minutes to check all registered channels
    @tasks.loop(minutes=10)
    async def daily_update_task(self):
        state = _load_state()
        if not state["channels"]:
            return

        today = _today_cst_str()
        changed = False

        for channel_id, config in list(state["channels"].items()):
            # Skip disabled channels
            if config.get("disabled"):
                continue

            # Skip if already sent today
            if config.get("last_sent_date") == today:
                continue

            # Skip if not past this channel's send time
            hour_utc = config.get("send_hour_utc", 18)
            minute_utc = config.get("send_minute_utc", 0)
            if not _is_past_send_time_utc(hour_utc, minute_utc):
                continue

            try:
                success = await _send_update_to_channel(self.bot, int(channel_id))
                if success:
                    config["last_sent_date"] = today
                    changed = True
                    print(f"Daily update sent to channel {channel_id} for {_yesterday_cst_str()}")
            except Exception as e:
                print(f"Daily update failed for channel {channel_id}, will retry: {e}")

        if changed:
            _save_state(state)

    # Startup check for missed updates across all channels
    async def _check_missed_updates(self):
        await self.bot.wait_until_ready()
        state = _load_state()
        if not state["channels"]:
            return

        today = _today_cst_str()
        changed = False

        for channel_id, config in list(state["channels"].items()):
            if config.get("disabled"):
                continue

            if config.get("last_sent_date") == today:
                continue

            hour_utc = config.get("send_hour_utc", 18)
            minute_utc = config.get("send_minute_utc", 0)
            if not _is_past_send_time_utc(hour_utc, minute_utc):
                continue

            try:
                success = await _send_update_to_channel(self.bot, int(channel_id))
                if success:
                    config["last_sent_date"] = today
                    changed = True
                    print(f"Daily update (startup catch-up) sent to channel {channel_id}")
            except Exception as e:
                print(f"Daily update startup catch-up failed for channel {channel_id}: {e}")

        if changed:
            _save_state(state)

    # Override cog init to start the task
    original_init = cls.__init__

    def new_init(self, bot):
        original_init(self, bot)
        self._daily_update_task = daily_update_task
        self._daily_update_task.start(self)
        bot.loop.create_task(_check_missed_updates(self))

    cls.__init__ = new_init

    cls.daily_update_cmd = daily_update_cmd
    cls._daily_update_task_func = daily_update_task
    cls._check_missed_updates = _check_missed_updates
