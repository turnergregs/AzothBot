import asyncio
import functools
import nextcord
from dotenv import load_dotenv
import os

load_dotenv()

AUTHORIZED_USER_IDS = set(
	int(uid.strip())
	for uid in os.getenv("AUTHORIZED_USER_IDS", "").split(",")
	if uid.strip().isdigit()
)

def safe_interaction(timeout=10, error_message="⚠️ Something went wrong.", require_authorized=False):
	def decorator(func):
		@functools.wraps(func)
		async def wrapper(self, interaction: nextcord.Interaction, *args, **kwargs):

			try:
				# Authorization check
				if require_authorized and interaction.user.id not in AUTHORIZED_USER_IDS:
					await interaction.response.send_message(
						"❌ You’re not authorized to use this command.", ephemeral=True
					)
					return

				await interaction.response.defer()

				result = await asyncio.wait_for(
					func(self, interaction, *args, **kwargs),
					timeout=timeout
				)

				if result and isinstance(result, str):
					await interaction.followup.send(result)
				elif result is None:
					pass
				else:
					await interaction.followup.send(str(result))

			except asyncio.TimeoutError:
				await interaction.followup.send("⏰ Timed out.")
			except Exception as e:
				await interaction.followup.send(f"{error_message}\n```{e}```")

		return wrapper
	return decorator
