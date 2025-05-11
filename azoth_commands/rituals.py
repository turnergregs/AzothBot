import os
import json
import nextcord
from nextcord.ext import commands
from nextcord import SlashOption, Interaction

from azoth_commands.helpers import safe_interaction
from azoth_commands.autocomplete import autocomplete_from_choices
from constants import DEV_GUILD_ID, BOT_PLAYER_ID, RITUAL_IMAGE_BUCKET

def add_ritual_commands(cls):

	@nextcord.slash_command(name="create_ritual", description="Create a new ritual, event, or consumable.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=15, error_message="❌ Failed to create ritual.", require_authorized=True)
	async def create_ritual_cmd(
		self,
		interaction: Interaction,
		type: str = SlashOption(description="Ritual type", autocomplete=True),
		foresight: int = SlashOption(description="Foresight value"),

		reward_name: str = SlashOption(description="Reward side name"),
		reward_text: str = SlashOption(description="Reward side text"),

		challenge_name: str = SlashOption(description="Challenge side name", required=False),
		challenge_text: str = SlashOption(description="Challenge side text", required=False),
		challenge_difficulty: str = SlashOption(description="Challenge difficulty", required=False, autocomplete=True),
	):
		from supabase_client import create_ritual, create_ritual_side, upload_card_image, download_card_image
		from azoth_logic.ritual_renderer import RitualRenderer
		from azoth_logic.image_generator import generate_card_image

		ritual_data = {
			"type": type,
			"foresight": foresight,
			"created_by": BOT_PLAYER_ID,
		}

		renderer = RitualRenderer()

		# --- Create reward side ---
		reward_side_data = {
			"name": reward_name,
			"text": reward_text,
			"actions": [],
			"triggers": [],
			"properties": [],
		}
		final_filename = reward_name.lower().replace(" ", "_") + ".png"

		# Generate art for reward side
		is_dark = True if type == "ritual" else False
		image_success, reward_image_path = generate_card_image(reward_side_data, is_dark)
		if not image_success:
			return f"❌ Failed to generate image for reward side: {reward_image_path}"

		with open(reward_image_path, "rb") as f:
			image_bytes = f.read()
		upload_success, image_path_or_error = upload_card_image(reward_name, image_bytes, RITUAL_IMAGE_BUCKET)
		if not upload_success:
			return f"✅ Created ritual side, but failed to upload image:\n{image_path_or_error}"

		reward_side_data["image"] = image_path_or_error
		reward_success, reward_side = create_ritual_side(reward_side_data)
		if not reward_success:
			return reward_side

		# Set correct ID based on type
		if type == "event":
			ritual_data["event_side_id"] = reward_side["id"]
		else:
			ritual_data["bonus_side_id"] = reward_side["id"]

		challenge_side_data = {}

		# --- Optionally create challenge side ---
		if type == "ritual" and challenge_name and challenge_text and challenge_difficulty:
			challenge_side_data = {
				"name": challenge_name,
				"text": challenge_text,
				"difficulty": challenge_difficulty,
				"actions": [],
				"triggers": [],
				"properties": [],
			}

			image_success, challenge_image_path = generate_card_image(challenge_side_data, False)
			if not image_success:
				return f"✅ Created reward side, but failed to generate challenge image:\n{challenge_image_path}"

			with open(challenge_image_path, "rb") as f:
				image_bytes = f.read()
			upload_success, challenge_image_upload = upload_card_image(challenge_name, image_bytes, RITUAL_IMAGE_BUCKET)
			if not upload_success:
				return f"✅ Created reward side, but failed to upload challenge image:\n{challenge_image_upload}"

			challenge_side_data["image"] = challenge_image_upload
			challenge_success, challenge_side = create_ritual_side(challenge_side_data)
			if not challenge_success:
				return f"✅ Created reward side, but failed to create challenge side:\n{challenge_side}"

			challenge_image_download_success, challenge_image_path = download_card_image(challenge_side_data["image"], RITUAL_IMAGE_BUCKET)
			if not challenge_image_download_success:
				return f"✅ Created `{type}`, but failed to download challenge image: {challenge_image_path}"

			ritual_data["challenge_side_id"] = challenge_side["id"]
			final_filename = challenge_name.lower().replace(" ", "_") + ".png"

		# --- Create the ritual ---
		ritual_success, ritual_result = create_ritual(ritual_data)
		if not ritual_success:
			return ritual_result

		ritual_data["bonus_side"] = reward_side_data
		if len(challenge_side_data) > 0:
			ritual_data["challenge_side"] = challenge_side_data

		# --- Final render ---

		# Download reward side image
		reward_image_download_success, reward_image_path = download_card_image(reward_side_data["image"], RITUAL_IMAGE_BUCKET)
		if not reward_image_download_success:
			return f"✅ Created `{type}`, but failed to download reward image: {reward_image_path}"

		# Render full ritual
		output_dir = os.path.join("assets", "rendered_rituals")
		final_path = os.path.join(output_dir, final_filename)
		renderer.render_card(ritual_data, output_dir=output_dir)

		# Send file to Discord
		if os.path.exists(final_path):
			await interaction.followup.send(
				content=f"✅ Created `{type}` successfully!",
				file=nextcord.File(final_path)
			)

			# Cleanup
			# for path in [reward_image_path, challenge_image_path, final_path]:
			# 	try:
			# 		if path:
			# 			os.remove(path)
			# 	except Exception as e:
			# 		print(f"⚠️ Cleanup failed: {path} — {e}")

			return None
		else:
			return f"✅ Created `{type}`, but render failed."


	# Autocomplete Helpers

	@create_ritual_cmd.on_autocomplete("type")
	async def autocomplete_type(self, interaction: Interaction, input: str):
		suggestions = autocomplete_from_choices("ritual_type", input)
		await interaction.response.send_autocomplete(suggestions)


	@create_ritual_cmd.on_autocomplete("challenge_difficulty")
	async def autocomplete_difficulty(self, interaction: Interaction, input: str):
		suggestions = autocomplete_from_choices("difficulty", input)
		await interaction.response.send_autocomplete(suggestions)

	cls.create_ritual_cmd = create_ritual_cmd
