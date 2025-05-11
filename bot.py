import nextcord
from nextcord.ext import commands
from dotenv import load_dotenv
import os

from azoth_commands import *

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = nextcord.Intents.default()
bot = commands.Bot(intents=intents)
# bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
	print(f"✅ Logged in as {bot.user}")

	try:
		dev_guild_id = int(os.getenv("DEV_GUILD_ID"))
		await bot.sync_application_commands(guild_id=dev_guild_id)
		print(f"🔁 Synced slash commands to dev guild {dev_guild_id}")
	except nextcord.HTTPException as e:
		print("❌ Failed to sync commands:")
		print(f"  Status: {e.status}")
		print(f"  Code: {e.code}")
		print(f"  Text: {e.text}")
		print(f"  Response: {e.response}")


bot.add_cog(AzothCommands(bot))
bot.run(TOKEN)
