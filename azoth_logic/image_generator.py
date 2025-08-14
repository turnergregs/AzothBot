import os
from azoth_logic.eigenfunction_generator import RandomEigenfunctionGenerator

# Cache the generator (don't reinitialize every time)
generator = RandomEigenfunctionGenerator(eigenfunctions_dir="eigenfunctions")

def generate_image(card_data: dict, is_dark: bool = False) -> tuple[bool, str | bytes]:
	"""
	Generates a PNG image for the card's element.
	Returns (success, image_path or error message).
	"""
	element = card_data.get("element")

	if not element:
		element = "light" if is_dark else "light"

	# if element == "all":
	# 	element = "dark"
	try:
		params, image_path = generator.generate_random_image(element)
		return True, image_path
	except Exception as e:
		return False, f"❌ Failed to generate image: {e}"
