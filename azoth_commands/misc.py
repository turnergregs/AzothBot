import os
import json
import nextcord
import aiohttp
from datetime import datetime, timezone
from nextcord.ext import commands
from nextcord import SlashOption, Interaction
from azoth_commands.helpers import safe_interaction, record_to_json, to_snake_case
from azoth_commands.autocomplete import autocomplete_from_table
from constants import DEV_GUILD_ID, BOT_PLAYER_ID
from supabase_helpers import fetch_all, update_record, create_record
from supabase_client import supabase


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

	    return _format_bulk_summary(success_lines, error_lines)


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

	    return _format_bulk_summary(success_lines, error_lines)


	cls.bulk_insert_cmd = bulk_insert_cmd
