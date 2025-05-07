# bot.py
import nextcord
from nextcord.ext import commands
from dotenv import load_dotenv
import os

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = nextcord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
	print(f"✅ Logged in as {bot.user}")

@bot.slash_command(name="ping", description="Check if AzothBot is online")
async def ping(interaction: nextcord.Interaction):
	await interaction.response.send_message("Pong!")

bot.run(TOKEN)
