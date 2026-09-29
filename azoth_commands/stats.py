import asyncio
import io
import os
import json
import nextcord
import aiohttp
import nextcord
from nextcord.ext import commands
from nextcord import SlashOption, Interaction
from azoth_commands.helpers import safe_interaction, record_to_json
from azoth_commands.autocomplete import autocomplete_from_table
from constants import DEV_GUILD_ID
from supabase_helpers import fetch_all, SupabaseError
from azoth_logic import stats_format as sf
from azoth_logic import stats_cards


# Every report /stats all runs, in order, with the options it runs them with.
# `None` is passed explicitly for optional filters: a callback invoked directly
# receives its SlashOption DEFAULT OBJECT for anything left out, not the
# default value. test_command_registration checks that every /stats report is
# listed here, so a new one cannot be missed.
ALL_REPORTS = [
    ("players", "stats_players", {"players": "new"}),
    ("leaderboard", "stats_leaderboard", {"limit": 10, "hero": None, "players": "all"}),
    ("player", "stats_player", {}),     # player filled in at run time
    ("breakdown by:hero", "stats_breakdown", {"by": "hero", "players": "new"}),
    ("breakdown by:ritual", "stats_breakdown", {"by": "ritual", "players": "new"}),
    ("breakdown by:version", "stats_breakdown", {"by": "version", "players": "new"}),
    ("bosses", "stats_bosses", {"players": "new"}),
    ("item", "stats_item", {"by": "version", "players": "new"}),   # item filled in at run time
    ("scoreboard", "stats_scoreboard", {}),
    ("draft picks", "stats_draft_picks", {"players": "new"}),
    ("draft items", "stats_draft_items", {"players": "new"}),
    ("draft pool", "stats_draft_pool", {}),
]


class _Deferred:
    """`interaction.response` for a report run inside /stats all.

    Every report's safe_interaction defers first, and an interaction can only
    be deferred once -- /stats all already has. The second defer is the only
    call that needs absorbing."""
    async def defer(self, *args, **kwargs):
        pass


class _Followup:
    """`interaction.followup`, noting which reports replied with text.

    A report answers with an embed when it works. Text is an error, a "not
    migrated", or "no data" -- exactly what a preview run is looking for, so
    they are collected for the summary at the end."""
    def __init__(self, followup):
        self._followup = followup
        self.label = None
        self.text_replies = []

    async def send(self, content=None, **kwargs):
        if content and not kwargs.get("embed"):
            self.text_replies.append(self.label)
            content = f"**{self.label}:** {content}"
        return await self._followup.send(content, **kwargs)


class _Preview:
    """An interaction that hands every report the real channel, once deferred."""
    def __init__(self, interaction):
        self._interaction = interaction
        self.response = _Deferred()
        self.followup = _Followup(interaction.followup)

    def __getattr__(self, name):
        return getattr(self._interaction, name)


# /stats item looks up bosses and heroes beside the content index's cards,
# aspects and rites. Those two have `archived_at`, so live means unarchived.
_ITEM_TABLES = {"boss": "bosses", "hero": "heroes"}


def _item_choices(query: str, limit: int = 25) -> dict:
    """{label: ref} over live bosses, heroes, cards, aspects and rites, exact
    and prefix matches first (content_index.choices' ranking)."""
    from azoth_logic import content_index
    from supabase_helpers import encode_item_ref

    entries = []
    for kind, table in _ITEM_TABLES.items():
        # autocomplete_from_table's rule: an autocomplete has no error channel,
        # so a failed read is logged and offers nothing rather than raising.
        try:
            rows = fetch_all(table, ["id", "name"], {"archived_at": None})
        except SupabaseError as e:
            print(f"AUTOCOMPLETE FAILED on `{table}`: {e}")
            rows = []
        entries += [(kind, r["id"], r["name"]) for r in rows if r.get("name")]
    try:
        entries += [(content_index.ref_type(k), i, n) for k, i, n in content_index.entries()]
    except SupabaseError as e:
        print(f"AUTOCOMPLETE FAILED on the content index: {e}")

    needle = (query or "").strip().lower()
    scored = []
    for kind, item_id, name in entries:
        low = str(name).lower()
        if needle and needle not in low:
            continue
        rank = 0 if low == needle else (1 if low.startswith(needle) else 2)
        scored.append((rank, low, kind, item_id, name))
    scored.sort(key=lambda r: (r[0], r[1]))
    return {content_index.label(content_index.KIND_FOR_REF.get(k, k), i, n): encode_item_ref(k, i)
            for _, _, k, i, n in scored[:limit]}


def _resolve_item(value: str):
    """An encoded ref or a typed name -> (kind, row), or (None, None).
    Bosses and heroes first by name, then the content index."""
    from azoth_logic import content_index
    from supabase_helpers import parse_item_ref

    ref_type, item_id = parse_item_ref(value)
    if ref_type in _ITEM_TABLES:
        rows = fetch_all(_ITEM_TABLES[ref_type], filters={"id": item_id})
        return (ref_type, rows[0]) if rows else (None, None)
    if ref_type:
        return content_index.resolve(value)
    name = (value or "").strip()
    for kind, table in _ITEM_TABLES.items():
        rows = fetch_all(table, filters={"name": name, "archived_at": None}) if name else []
        if rows:
            return kind, rows[0]
    return content_index.resolve(name)


async def _send_card(interaction, card, filename, *, footer, colour=0x5865F2):
    """A report drawn as an image, in an embed that carries a text footer.

    The image states everything the chart needs; the footer repeats what it
    rests on as text so it can be copied. Drawn off the event loop: PIL blocks
    for a noticeable moment, which would stall the gateway heartbeat.
    """
    data = await asyncio.to_thread(card.png)
    embed = nextcord.Embed(colour=colour)
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text=footer)
    await interaction.followup.send(embed=embed,
                                    file=nextcord.File(io.BytesIO(data), filename=filename))


def add_stats_commands(cls):

    # Top-level group for stats commands
    @nextcord.slash_command(name="stats", description="Statistics and data analysis", guild_ids=[DEV_GUILD_ID])
    async def stats_cmd(self, interaction: Interaction):
        pass

    # --- Players ---
    # The engagement side, drawn as an image (2026-09-28): who plays and how
    # each player splits their time between runs and the Codex, from
    # player_summary_view. It replaced /stats active_players (a text table of
    # games and hours) and /stats engagement, whose developer list by name is
    # now the `developer` cohort. See stats_cards § /stats players.
    @stats_cmd.subcommand(name="players", description="Who is playing, and where their time goes")
    @safe_interaction(timeout=20, error_message="❌ Failed to fetch players.")
    async def stats_players(
        self,
        interaction: Interaction,
        players: str = SlashOption(
            description="Whose rows to show (default: new playtesters)",
            required=False,
            default="new",
            choices={label: key for key, label in stats_cards.COHORT_LABELS.items()},
        ),
    ):
        try:
            rows = fetch_all("player_summary_view")
        except SupabaseError:
            return ("❌ `player_summary_view` is not migrated — run "
                    "`db/migrations/2026-09-28_player_summary_view.sql`.")

        wanted = stats_cards.COHORTS[players]
        rows = [r for r in rows if r.get("cohort") in wanted]
        if not rows:
            return "❌ No players recorded yet."

        await _send_card(interaction,
                         stats_cards.players_card(rows, stats_cards.COHORT_LABELS[players]),
                         "players.png", footer=stats_cards.players_footer(rows),
                         colour=0x3498DB)

    # --- Leaderboard ---
    # A ranked table of PLAYERS by their best run, drawn as an image
    # (2026-09-29) from leaderboard_best_view. Everyone by default: it is the
    # community board. `player:` and `version:` went with the text version: a
    # player's own best is on their card, and every run is at the cutoff.
    @stats_cmd.subcommand(name="leaderboard", description="Top combos, best run per player")
    @safe_interaction(timeout=20, error_message="❌ Failed to fetch leaderboard.")
    async def stats_leaderboard(
        self,
        interaction: Interaction,
        limit: int = SlashOption(description="How many players to show (default 10)",
                                 default=10, min_value=1, max_value=25),
        hero: str = SlashOption(description="Only runs with this hero", required=False,
                                autocomplete=True),
        players: str = SlashOption(
            description="Whose runs to rank (default: everyone)",
            required=False,
            default="all",
            choices={label: key for key, label in stats_cards.COHORT_LABELS.items()},
        ),
    ):
        try:
            rows = fetch_all("leaderboard_best_view")
        except SupabaseError:
            return ("❌ `leaderboard_best_view` is not migrated — run "
                    "`db/migrations/2026-09-29_leaderboard_best_view.sql`.")
        wanted = stats_cards.COHORTS[players]
        rows = [r for r in rows if r.get("cohort") in wanted]
        if not stats_cards.leaderboard_rows(rows, hero):
            return "❌ No runs to rank" + (f" with {hero}." if hero else ".")

        population = stats_cards.COHORT_LABELS[players]
        await _send_card(interaction,
                         stats_cards.leaderboard_card(rows, population, hero, limit),
                         "leaderboard.png", footer=stats_cards.leaderboard_footer(rows, hero),
                         colour=0xF1C40F)

    # --- Player ---
    # One player's profile, drawn as an image (2026-09-28): runs, act 3 wins,
    # best combo and time; a bar per hero split by furthest act; what they
    # drafted most and made in the Codex. See stats_cards § /stats player for
    # what the ten-section text card it replaced carried, and why it went.
    @stats_cmd.subcommand(name="player", description="One player's profile")
    @safe_interaction(timeout=20, error_message="❌ Failed to fetch player stats.")
    async def stats_player(
        self,
        interaction: Interaction,
        player: str = SlashOption(description="Player name", required=True, autocomplete=True)
    ):
        try:
            runs = fetch_all("player_run_view", filters={"player": player}, sort=["started_at"])
        except SupabaseError:
            return ("❌ `player_run_view` is not migrated — run "
                    "`db/migrations/2026-09-28_ritual_stats.sql`.")
        # The profile's other two sources are extras: without them the card
        # loses a tile or a section, not the reply.
        try:
            summary = next(iter(fetch_all("player_summary_view", filters={"player": player})), None)
        except SupabaseError:
            summary = None
        try:
            info = next(iter(fetch_all("player_info_view", filters={"player": player})), None)
        except SupabaseError:
            info = None
        if not runs and not summary:
            return f"❌ No stats found for `{player}`."

        await _send_card(interaction, stats_cards.player_card(player, runs, summary, info),
                         "player.png", footer=stats_cards.player_footer(runs), colour=0x5865F2)

    # --- Breakdown ---
    # Runs grouped by hero, ritual or version, drawn as an image (2026-09-28).
    # It replaced a text table per grouping (hero_info_view, version_info_view,
    # ritual_info_view) and /stats habits. See stats_cards § /stats breakdown
    # for what each grouping shows and why.
    @stats_cmd.subcommand(name="breakdown", description="Runs grouped by hero, ritual or version")
    @safe_interaction(timeout=20, error_message="❌ Failed to fetch breakdown.")
    async def stats_breakdown(
        self,
        interaction: Interaction,
        by: str = SlashOption(
            description="What to group the runs by",
            required=True,
            choices={"Hero": "hero", "Ritual": "ritual", "Version": "version"},
        ),
        players: str = SlashOption(
            description="Whose runs to count (default: new playtesters)",
            required=False,
            default="new",
            choices={label: key for key, label in stats_cards.COHORT_LABELS.items()},
        ),
    ):
        try:
            rows = fetch_all("breakdown_view", filters={"dimension": by})
        except SupabaseError:
            return ("❌ `breakdown_view` is not migrated — run "
                    "`db/migrations/2026-09-28_breakdown_view.sql`.")

        rows, filtered = stats_cards.select_cohort(
            rows, players, ("dimension", "grp", "furthest_act"), stats_cards.BREAKDOWN_COUNTS)
        if not stats_cards.breakdown_groups(rows, by):
            return "❌ No runs recorded for these players yet."

        population = stats_cards.COHORT_LABELS[players] if filtered else "Everyone"
        footer = stats_cards.breakdown_footer(rows, by)
        if not filtered:
            footer += " · player filter needs 2026-09-28_player_cohorts.sql"
        await _send_card(interaction, stats_cards.breakdown_card(rows, by, population),
                         f"breakdown_{by}.png", footer=footer, colour=0xE67E22)

    # --- Everything ---
    # For checking the reports after a view or formatting change: every one,
    # with its default options, in the channel. Authorized only because it
    # posts ~16 messages at once. The daily report comes last as a PREVIEW --
    # built the same way, but it never touches daily_update_state.json, so it
    # cannot claim or skip a scheduled send.
    @stats_cmd.subcommand(name="all", description="Run every stats report (for checking changes)")
    @safe_interaction(timeout=180, error_message="❌ /stats all stopped.", require_authorized=True)
    async def stats_all(
        self,
        interaction: Interaction,
        player: str = SlashOption(description="Player for the player card (default: most games)",
                                  required=False, autocomplete=True),
        daily: bool = SlashOption(description="Include a preview of yesterday's daily report",
                                  required=False, default=True),
    ):
        if not player:
            top = fetch_all("player_activity_view", ["player"], sort=["-game_count"], limit=1)
            player = top[0]["player"] if top else None

        preview = _Preview(interaction)
        for label, attr, kwargs in ALL_REPORTS:
            if attr == "stats_player":
                if not player:
                    continue
                kwargs = {"player": player}
                label = f"player ({player})"
            if attr == "stats_item":
                # The most-fought boss: the one most likely to have data.
                try:
                    fights: dict = {}
                    for r in fetch_all("boss_fight_view", ["boss", "fights"]):
                        fights[r["boss"]] = fights.get(r["boss"], 0) + int(r.get("fights") or 0)
                except SupabaseError:
                    fights = {}
                if not any(fights.values()):
                    continue
                boss = max(fights, key=fights.get)
                kwargs = {**kwargs, "item": boss}
                label = f"item ({boss})"
            preview.followup.label = label
            # The report's own safe_interaction catches and posts its errors,
            # so one failing report never stops the rest.
            await getattr(cls, attr).callback(self, preview, **kwargs)

        count = len(ALL_REPORTS)
        if daily:
            # Off the event loop: _fetch_daily_stats is a dozen blocking HTTP
            # calls. Safe here, unlike in the scheduler, because nothing is
            # claimed -- see the comment above _fetch_daily_stats's call site.
            from azoth_commands import daily_update as du
            preview.followup.label = "daily report"
            try:
                messages = await asyncio.to_thread(
                    lambda: du._build_update_messages(du._fetch_daily_stats()))
                for message in messages:
                    embed = message["embed"]
                    embed.title = f"{embed.title or 'Daily report'} (preview)"
                    await interaction.followup.send(**message)
            except Exception as e:
                await preview.followup.send(f"❌ failed\n```{e}```")
            count += 1

        failed = preview.followup.text_replies
        summary = f"✅ {count} reports run."
        if failed:
            summary += f" {len(failed)} replied with text instead of an embed: " + ", ".join(failed)
        await interaction.followup.send(summary)

    # --- Bosses ---
    # 2026-09-28, the first report drawn as an image (stats_charts). Each boss's
    # win rate, read against its act's overall rate: Caleb's addition to the
    # first draft, since later acts are meant to be harder.
    @stats_cmd.subcommand(name="bosses", description="Boss win rates, by act")
    @safe_interaction(timeout=20, error_message="❌ Failed to fetch boss stats.")
    async def stats_bosses(
        self,
        interaction: Interaction,
        players: str = SlashOption(
            description="Whose fights to count (default: new playtesters)",
            required=False,
            default="new",
            choices={label: key for key, label in stats_cards.COHORT_LABELS.items()},
        ),
    ):
        try:
            rows = fetch_all("boss_fight_view")
        except SupabaseError:
            return ("❌ `boss_fight_view` is not migrated — run "
                    "`db/migrations/2026-09-28_player_cohorts.sql` then "
                    "`2026-09-28_boss_fight_view.sql`.")
        if not rows:
            return "❌ No bosses found."

        rows, filtered = stats_cards.select_cohort(rows, players, "boss",
                                                   stats_cards.BOSS_COUNTS)
        population = stats_cards.COHORT_LABELS[players] if filtered else "Everyone"
        footer = stats_cards.bosses_footer(rows)
        if not filtered:
            footer += " · player filter needs 2026-09-28_player_cohorts.sql"
        await _send_card(interaction, stats_cards.bosses_card(rows, population), "bosses.png",
                         footer=footer, colour=0xC0392B)

    # --- One item ---
    # 2026-09-29, after Veln: its hp was halved in 0.9.11 and /stats bosses,
    # pooling every version, still ranked it the hardest. One boss, card,
    # aspect, rite or hero, its rate per version (or ritual, or hero), each
    # against the rest of its kind in that same group. See stats_cards
    # § /stats item and docs/ANALYTICS.md § One item.
    @stats_cmd.subcommand(name="item", description="One boss, card, aspect, rite or hero, split by version")
    @safe_interaction(timeout=30, error_message="❌ Failed to fetch item stats.")
    async def stats_item(
        self,
        interaction: Interaction,
        item: str = SlashOption(description="The boss, card, aspect, rite or hero", autocomplete=True),
        by: str = SlashOption(
            description="What to split it by (default: version)",
            required=False,
            default="version",
            choices={"Version": "version", "Ritual": "ritual", "Hero": "hero"},
        ),
        players: str = SlashOption(
            description="Whose games to count (default: new playtesters)",
            required=False,
            default="new",
            choices={label: key for key, label in stats_cards.COHORT_LABELS.items()},
        ),
    ):
        from azoth_logic import stats_thumbs

        kind, row = await asyncio.to_thread(_resolve_item, item)
        if not row:
            return f"❌ Could not find a live boss, card, aspect, rite or hero called `{item}`."
        name = row.get("name") or item
        if by not in stats_cards.ITEM_AXES[kind]:
            return f"❌ {name} is a hero: split it by version or ritual."

        view, id_column, counts = stats_cards.ITEM_SPLITS[kind]
        filters = {id_column: row["id"], "dimension": by}
        if view == "draft_item_split_view":
            filters["item_type"] = kind
        try:
            rows = fetch_all(view, filters=filters)
        except SupabaseError:
            return (f"❌ `{view}` is not migrated — run "
                    "`db/migrations/2026-09-29_item_split_views.sql`.")
        rows, _ = stats_cards.select_cohort(rows, players, "grp", counts)
        groups = [g for g in stats_cards.item_groups(rows, kind) if g["n"]]
        if not groups:
            unit = stats_cards.ITEM_MEASURES[kind][1]
            return f"❌ No {unit} recorded for {name} yet, for these players."

        act = row.get("act") if kind == "boss" else None
        colour = stats_cards.item_colour(kind, row, act)
        thumb = await asyncio.to_thread(stats_thumbs.thumbnail, kind, row, colour)
        card = stats_cards.item_card(name, kind, groups, by, stats_cards.COHORT_LABELS[players],
                                     act=act, colour=colour, thumb=thumb)
        await _send_card(interaction, card, "item.png",
                         footer=stats_cards.item_footer(kind, groups, by), colour=int(colour[1:], 16))

    @stats_item.on_autocomplete("item")
    async def autocomplete_stats_item(self, interaction: Interaction, input: str):
        await interaction.response.send_autocomplete(await asyncio.to_thread(_item_choices, input))

    # --- Turn Scoreboard ---
    @stats_cmd.subcommand(name="scoreboard", description="End-of-turn bonus thresholds, by act")
    @safe_interaction(timeout=10, error_message="❌ Failed to fetch scoreboard stats.")
    async def stats_scoreboard(self, interaction: Interaction):
        # No sort: turn_scoreboard_view carries `order by act nulls last, axis`,
        # and asking PostgREST for `act.asc` would sort the rollup row (act
        # NULL) to the top. The renderer re-orders anyway; this just avoids
        # fighting the view.
        #
        # Caught the way the image reports catch theirs: an unmigrated view
        # is PGRST205, and "not migrated" is a different answer from "no turns
        # yet". Naming the file is the whole value of catching it.
        try:
            records = fetch_all("turn_scoreboard_view")
        except SupabaseError:
            return ("❌ `turn_scoreboard_view` is not migrated — run "
                    "`db/migrations/2026-08-31_turn_scoreboard.sql`.")

        if not records:
            return ("❌ No turn scoreboards recorded yet. These columns postdate "
                    "`0.9.1`, so runs played before that build carry none.")

        embed = nextcord.Embed(title="Turn scoreboard", colour=0xE91E63)
        embed.add_field(name="Threshold hit rate", inline=False,
                        value=sf.scoreboard_hits(records))
        embed.add_field(name="Average count / threshold", inline=False,
                        value=sf.scoreboard_counts(records))
        embed.add_field(name="Which axis paid", inline=False,
                        value=sf.scoreboard_paid(records))

        # cutoff=False: this is the one view that does not filter on
        # analytics_cutoff(). It filters `bonus_key is not null`
        # instead — the columns date themselves, and bumping the cutoff for an
        # additive change would have emptied every other /stats reply. Claiming
        # a cutoff the view is not enforcing is worse than claiming none.
        turns = sf.scoreboard_sample(records)
        embed.set_footer(text=sf.footer(
            records, note=f"{turns} regular turn{'' if turns == 1 else 's'} scored",
            cutoff=False))
        await interaction.followup.send(embed=embed)

    # --- Draft ---------------------------------------------------------
    # A subcommand GROUP: picks and items are play (draft offers, with the
    # players: filter); pool is content (what the shipped draft decks hold),
    # with no cohort. Each is its own image, so each states what it rests on.
    # Redrawn 2026-09-29: picks replaced `breakdown` and `embellishments`,
    # items replaced the text `rates`, pool is the renamed `composition`.
    @stats_cmd.subcommand(name="draft", description="The draft pool and how it is picked")
    async def stats_draft(self, interaction: Interaction):
        pass

    @stats_draft.subcommand(name="picks", description="Pick rates by type, pack, element, valence and embellishment")
    @safe_interaction(timeout=20, error_message="❌ Failed to fetch draft picks.")
    async def stats_draft_picks(
        self,
        interaction: Interaction,
        players: str = SlashOption(
            description="Whose drafts to count (default: new playtesters)",
            required=False,
            default="new",
            choices={label: key for key, label in stats_cards.COHORT_LABELS.items()},
        ),
    ):
        try:
            rows = fetch_all("draft_offer_view")
        except SupabaseError:
            return ("❌ `draft_offer_view` is not migrated — run "
                    "`db/migrations/2026-09-29_draft_offer_views.sql`.")
        rows, _ = stats_cards.select_cohort(rows, players, ("dimension", "bucket"),
                                            stats_cards.DRAFT_COUNTS)
        if not any(r.get("offered") for r in rows):
            return "❌ No draft offers recorded for these players yet."
        await _send_card(interaction,
                         stats_cards.draft_picks_card(rows, stats_cards.COHORT_LABELS[players]),
                         "draft_picks.png", footer=stats_cards.draft_picks_footer(rows),
                         colour=0x1ABC9C)

    @stats_draft.subcommand(name="items", description="The most and least picked items")
    @safe_interaction(timeout=20, error_message="❌ Failed to fetch draft items.")
    async def stats_draft_items(
        self,
        interaction: Interaction,
        players: str = SlashOption(
            description="Whose drafts to count (default: new playtesters)",
            required=False,
            default="new",
            choices={label: key for key, label in stats_cards.COHORT_LABELS.items()},
        ),
    ):
        try:
            rows = fetch_all("draft_item_offer_view")
        except SupabaseError:
            return ("❌ `draft_item_offer_view` is not migrated — run "
                    "`db/migrations/2026-09-29_draft_offer_views.sql`.")
        rows, _ = stats_cards.select_cohort(rows, players, ("item_type", "item_id", "item_name"),
                                            stats_cards.DRAFT_COUNTS)
        rows = [r for r in rows if r.get("offered")]
        if not rows:
            return "❌ No draft offers recorded for these players yet."
        await _send_card(interaction,
                         stats_cards.draft_items_card(rows, stats_cards.COHORT_LABELS[players]),
                         "draft_items.png", footer=stats_cards.draft_items_footer(rows),
                         colour=0x1ABC9C)

    @stats_draft.subcommand(name="pool", description="What the draft pool holds")
    @safe_interaction(timeout=20, error_message="❌ Failed to fetch the draft pool.")
    async def stats_draft_pool(self, interaction: Interaction):
        records = fetch_all("draft_deck_view")
        if not records:
            return "❌ No draft pool data available."
        await _send_card(interaction, stats_cards.draft_pool_card(records[0]), "draft_pool.png",
                         footer="base draft decks, not archived · rites are templates injected per run",
                         colour=0x2ECC71)


    @stats_player.on_autocomplete("player")
    @stats_all.on_autocomplete("player")
    async def autocomplete_active_player(self, interaction: Interaction, input: str):
        suggestions = autocomplete_from_table(table_name="active_players_view", input=input)
        await interaction.response.send_autocomplete(suggestions[:25])


    @stats_leaderboard.on_autocomplete("hero")
    async def autocomplete_hero(self, interaction: Interaction, input: str):
        suggestions = autocomplete_from_table(table_name="heroes", input=input, filters={"archived_at": None})
        await interaction.response.send_autocomplete(suggestions[:25])


    # Expose on class
    cls.stats_cmd = stats_cmd
    cls.stats_players = stats_players
    cls.stats_leaderboard = stats_leaderboard
    cls.stats_player = stats_player
    cls.stats_breakdown = stats_breakdown
    cls.stats_all = stats_all
    cls.stats_bosses = stats_bosses
    cls.stats_item = stats_item
    cls.stats_scoreboard = stats_scoreboard
    # The group AND each of its subcommands. Assigning only the group would
    # leave the three bodies unreachable in exactly the way
    # test_command_registration.py exists to catch.
    cls.stats_draft = stats_draft
    cls.stats_draft_picks = stats_draft_picks
    cls.stats_draft_items = stats_draft_items
    cls.stats_draft_pool = stats_draft_pool
