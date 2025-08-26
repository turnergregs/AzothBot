import os
import json
import nextcord
import aiohttp
from nextcord.ext import commands
from nextcord import SlashOption, Interaction
from azoth_commands.helpers import safe_interaction, record_to_json, to_snake_case
from azoth_commands.autocomplete import autocomplete_from_table
from constants import DEV_GUILD_ID, BOT_PLAYER_ID
from supabase_helpers import fetch_all, update_record


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
	            except Exception:
	                return "❌ Uploaded file is not valid JSON."

	    if not isinstance(payload, dict):
	        return "❌ JSON must be an object with table names as keys."

	    summary = []
	    total_updates = 0

	    # Iterate over each table in the JSON
	    for table, updates in payload.items():
	        if not isinstance(updates, list):
	            summary.append(f"⚠️ Skipped `{table}` (not a list).")
	            continue

	        table_updates = 0
	        for entry in updates:
	            original_name = entry.get("name")
	            if not original_name:
	                continue

	            update_data = entry.copy()
	            update_data.pop("name", None)

	            if "new_name" in update_data:
	                update_data["name"] = update_data.pop("new_name")

	            # Lookup record by original name
	            matches = fetch_all(table, filters={"name": original_name})
	            if not matches:
	                summary.append(f"⚠️ `{table}`: No match for `{original_name}`")
	                continue

	            record = matches[0]
	            result = update_record(table, record["id"], update_data)
	            if result:
	                table_updates += 1

	        if table_updates > 0:
	            summary.append(f"✅ Updated {table_updates} record(s) in `{table}`.")
	            total_updates += table_updates

	    if total_updates == 0:
	        return "❌ No records were updated."

	    return "\n".join(summary)


	cls.bulk_update_cmd = bulk_update_cmd
