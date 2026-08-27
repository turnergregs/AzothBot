import asyncio
import io
import nextcord
import aiohttp
from nextcord import SlashOption, Interaction
from azoth_commands.helpers import safe_interaction, pack_fields_into_embeds
from constants import DEV_GUILD_ID
from azoth_logic import bulk_apply, bulk_report, content_index, deck_render, taxonomy


# How many updated items get drawn. Rendering is ~0.7s each on a cold cache, and
# a wall of thumbnails stops being useful well before this.
MAX_RENDERED = 12


async def _send_bulk_report(interaction, title, total, groups, error_lines,
                            touched=None, footer=None, empty_note="no detail"):
    """Reply with what the bulk action actually did.

    `groups` maps (table, name) -> lines. bulk_update passes one entry per
    RECORD with its field diff; bulk_insert passes one entry per TABLE with a
    line per new row. Both render as embed fields, which is what keeps a long
    report readable -- a plain message caps at 2000 characters and a big insert
    blows past it.

    A big report does not fit in ONE embed either: fields cap at 1024 characters
    each but the whole embed caps at 6000, and going over is a 400 that loses the
    entire reply. `pack_fields_into_embeds` splits instead of truncating, because
    a write report that silently drops rows is worse than a second message.
    """
    fields = []
    for (table, name), lines in groups.items():
        label = f"{name} · {table}" if name else f"{table} ({len(lines)})"
        fields.append((label, bulk_report.fit(lines) if lines else f"*{empty_note}*", False))

    if error_lines:
        fields.append((f"Errors ({len(error_lines)})", bulk_report.fit(error_lines), False))

    image = None
    if touched:
        renderable = touched[:MAX_RENDERED]
        try:
            # Blocking: art downloads plus PIL. Off the event loop.
            data = await asyncio.to_thread(
                deck_render.render_grid,
                [r for r, _ in renderable], min(4, len(renderable)), 240,
                [k for _, k in renderable])
            image = nextcord.File(io.BytesIO(data), filename="updated.png")
            if len(touched) > len(renderable):
                footer = f"Showing {len(renderable)} of {len(touched)} updated items."
        except Exception as e:
            fields.append(("Render", f"⚠️ could not render: {e}", False))

    embeds = pack_fields_into_embeds(
        fields,
        title=f"{title} — {total} record{'' if total == 1 else 's'}",
        colour=0x2ecc71 if not error_lines else 0xe67e22,
        footer=footer,
    )

    # The image rides with the LAST embed, after every field it summarises.
    for embed in embeds[:-1]:
        await interaction.followup.send(embed=embed)
    if image:
        await interaction.followup.send(embed=embeds[-1], file=image)
    else:
        await interaction.followup.send(embed=embeds[-1])


async def _download_payload(json_file):
    """The uploaded attachment, parsed. Returns (payload, error_message)."""
    async with aiohttp.ClientSession() as session:
        async with session.get(json_file.url) as resp:
            try:
                return await resp.json(), None
            except Exception as e:
                return None, f"❌ Uploaded file is not valid JSON: {e}"


def add_misc_commands(cls):

	@nextcord.slash_command(name="bulk_update", description="Bulk update fields on existing records using a JSON file.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=60, error_message="❌ Failed to bulk update.", require_authorized=True)
	async def bulk_update_cmd(
	    self,
	    interaction: Interaction,
	    json_file: nextcord.Attachment = SlashOption(description="Upload a JSON file", required=True)
	):
	    payload, error = await _download_payload(json_file)
	    if error:
	        return error

	    # One RPC, one transaction. A bad record anywhere aborts the whole
	    # payload, so there is no partial state to report -- either every record
	    # applied or none did. The call is blocking network I/O; off the loop.
	    try:
	        results = await asyncio.to_thread(bulk_apply.apply, payload, "update")
	    except bulk_apply.BulkApplyError as e:
	        return f"❌ Nothing was written — the whole payload was rolled back.\n{e}"

	    changes: dict = {}
	    touched: list = []
	    for result in results:
	        table = result["table"]
	        # `before` and `after` come back from the function precisely so the
	        # field diff can still be built here, where the tests can reach it.
	        changes[(table, result["name"])] = bulk_report.diff(
	            result["before"] or {}, result["after"])
	        if table in bulk_report.RENDERABLE:
	            touched.append((result["after"], bulk_report.RENDERABLE[table]))

	    content_index.invalidate()
	    taxonomy.invalidate()
	    await _send_bulk_report(
	        interaction, "Bulk update", len(results), changes, [],
	        touched=touched, empty_note="no field changed")


	cls.bulk_update_cmd = bulk_update_cmd


	@nextcord.slash_command(name="bulk_insert", description="Bulk insert new records using a JSON file.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=60, error_message="❌ Failed to bulk insert.", require_authorized=True)
	async def bulk_insert_cmd(
	    self,
	    interaction: Interaction,
	    json_file: nextcord.Attachment = SlashOption(description="Upload a JSON file", required=True)
	):
	    payload, error = await _download_payload(json_file)
	    if error:
	        return error

	    # All-or-nothing, same as /bulk_update. This matters more on insert: a
	    # half-applied insert used to leave orphan rows that no command could
	    # remove once the /delete_* commands were retired.
	    try:
	        results = await asyncio.to_thread(bulk_apply.apply, payload, "insert")
	    except bulk_apply.BulkApplyError as e:
	        return f"❌ Nothing was written — the whole payload was rolled back.\n{e}"

	    created: dict = {}
	    for result in results:
	        table = result["table"]
	        created.setdefault(table, []).append(
	            bulk_report.summarize_new(table, result["after"]))

	    content_index.invalidate()
	    taxonomy.invalidate()
	    # Deliberately NOT rendered. Art is uploaded after an insert, not with
	    # it, so every card would come back with a hole in the middle -- which
	    # reads as a broken render rather than as "no art yet". The per-row
	    # summary flags `no art` instead.
	    await _send_bulk_report(
	        interaction, "Bulk insert", len(results),
	        {(t, ""): lines for t, lines in created.items()}, [],
	        footer="Art is uploaded separately, so nothing is rendered here.")


	cls.bulk_insert_cmd = bulk_insert_cmd
