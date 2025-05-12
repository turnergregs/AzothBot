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
		from supabase_client import create_ritual, download_image
		from azoth_logic.ritual_renderer import RitualRenderer

		ritual_data = {
			"type": type,
			"foresight": foresight,
			"created_by": BOT_PLAYER_ID,
		}

		if type == "ritual" and not (challenge_name and challenge_text and challenge_difficulty):
			return "❌ Attempting to create Ritual type with no challenge side"

		renderer = RitualRenderer()

		# --- Reward Side ---
		is_reward_dark = type == "ritual"
		reward_success, reward_result = build_ritual_side(reward_name, reward_text, is_reward_dark)
		if not reward_success:
			return reward_result
		reward_data, reward_record = reward_result

		if type == "event":
			ritual_data["event_side_id"] = reward_record["id"]
		else:
			ritual_data["bonus_side_id"] = reward_record["id"]

		# --- Optional Challenge Side ---
		challenge_data, final_filename = None, reward_name.lower().replace(" ", "_") + ".png"
		if type == "ritual" and challenge_name and challenge_text and challenge_difficulty:
			challenge_success, challenge_result = build_ritual_side(challenge_name, challenge_text, False, challenge_difficulty)
			if not challenge_success:
				return challenge_result
			challenge_data, challenge_record = challenge_result
			ritual_data["challenge_side_id"] = challenge_record["id"]
			final_filename = challenge_name.lower().replace(" ", "_") + ".png"

		# --- Create Ritual ---
		success, ritual_result = create_ritual(ritual_data)
		if not success:
			return ritual_result

		# Add sides back for rendering
		ritual_data["bonus_side"] = reward_data
		if challenge_data:
			ritual_data["challenge_side"] = challenge_data

		# --- Render ---
		output_dir = os.path.join("assets", "rendered_rituals")
		final_path = os.path.join(output_dir, final_filename)

		# Download reward image
		image_success, local_image = download_image(reward_data["image"], RITUAL_IMAGE_BUCKET)
		if not image_success:
			return f"✅ Created `{type}`, but could not download reward image."

		# (Optional) download challenge image
		if challenge_data:
			download_image(challenge_data["image"], RITUAL_IMAGE_BUCKET)

		renderer.render_card(ritual_data, output_dir=output_dir)

		if os.path.exists(final_path):
			await interaction.followup.send(
				content=f"✅ Created `{type}` successfully!",
				file=nextcord.File(final_path)
			)
			return None
		else:
			return f"✅ Created `{type}`, but render failed."


	@nextcord.slash_command(
		name="update_ritual",
		description="Update a ritual's fields or regenerate its image.",
		guild_ids=[DEV_GUILD_ID]
	)
	@safe_interaction(timeout=15, error_message="❌ Failed to update ritual.", require_authorized=True)
	async def update_ritual_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Ritual name to update", autocomplete=True),
		foresight: int = SlashOption(description="New foresight value", required=False),
		reward_name: str = SlashOption(description="New reward name", required=False),
		reward_text: str = SlashOption(description="New reward text", required=False),
		challenge_name: str = SlashOption(description="New challenge name", required=False),
		challenge_text: str = SlashOption(description="New challenge text", required=False),
		challenge_difficulty: str = SlashOption(description="New challenge difficulty", required=False, autocomplete=True),
		regenerate_image: bool = SlashOption(description="Regenerate the card image?", required=False, default=False),
	):
		from supabase_client import (
			get_ritual_by_name,
			update_ritual_fields,
			update_ritual_side_fields,
			download_image,
		)
		from azoth_logic.ritual_renderer import RitualRenderer

		# Fetch ritual with its side data included
		success, ritual = get_ritual_by_name(name)
		if not success:
			return ritual

		ritual_update_data = {}
		if foresight is not None: ritual_update_data["foresight"] = foresight

		# Keep track of which side to regenerate for file naming
		filename = None

		# --- Update Reward Side ---
		reward_side = ritual.get("bonus_side") or ritual.get("event_side")
		reward_updates = {}
		if reward_name: reward_updates["name"] = reward_name
		if reward_text: reward_updates["text"] = reward_text

		if regenerate_image:
			merged_reward = reward_side | reward_updates  # combine original + updates
			image_success, image_path_or_error = generate_and_upload_ritual_image(
				merged_reward,
				is_dark=ritual["type"] == "ritual"
			)
			if not image_success:
				return f"✅ Ritual loaded, but image regeneration failed:\n{image_path_or_error}"
			reward_updates["image"] = image_path_or_error
			reward_side["image"] = image_path_or_error
			filename = merged_reward["name"].lower().replace(" ", "_") + ".png"

		if reward_updates:
			update_ritual_side_fields(reward_side["id"], reward_updates)

		# --- Update Challenge Side (if present) ---
		if ritual.get("type") == "ritual" and ritual.get("challenge_side"):
			challenge_side = ritual["challenge_side"]
			challenge_updates = {}
			if challenge_name: challenge_updates["name"] = challenge_name
			if challenge_text: challenge_updates["text"] = challenge_text
			if challenge_difficulty: challenge_updates["difficulty"] = challenge_difficulty

			if regenerate_image:
				merged_challenge = challenge_side | challenge_updates
				image_success, result = generate_and_upload_ritual_image(merged_challenge, is_dark=False)
				if not image_success:
					return f"✅ Ritual loaded, but challenge image generation failed:\n{result}"
				challenge_updates["image"] = result
				ritual["challenge_side"]["image"] = result
				filename = merged_challenge["name"].lower().replace(" ", "_") + ".png"

			if challenge_updates:
				update_ritual_side_fields(challenge_side["id"], challenge_updates)

		# --- Update Ritual Itself ---
		if ritual_update_data:
			update_ritual_fields(ritual["id"], ritual_update_data)

		# --- Render Updated Ritual ---
		from supabase_client import download_image
		renderer = RitualRenderer()
		output_dir = os.path.join("assets", "rendered_rituals")
		final_path = os.path.join(output_dir, filename)

		# Download updated images
		if reward_side.get("image"):
			download_image(reward_side["image"], RITUAL_IMAGE_BUCKET)
		if ritual.get("challenge_side") and ritual["challenge_side"].get("image"):
			download_image(ritual["challenge_side"]["image"], RITUAL_IMAGE_BUCKET)

		renderer.render_card(ritual, output_dir=output_dir)

		if os.path.exists(final_path):
			await interaction.followup.send(
				content=f"✅ Ritual `{name}` updated.",
				file=nextcord.File(final_path)
			)
		else:
			return f"✅ Ritual `{name}` updated, but render failed."

		return None  # response already sent


	@nextcord.slash_command(name="delete_ritual", description="Delete a ritual by name.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to delete ritual.", require_authorized=True)
	async def delete_ritual_cmd(self, interaction: Interaction, name: str = SlashOption(description="Ritual name", autocomplete=True)):
		from supabase_client import delete_ritual_by_name

		success, result = delete_ritual_by_name(name)
		return result


	@nextcord.slash_command(name="get_ritual", description="Get ritual details as JSON.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to get ritual.")
	async def get_ritual_cmd(self, interaction: Interaction, name: str = SlashOption(description="Ritual name", autocomplete=True)):
		from supabase_client import get_ritual_by_name

		success, ritual = get_ritual_by_name(name)
		if not success:
			return ritual  # error message

		ritual_json = json.dumps(ritual, indent=2)
		return f"```json\n{ritual_json}\n```"


	@nextcord.slash_command(name="render_ritual", description="Render a ritual’s full card image.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=10, error_message="❌ Failed to render ritual.")
	async def render_ritual_cmd(self, interaction: Interaction, name: str = SlashOption(description="Ritual name", autocomplete=True)):
		from supabase_client import get_ritual_by_name, download_image
		from azoth_logic.ritual_renderer import RitualRenderer
		from azoth_commands.helpers import get_local_image_path
		import io

		success, ritual = get_ritual_by_name(name)
		if not success:
			return ritual

		renderer = RitualRenderer()

		# Download art for all sides
		for side_key in ["bonus_side", "challenge_side", "event_side"]:
			if side_key in ritual:
				image_name = ritual[side_key]["image"]
				local_path = get_local_image_path(image_name)
				if not os.path.exists(local_path):
					success, result = download_image(image_name, RITUAL_IMAGE_BUCKET)
					if not success:
						return f"⚠️ Failed to download art for `{ritual[side_key]['name']}`: {result}"

		# Render ritual
		output_dir = "assets/rendered_rituals"
		renderer.render_card(ritual, output_dir)

		final_name = name.lower().replace(" ", "_") + ".png"
		final_path = os.path.join(output_dir, final_name)

		if not os.path.exists(final_path):
			return "❌ Ritual render failed."

		# Send rendered ritual
		with open(final_path, "rb") as f:
			image_bytes = f.read()
		file = nextcord.File(io.BytesIO(image_bytes), filename=final_name)
		await interaction.followup.send(file=file)

		try:
			os.remove(final_path)
		except Exception as e:
			print(f"⚠️ Cleanup failed: {e}")


	# Ritual Helpers

	def generate_and_upload_ritual_image(side_data: dict, is_dark: bool) -> tuple[bool, str | bytes]:
		from azoth_logic.image_generator import generate_card_image
		from supabase_client import upload_image

		success, image_path_or_error = generate_card_image(side_data, is_dark)
		if not success:
			return False, image_path_or_error

		with open(image_path_or_error, "rb") as f:
			image_bytes = f.read()

		return upload_image(side_data["name"], image_bytes, RITUAL_IMAGE_BUCKET)


	def build_ritual_side(name: str, text: str, is_dark: bool, difficulty: str | None = None) -> tuple[bool, dict | str]:
		from supabase_client import create_ritual_side

		side_data = {
			"name": name,
			"text": text,
			"actions": [],
			"triggers": [],
			"properties": [],
		}
		if difficulty:
			side_data["difficulty"] = difficulty

		upload_success, image_or_error = generate_and_upload_ritual_image(side_data, is_dark)
		if not upload_success:
			return False, f"Failed to upload image for `{name}`:\n{image_or_error}"

		side_data["image"] = image_or_error
		create_success, created = create_ritual_side(side_data)
		if not create_success:
			return False, f"Failed to create ritual side `{name}`:\n{created}"

		return True, (side_data, created)


	# Autocomplete Helpers

	@create_ritual_cmd.on_autocomplete("type")
	async def autocomplete_type(self, interaction: Interaction, input: str):
		suggestions = autocomplete_from_choices("ritual_type", input)
		await interaction.response.send_autocomplete(suggestions)


	@create_ritual_cmd.on_autocomplete("challenge_difficulty")
	@update_ritual_cmd.on_autocomplete("challenge_difficulty")
	async def autocomplete_difficulty(self, interaction: Interaction, input: str):
		suggestions = autocomplete_from_choices("difficulty", input)
		await interaction.response.send_autocomplete(suggestions)


	@update_ritual_cmd.on_autocomplete("name")
	@delete_ritual_cmd.on_autocomplete("name")
	@get_ritual_cmd.on_autocomplete("name")
	@render_ritual_cmd.on_autocomplete("name")
	async def autocomplete_ritual_name(self, interaction: Interaction, input: str):
		from supabase_client import get_all_ritual_names
		matches = [n for n in get_all_ritual_names() if input.lower() in n.lower()]
		await interaction.response.send_autocomplete(matches[:25])


	cls.create_ritual_cmd = create_ritual_cmd
	cls.update_ritual_cmd = update_ritual_cmd
	cls.delete_ritual_cmd = delete_ritual_cmd
	cls.get_ritual_cmd	  = get_ritual_cmd
	cls.render_ritual_cmd = render_ritual_cmd
