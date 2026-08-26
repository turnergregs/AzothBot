import nextcord
from nextcord.ext import commands
from dotenv import load_dotenv
import os

from azoth_commands import *
from supabase_client import SUPABASE_ROLE

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = nextcord.Intents.default()
bot = commands.Bot(intents=intents)
# bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
	print(f"✅ Logged in as {bot.user}")

	# Say which Supabase key is loaded, out loud, every start. The difference is
	# otherwise invisible until a command quietly returns nothing: an anon key
	# gets HTTP 200 + an empty array on tables it may not read.
	# See docs/DB_SCHEMA.md § Which key you are holding.
	if SUPABASE_ROLE == "service_role":
		print("🔑 Supabase key: service_role (full read/write, RLS bypassed)")
	else:
		print(f"⚠️  Supabase key: {SUPABASE_ROLE} — NOT service_role.")
		print("   /stats and the turn-grain tables will be unavailable.")
		print("   Commands that write content will fail. This is a dev key.")

	try:
		dev_guild_id = int(os.getenv("DEV_GUILD_ID"))
		await bot.sync_application_commands(guild_id=dev_guild_id)
		# await bot.sync_all_application_commands()
		print(f"🔁 Synced slash commands to dev guild {dev_guild_id}")
	except nextcord.HTTPException as e:
		print("❌ Failed to sync commands:")
		print(f"  Status: {e.status}")
		print(f"  Code: {e.code}")
		print(f"  Text: {e.text}")
		print(f"  Response: {e.response}")


bot.add_cog(AzothCommands(bot))
bot.run(TOKEN)
