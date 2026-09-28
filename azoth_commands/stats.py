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
    # Ritual sits beside Games, not at the end: the width trim takes columns off
    # the right, and "which ritual are they on" is why it was added.
    "active_players": ["player", "game_count", "max_ritual", "hours_played",
                       "highest_combo"],
    "leaderboard": ["player", "combo", "hero", "turns", "act", "level"],
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


# Every report /stats all runs, in order, with the options it runs them with.
# `None` is passed explicitly for optional filters: a callback invoked directly
# receives its SlashOption DEFAULT OBJECT for anything left out, not the
# default value. test_command_registration checks that every /stats report is
# listed here, so a new one cannot be missed.
ALL_REPORTS = [
    ("active players", "stats_active_players", {"limit": 25}),
    ("leaderboard", "stats_leaderboard", {"limit": 10, "player": None, "hero": None,
                                          "version": None}),
    ("player", "stats_player", {}),     # player filled in at run time
    ("breakdown by:hero", "stats_breakdown", {"by": "hero", "players": "new"}),
    ("breakdown by:ritual", "stats_breakdown", {"by": "ritual", "players": "new"}),
    ("breakdown by:version", "stats_breakdown", {"by": "version", "players": "new"}),
    ("engagement", "stats_engagement", {"include_devs": False}),
    ("bosses", "stats_bosses", {"players": "new"}),
    ("scoreboard", "stats_scoreboard", {}),
    ("draft composition", "stats_draft_composition", {}),
    ("draft breakdown", "stats_draft_breakdown", {}),
    ("draft embellishments", "stats_draft_embellishments", {}),
    ("draft rates", "stats_draft_rates", {"limit": 15, "order": "most", "item_type": None}),
]


# Left out of /stats engagement unless asked for. Recorded like anyone else --
# their sessions are how a new build's tracking gets checked first -- but the
# two people who build content for a living would otherwise define "a player
# who prefers the tools". Matched on players.name, so a rename needs this too.
DEVELOPERS = {"Turner", "Caleb Gannon"}


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

        # The row count, not "by games played": the order is plain from the
        # Games column, and how many people are playing is not. With a limit
        # it is the players SHOWN, which is what the reader is looking at.
        #
        # `max_ritual` arrives with 2026-09-28_ritual_stats.sql. Before that the
        # column would render as a row of dashes, so it is left out and the
        # footer says why rather than implying nobody has played a ritual.
        columns = COLUMNS["active_players"]
        note = f"{len(records)} player{'' if len(records) == 1 else 's'}"
        if "max_ritual" not in records[0]:
            columns = [c for c in columns if c != "max_ritual"]
            note += " · ritual: run 2026-09-28_ritual_stats.sql"
        await _send_table(interaction, "Active players", records, columns, note=note)

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

        # One row per RUN (2026-09-28_ritual_stats.sql), caught the same way:
        # the hero table and both charts are drawn from it, and None renders as
        # "not migrated" in each rather than as a player with no runs.
        try:
            runs = fetch_all("player_run_view", filters={"player": player},
                             sort=["started_at"])
        except SupabaseError:
            runs = None

        try:
            link_rows = fetch_all("player_link_view", filters={"player": player})
        except SupabaseError:
            link_rows = None

        # One row, and hand-grouped rather than a field per column: the view
        # carries 22 columns and a flat dump of them is the JSON blob again with
        # nicer punctuation.
        row = records[0]
        embed = nextcord.Embed(title=row.get("player") or player, colour=0x5865F2)

        embed.add_field(name="Runs", inline=True, value=sf.record(row))
        embed.add_field(name="Best combo", inline=True,
                        value=sf.value("best_combo", row.get("best_combo")))

        # Per hero, replacing a single "Highest Ritual": ritual ladders are per
        # hero, and one number across heroes did not say which it was on.
        embed.add_field(name="Heroes (Top = highest ritual, Clr = beat act 3)",
                        inline=False, value=sf.player_heroes(runs))
        embed.add_field(name="Games by ritual", inline=False,
                        value=sf.player_ritual_chart(runs))
        embed.add_field(name="Furthest act", inline=False,
                        value=sf.player_act_chart(runs))

        embed.add_field(name="Max Reached", inline=False, value=sf.reached(row))
        # The spread first, then the per-act averages it summarises: whether a
        # player clears in two links or always runs out of nodes is invisible
        # in "3.5".
        embed.add_field(name="Regular turns by links played", inline=False,
                        value=sf.player_link_chart(link_rows))
        embed.add_field(name="Links per turn", inline=False,
                        value=sf.links_table(acts, row))
        embed.add_field(name="Patterns cleared", inline=False,
                        value=sf.clearing_table(acts, row))

        drafted = sf.most_drafted(row)
        if drafted:
            embed.add_field(name="Most drafted", inline=False, value=drafted)

        embed.set_footer(text=sf.footer(records, note=sf.last_played(row)))
        await interaction.followup.send(embed=embed)

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
                embeds = await asyncio.to_thread(
                    lambda: du._build_update_embeds(du._fetch_daily_stats()))
                for embed in embeds:
                    embed.title = f"{embed.title} (preview)"
                    await interaction.followup.send(embed=embed)
            except Exception as e:
                await preview.followup.send(f"❌ failed\n```{e}```")
            count += 1

        failed = preview.followup.text_replies
        summary = f"✅ {count} reports run."
        if failed:
            summary += f" {len(failed)} replied with text instead of an embed: " + ", ".join(failed)
        await interaction.followup.send(summary)

    # --- Engagement ---
    # 2026-09-28. Time in runs against time in the Codex tools, per player,
    # from engagement_spans. See stats_format § Engagement.
    @stats_cmd.subcommand(name="engagement", description="Time in the game vs time in the Codex tools")
    @safe_interaction(timeout=10, error_message="❌ Failed to fetch engagement.")
    async def stats_engagement(
        self,
        interaction: Interaction,
        include_devs: bool = SlashOption(description="Include Turner and Caleb",
                                         required=False, default=False),
    ):
        try:
            rows = fetch_all("player_engagement_view")
            actions = fetch_all("player_engagement_actions_view")
        except SupabaseError:
            return ("❌ `player_engagement_view` is not migrated — run "
                    "`db/migrations/2026-09-28_engagement_spans.sql`.")

        excluded = 0
        if not include_devs:
            excluded = sum(1 for r in rows if r.get("player") in DEVELOPERS)
            rows = [r for r in rows if r.get("player") not in DEVELOPERS]
            actions = [a for a in actions if a.get("player") not in DEVELOPERS]
        if not rows:
            return ("❌ No engagement recorded yet. It starts with the first build "
                    "carrying EngagementTracker.")
        counted, too_little = sf.engagement_split(rows)

        embed = nextcord.Embed(title="Engagement", colour=0x8E44AD)
        embed.add_field(name="Players by share of time in the tools", inline=False,
                        value=sf.engagement_chart(counted))
        embed.add_field(name="Time per player (active only)", inline=False,
                        value=sf.engagement_table(counted))
        embed.add_field(name="Made in the Codex", inline=False,
                        value=sf.engagement_actions_chart(actions))

        floor = sf.duration(sf.MIN_ENGAGEMENT_SEC)
        note = f"{len(counted)} player{'' if len(counted) == 1 else 's'} with {floor}+"
        if too_little:
            note += f" · {too_little} with less not shown"
        if excluded:
            note += " · developers excluded"
        embed.set_footer(text=sf.footer([], note=note))
        await interaction.followup.send(embed=embed)

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
    # A subcommand GROUP, 2026-09-03. The three replies are neighbours but they
    # are not one reply: the composition is content with no games behind it and
    # no cutoff, while the two rate views are games at 0.9.10+ filtered further
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

    # --- Draft Rates, by embellishment ---
    @stats_draft.subcommand(name="embellishments",
                            description="Does an embellished card get picked more?")
    @safe_interaction(timeout=10, error_message="❌ Failed to fetch embellishment data.")
    async def stats_draft_embellishments(self, interaction: Interaction):
        try:
            records = fetch_all("draft_embellishment_rates_view")
        except SupabaseError:
            return ("❌ `draft_embellishment_rates_view` is not migrated — run "
                    "`db/migrations/2026-09-04_draft_item_embellishments.sql`.")

        if not records:
            # Distinct from "not migrated": the view filters
            # `embellished is not null`, so it is empty until runs from a client
            # that records the columns land. Saying which is the difference
            # between waiting and debugging.
            return ("❌ No embellishment data yet — `draft_items` only carries it "
                    "from the 2026-09-04 migration onward.")

        embed = nextcord.Embed(title="Draft picks by embellishment", colour=0x1ABC9C)
        # The headline split leads, and the lift is stated in words underneath
        # it rather than left as a subtraction between two table rows.
        embed.add_field(name="Bare vs embellished", inline=False,
                        value=sf.draft_rate_by_embellishment(records))
        embed.add_field(name="Lift", inline=False,
                        value=sf.draft_embellishment_lift_line(records))
        embed.add_field(name="By kind", inline=False,
                        value=sf.draft_rate_by_kind(records))
        embed.add_field(name="By enhancement", inline=False,
                        value=sf.draft_rate_by_enhancement(records))
        embed.add_field(name="By attribute", inline=False,
                        value=sf.draft_rate_by_attribute(records))

        # NOT len(records) and not a sum: an offer appears in the `embellished`
        # dimension and again in every kind it carries.
        offers = sf.draft_embellishment_offers(records)
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
            # draft_rates_view.item_type says `rite`, or `event` on a database
            # the rename migration has not reached. The filter below asks
            # rite_schema which one to send.
            choices={"Card": "card", "Aspect": "aspect", "Rite": "rite"},
        ),
    ):
        # draft_rates_view returns ONE ROW PER ITEM as of 2026-08-26, carrying
        # times_picked AND times_offered rather than a pre-formatted string, so
        # the limit has to be applied here or this dumps every draftable item.
        # The view is ordered pick_rate DESC, so "least" just reverses it.
        from azoth_logic import rite_schema
        filters = {"item_type": rite_schema.db_content_type(item_type)} if item_type else None
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
    cls.stats_active_players = stats_active_players
    cls.stats_leaderboard = stats_leaderboard
    cls.stats_player = stats_player
    cls.stats_breakdown = stats_breakdown
    cls.stats_all = stats_all
    cls.stats_engagement = stats_engagement
    cls.stats_bosses = stats_bosses
    cls.stats_scoreboard = stats_scoreboard
    # The group AND each of its subcommands. Assigning only the group would
    # leave the three bodies unreachable in exactly the way
    # test_command_registration.py exists to catch.
    cls.stats_draft = stats_draft
    cls.stats_draft_composition = stats_draft_composition
    cls.stats_draft_breakdown = stats_draft_breakdown
    cls.stats_draft_embellishments = stats_draft_embellishments
    cls.stats_draft_rates = stats_draft_rates
