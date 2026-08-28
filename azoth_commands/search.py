"""`/search` -- find cards, aspects and rites and render the results.

Mirrors the Codex's search (scripts/UI/codex/content_search.gd in the azoth
repo) so a query means the same thing in both places, including its deep search
of the `actions` / `triggers` / `properties` JSON.

Results render as a static grid, for the same reason `/render_deck` does: twenty
animated cards would be tens of megabytes and unreadable at thumbnail size.
`/render` is still the way to watch one move.
"""
import asyncio
import io

import nextcord
from nextcord import Interaction, SlashOption

from azoth_commands.helpers import safe_interaction, missing_asset_hint
from constants import DEV_GUILD_ID
from supabase_helpers import fetch_all, SupabaseError
from azoth_logic import content_index as ci
from azoth_logic import content_search as cs
from azoth_logic import deck_render

# Rendering is ~0.7s per item on a cold cache, almost all of it art download, so
# an uncapped search over the 626-item pool would run for minutes. Truncation is
# always reported rather than silent.
DEFAULT_LIMIT = 20
MAX_LIMIT = 40

# Columns in the results sheet. Fewer than a deck's 10 because a search returns
# tens, not hundreds, and larger tiles stay readable.
SEARCH_COLUMNS = 5
SEARCH_CARD_WIDTH = 260


def _pool():
    """Every searchable item as (row, kind) pairs -- LIVE content only.

    Three tables, one pass. At 626 rows this is well under a second and simpler
    than pushing filters into PostgREST -- and the deep JSON search could not be
    expressed there anyway.

    The liveness filter cuts that 626 to 233: a row in no unarchived deck cannot
    be drafted, summoned or drawn, so a search that returned it would be
    answering about content that does not exist in the game. See
    `content_index` § Liveness. When the deck read fails, `live_ids` comes back
    empty and this filters nothing -- a search that silently returned zero rows
    because of a bad key would be worse than one that over-answers.
    """
    live = ci.live_ids()
    pool = []
    for kind, table in cs.KIND_TABLE.items():
        ids = live.get(kind) if any(live.values()) else None
        for row in fetch_all(table, limit=1000):
            if ids is None or row.get("id") in ids:
                pool.append((row, kind))
    return pool


def _live_rows(table: str, kind: str, columns: list) -> list:
    """Rows of one table, live only -- for the vocabulary autocompletes.

    They exist to suggest values that `/search` can actually match, so they have
    to be drawn from the same pool it searches. Offering a subtype that survives
    only on retired cards is offering a guaranteed empty result.
    """
    live = ci.live_ids()
    ids = live.get(kind) if any(live.values()) else None
    return [r for r in fetch_all(table, columns, limit=1000)
            if ids is None or r.get("id") in ids]


def add_search_commands(cls):

    @nextcord.slash_command(name="search", description="Search cards, aspects and rites.",
                            guild_ids=[DEV_GUILD_ID])
    # Cold-cache worst case is 40 items x ~0.7s; the art cache makes repeats far
    # quicker, but the first search of a session pays full price.
    @safe_interaction(timeout=120, error_message="❌ Search failed.")
    async def search_cmd(
        self,
        interaction: Interaction,
        query: str = SlashOption(
            description="Free text: name, rules text, subtype — and inside actions/triggers/properties",
            required=False),
        content_type: str = SlashOption(
            description="Restrict to one content type", required=False,
            choices={"Card": "card", "Aspect": "aspect", "Rite": "rite"}),
        element: str = SlashOption(
            description="Card element", required=False,
            choices={"Blood": "blood", "Sol": "sol", "Anima": "anima",
                     "Colourless": cs.COLOURLESS}),
        valence: int = SlashOption(description="Exact valence", required=False),
        subtype: str = SlashOption(description="Subtype, e.g. Wild", required=False,
                                   autocomplete=True),
        card_type: str = SlashOption(
            description="Card type", required=False,
            choices={"Spell": "spell", "Catalyst": "catalyst", "Power": "power"}),
        action: str = SlashOption(description="Runs a named action, e.g. Draw",
                                  required=False, autocomplete=True),
        sort: str = SlashOption(description="Result order", required=False, default="name",
                                choices={"Name": "name", "Valence": "valence",
                                         "Element": "element"}),
        limit: int = SlashOption(description=f"Max results (default {DEFAULT_LIMIT})",
                                 required=False, default=DEFAULT_LIMIT),
    ):
        filters = {"query": query, "content_type": content_type, "element": element,
                   "valence": valence, "subtype": subtype, "card_type": card_type,
                   "action": action}

        # Three table reads plus a deep JSON scan over 626 rows -- blocking, and
        # long enough to stall the gateway heartbeat if left on the event loop.
        try:
            hits = await asyncio.to_thread(lambda: cs.search(_pool(), sort=sort, **filters))
        except SupabaseError as e:
            return f"⚠️ Could not read content: {e}"

        summary = cs.describe(filters)
        if not hits:
            return f"🔍 No results — {summary}"

        capped = max(1, min(limit, MAX_LIMIT))
        shown = hits[:capped]
        note = f" · showing {len(shown)} of {len(hits)}" if len(hits) > len(shown) else ""

        try:
            image = await asyncio.to_thread(
                deck_render.render_grid,
                [i for i, _ in shown], SEARCH_COLUMNS, SEARCH_CARD_WIDTH,
                [k for _, k in shown])
        except FileNotFoundError as e:
            return f"⚠️ Missing render asset: {e}\n{missing_asset_hint(e)}"

        names = ", ".join(f"`{i['name']}`" for i, _ in shown[:10])
        if len(shown) > 10:
            names += f", … (+{len(shown) - 10})"

        await interaction.followup.send(
            f"🔍 **{len(hits)}** result{'' if len(hits) == 1 else 's'} — {summary}{note}\n{names}",
            file=nextcord.File(io.BytesIO(image), filename="search.png"))

    @search_cmd.on_autocomplete("subtype")
    async def autocomplete_subtype(self, interaction: Interaction, input: str):
        # Derived from live content rather than a hardcoded list, so a new
        # subtype appears without a code change -- and scoped to the same pool
        # `/search` covers, so no suggestion can return zero results.
        def collect():
            found = set()
            for row in _live_rows("cards", "card", ["id", "subtypes"]):
                for s in row.get("subtypes") or []:
                    if s and (not input or input.lower() in str(s).lower()):
                        found.add(str(s))
            return sorted(found)[:25]

        await interaction.response.send_autocomplete(await asyncio.to_thread(collect))

    @search_cmd.on_autocomplete("action")
    async def autocomplete_action(self, interaction: Interaction, input: str):
        def collect():
            found = set()
            for row in _live_rows("cards", "card", ["id", "actions"]):
                for a in row.get("actions") or []:
                    name = a.get("name") if isinstance(a, dict) else None
                    if name and (not input or input.lower() in str(name).lower()):
                        found.add(str(name))
            return sorted(found)[:25]

        await interaction.response.send_autocomplete(await asyncio.to_thread(collect))

    cls.search_cmd = search_cmd
