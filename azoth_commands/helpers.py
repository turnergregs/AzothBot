import asyncio
import functools
import nextcord
import os
import re

from dotenv import load_dotenv
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


def generate_image_filename(name: str, version: int) -> str:
	safe_name = re.sub(r'\W+', '_', name.lower()).strip('_')
	return f"{safe_name}_{version}.png"

def generate_local_filename(name: str) -> str:
	safe_name = re.sub(r'\W+', '_', name.lower()).strip('_')
	return f"{safe_name}.png"

def get_local_image_path(supabase_image_name: str, download_dir: str = "assets/downloaded_images") -> str:
	"""
	Converts a Supabase image name (e.g. 'catalyst_of_anima_2.png') into a local path
	where the image is saved as just 'catalyst_of_anima.png'.
	"""
	match = re.match(r"(.+?)(?:_\d+)?\.png$", supabase_image_name)
	base_name = match.group(1) if match else os.path.splitext(supabase_image_name)[0]
	local_filename = f"{base_name}.png"
	return os.path.join(download_dir, local_filename)


def generate_and_upload_image(obj_data: dict, bucket: str, ritual_side: str = "") -> tuple[bool, str | bytes]:
	from azoth_logic.image_generator import generate_image
	from supabase_storage import upload_image
	"""
	Generates and uploads an image for any supported game object type (card, ritual, event, etc.)
	Returns (success: bool, file_path or error string)
	"""

	# Generate image using shared function
	if ritual_side == "reward":
		success, image_path = generate_image(obj_data, is_dark=True)
	else:
		success, image_path = generate_image(obj_data)
	if not success:
		return False, image_path  # this is the error string

	try:
		with open(image_path, "rb") as f:
			image_bytes = f.read()

		if ritual_side != "":
			return upload_image(obj_data[f"{ritual_side}_name"], image_bytes, bucket)
		else:
			return upload_image(obj_data["name"], image_bytes, bucket)

	except Exception as e:
		return False, f"❌ Failed to upload image: {e}"
