import asyncio
import io
import nextcord
import aiohttp
from datetime import datetime, timezone
from nextcord import SlashOption, Interaction
from azoth_commands.helpers import safe_interaction, pack_fields_into_embeds
from constants import DEV_GUILD_ID
from supabase_helpers import fetch_all
from supabase_client import supabase
from azoth_logic import bulk_report, content_index, deck_render


# Discord messages cap at 2000 chars; leave room for the success summary
_MAX_ERROR_LINES = 15
_MAX_RESPONSE_CHARS = 1800


def _format_bulk_summary(success_lines: list[str], error_lines: list[str]) -> str:
	parts: list[str] = []
	if success_lines:
		parts.extend(success_lines)
	if error_lines:
		shown = error_lines[:_MAX_ERROR_LINES]
		parts.append("**Errors:**")
		parts.extend(shown)
		if len(error_lines) > _MAX_ERROR_LINES:
			parts.append(f"... and {len(error_lines) - _MAX_ERROR_LINES} more (see bot console).")
	message = "\n".join(parts)
	if len(message) > _MAX_RESPONSE_CHARS:
		message = message[:_MAX_RESPONSE_CHARS] + "\n...(truncated; see bot console)"
	return message


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


def add_misc_commands(cls):

	@nextcord.slash_command(name="bulk_update", description="Bulk update fields on existing records using a JSON file.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=60, error_message="❌ Failed to bulk update.", require_authorized=True)
	async def bulk_update_cmd(
	    self,
	    interaction: Interaction,
	    json_file: nextcord.Attachment = SlashOption(description="Upload a JSON file", required=True)
	):
	    # Download the uploaded JSON file
	    async with aiohttp.ClientSession() as session:
	        async with session.get(json_file.url) as resp:
	            try:
	                payload = await resp.json()
	            except Exception as e:
	                return f"❌ Uploaded file is not valid JSON: {e}"

	    if not isinstance(payload, dict):
	        return "❌ JSON must be an object with table names as keys."

	    success_lines: list[str] = []
	    error_lines: list[str] = []
	    changes: dict = {}
	    touched: list = []
	    total_updates = 0

	    # Iterate over each table in the JSON
	    for table, updates in payload.items():
	        if not isinstance(updates, list):
	            error_lines.append(f"⚠️ Skipped `{table}` (value is not a list).")
	            continue

	        table_updates = 0
	        for entry in updates:
	            original_name = entry.get("name")
	            if not original_name:
	                error_lines.append(f"⚠️ `{table}`: entry missing `name` field; skipped.")
	                continue

	            update_data = entry.copy()
	            update_data.pop("name", None)

	            if "new_name" in update_data:
	                update_data["name"] = update_data.pop("new_name")

	            # Lookup record by original name
	            try:
	                matches = fetch_all(table, filters={"name": original_name})
	            except Exception as e:
	                error_lines.append(f"❌ `{table}` / `{original_name}`: lookup failed — `{e}`")
	                continue

	            if not matches:
	                error_lines.append(f"⚠️ `{table}` / `{original_name}`: no record with that name.")
	                continue

	            record = matches[0]
	            try:
	                update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
	                response = supabase.table(table).update(update_data).eq("id", record["id"]).execute()
	                if response.data:
	                    table_updates += 1
	                    # `record` is the row as it was; response.data[0] is the row
	                    # as written. Diffing them is what turns "updated 5
	                    # records" into a report you can actually check.
	                    after = response.data[0]
	                    changes[(table, after.get("name") or original_name)] = bulk_report.diff(record, after)
	                    if table in bulk_report.RENDERABLE:
	                        touched.append((after, bulk_report.RENDERABLE[table]))
	                else:
	                    error_lines.append(f"⚠️ `{table}` / `{original_name}`: update returned no rows.")
	            except Exception as e:
	                error_lines.append(f"❌ `{table}` / `{original_name}`: {e}")

	        if table_updates > 0:
	            success_lines.append(f"✅ Updated {table_updates} record(s) in `{table}`.")
	            total_updates += table_updates

	    if total_updates == 0 and not error_lines:
	        return "❌ No records were updated (input contained no actionable rows)."

	    if total_updates == 0:
	        return "❌ No records were updated.\n" + _format_bulk_summary(success_lines, error_lines)

	    content_index.invalidate()
	    await _send_bulk_report(
	        interaction, "Bulk update", total_updates, changes, error_lines,
	        touched=touched, empty_note="no field changed")


	cls.bulk_update_cmd = bulk_update_cmd


	@nextcord.slash_command(name="bulk_insert", description="Bulk insert new records using a JSON file.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=60, error_message="❌ Failed to bulk insert.", require_authorized=True)
	async def bulk_insert_cmd(
	    self,
	    interaction: Interaction,
	    json_file: nextcord.Attachment = SlashOption(description="Upload a JSON file", required=True)
	):
	    # Download the uploaded JSON file
	    async with aiohttp.ClientSession() as session:
	        async with session.get(json_file.url) as resp:
	            try:
	                payload = await resp.json()
	            except Exception as e:
	                return f"❌ Uploaded file is not valid JSON: {e}"

	    if not isinstance(payload, dict):
	        return "❌ JSON must be an object with table names as keys."

	    success_lines: list[str] = []
	    error_lines: list[str] = []
	    created: dict = {}
	    total_inserts = 0

	    for table, records in payload.items():
	        if not isinstance(records, list):
	            error_lines.append(f"⚠️ Skipped `{table}` (value is not a list).")
	            continue

	        table_inserts = 0
	        for index, entry in enumerate(records):
	            if not isinstance(entry, dict) or not entry:
	                error_lines.append(f"⚠️ `{table}[{index}]`: entry is empty or not an object; skipped.")
	                continue

	            label = entry.get("name") or f"index {index}"
	            try:
	                response = supabase.table(table).insert(entry).execute()
	                if response.data:
	                    table_inserts += 1
	                    created.setdefault(table, []).append(
	                        bulk_report.summarize_new(table, response.data[0]))
	                else:
	                    error_lines.append(f"⚠️ `{table}` / `{label}`: insert returned no data.")
	            except Exception as e:
	                error_lines.append(f"❌ `{table}` / `{label}`: {e}")

	        if table_inserts > 0:
	            success_lines.append(f"✅ Inserted {table_inserts} record(s) into `{table}`.")
	            total_inserts += table_inserts

	    if total_inserts == 0 and not error_lines:
	        return "❌ No records were inserted (input contained no actionable rows)."

	    if total_inserts == 0:
	        return "❌ No records were inserted.\n" + _format_bulk_summary(success_lines, error_lines)

	    content_index.invalidate()
	    # Deliberately NOT rendered. Art is uploaded after an insert, not with
	    # it, so every card would come back with a hole in the middle -- which
	    # reads as a broken render rather than as "no art yet". The per-row
	    # summary flags `no art` instead.
	    await _send_bulk_report(
	        interaction, "Bulk insert", total_inserts,
	        {(t, ""): lines for t, lines in created.items()}, error_lines,
	        footer="Art is uploaded separately, so nothing is rendered here.")


	cls.bulk_insert_cmd = bulk_insert_cmd
