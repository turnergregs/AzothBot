from PIL import Image, ImageDraw
import io

def render_card_image(card: dict) -> bytes:
	img = Image.new("RGB", (400, 600), color=(245, 245, 245))
	draw = ImageDraw.Draw(img)

	draw.text((20, 20), f"{card['name']}", fill="black")
	draw.text((20, 60), f"{card['type']} • {card['element']}", fill="black")
	draw.text((20, 100), f"Valence: {card['valence']}", fill="black")
	draw.text((20, 160), card['text'], fill="black")

	buffer = io.BytesIO()
	img.save(buffer, format="PNG")
	buffer.seek(0)
	return buffer.getvalue()
