"""`/show`, `/render` and `/rules` -- one command each, across all content types.

Replaces six typed commands (`/get_card`, `/get_aspect`, `/get_rite` and their
`/render_*` counterparts). You pick from an autocomplete that disambiguates by
type and id -- `Diversity (Card #447)` -- and the command dispatches on what you
picked, so there is nothing to remember about which noun a thing is.

The value behind each choice is the same encoded ref the deck commands use
(`card:447`), so one lookup path serves both.
"""
import asyncio
import io
import json

import nextcord
from nextcord import Interaction, SlashOption

from azoth_commands.helpers import safe_interaction, missing_asset_hint, to_snake_case
from constants import DEV_GUILD_ID
from azoth_logic import (card_layout, card_render, content_index as ci, deck_render,
                         fate_layout, fate_render, holo, placeholders, upgrades)


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
      * `attunement` on aspects -- every live aspect is 1, so it distinguishes
        nothing (dropped 2026-08-28)

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
    elif kind == "rite":
        if row.get("foresight") is not None:
            facts.append(("Foresight", str(row["foresight"])))
    return facts


def _render_any(kind: str, row: dict):
    """(bytes, extension) for any content type."""
    if kind == "card":
        return card_render.render(row)
    return fate_render.render(row, kind)


def _comparison_labels(tiers: list) -> list:
    """Captions for a base face followed by each upgraded tier.

    ASCII only. The card font has no arrow glyph and a missing one renders as a
    silent gap, so "Upgraded -> Aspect" would read as "Upgraded   Aspect".
    """
    labels = ["Base"]
    for _, kind, level in tiers:
        name = "Upgraded" if len(tiers) == 1 else f"Tier {level}"
        # Only worth saying when the upgrade CHANGES what the thing is.
        labels.append(f"{name} ({ci.DISPLAY[kind]})" if kind != "card" else name)
    return labels


def _comparison(kind: str, row: dict):
    """The card beside each of its upgraded states. Returns (bytes, extension).

    Animated whenever either face is: a GIF when at least one side has
    eigenfunction art, a PNG when neither does. A still side holds its frame
    while the other moves, so a card that upgrades into a flat-art aspect still
    animates on the left.
    """
    tiers = upgrades.tiers(row, kind)
    # The `+` goes on the face, the way the game puts it on the label -- never
    # into the row, so the cache key and the filename stay the database's.
    upgraded = [{**t[0], "name": upgrades.plus_name(t[0].get("name"))} for t in tiers]
    return deck_render.render_comparison(
        [row] + upgraded,
        [kind] + [t[1] for t in tiers],
        _comparison_labels(tiers),
        # Both faces wear the sheen -- every card does, in-game -- but the
        # upgraded one wears it at the higher intensity `set_upgrade_card_visuals`
        # sets. That difference is the marker; a base card is not un-foiled.
        holo_levels=[holo.HOLO_INTENSITY] + [holo.UPGRADED_INTENSITY] * len(tiers),
        animate=True)


# The `jsonb` blobs. `/show` omits all four on purpose -- each one runs past
# Discord's 2000-character message limit on its own, and `upgrades` alone dwarfs
# the card it belongs to. Until now the documented way to read them was "query
# the database directly", for the fields that actually define the mechanic.
#
# A file attachment has no such limit, which is the whole trick here.
MECHANIC_FIELDS = ("actions", "triggers", "properties", "upgrades")


def _mechanics(row: dict) -> dict:
    """The mechanic-defining fields that are actually populated.

    Empty ones are dropped rather than emitted as `[]`: a file of empty arrays
    reads as "this has no rules", which is a different claim from "this field is
    unused on this card".
    """
    return {field: row[field] for field in MECHANIC_FIELDS if row.get(field)}


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
            # A miss is not always a missing row: retired content resolves to
            # nothing on purpose, and says so rather than claiming to be gone.
            return await asyncio.to_thread(ci.absence_reason, name)

        # `/show` prints the text instead of drawing it, so it is the one
        # surface that does not pass through `rich_text.tokenize` -- it resolves
        # display placeholders itself, or Recollection reads "Create last used
        # Rite ({last_rite})" here while the render beside it says "(None)".
        embed = nextcord.Embed(
            title=row.get("name") or "(unnamed)",
            description=placeholders.resolve(row.get("text") or "") or "*no rules text*",
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
        show_upgrade: bool = SlashOption(
            description="Show the upgraded version beside it (default: off)",
            required=False),
    ):
        kind, row = await asyncio.to_thread(ci.resolve, name)
        if not row:
            # A miss is not always a missing row: retired content resolves to
            # nothing on purpose, and says so rather than claiming to be gone.
            return await asyncio.to_thread(ci.absence_reason, name)

        # Opt-in. The plain single face is what `/render` is usually for, and
        # the comparison costs a second face's art and drawing -- and gives up
        # the animation on any card whose upgrade does not animate.
        upgradeable = upgrades.has_upgrade(row)
        comparing = bool(show_upgrade) and upgradeable

        # Downloading art and drawing are both blocking and can run for seconds.
        # On the event loop they starve the gateway heartbeat, and `wait_for`
        # cannot interrupt them either -- so the timeout would never fire.
        try:
            if comparing:
                data, ext = await asyncio.to_thread(_comparison, kind, row)
            else:
                data, ext = await asyncio.to_thread(_render_any, kind, row)
        except FileNotFoundError as e:
            return f"⚠️ Missing render asset: {e}\n{missing_asset_hint(e)}"
        except Exception as e:
            return f"⚠️ Could not render `{row['name']}`: {e}"

        note = None
        if comparing:
            note = f"🖼️ **{row['name']}** — base and upgraded."
        elif show_upgrade and not upgradeable:
            # Asked for a comparison against nothing. Saying so beats returning
            # one face and leaving someone to wonder which one it is.
            note = f"🖼️ **{row['name']}** has no upgrade to compare against."

        await interaction.followup.send(
            note,
            file=nextcord.File(io.BytesIO(data),
                               filename=f"{to_snake_case(row['name'])}.{ext}"))

    @nextcord.slash_command(name="rules", description="The mechanics JSON for a card, aspect or rite.",
                            guild_ids=[DEV_GUILD_ID])
    @safe_interaction(timeout=10, error_message="❌ Failed to read rules.")
    async def rules_cmd(
        self,
        interaction: Interaction,
        name: str = SlashOption(description="Card, aspect or rite", autocomplete=True),
    ):
        kind, row = await asyncio.to_thread(ci.resolve, name)
        if not row:
            # A miss is not always a missing row: retired content resolves to
            # nothing on purpose, and says so rather than claiming to be gone.
            return await asyncio.to_thread(ci.absence_reason, name)

        mechanics = _mechanics(row)
        if not mechanics:
            return (f"`{row['name']}` has no actions, triggers, properties or "
                    f"upgrades — its rules text is all there is.")

        blob = json.dumps(mechanics, indent=2, ensure_ascii=False)

        # One line per field so the reply says what is in the file without
        # opening it -- `upgrades` in particular is worth knowing about before
        # you download 8 KB to find out.
        summary = ", ".join(
            f"`{field}` ({len(value)})" if isinstance(value, list) else f"`{field}`"
            for field, value in mechanics.items())

        await interaction.followup.send(
            f"⚙️ **{row['name']}** — {ci.DISPLAY[kind]} #{row['id']}\n{summary}",
            file=nextcord.File(io.BytesIO(blob.encode("utf-8")),
                               filename=f"{to_snake_case(row['name'])}_rules.json"))

    @show_cmd.on_autocomplete("name")
    @render_cmd.on_autocomplete("name")
    @rules_cmd.on_autocomplete("name")
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
    cls.rules_cmd = rules_cmd
