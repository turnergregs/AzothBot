import json
import math
import os
import tempfile
import traceback
import nextcord
from datetime import datetime, time, timedelta, timezone
from nextcord import Interaction, SlashOption
from nextcord.ext import tasks
from azoth_commands.helpers import safe_interaction, AUTHORIZED_USER_IDS
from constants import DEV_GUILD_ID
from supabase_client import supabase, SUPABASE_ROLE

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
    # Atomic write: dump to a temp file in the same dir, then os.replace() (atomic
    # on the same filesystem). Prevents a crash mid-write from truncating/corrupting
    # the state file, which _load_state would otherwise silently reset to empty.
    dir_ = os.path.dirname(STATE_FILE) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_, prefix=".daily_update_state.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
        os.replace(tmp_path, STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


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

def _to_number(value, default=0):
    """Coerce a possibly-stringified numeric DB value to int/float.

    PostgREST returns large numeric columns (e.g. the BigNum-backed
    `highest_combo`, and sometimes `elapsed_sec` / `turns_played`) as strings,
    which breaks arithmetic like sum()/+/max(). Falls back to `default` for
    non-numeric values (e.g. a serialized BigNum object) so stats never crash.
    """
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default


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

    # Performance correlation: for each picked item, the typical combo of the
    # games it appeared in.
    #
    # This was `level_reached + highest_combo`, which added a linear quantity to
    # an exponential one -- combo is a BigNum growing exponentially, so it
    # swamped the level term completely and the "score" was combo with noise.
    # (Same class of error as the avg_combo the analytics views used to report;
    # see docs/ANALYTICS.md.)
    #
    # Now log10(combo), averaged in log space, i.e. a geometric mean. Read it as
    # an order of magnitude. Level is dropped rather than rescaled: the two
    # aren't commensurable and combining them needed a justification nobody had.
    #
    # This remains a CORRELATION, not an effect. An item's combo is mostly the
    # run it landed in, and good players draft differently -- it says "appeared
    # in high-combo games", never "causes high combos".
    item_game_scores = {}  # name -> list of log10(combo)
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
        combo = _to_number(game.get("highest_combo"))
        if combo <= 0:
            continue
        item_game_scores.setdefault(name, []).append(math.log10(combo))

    # Items that appear in at least 2 games for meaningful averages
    performance = {}
    for name, scores in item_game_scores.items():
        if len(scores) >= 2:
            performance[name] = {"avg_combo_log10": sum(scores) / len(scores), "games": len(scores)}

    top_performers = sorted(performance.items(), key=lambda x: -x[1]["avg_combo_log10"])[:5]

    return {
        "total_drafts": len(all_drafts),
        "total_picks": sum(pick_count.values()),
        "most_picked": most_picked,
        "least_picked": least_picked,
        "top_performers": top_performers,
    }


def _fetch_turn_grain_stats(solo_game_uuids: list[str]) -> dict:
    """Turn-grain aggregates for a set of games: boss outcomes, links per turn,
    and level-up reward pick rates.

    Returns a dict with an "error" key set when the data could not be read, so
    callers can say "unavailable" instead of printing a zero. That distinction
    matters here: `turns` is INSERT-only for anon, so PostgREST answers a blocked
    read with HTTP 200 and an empty array -- identical in shape to "nothing
    happened yesterday". Reporting 0 for an unreadable table is the exact bug
    that made the frozen `boss_fights` look fine for weeks.

    Correctness notes, all from docs/DB_SCHEMA.md:

    * Regular and boss turns are counted SEPARATELY. A boss fight IS one turn and
      runs until someone dies, so it holds many times the nodes of a regular turn
      -- pooling them makes both averages meaningless (caveat 8).
    * Turns with ZERO nodes are in the denominator. That is the whole reason the
      `turns` table exists; counting only turns that produced nodes reintroduces
      the bias it was built to remove.
    * A node is a link OR a skip, so "links played" and "nodes consumed" differ.
      Only `kind = 'link'` counts here.
    * Callers pass SOLO uuids only. Co-op records one row per participant, which
      would multiply every turn-grain row (caveat 9).
    * `result` is deliberately NOT filtered. An abandoned or restarted run's
      completed turns are perfectly good data; only its outcome is unknown.
    """
    if SUPABASE_ROLE != "service_role":
        return {"error": f"needs the service-role key (loaded key is `{SUPABASE_ROLE}`)"}
    if not solo_game_uuids:
        return {}

    try:
        turns = []
        for i in range(0, len(solo_game_uuids), 50):
            chunk = solo_game_uuids[i:i + 50]
            turns.extend((
                supabase.table("turns")
                .select("uuid, game_uuid, boss_id, boss_result")
                .in_("game_uuid", chunk)
                .execute()
            ).data or [])

        turn_uuids = [t["uuid"] for t in turns if t.get("uuid")]

        nodes = []
        for i in range(0, len(turn_uuids), 50):
            chunk = turn_uuids[i:i + 50]
            nodes.extend((
                supabase.table("turn_nodes")
                .select("turn_uuid, kind")
                .in_("turn_uuid", chunk)
                .execute()
            ).data or [])

        levelups = []
        for i in range(0, len(turn_uuids), 50):
            chunk = turn_uuids[i:i + 50]
            levelups.extend((
                supabase.table("levelups")
                .select("turn_uuid, options, chosen")
                .in_("turn_uuid", chunk)
                .execute()
            ).data or [])
    except Exception as e:
        return {"error": str(e)}

    # Seed every turn at zero so no-node turns stay in the denominator.
    links_by_turn = {t["uuid"]: 0 for t in turns if t.get("uuid")}
    for n in nodes:
        if n.get("kind") == "link" and n["turn_uuid"] in links_by_turn:
            links_by_turn[n["turn_uuid"]] += 1

    regular = [links_by_turn[t["uuid"]] for t in turns
               if t.get("uuid") and t.get("boss_id") is None]
    boss = [links_by_turn[t["uuid"]] for t in turns
            if t.get("uuid") and t.get("boss_id") is not None]

    boss_rows = [t for t in turns if t.get("boss_id") is not None]

    # Level-up rewards. `options` is the denominator -- raw pick counts are
    # uninterpretable without it, because common rewards are simply offered more.
    offered, taken = {}, {}
    for lu in levelups:
        for reward in (lu.get("options") or []):
            offered[reward] = offered.get(reward, 0) + 1
        for reward in (lu.get("chosen") or []):
            taken[reward] = taken.get(reward, 0) + 1

    reward_rates = {
        name: {"taken": taken.get(name, 0), "offered": count,
               "rate": taken.get(name, 0) / count}
        for name, count in offered.items() if count > 0
    }
    top_rewards = sorted(
        reward_rates.items(), key=lambda kv: (-kv[1]["rate"], -kv[1]["offered"])
    )[:5]

    return {
        "boss_turns": len(boss_rows),
        "boss_wins": sum(1 for t in boss_rows if t.get("boss_result") == "win"),
        "boss_losses": sum(1 for t in boss_rows if t.get("boss_result") == "loss"),
        "regular_turns": len(regular),
        "avg_links_regular": (sum(regular) / len(regular)) if regular else 0,
        "boss_turn_count": len(boss),
        "avg_links_boss": (sum(boss) / len(boss)) if boss else 0,
        "levelup_packs": len(levelups),
        "top_rewards": top_rewards,
    }


def _fetch_daily_stats():
    """Query supabase for yesterday's game activity stats."""
    start, end = _yesterday_range_utc()

    # Games finished yesterday
    games = (
        supabase.table("games")
        .select("id, uuid, player_uuid, level_reached, highest_combo, turns_played, elapsed_sec, result, act_reached, game_type, version")
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

    # Turn-grain data: boss outcomes, links per turn, level-up rewards.
    #
    # `boss_fights` was FROZEN 2026-08-25, so the old boss section here reported
    # zero every single day. Boss data now comes from `turns` -- a boss fight IS
    # one turn, flagged by turns.boss_id with the outcome in turns.boss_result.
    #
    # Solo uuids only: co-op records one row per participant and would multiply
    # every turn-grain row (docs/DB_SCHEMA.md caveat 9).
    solo_uuids = [g["uuid"] for g in games if g.get("uuid") and g.get("game_type") == "solo"]
    turn_grain = _fetch_turn_grain_stats(solo_uuids)

    # Draft data for yesterday's games
    game_uuids = [g["uuid"] for g in games if g.get("uuid")]
    game_by_uuid = {g["uuid"]: g for g in games if g.get("uuid")}
    draft_stats = _fetch_draft_stats(game_uuids, game_by_uuid)

    total_games = len(games)
    unique_players = len({g["player_uuid"] for g in games})
    new_player_count = len(new_players)

    # COUNTS stay inclusive -- a restart is still someone playing, and this is an
    # activity report. AVERAGES do not: a restart is usually a few seconds and
    # one turn, so pooling them drags every mean toward zero. Same reasoning for
    # co-op, which records one row per participant (docs/DB_SCHEMA.md caveat 9).
    # The embed labels which population each number describes.
    measured = [
        g for g in games
        if g.get("result") != "restart" and g.get("game_type") == "solo"
    ]
    restarts = sum(1 for g in games if g.get("result") == "restart")
    coop_rows = sum(1 for g in games if g.get("game_type") != "solo")

    max_level = max((_to_number(g.get("level_reached")) for g in measured), default=0)
    max_combo = max((_to_number(g.get("highest_combo")) for g in measured), default=0)
    max_act = max((_to_number(g.get("act_reached")) for g in measured), default=0)

    durations = [_to_number(g["elapsed_sec"]) for g in measured if g.get("elapsed_sec")]
    avg_duration = sum(durations) / len(durations) if durations else 0
    total_playtime = sum(_to_number(g.get("elapsed_sec")) for g in games)

    turns = [_to_number(g["turns_played"]) for g in measured if g.get("turns_played")]
    avg_turns = sum(turns) / len(turns) if turns else 0

    # NULL result on a 0.8.0+ row means the run was abandoned or is still in
    # progress -- it is real data, not a gap. "unknown" implied a defect.
    results = {}
    for g in games:
        r = g.get("result") or "abandoned / in progress"
        results[r] = results.get(r, 0) + 1

    boss_error = turn_grain.get("error")
    total_boss_fights = turn_grain.get("boss_turns", 0)
    boss_wins = turn_grain.get("boss_wins", 0)
    boss_losses = turn_grain.get("boss_losses", 0)

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
        "measured_games": len(measured),
        "restarts": restarts,
        "coop_rows": coop_rows,
        "boss_error": boss_error,
        "turn_grain": turn_grain,
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

    # Player activity. Counts are inclusive; restarts/co-op are called out so the
    # averages below can be read against the right denominator.
    player_lines = [
        f"**{stats['unique_players']}** unique players",
        f"**{stats['new_players']}** new players",
        f"**{stats['total_games']}** games started",
    ]
    if stats.get("restarts"):
        player_lines.append(f"— of which **{stats['restarts']}** were restarts")
    if stats.get("coop_rows"):
        player_lines.append(
            f"— **{stats['coop_rows']}** co-op rows (one per participant, not per session)"
        )
    fields.append(("Players & Games", "\n".join(player_lines), False))

    # Game highlights
    highlight_lines = [
        f"Highest level reached: **{stats['max_level']}**",
        f"Highest act reached: **{stats['max_act']}**",
        f"Highest combo: **{stats['max_combo']}**",
    ]
    fields.append(("Highlights", "\n".join(highlight_lines), False))

    # Time stats
    measured = stats.get("measured_games", stats["total_games"])
    tg = stats.get("turn_grain") or {}
    time_lines = [
        f"Avg game duration: **{_format_duration(stats['avg_duration_sec'])}**",
        f"Avg turns per game: **{stats['avg_turns']:.1f}**",
        f"Total playtime: **{_format_duration(stats['total_playtime_sec'])}**",
    ]
    # Links per turn, regular and boss kept apart -- a boss fight is one turn and
    # runs until someone dies, so pooling the two makes both numbers meaningless.
    # The turn count is shown because with a handful of runs the average is noise.
    if tg.get("error"):
        time_lines.append(f"Links per turn: *unavailable — {tg['error']}*")
    else:
        if tg.get("regular_turns"):
            time_lines.append(
                f"Avg links per regular turn: **{tg['avg_links_regular']:.1f}** "
                f"({tg['regular_turns']} turns)"
            )
        if tg.get("boss_turn_count"):
            time_lines.append(
                f"Avg links per boss turn: **{tg['avg_links_boss']:.1f}** "
                f"({tg['boss_turn_count']} fights)"
            )
    fields.append((
        f"Session Stats (averages over {measured} completed solo run"
        f"{'' if measured == 1 else 's'})",
        "\n".join(time_lines), False))

    # Game results breakdown
    if stats["game_results"]:
        result_lines = [f"{k}: **{v}**" for k, v in sorted(stats["game_results"].items())]
        fields.append(("Game Results", "\n".join(result_lines), True))

    # Boss fights, from turn-grain data (boss_fights was frozen 2026-08-25).
    if stats.get("boss_error"):
        fields.append((
            "Boss Fights",
            "⚠️ Unavailable — could not read `turns`. The turn-grain tables need "
            "the service-role key; see docs/DB_SCHEMA.md.",
            True))
    elif stats["total_boss_fights"] > 0:
        boss_lines = [
            f"**{stats['total_boss_fights']}** boss turns",
            f"**{stats['boss_wins']}** wins / **{stats['boss_losses']}** losses",
        ]
        fields.append(("Boss Fights", "\n".join(boss_lines), True))

    # Level-up rewards. `options` is the denominator: raw pick counts are
    # uninterpretable on their own, because common rewards get offered far more
    # often than rare ones and would top any list by volume alone.
    if tg.get("top_rewards"):
        lines = [
            f"**{name}** — {r['rate'] * 100:.0f}% ({r['taken']}/{r['offered']})"
            for name, r in tg["top_rewards"]
        ]
        fields.append((
            f"Most Picked Level-Up Rewards ({tg.get('levelup_packs', 0)} packs)",
            "\n".join(lines), False))

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
            # Correlation, not effect -- see _fetch_draft_stats.
            lines = []
            for name, s in draft["top_performers"]:
                lines.append(f"**{name}** — typical combo ~10^{s['avg_combo_log10']:.1f} ({s['games']} games)")
            fields.append(("Picks Seen in High-Combo Games", "\n".join(lines), False))

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

async def _claim_and_send(bot, state: dict, channel_id: str, config: dict, today: str) -> bool:
    """Send the daily report to a channel, claiming the day BEFORE sending.

    The dedup field (last_sent_date) is persisted *before* the first channel.send,
    so a partial or failed send can never cause the next loop/startup pass to
    re-send the report (the cause of the duplicate-message flood). Trade-off: a
    genuine send failure means that day's report is skipped rather than retried —
    a safe failure mode for a single-instance bot.

    Returns True if a send was attempted (channel was available), False if the
    channel could not be resolved (no claim made, safe to retry next cycle).
    """
    channel = bot.get_channel(int(channel_id))
    if not channel:
        print(f"Daily update: channel {channel_id} not found; will retry next cycle")
        return False

    # Build the report first so a data/build error doesn't consume the day's claim.
    #
    # ⚠️ _fetch_daily_stats() is SYNCHRONOUS, and that is load-bearing. It blocks
    # the event loop, so nothing can interleave between a caller's _load_state()
    # and the _save_state() below. That is what stops daily_update_task and
    # _check_missed_updates -- which both run at startup and each load their own
    # copy of the state -- from claiming the same day and double-sending.
    #
    # If this is ever made async (natural enough; it is a dozen blocking HTTP
    # calls), that window opens and the claim must be moved behind a real lock,
    # e.g. an asyncio.Lock held across load -> claim -> save.
    stats = _fetch_daily_stats()
    embeds = _build_update_embeds(stats)

    # Claim the day and persist it before sending anything.
    config["last_sent_date"] = today
    state["channels"][channel_id] = config
    _save_state(state)

    try:
        for embed in embeds:
            await channel.send(embed=embed)
        print(f"Daily update sent to channel {channel_id} for {_yesterday_cst_str()}")
    except Exception as e:
        print(
            f"Daily update send FAILED for channel {channel_id} after claiming {today}; "
            f"will NOT retry today to avoid duplicate spam: {e}"
        )
    return True


# ---------------------------------------------------------------------------
# The due-channel sweep
# ---------------------------------------------------------------------------

async def _send_due_channels(bot, source: str) -> None:
    """Send the report to every channel whose send time has passed today.

    Shared by the 10-minute loop and the startup catch-up pass, which were
    near-identical copies of this decision.

    ⚠️ NOTHING may propagate out of here, and that is load-bearing.

    `_claim_and_send` raises on a data or build error ON PURPOSE -- raising is
    what leaves the day unclaimed and therefore retryable next cycle
    (test_report_error_does_not_consume_the_day). But nextcord's `tasks.Loop`
    only tolerates the five connection-ish types in its `_valid_exception`
    tuple; anything else is printed to stderr and **re-raised**, which ends the
    loop for the life of the process. So an escaping error did not postpone one
    report -- it silently stopped every future one, because the retry the
    unclaimed day was waiting for no longer existed.

    Caught per channel as well as per cycle, so one unreachable or misconfigured
    channel cannot take the others down with it.

    `asyncio.CancelledError` derives from BaseException, so shutdown still
    cancels this cleanly.
    """
    try:
        state = _load_state()
        channels = state.get("channels") or {}
        if not channels:
            return

        today = _today_cst_str()

        for channel_id, config in list(channels.items()):
            try:
                # A hand-edited or partially-written state file can hold
                # something that is not a dict; .get() on it would be an
                # AttributeError that used to kill the loop.
                if not isinstance(config, dict):
                    print(f"Daily update [{source}]: channel {channel_id} has a "
                          f"malformed state entry ({type(config).__name__}); skipping")
                    continue

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

                # Claims today (persisted) before sending, so a failed/partial
                # send can never re-fire on the next cycle.
                await _claim_and_send(bot, state, channel_id, config, today)
            except Exception as e:
                traceback.print_exc()
                print(f"Daily update [{source}]: channel {channel_id} failed with "
                      f"{type(e).__name__}: {e}. The day was NOT claimed; the next "
                      f"cycle will retry it.")
    except Exception as e:
        traceback.print_exc()
        print(f"Daily update [{source}]: cycle aborted with {type(e).__name__}: {e}. "
              f"The schedule is intact; the next cycle will retry.")


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
                attempted = await _claim_and_send(self.bot, state, channel_id, channel_config, today)
                if attempted:
                    return f"Daily updates **enabled** for this channel. Sent catch-up update for {_yesterday_cst_str()}."

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

    # Background task — runs every 10 minutes to check all registered channels.
    #
    # The body is _send_due_channels, which swallows everything: an exception
    # reaching tasks.Loop does not skip one report, it stops the loop for the
    # life of the process. See the warning there.
    @tasks.loop(minutes=10)
    async def daily_update_task(self):
        await _send_due_channels(self.bot, "loop")

    # Startup check for missed updates across all channels
    async def _check_missed_updates(self):
        await self.bot.wait_until_ready()
        await _send_due_channels(self.bot, "startup")

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
