"""`/get` and `/render` -- one command each, across all content types.

Replaces six typed commands (`/get_card`, `/get_aspect`, `/get_rite` and their
`/render_*` counterparts). You pick from an autocomplete that disambiguates by
type and id -- `Diversity (Card #447)` -- and the command dispatches on what you
picked, so there is nothing to remember about which noun a thing is.

The value behind each choice is the same encoded ref the deck commands use
(`card:447`), so one lookup path serves both.
"""
import asyncio
import io

import nextcord
from nextcord import Interaction, SlashOption

from azoth_commands.helpers import safe_interaction, missing_asset_hint, to_snake_case
from constants import DEV_GUILD_ID
from azoth_logic import card_layout, card_render, content_index as ci, fate_layout, fate_render


# Embed accent, matching the card face. Aspects carry their own palette; rites
# take their foreground colour, falling back to the scene's blue.
def _accent(kind: str, row: dict) -> int:
    if kind == "aspect":
        _, secondary = fate_layout.aspect_colors(row)
        rgb = secondary
    elif kind == "rite":
        rgb = fate_layout.rite_text_color(row) or fate_layout.RITE_NAME_COLOR
    else:
        rgb = card_layout.element_color(row.get("element"))
    return (rgb[0] << 16) | (rgb[1] << 8) | rgb[2]


def _facts(kind: str, row: dict) -> list:
    """The type-specific attributes worth showing, as (label, value) pairs.

    Only the fields that describe the thing. Deliberately NOT shown:

      * `created_at` / `updated_at` / `created_by` -- audit metadata
      * `image` / `image_data` -- rendering internals; `/render` is the view
      * `upgrades` -- a nested blob that dwarfs everything else on the card
      * `actions` / `triggers` / `properties` -- jsonb, and past Discord's
        2000-char limit on their own

    Empty and null values are dropped rather than shown as `null`, which is most
    of what made the old JSON dump hard to read.
    """
    facts = []
    if kind == "card":
        element = row.get("element")
        facts.append(("Element", str(element).capitalize() if element else "Colourless"))
        if row.get("valence") is not None:
            facts.append(("Valence", str(row["valence"])))
        card_type = row.get("type")
        if card_type and str(card_type).lower() != "spell":
            facts.append(("Type", str(card_type).capitalize()))
        if row.get("subtypes"):
            facts.append(("Subtypes", ", ".join(str(s) for s in row["subtypes"])))
        if row.get("split"):
            split = row["split"]
            facts.append(("Split", f"{str(split.get('element','?')).capitalize()} "
                                   f"valence {split.get('valence','?')}"))
    elif kind == "aspect":
        if row.get("attunement") is not None:
            facts.append(("Attunement", str(row["attunement"])))
    elif kind == "rite":
        if row.get("foresight") is not None:
            facts.append(("Foresight", str(row["foresight"])))
    return facts


def _render_any(kind: str, row: dict):
    """(bytes, extension) for any content type."""
    if kind == "card":
        return card_render.render(row)
    return fate_render.render(row, kind)


def add_content_commands(cls):

    @nextcord.slash_command(name="show", description="Show details for a card, aspect or rite.",
                            guild_ids=[DEV_GUILD_ID])
    @safe_interaction(timeout=10, error_message="❌ Failed to show content.")
    async def show_cmd(
        self,
        interaction: Interaction,
        name: str = SlashOption(description="Card, aspect or rite", autocomplete=True),
    ):
        kind, row = await asyncio.to_thread(ci.resolve, name)
        if not row:
            return f"❌ Could not find `{name}`."

        embed = nextcord.Embed(
            title=row.get("name") or "(unnamed)",
            description=row.get("text") or "*no rules text*",
            colour=_accent(kind, row),
        )
        for label, value in _facts(kind, row):
            embed.add_field(name=label, value=value, inline=True)
        embed.set_footer(text=f"{ci.DISPLAY[kind]} #{row['id']}")
        await interaction.followup.send(embed=embed)

    @nextcord.slash_command(name="render", description="Render a card, aspect or rite.",
                            guild_ids=[DEV_GUILD_ID])
    @safe_interaction(timeout=30, error_message="❌ Failed to render.")
    async def render_cmd(
        self,
        interaction: Interaction,
        name: str = SlashOption(description="Card, aspect or rite", autocomplete=True),
    ):
        kind, row = await asyncio.to_thread(ci.resolve, name)
        if not row:
            return f"❌ Could not find `{name}`."

        # Downloading art and drawing are both blocking and can run for seconds.
        # On the event loop they starve the gateway heartbeat, and `wait_for`
        # cannot interrupt them either -- so the timeout would never fire.
        try:
            data, ext = await asyncio.to_thread(_render_any, kind, row)
        except FileNotFoundError as e:
            return f"⚠️ Missing render asset: {e}\n{missing_asset_hint(e)}"
        except Exception as e:
            return f"⚠️ Could not render `{row['name']}`: {e}"

        await interaction.followup.send(
            file=nextcord.File(io.BytesIO(data), filename=f"{to_snake_case(row['name'])}.{ext}"))

    @show_cmd.on_autocomplete("name")
    @render_cmd.on_autocomplete("name")
    async def autocomplete_content(self, interaction: Interaction, input: str):
        # Served from an in-process index (azoth_logic/content_index.py). Reading
        # the three tables live costs 0.85-2.3s, and Discord fires this on every
        # keystroke. A cache MISS still pays that, so it goes to a thread --
        # Discord allows 3s for an autocomplete reply and the event loop has to
        # stay free to send it.
        choices = await asyncio.to_thread(ci.choices, input)
        await interaction.response.send_autocomplete(choices)

    cls.show_cmd = show_cmd
    cls.render_cmd = render_cmd
