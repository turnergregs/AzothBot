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


# Columns worth showing, per view. Explicit rather than "whatever the view
# returns": the views order `avg_turns` before `avg_combo_log10`, so the width
# trim in stats_format.table would throw away the combo -- the column anyone
# actually came for. Anything trimmed beyond this is named in the footer.
COLUMNS = {
    "leaderboard": ["player", "combo", "hero", "turns", "act", "level"],
}


# Every report /stats all runs, in order, with the options it runs them with.
# `None` is passed explicitly for optional filters: a callback invoked directly
# receives its SlashOption DEFAULT OBJECT for anything left out, not the
# default value. test_command_registration checks that every /stats report is
# listed here, so a new one cannot be missed.
ALL_REPORTS = [
    ("players", "stats_players", {"players": "new"}),
    ("leaderboard", "stats_leaderboard", {"limit": 10, "player": None, "hero": None,
                                          "version": None}),
    ("player", "stats_player", {}),     # player filled in at run time
    ("breakdown by:hero", "stats_breakdown", {"by": "hero", "players": "new"}),
    ("breakdown by:ritual", "stats_breakdown", {"by": "ritual", "players": "new"}),
    ("breakdown by:version", "stats_breakdown", {"by": "version", "players": "new"}),
    ("bosses", "stats_bosses", {"players": "new"}),
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


async def _send_table(interaction, title, rows, columns=None, *, rank=False,
                      note=None, cutoff=True, colour=0x5865F2):
    """A view rendered as one embed: aligned table, and what it rests on."""
    text, dropped = sf.table(rows, columns, rank=rank)
    embed = nextcord.Embed(title=title, description=sf.block(text), colour=colour)
    embed.set_footer(text=sf.footer(rows, note=note, dropped=dropped, cutoff=cutoff))
    await interaction.followup.send(embed=embed)


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
    @stats_cmd.subcommand(name="leaderboard", description="Show top combos")
    @safe_interaction(timeout=10, error_message="❌ Failed to fetch leaderboard.")
    async def stats_leaderboard(
        self,
        interaction: Interaction,
        limit: int = SlashOption(description="How many results to return", default=10),
        player: str = SlashOption(description="Filter by player name", required=False, autocomplete=True),
        hero: str = SlashOption(description="Filter by starting hero", required=False, autocomplete=True),
        version: str = SlashOption(description="Filter by game version", required=False, autocomplete=True)
    ):
        filters = {}
        if version:
            filters["version"] = version
        if player:
            filters["player"] = player
        if hero:
            filters["hero"] = hero

        # No explicit sort: leaderboard_view carries
        # `ORDER BY highest_combo::numeric DESC`, and PostgREST preserves it
        # under a LIMIT (verified 2026-08-26). Sorting here on `combo` would be
        # WRONG -- it is a text column, so a text sort ranks "9" above
        # "2596148429267413814265248164610048".
        # The limit must go to the server: without it PostgREST caps at 1000
        # rows of an ~1830-row view and the slice reads a truncated page.
        records = fetch_all("leaderboard_view", filters=filters, limit=limit)

        if not records:
            return "❌ No leaderboard data available."

        applied = ", ".join(f"{k}: {v}" for k, v in filters.items())
        await _send_table(interaction, "Leaderboard", records,
                          COLUMNS["leaderboard"], rank=True,
                          note=applied or "top combos", colour=0xF1C40F)

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

    # --- Turn Scoreboard ---
    @stats_cmd.subcommand(name="scoreboard", description="End-of-turn bonus thresholds, by act")
    @safe_interaction(timeout=10, error_message="❌ Failed to fetch scoreboard stats.")
    async def stats_scoreboard(self, interaction: Interaction):
        # No sort: turn_scoreboard_view carries `order by act nulls last, axis`,
        # and asking PostgREST for `act.asc` would sort the rollup row (act
        # NULL) to the top. The renderer re-orders anyway; this just avoids
        # fighting the view.
        #
        # Caught the way player_act_view is on /stats player: an unmigrated view
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

    @stats_draft.subcommand(name="picks", description="Pick rates by type, element, valence and embellishment")
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


    @stats_leaderboard.on_autocomplete("player")
    @stats_player.on_autocomplete("player")
    @stats_all.on_autocomplete("player")
    async def autocomplete_active_player(self, interaction: Interaction, input: str):
        suggestions = autocomplete_from_table(table_name="active_players_view", input=input)
        await interaction.response.send_autocomplete(suggestions[:25])


    @stats_leaderboard.on_autocomplete("hero")
    async def autocomplete_hero(self, interaction: Interaction, input: str):
        suggestions = autocomplete_from_table(table_name="heroes", input=input, filters={"archived_at": None})
        await interaction.response.send_autocomplete(suggestions[:25])


    @stats_leaderboard.on_autocomplete("version")
    async def autocomplete_version(self, interaction: Interaction, input: str):
        # Was pointed at `game_stats`, which does not exist -- this autocomplete
        # returned nothing on every keystroke. `games` is the real source, but
        # it has one row per RUN, so versions must be de-duplicated here.
        # Ordered newest-first and capped, since PostgREST would otherwise cap
        # at 1000 arbitrary rows and miss recent versions entirely.
        try:
            rows = fetch_all("games", ["version"], sort=["-created_at"], limit=1000)
        except SupabaseError as e:
            print(f"AUTOCOMPLETE FAILED on `games`.`version`: {e}")
            await interaction.response.send_autocomplete([])
            return

        seen = []
        for row in rows:
            v = row.get("version")
            if v and v not in seen and input.lower() in v.lower():
                seen.append(v)
        await interaction.response.send_autocomplete(seen[:25])


    # Expose on class
    cls.stats_cmd = stats_cmd
    cls.stats_players = stats_players
    cls.stats_leaderboard = stats_leaderboard
    cls.stats_player = stats_player
    cls.stats_breakdown = stats_breakdown
    cls.stats_all = stats_all
    cls.stats_bosses = stats_bosses
    cls.stats_scoreboard = stats_scoreboard
    # The group AND each of its subcommands. Assigning only the group would
    # leave the three bodies unreachable in exactly the way
    # test_command_registration.py exists to catch.
    cls.stats_draft = stats_draft
    cls.stats_draft_picks = stats_draft_picks
    cls.stats_draft_items = stats_draft_items
    cls.stats_draft_pool = stats_draft_pool
