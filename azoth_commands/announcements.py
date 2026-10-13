"""`/announce` -- post news a hero says on the game's title screen.

One row of `announcements` is one announcement: the game reads the live rows
when the title loads and shows each player the ones they have not confirmed.
No build is needed, so this is live for every player from their next launch.
See the game repo's docs/ANNOUNCEMENTS.md.

There is deliberately no update command. Which announcements a player has
confirmed is a list of ids on the player's own machine, so an edited row keeps
its id and is never shown again to anyone who confirmed it. News is corrected
by pulling the row (`archived_at`) and posting a new one.

The checks and the reply's wording are in `azoth_logic/announcements.py`, where
the tests can reach them.
"""
import nextcord
from nextcord import Interaction, SlashOption

from azoth_commands.autocomplete import autocomplete_from_table
from azoth_commands.helpers import safe_interaction
from azoth_logic import announcements
from constants import DEV_GUILD_ID
from supabase_helpers import create_record, fetch_all

# Archived heroes are not in the game, so they cannot speak.
LIVE_HEROES = {"archived_at": None}


def add_announcement_commands(cls):

    @nextcord.slash_command(name="announce",
                            description="Post news a hero says on the game's title screen.",
                            guild_ids=[DEV_GUILD_ID])
    @safe_interaction(timeout=10, error_message="❌ Failed to create the announcement.",
                      require_authorized=True)
    async def announce_cmd(
        self,
        interaction: Interaction,
        body: str = SlashOption(
            description="What the hero says. About 110 characters fit; [term]...[/term] is gold."),
        speaker: str = SlashOption(
            description="Whose bubble it is (default: Lumis)",
            required=False, autocomplete=True),
        starts: str = SlashOption(
            description="When it goes live, UTC: 2026-10-20 or 2026-10-20 18:00 (default: now)",
            required=False),
        ends: str = SlashOption(
            description="When it stops showing, UTC, same format (default: never)",
            required=False),
        min_version: str = SlashOption(
            description="Lowest build it is for, like 0.11.0 (default: no limit)",
            required=False),
        max_version: str = SlashOption(
            description="Highest build it is for (default: no limit)",
            required=False),
        preview: bool = SlashOption(
            description="Show what would be posted without posting it",
            required=False, default=False),
    ):
        try:
            heroes = fetch_all("heroes", ["id", "name"], LIVE_HEROES)
            hero = (announcements.find_hero(speaker, heroes) if speaker
                    else announcements.default_hero(heroes))
            row = announcements.build_row(
                body, hero["id"] if hero else None, starts, ends, min_version, max_version)
        except announcements.AnnouncementError as e:
            return f"❌ {e}"

        speaker_name = hero["name"] if hero else None
        if preview:
            return announcements.describe(row, speaker_name)

        created = create_record(announcements.TABLE, row)
        if not created:
            return "❌ Failed to create the announcement."
        return announcements.describe(row, speaker_name, row_id=created[0]["id"])

    @announce_cmd.on_autocomplete("speaker")
    async def autocomplete_announcement_speaker(self, interaction: Interaction, input: str):
        suggestions = autocomplete_from_table("heroes", input, filters=LIVE_HEROES)
        await interaction.response.send_autocomplete(suggestions[:25])

    cls.announce_cmd = announce_cmd
