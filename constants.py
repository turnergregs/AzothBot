import os

# Guild for slash command testing
DEV_GUILD_ID = int(os.getenv("DEV_GUILD_ID"))

# Bot player ID for ownership, audit, etc.
BOT_PLAYER_ID = int(os.getenv("BOT_PLAYER_ID"))

# Directory for downloading and rendering card/ritual images
DOWNLOADED_IMAGE_DIR = "assets/downloaded_images"
RENDERED_IMAGE_DIR = "assets/rendered_cards"

# supabase file storage directories
CARD_IMAGE_BUCKET = "cardimages"
RITUAL_IMAGE_BUCKET = "ritualimages"