import os

# Guild for slash command testing
DEV_GUILD_ID = int(os.getenv("DEV_GUILD_ID"))

# Bot player ID for ownership, audit, etc.
BOT_PLAYER_ID = int(os.getenv("BOT_PLAYER_ID"))

ASSET_RENDER_PATHS = {
    "card": "assets/renders/cards",
    "aspect": "assets/renders/aspects",
    "ritual": "assets/renders/rituals",
    "deck": "assets/renders/decks",
    "hand": "assets/renders/hands",
    "event": "assets/renders/events",
    "consumable": "assets/renders/consumables",
    "hero": "assets/renders/heroes",
}

ASSET_DOWNLOAD_PATHS = {
    "card": "assets/downloaded_images/cards",
    "aspect": "assets/downloaded_images/aspects",
    "ritual": "assets/downloaded_images/rituals",
    "event": "assets/downloaded_images/events",
    "consumable": "assets/downloaded_images/consumables",
    "hero": "assets/downloaded_images/heroes",
}

ASSET_BUCKET_NAMES = {
    "card": "cardimages",
    "aspect": "aspectimages",
    "ritual": "ritualimages",
    "event": "eventimages",
    "consumable": "consumableimages",
    "hero": "heroimages",
}
