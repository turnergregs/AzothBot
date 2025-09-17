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
from supabase_helpers import fetch_all


def add_stats_commands(cls):

    # Top-level group for stats commands
    @nextcord.slash_command(name="stats", description="Statistics and data analysis", guild_ids=[DEV_GUILD_ID])
    async def stats_cmd(self, interaction: Interaction):
        pass

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

        records = fetch_all("leaderboard_view", filters=filters)[:limit]

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
        suggestions = autocomplete_from_table(table_name="game_stats", input=input, column="version")
        await interaction.response.send_autocomplete(suggestions[:25])


    # Expose on class
    cls.stats_cmd = stats_cmd
    cls.stats_leaderboard = stats_leaderboard
    cls.stats_player = stats_player
    cls.stats_hero = stats_hero
    cls.stats_version = stats_version
    cls.stats_draft_deck = stats_draft_deck
