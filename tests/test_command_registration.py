"""What the cog ACTUALLY exposes, and whether its modules are internally sound.

Two bug classes live here, both of which shipped in the 2026-08-26 overhaul and
neither of which any other test could see, because nothing else imports the
nextcord command layer.

  1. A command defined but never assigned onto the cog. `/render_card` and
     `/render_aspect` were both left in this state -- complete function bodies,
     autocomplete wired, reachable by nobody. It is the same failure that hid
     `rituals.py` and `consumables.py` for months, and it is invisible at import
     because the module still imports cleanly.

  2. A NAME that does not exist at runtime. The overhaul deleted the module-level
     `renderer = CardRenderer()` and left four call sites behind, so
     `/create_card` and `/create_rite` raised NameError on their happy path --
     AFTER writing the row and uploading the art, so the reply read as a failed
     write on a write that had landed.
"""
import ast
import subprocess
import sys
from pathlib import Path

import pytest

from azoth_commands import AzothCommands

REPO = Path(__file__).resolve().parent.parent
COMMAND_DIR = REPO / "azoth_commands"

# Modules whose attachers are called from azoth_commands/__init__.py.
ATTACHED = ["decks", "cards", "content", "aspects", "rites", "search", "cache",
            "stats", "misc", "daily_update"]

# Deliberately NOT attached. `heroes.py` is retired, not broken -- see
# azoth_commands/__init__.py and docs/CARD_RENDERING.md § Retired.
DETACHED = ["heroes"]


# Both a top-level command and a subcommand have to be counted. They are
# DIFFERENT nextcord types and DIFFERENT decorators, and an earlier version of
# this file matched neither for subcommands -- so it passed while being blind to
# all nine of them in `stats.py` and `cache.py`.
_COMMAND_TYPES = ("SlashApplicationCommand", "SlashApplicationSubcommand")
_COMMAND_DECORATORS = ("slash_command", "subcommand")


def _registered_command_names() -> set:
    names = set()
    for attr in dir(AzothCommands):
        obj = getattr(AzothCommands, attr, None)
        name = getattr(obj, "name", None)
        if name and type(obj).__name__ in _COMMAND_TYPES:
            names.add(name)
    return names


def _defined_command_names(module: str) -> set:
    """Every command and subcommand a module's source declares.

    Matches `@nextcord.slash_command(name=...)` and `@<group>.subcommand(name=...)`.
    Names are compared bare, not qualified -- a subcommand is `status`, not
    `cache status` -- which is fine while no two differ only by parent.
    """
    tree = ast.parse((COMMAND_DIR / f"{module}.py").read_text())
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        attr = getattr(func, "attr", None) or getattr(func, "id", None)
        if attr not in _COMMAND_DECORATORS:
            continue
        for kw in node.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                found.add(kw.value.value)
    return found


def test_subcommands_are_covered_by_this_file():
    """Guard on the guard.

    The checks below are only worth anything if they SEE subcommands. If a
    nextcord upgrade renames the type or the decorator, every assertion here
    quietly starts passing vacuously.
    """
    registered = _registered_command_names()
    assert {"status", "clear"} <= registered, "cache subcommands not seen"
    assert {"leaderboard", "player"} <= registered, "stats subcommands not seen"
    assert "status" in _defined_command_names("cache")


# ---------------------------------------------------------------------------
# Every defined command is reachable
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module", ATTACHED)
def test_every_command_a_module_defines_is_registered(module):
    """A command body with no `cls.x = x` is dead code that still imports.

    2026-08-26: `/render_card`, `/render_rite` and `/render_aspect` were all left
    like this when `/render` replaced them. The bodies were correct; nothing
    could call them.
    """
    defined = _defined_command_names(module)
    orphans = defined - _registered_command_names()
    assert not orphans, (
        f"{module}.py defines {sorted(orphans)} but never assigns them onto the cog. "
        f"Either add `cls.<name>_cmd = <name>_cmd` or delete the body -- see AGENTS.md "
        f"§ 'A command can exist in the source and not exist at runtime'.")


@pytest.mark.parametrize("module", DETACHED)
def test_retired_modules_stay_unregistered(module):
    """The inverse guard: re-attaching heroes would resurrect a broken renderer.

    Hero cards target `hero_card.tscn` and were never ported to card_render, so
    `/render_hero` draws the wrong frame. This test is what stops someone
    "fixing" the missing attacher call.
    """
    leaked = _defined_command_names(module) & _registered_command_names()
    assert not leaked, (
        f"{module}.py is retired but {sorted(leaked)} reached the cog. "
        f"See azoth_commands/__init__.py.")


def test_the_cache_maintenance_commands_exist():
    """`art_cache.stats()` and `clear()` shipped with no caller at all -- the same
    unreachable-code shape as `/render_card`. `/cache` is what reaches them."""
    assert {"cache", "status", "clear"} <= _registered_command_names()


def test_the_generic_lookup_commands_exist():
    """`/show`, `/render` and `/search` replaced six typed commands; if the
    replacements are missing there is no way to inspect content at all.

    `/show` was `/get` until 2026-08-27 (renamed with `/get_deck` -> `/show_deck`)."""
    registered = _registered_command_names()
    assert {"show", "render", "search"} <= registered


@pytest.mark.parametrize("gone", ["get_card", "get_aspect", "get_rite", "get_event",
                                  "render_card", "render_aspect", "render_rite",
                                  "render_event", "create_event", "render_hero",
                                  # Renamed 2026-08-27.
                                  "get", "get_deck", "draft_deck"])
def test_superseded_commands_are_gone(gone):
    """Names retired on 2026-08-26, plus the 2026-08-27 renames.

    A reappearance means a revert went half-way -- or, for the renames, that
    someone re-added the old name alongside the new one."""
    assert gone not in _registered_command_names()


@pytest.mark.parametrize("gone", ["delete_card", "delete_aspect", "delete_rite",
                                 "delete_deck"])
def test_no_command_deletes_content(gone):
    """All four /delete_* commands were commented out 2026-08-27.

    Three of them (`cards`, `aspects`, `events`) hard-deleted: those tables have
    no `archived_at` column, so there was no undo, and the game's
    `prune_content_dirs()` reads a missing row as the deletion signal -- one
    misclick removed the item from the offline snapshot too. `/delete_deck` was
    the safe one (it set `archived_at`) and went with them for consistency;
    `/update_deck archived:True` still covers that case.

    Content is retired by pulling it from the draft decks, not by deleting rows.
    Restoring any of these means deciding what "delete" should mean first."""
    assert gone not in _registered_command_names()


@pytest.mark.parametrize("hidden", ["stage", "postpone", "merge_staging"])
def test_the_deck_curation_commands_stay_hidden(hidden):
    """Commented out 2026-08-27 (azoth_commands/decks.py).

    All three are unsafe as written -- `/stage` and `/merge_staging` hardcode
    deck ids 21/22/20/3, and deck 21 is archived while deck 22 is "Testing
    Fates" despite the constant being ASPECT_DECK_ID. Uncommenting them without
    fixing the ids re-ships that bug, so this is the tripwire."""
    assert hidden not in _registered_command_names()


# ---------------------------------------------------------------------------
# No undefined names
# ---------------------------------------------------------------------------

def test_no_undefined_names_in_the_command_layer():
    """The NameError guard.

    Nothing else in the suite imports these modules deeply enough to execute a
    command body, so a name deleted out from under its call sites goes unnoticed
    until someone runs the command in Discord. Static analysis is the only thing
    that sees it without a live Supabase and a live gateway.

    Scoped to undefined names ONLY -- unused imports are noisy, historical, and
    harmless, and failing on them would make this test something people disable.
    """
    result = subprocess.run(
        [sys.executable, "-m", "pyflakes", "azoth_commands", "azoth_logic", "tools"],
        cwd=REPO, capture_output=True, text=True,
    )
    undefined = [ln for ln in result.stdout.splitlines() if "undefined name" in ln]

    # fate_renderer.py is an ARCHIVE -- unreachable at runtime, kept as the record
    # of the previous template. Its `side_data` bug predates the rewrite and is
    # not worth touching in a module nothing imports.
    undefined = [ln for ln in undefined if "azoth_logic/fate_renderer.py" not in ln]

    assert not undefined, "undefined names reachable at runtime:\n" + "\n".join(undefined)
