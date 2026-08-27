"""`/cache` -- inspect and clear the on-disk render cache.

`art_cache.stats()` and `clear()` existed from the start with **no caller**,
which is the same shape as `/render_card` and `rituals.py`: working code that
nothing can reach. This is the command that makes them reachable, and the only
way to see whether the eviction policy is actually holding.

`status` is open to the guild; `clear` is authorized, like every other
destructive command. Clearing is safe -- the cache rebuilds on the next render --
but it costs every cached item its next render, so it is not a no-op either.
"""
import nextcord
from nextcord import Interaction, SlashOption

from azoth_commands.helpers import safe_interaction
from constants import DEV_GUILD_ID
from azoth_logic import art_cache


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


def _bar(used: int, cap: int, width: int = 20) -> str:
    filled = 0 if cap <= 0 else min(width, round(width * used / cap))
    return "█" * filled + "░" * (width - filled)


def add_cache_commands(cls):

    @nextcord.slash_command(name="cache", description="Render cache", guild_ids=[DEV_GUILD_ID])
    async def cache_cmd(self, interaction: Interaction):
        pass

    @cache_cmd.subcommand(name="status", description="Show render cache size and headroom")
    @safe_interaction(timeout=10, error_message="❌ Failed to read the cache.")
    async def cache_status(self, interaction: Interaction):
        s = art_cache.stats()

        embed = nextcord.Embed(
            title="Render cache",
            description=f"`{art_cache.CACHE_ROOT}`\nTotal **{_mb(s['total_bytes'])}**",
            colour=0x3498db,
        )
        # Art is bounded by the content pool; renders are not. Saying so here is
        # the difference between "80% full" reading as a problem and as normal.
        embed.add_field(
            name=f"Art — {s['art_files']} files",
            value=(f"`{_bar(s['art_bytes'], s['art_max_bytes'])}` "
                   f"{_mb(s['art_bytes'])} / {_mb(s['art_max_bytes'])}\n"
                   f"One file per content item, so this settles near 250 MB and stops."),
            inline=False,
        )
        embed.add_field(
            name=f"Renders — {s['render_files']} files",
            value=(f"`{_bar(s['render_bytes'], s['render_max_bytes'])}` "
                   f"{_mb(s['render_bytes'])} / {_mb(s['render_max_bytes'])}\n"
                   f"One file per *version*; every edit orphans the previous render. "
                   f"Evicted least-recently-used on write."),
            inline=False,
        )
        embed.set_footer(text="Deleting the cache is always safe — it rebuilds on the next render.")
        await interaction.followup.send(embed=embed)

    @cache_cmd.subcommand(name="clear", description="Delete the render cache (it rebuilds)")
    @safe_interaction(timeout=15, error_message="❌ Failed to clear the cache.",
                      require_authorized=True)
    async def cache_clear(
        self,
        interaction: Interaction,
        which: str = SlashOption(
            description="What to drop", required=False, default="all",
            choices={"Everything": "all", "Renders only": "renders", "Art only": "art"}),
    ):
        before = art_cache.stats()

        if which == "all":
            art_cache.clear()
        else:
            # Dropping renders alone is the useful case: art is expensive to
            # re-download and bounded anyway, while a bad render is the thing you
            # actually want to force a redraw of.
            art_cache.clear_dir("renders" if which == "renders" else "art")

        after = art_cache.stats()
        freed = before["total_bytes"] - after["total_bytes"]
        files = ((before["art_files"] + before["render_files"])
                 - (after["art_files"] + after["render_files"]))
        return (f"🧹 Cleared **{which}** — {files} file(s), {_mb(freed)} freed.\n"
                f"Now {_mb(after['total_bytes'])}. The next render of each item pays full price.")

    cls.cache_cmd = cache_cmd
    cls.cache_status = cache_status
    cls.cache_clear = cache_clear
