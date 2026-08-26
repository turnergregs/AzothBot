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

        return f"```json\n{json.dumps(records, indent=2)}\n```"

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

        return f"```json\n{json.dumps(records, indent=2)}\n```"

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
        return f"```json\n{json.dumps(records, indent=2)}\n```"

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
        return f"```json\n{json.dumps(records, indent=2)}\n```"

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
        return f"```json\n{json.dumps(records, indent=2)}\n```"

    # --- Draft Deck Data ---
    @stats_cmd.subcommand(name="draft_deck", description="Draft deck composition data")
    @safe_interaction(timeout=10, error_message="❌ Failed to fetch draft deck data.")
    async def stats_draft_deck(self, interaction: Interaction):
        records = fetch_all("draft_deck_view")
        if not records:
            return "❌ No draft deck data available."
        return f"```json\n{json.dumps(records, indent=2)}\n```"

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
        return f"```json\n{json.dumps(records, indent=2)}\n```"


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
    cls.stats_draft_deck = stats_draft_deck
    cls.stats_draft_rates = stats_draft_rates
