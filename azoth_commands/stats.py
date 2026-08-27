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


# Columns worth showing, per view. Explicit rather than "whatever the view
# returns": the views order `avg_turns` before `avg_combo_log10`, so the width
# trim in stats_format.table would throw away the combo -- the column anyone
# actually came for. Anything trimmed beyond this is named in the footer.
COLUMNS = {
    "active_players": ["player", "game_count", "hours_played", "highest_combo"],
    "leaderboard": ["player", "combo", "hero", "turns", "act", "level"],
    "hero": ["hero_name", "game_count", "avg_act", "avg_level", "avg_combo_log10", "max_combo"],
    "version": ["version", "game_count", "avg_act", "avg_level", "avg_combo_log10", "max_combo"],
}


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

    # --- Active Players ---
    @stats_cmd.subcommand(name="active_players", description="List active players and their play statistics")
    @safe_interaction(timeout=10, error_message="❌ Failed to fetch active players.")
    async def stats_active_players(
        self,
        interaction: Interaction,
        limit: int = SlashOption(description="How many players to return", default=25)
    ):
        records = fetch_all("player_activity_view", sort=["-game_count"], limit=limit)

        if not records:
            return "❌ No active players found."

        await _send_table(interaction, "Active players", records,
                          COLUMNS["active_players"], note="by games played")

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

    # --- Player Info ---
    @stats_cmd.subcommand(name="player", description="Player statistics")
    @safe_interaction(timeout=10, error_message="❌ Failed to fetch player stats.")
    async def stats_player(
        self,
        interaction: Interaction,
        player: str = SlashOption(description="Player name", required=True, autocomplete=True)
    ):
        records = fetch_all("player_info_view", filters={"player": player})
        if not records:
            return f"❌ No stats found for `{player}`."

        # A second view, because the act breakdown is one row PER ACT and this
        # card is one row per player. Filtered server-side rather than fetched
        # whole and sliced -- see fetch_all's note on the 1000-row cap.
        #
        # Caught, and ONLY here: if the act migration has not been applied the
        # table is missing and PostgREST says so with PGRST205. That should not
        # take down the whole card for the sake of one section -- but it is
        # named in the reply rather than rendered as "no data", because "not
        # migrated" and "no turns yet" are different problems.
        try:
            acts = fetch_all("player_act_view", filters={"player": player},
                             sort=["act"])
        except SupabaseError:
            acts = None

        # One row, and hand-grouped rather than a field per column: the view
        # carries 22 columns and a flat dump of them is the JSON blob again with
        # nicer punctuation.
        row = records[0]
        embed = nextcord.Embed(title=row.get("player") or player, colour=0x5865F2)

        embed.add_field(name="Runs", inline=True, value=sf.record(row))
        embed.add_field(name="Best combo", inline=True,
                        value=sf.value("best_combo", row.get("best_combo")))
        embed.add_field(name="Highest Ritual", inline=True,
                        value=sf.value("max_ritual", row.get("max_ritual")))

        embed.add_field(name="Links per turn", inline=False, value=sf.links(row))
        embed.add_field(name="Links per turn, by act", inline=False,
                        value=sf.act_table(acts))
        embed.add_field(name="Patterns cleared", inline=False, value=sf.clearing(row))
        embed.add_field(name="Max Reached", inline=False, value=sf.reached(row))

        drafted = sf.most_drafted(row)
        if drafted:
            embed.add_field(name="Most drafted", inline=False, value=drafted)

        embed.set_footer(text=sf.footer(records, note=sf.last_played(row)))
        await interaction.followup.send(embed=embed)

    # --- Hero Info ---
    @stats_cmd.subcommand(name="hero", description="Hero statistics")
    @safe_interaction(timeout=10, error_message="❌ Failed to fetch hero stats.")
    async def stats_hero(
        self,
        interaction: Interaction,
    ):
        records = fetch_all("hero_info_view")
        if not records:
            return "❌ No hero stats available."

        await _send_table(interaction, "Heroes", records, COLUMNS["hero"],
                          colour=0xE67E22)

    # --- Version Info ---
    @stats_cmd.subcommand(name="version", description="Version statistics")
    @safe_interaction(timeout=10, error_message="❌ Failed to fetch version stats.")
    async def stats_version(
        self,
        interaction: Interaction
    ):
        records = fetch_all("version_info_view")
        if not records:
            return "❌ No version stats available."

        # The ONE view with no cutoff -- comparing versions is the point, and
        # filtering to >= 0.8.2 would leave it a single row.
        await _send_table(interaction, "Versions", records, COLUMNS["version"],
                          cutoff=False, colour=0x9B59B6)

    # --- Draft Deck Data ---
    @stats_cmd.subcommand(name="draft_pool", description="Draft pool composition data")
    @safe_interaction(timeout=10, error_message="❌ Failed to fetch draft pool data.")
    async def stats_draft_pool(self, interaction: Interaction):
        records = fetch_all("draft_deck_view")
        if not records:
            return "❌ No draft pool data available."

        row = records[0]
        embed = nextcord.Embed(title="Draft pool", colour=0x2ECC71)
        embed.add_field(name="Contents", inline=False, value=(
            f"**{row.get('cards', 0)}** cards · "
            f"**{row.get('aspects', 0)}** aspects · "
            f"**{row.get('events', 0)}** rites"))
        embed.add_field(name="Element", inline=False, value=(
            f"anima **{row.get('anima', 0)}** · blood **{row.get('blood', 0)}** · "
            f"sol **{row.get('sol', 0)}** · colourless **{row.get('combo', 0)}**"))
        embed.add_field(name="Valence", inline=False, value=" · ".join(
            f"{v}v **{row.get(f'{v}v', 0)}**" for v in range(1, 7)))
        # Content only -- no games behind it, so no cutoff and no game count.
        embed.set_footer(text="base decks, not archived, usage draft or rite")
        await interaction.followup.send(embed=embed)

    # --- Draft Rate Data ---
    @stats_cmd.subcommand(name="draft_rates", description="Draft pick rates, per item")
    @safe_interaction(timeout=10, error_message="❌ Failed to fetch draft rate data.")
    async def stats_draft_rates(
        self,
        interaction: Interaction,
        limit: int = SlashOption(description="How many items to return", default=15),
        order: str = SlashOption(
            description="Most or least picked",
            required=False,
            default="most",
            choices={"Most picked": "most", "Least picked": "least"},
        ),
        item_type: str = SlashOption(
            description="Restrict to one content type",
            required=False,
            choices={"Card": "card", "Aspect": "aspect", "Event": "event"},
        ),
    ):
        # draft_rates_view returns ONE ROW PER ITEM as of 2026-08-26, carrying
        # times_picked AND times_offered rather than a pre-formatted string, so
        # the limit has to be applied here or this dumps every draftable item.
        # The view is ordered pick_rate DESC, so "least" just reverses it.
        filters = {"item_type": item_type} if item_type else None
        sort = ["pick_rate", "-times_offered"] if order == "least" else None

        records = fetch_all("draft_rates_view", filters=filters, sort=sort, limit=limit)
        if not records:
            return "❌ No draft rate data available."

        note = f"{order} picked" + (f", {item_type}s only" if item_type else "")
        await _send_table(interaction, "Draft pick rates", records,
                          rank=True, note=note, colour=0x1ABC9C)


    @stats_leaderboard.on_autocomplete("player")
    @stats_player.on_autocomplete("player")
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
    cls.stats_active_players = stats_active_players
    cls.stats_leaderboard = stats_leaderboard
    cls.stats_player = stats_player
    cls.stats_hero = stats_hero
    cls.stats_version = stats_version
    cls.stats_draft_pool = stats_draft_pool
    cls.stats_draft_rates = stats_draft_rates
