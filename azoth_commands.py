import os
from dotenv import load_dotenv
import nextcord
import json
from nextcord.ext import commands
from nextcord import Interaction, SlashOption
from utils.interaction_helpers import safe_interaction

load_dotenv()
DEV_GUILD_ID = int(os.getenv("DEV_GUILD_ID"))
BOT_PLAYER_ID = int(os.getenv("BOT_PLAYER_ID"))

def autocomplete_from_choices(field: str, input: str) -> list[str]:
	from supabase_client import get_element_choices, get_attribute_choices, get_type_choices
	lookup = {
		"element": get_element_choices,
		"attributes": get_attribute_choices,
		"type": get_type_choices,
	}

	choices_func = lookup.get(field)
	if not choices_func:
		return []

	all_choices = choices_func()
	return [v for v in all_choices if input in v][:25]


class AzothCommands(commands.Cog):
	def __init__(self, bot):
		self.bot = bot

	# Card CRUD commands
	@nextcord.slash_command(name="create_card", description="Create a new card.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=15, error_message="❌ Failed to create card.", require_authorized=True)
	async def create_card_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Card name"),
		type: str = SlashOption(description="Card type", autocomplete=True),
		valence: int = SlashOption(description="Card valence"),
		element: str = SlashOption(description="Element", autocomplete=True),
		text: str = SlashOption(description="Card rules text"),
		attributes: str = SlashOption(description="Attributes (comma-separated)", required=False),
	):
		from azoth_logic.image_generator import generate_card_image
		from azoth_logic.card_renderer import CardRenderer
		from supabase_client import create_card, upload_card_image, update_card_fields, download_card_image
		import os

		attr_list = [a.strip() for a in attributes.split(",")] if attributes else []

		card_data = {
			"name": name,
			"type": type,
			"valence": valence,
			"element": element,
			"text": text,
			"attributes": attr_list,
			"created_by": BOT_PLAYER_ID,
			"actions": [],
			"triggers": [],
			"properties": [],
		}

		# Step 1: Create card
		success, created_card = create_card(card_data)
		if not success:
			return created_card  # Error message

		# Step 2: Generate card art image
		image_success, image_path_or_error = generate_card_image(card_data)
		if not image_success:
			return f"✅ Created `{name}`, but image generation failed:\n{image_path_or_error}"
		image_path = image_path_or_error

		# Step 3: Upload image to Supabase
		with open(image_path, "rb") as f:
			image_bytes = f.read()
		upload_success, file_path_or_error = upload_card_image(name, image_bytes)
		if not upload_success:
			return f"✅ Created `{name}`, but failed to upload image:\n{file_path_or_error}"

		# Step 4: Update card with image path
		update_card_fields(created_card, {"image": file_path_or_error})
		created_card["image"] = file_path_or_error

		# Step 5: Download uploaded image from Supabase
		image_download_success, image_local_path = download_card_image(file_path_or_error)
		if not image_download_success:
			return f"✅ Created `{name}`, image uploaded, but could not retrieve it:\n{image_local_path}"

		# Step 6: Render full card
		output_dir = "assets/rendered_cards"
		renderer = CardRenderer()
		renderer.render_card(created_card, output_dir=output_dir)

		final_name = name.lower().replace(" ", "_") + ".png"
		final_path = os.path.join(output_dir, final_name)

		if not os.path.exists(final_path):
			return f"✅ Created `{name}`, but final render failed."

		# Step 7: Send final card to Discord
		await interaction.followup.send(
			content=f"✅ Created `{name}` successfully!",
			file=nextcord.File(final_path)
		)

		# Step 8: Clean up local files
		for path in [image_path, image_local_path, final_path]:
			try:
				os.remove(path)
			except Exception as e:
				print(f"⚠️ Cleanup failed: {path} — {e}")

		return None  # already sent a response


	@nextcord.slash_command(name="update_card", description="Update fields on an existing card.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to update card.", require_authorized=True)
	async def update_card_cmd(
		self,
		interaction: Interaction,
		name: str = SlashOption(description="Name of the card to update", autocomplete=True),
		new_name: str = SlashOption(description="New card name"),
		type: str = SlashOption(description="New type", required=False, autocomplete=True),
		valence: int = SlashOption(description="New valence", required=False),
		element: str = SlashOption(description="New element", required=False, autocomplete=True),
		text: str = SlashOption(description="New rules text", required=False),
		attributes: str = SlashOption(description="New attributes (comma-separated)", required=False),
	):
		from supabase_client import get_card_by_name, update_card_fields
		success, card = get_card_by_name(name)
		if not success:
			return card  # this is the error message

		update_data = {}
		if new_name: update_data["name"] = new_name
		if type: update_data["type"] = type
		if valence is not None: update_data["valence"] = valence
		if element: update_data["element"] = element
		if text: update_data["text"] = text
		if attributes is not None:
			update_data["attributes"] = [a.strip() for a in attributes.split(",")]

		success, result = update_card_fields(card, update_data)
		if success:
			return f"✅ Updated `{name}`:\n{result}"
		else:
			return result


	@nextcord.slash_command(name="get_card", description="Get card details.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to get card.")
	async def get_card_cmd(self, interaction: Interaction, name: str):
		from supabase_client import get_card_by_name

		success, card = get_card_by_name(name)
		if not success:
			return card  # this is the error message string

		card_json = json.dumps(card, indent=2)
		return f"```json\n{card_json}\n```"
	

	@nextcord.slash_command(name="delete_card", description="Delete a card.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=5, error_message="❌ Failed to delete card.", require_authorized=True)
	async def delete_card_cmd(self, interaction: Interaction, name: str):
		from supabase_client import delete_card_by_name
		success, response = delete_card_by_name(name)
		return response


	@nextcord.slash_command(name="render_card", description="Render a card and return the image.", guild_ids=[DEV_GUILD_ID])
	@safe_interaction(timeout=10, error_message="❌ Failed to render card.")
	async def render_card_cmd(self, interaction: Interaction, name: str = SlashOption(description="Card name", autocomplete=True)):
		from supabase_client import get_card_by_name, download_card_image
		from azoth_logic.card_renderer import CardRenderer

		success, card = get_card_by_name(name)
		if not success:
			return card

		# Download the art from Supabase
		image_success, image_path_or_error = download_card_image(card["image"])
		if not image_success:
			return f"⚠️ Could not load image for `{name}`:\n{image_path_or_error}"

		# Render the full card using the image
		output_dir = "assets/rendered_cards"
		renderer = CardRenderer()
		renderer.render_card(card, output_dir=output_dir)

		# Send the rendered file
		filename = name.lower().replace(" ", "_") + ".png"
		final_path = os.path.join(output_dir, filename)

		if not os.path.exists(final_path):
			return f"❌ Render failed — output not found."

		await interaction.followup.send(file=nextcord.File(final_path))

		try:
			os.remove(final_path)
		except Exception as e:
			print(f"⚠️ Could not delete rendered card image: {e}")


	# Deck CRUD commands

	# Autocomplete functions
	@create_card_cmd.on_autocomplete("element")
	@update_card_cmd.on_autocomplete("element")
	async def autocomplete_element(self, interaction: Interaction, input: str):
		suggestions = autocomplete_from_choices("element", input)
		await interaction.response.send_autocomplete(suggestions)

	@create_card_cmd.on_autocomplete("type")
	@update_card_cmd.on_autocomplete("type")
	async def autocomplete_type(self, interaction: Interaction, input: str):
		suggestions = autocomplete_from_choices("type", input)
		await interaction.response.send_autocomplete(suggestions)

	@create_card_cmd.on_autocomplete("attributes")
	@update_card_cmd.on_autocomplete("attributes")
	async def autocomplete_attributes(self, interaction: Interaction, input: str):
		# Split into parts based on commas
		parts = [p.strip() for p in input.split(",")]
		existing = parts[:-1]
		current = parts[-1]

		matches = autocomplete_from_choices("attributes", current)

		prefix = ", ".join(existing) + ", " if existing else ""
		suggestions = [prefix + match for match in matches][:25]

		await interaction.response.send_autocomplete(suggestions)

	@update_card_cmd.on_autocomplete("name")
	@delete_card_cmd.on_autocomplete("name")
	@get_card_cmd.on_autocomplete("name")
	@render_card_cmd.on_autocomplete("name")
	async def autocomplete_card_name(self, interaction: Interaction, input: str):
		from supabase_client import get_all_card_names

		all_names = get_all_card_names()
		matches = [n for n in all_names if input.lower() in n.lower()][:25]
		await interaction.response.send_autocomplete(matches)


