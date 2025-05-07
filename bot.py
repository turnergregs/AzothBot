import nextcord
from nextcord.ext import commands
from dotenv import load_dotenv
import os

from azoth_commands import AzothCommands

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = nextcord.Intents.default()
bot = commands.Bot(intents=intents)
# bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
	print(f"✅ Logged in as {bot.user}")
	await bot.sync_all_application_commands()

bot.add_cog(AzothCommands(bot))
bot.run(TOKEN)
