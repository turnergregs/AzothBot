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
    # Was ABSENT until 2026-09-03, which is exactly the failure the note above
    # describes. draft_rates_view returns item_type, item_id, item_name,
    # element, valence and only then the five rate columns, so the width trim
    # dropped `times_offered`, `times_picked`, `times_reserved`, `pick_rate`
    # and `reserve_rate` -- every number in the table -- and the reply was a
    # ranked list of names with no visible reason for the ranking. `item_id` is
    # left out for the opposite reason: it is a join key, not information.
    "draft_rates": ["item_name", "item_type", "pick_rate", "times_picked",
                    "times_offered"],
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

        embed.add_field(name="Max Reached", inline=False, value=sf.reached(row))
        embed.add_field(name="Links per turn", inline=False,
                        value=sf.links_table(acts, row))
        embed.add_field(name="Patterns cleared", inline=False,
                        value=sf.clearing_table(acts, row))

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

        # cutoff=False: this is the ONE view besides version_info_view that does
        # not filter on analytics_cutoff(). It filters `bonus_key is not null`
        # instead — the columns date themselves, and bumping the cutoff for an
        # additive change would have emptied every other /stats reply. Claiming
        # a cutoff the view is not enforcing is worse than claiming none.
        turns = sf.scoreboard_sample(records)
        embed.set_footer(text=sf.footer(
            records, note=f"{turns} regular turn{'' if turns == 1 else 's'} scored",
            cutoff=False))
        await interaction.followup.send(embed=embed)

    # --- Draft ---------------------------------------------------------
    # A subcommand GROUP, 2026-09-03. The three replies are neighbours but they
    # are not one reply: the composition is content with no games behind it and
    # no cutoff, while the two rate views are ~2 games at 0.9.0 filtered further
    # by `having times_offered >= 5`. One embed carries one footer, and merging
    # them would have to either claim the cutoff over content numbers or drop it
    # over game numbers. Grouping gets the tidiness without the lie.
    @stats_cmd.subcommand(name="draft", description="The draft pool and how it is picked")
    async def stats_draft(self, interaction: Interaction):
        pass

    # --- Draft Pool Composition ---
    @stats_draft.subcommand(name="composition", description="Draft pool composition")
    @safe_interaction(timeout=10, error_message="❌ Failed to fetch draft pool data.")
    async def stats_draft_composition(self, interaction: Interaction):
        records = fetch_all("draft_deck_view")
        if not records:
            return "❌ No draft pool data available."

        # Bar charts rather than runs of "label N", because both fields are
        # DISTRIBUTIONS and a distribution read as prose is just arithmetic
        # homework. The element chart is coloured to the game's own element
        # colours; see stats_format.ANSI_ELEMENT.
        #
        # The valence field used to render `range(1, 7)` against a column per
        # valence, so cards at 7 and 9 -- four of them, in the pool today -- were
        # counted by nothing and shown by nothing, and the field silently
        # described 108 of 136 cards. stats_format reads the jsonb histogram
        # added by 2026-09-03_draft_pool_histograms.sql, which cannot have that
        # failure, and falls back to the old columns with the reply saying so.
        row = records[0]
        embed = nextcord.Embed(title="Draft pool", colour=0x2ECC71)
        embed.add_field(name="Contents", inline=False,
                        value=sf.draft_pool_contents(row))
        embed.add_field(name="Element", inline=False,
                        value=sf.draft_pool_elements(row))
        embed.add_field(name="Valence", inline=False,
                        value=sf.draft_pool_valence(row))

        # Rites LAST and in their own field, never folded into Contents. They
        # are templates drawn with replacement into injected slots, not pool
        # members counted once each, so "22 rites" beside "136 cards" reads as
        # 22 pool slots and is wrong by construction. Dropped entirely on a view
        # that does not carry them, rather than shown as zero.
        rites = sf.draft_pool_rites(row)
        if rites:
            embed.add_field(name="Rites", inline=False, value=rites)

        # Content only -- no games behind it, so no cutoff and no game count.
        embed.set_footer(text="base decks, not archived — usage draft, plus rite templates")
        await interaction.followup.send(embed=embed)

    # --- Draft Rates, by element and valence ---
    @stats_draft.subcommand(name="breakdown", description="Pick rate by element and valence")
    @safe_interaction(timeout=10, error_message="❌ Failed to fetch draft breakdown.")
    async def stats_draft_breakdown(self, interaction: Interaction):
        # Caught the way /stats scoreboard is: an unmigrated view is PGRST205,
        # and "not migrated" is a different answer from "nobody has drafted
        # yet". Naming the file is the whole value of catching it.
        try:
            records = fetch_all("draft_dimension_rates_view")
        except SupabaseError:
            return ("❌ `draft_dimension_rates_view` is not migrated — run "
                    "`db/migrations/2026-09-03_draft_dimension_rates.sql`.")

        if not records:
            return "❌ No card draft data at or above the cutoff yet."

        embed = nextcord.Embed(title="Draft picks by kind", colour=0x1ABC9C)
        # Type first: it is the only breakdown covering every offer, and the
        # two below it are cards only.
        embed.add_field(name="By type", inline=False,
                        value=sf.draft_rate_by_type(records))
        embed.add_field(name="By element", inline=False,
                        value=sf.draft_rate_by_element(records))
        embed.add_field(name="By valence", inline=False,
                        value=sf.draft_rate_by_valence(records))

        # NOT len(records) and not a sum over the view: every offer is counted
        # once under its element and again under its valence, so the view totals
        # twice the real number. draft_offers_sampled reads one dimension.
        offers = sf.draft_offers_sampled(records)
        embed.set_footer(text=sf.footer(
            records, note=f"{offers} card offer{'' if offers == 1 else 's'}"))
        await interaction.followup.send(embed=embed)

    # --- Draft Rate Data ---
    @stats_draft.subcommand(name="rates", description="Draft pick rates, per item")
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
            # The LABEL is Rite; the value stays `event` because that is what
            # draft_rates_view.item_type stores. Renaming the value would need a
            # view change for a word.
            choices={"Card": "card", "Aspect": "aspect", "Rite": "event"},
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
                          COLUMNS["draft_rates"], rank=True, note=note,
                          colour=0x1ABC9C)


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
    cls.stats_scoreboard = stats_scoreboard
    # The group AND each of its subcommands. Assigning only the group would
    # leave the three bodies unreachable in exactly the way
    # test_command_registration.py exists to catch.
    cls.stats_draft = stats_draft
    cls.stats_draft_composition = stats_draft_composition
    cls.stats_draft_breakdown = stats_draft_breakdown
    cls.stats_draft_rates = stats_draft_rates
