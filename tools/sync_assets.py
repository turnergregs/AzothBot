"""Copy the card-rendering assets AzothBot needs out of a local azoth checkout.

AzothBot renders cards from vendored copies of the game's art so it can run
standalone -- the bot host has no Godot and no game repo. That means the copies
DRIFT whenever the game's art changes. This script is how you refresh them:

    python -m tools.sync_assets --azoth ../azoth

It also regenerates `assets/card_art/card_symbols.json` from `Utils.replace_dict`
in `scripts/autoloads/utils.gd`, so the `[1life]`-style token map stays in step
with the game rather than being transcribed by hand.

Run it after any change to card borders, card symbols, or the symbol tokens.
See docs/CARD_RENDERING.md.

WHAT THIS SCRIPT CANNOT DO: the aspect and rite backgrounds under
`assets/card_art/backgrounds/` are SHADER OUTPUT, not files in the repo. They are
exported from a running Godot instance by `tools/BackgroundExportTool.tscn` in
the azoth repo. This script verifies they are present and tells you how to
re-export, but it cannot produce them -- copying is not an option when the source
does not exist as a file.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

BOT_ROOT = Path(__file__).resolve().parent.parent

# Borders and background, from BaseCard.border_paths in scripts/cards/base_card.gd.
# Keys are what a card's `element` field holds; "default" covers a null element.
BORDERS = {
    "default": "assets/images/cards/borders/blurred_white_border_noline.png",
    "anima": "assets/images/cards/borders/blurred_anima_border_noline.png",
    "blood": "assets/images/cards/borders/blurred_blood_border_noline.png",
    "sol": "assets/images/cards/borders/blurred_sol_border_noline.png",
    # Split cards draw a second border over the first. Phase 2.
    "split_anima": "assets/images/cards/borders/blurred_second_anima_border3.png",
    "split_blood": "assets/images/cards/borders/blurred_second_blood_border3.png",
    "split_sol": "assets/images/cards/borders/blurred_second_sol_border3.png",
}

# The Background TextureRect in scenes/cards/card.tscn.
BACKGROUND = "assets/images/cards/borders/blurred_card_background2.png"

FONT = "assets/fonts/Aldrich-Regular.ttf"

# Where vendored copies land, relative to the bot root.
DEST_BORDERS = "assets/card_art/borders"
DEST_BACKGROUNDS = "assets/card_art/backgrounds"
DEST_SYMBOLS = "assets/card_art/symbols"
DEST_FONTS = "assets/fonts"
SYMBOL_MAP = "assets/card_art/card_symbols.json"

# Shader-exported backgrounds. NOT copied -- only checked for. See the module
# docstring. `fate_layout.rite_variant()` decides which of the four a rite gets;
# each needs a baked PNG and a recolourable mask, and `attribute` also needs the
# animated mask sequence.
RITE_VARIANTS = ("attribute", "rest", "trash", "upgrade")
EXPECTED_BACKGROUNDS = (
    ["aspect_background.png"]
    + [f"rite_background_{v}.png" for v in RITE_VARIANTS]
    + [f"rite_background_{v}_mask.png" for v in RITE_VARIANTS]
    + ["rite_background_attribute_mask_anim.webp"]
)

EXPORT_COMMAND = (
    "godot --path <azoth> tools/BackgroundExportTool.tscn -- --out=/tmp/bgs\n"
    "  cp /tmp/bgs/* assets/card_art/backgrounds/"
)


# One entry of Utils.replace_dict, e.g.
#   "[1life]": "[img=c,c,height=PUT_FONTSIZE_HERE]res://.../1life.png[/img]",
# Some carry a colour, with or without a comma:
#   height=PUT_FONTSIZE_HERE, color=#8769E9   /   height=PUT_FONTSIZE_HERE color=#ffc631
_ENTRY = re.compile(
    r'"(?P<token>\[[^"]*\])"\s*:\s*"\[img=c,c,height=PUT_FONTSIZE_HERE'
    r'(?:\s*,?\s*color=(?P<color>#[0-9A-Fa-f]{6}))?'
    r'\](?P<path>res://[^\[]+?)\[/img\]"'
)


def parse_symbol_tokens(utils_gd: Path) -> dict:
    """Extract Utils.replace_dict as {token: {"file": ..., "color": ...|None}}.

    Only the literal `[img=...]` entries are taken. The dict is a plain literal
    in the game -- nothing generates entries at runtime -- so a static parse is
    complete rather than a best effort.
    """
    text = utils_gd.read_text(encoding="utf-8")
    start = text.index("var replace_dict = {")
    end = text.index("\n}", start)
    body = text[start:end]

    tokens = {}
    for m in _ENTRY.finditer(body):
        tokens[m.group("token")] = {
            "file": m.group("path").rsplit("/", 1)[-1],
            "source": m.group("path")[len("res://"):],
            "color": m.group("color"),
        }
    return tokens


def _copy(src: Path, dst: Path, report: list) -> bool:
    if not src.is_file():
        report.append(f"MISSING  {src}")
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_file() and dst.stat().st_size == src.stat().st_size:
        if dst.read_bytes() == src.read_bytes():
            return True                      # unchanged; don't churn git
    shutil.copy2(src, dst)
    report.append(f"updated  {dst.relative_to(BOT_ROOT)}")
    return True


def sync(azoth: Path, dry_run: bool = False) -> int:
    utils_gd = azoth / "scripts/autoloads/utils.gd"
    if not utils_gd.is_file():
        print(f"error: {azoth} does not look like an azoth checkout "
              f"(no scripts/autoloads/utils.gd)", file=sys.stderr)
        return 2

    report: list[str] = []
    missing = 0

    tokens = parse_symbol_tokens(utils_gd)
    print(f"parsed {len(tokens)} symbol tokens from utils.gd")

    if dry_run:
        print("(dry run -- nothing written)")

    # Symbols and icons, exactly the files the tokens reference.
    for token, info in sorted(tokens.items()):
        src = azoth / info["source"]
        dst = BOT_ROOT / DEST_SYMBOLS / info["file"]
        if dry_run:
            if not src.is_file():
                report.append(f"MISSING  {src}"); missing += 1
            continue
        if not _copy(src, dst, report):
            missing += 1

    # Borders and the card background.
    for name, rel in list(BORDERS.items()) + [("background", BACKGROUND)]:
        src = azoth / rel
        dst = BOT_ROOT / DEST_BORDERS / Path(rel).name
        if dry_run:
            if not src.is_file():
                report.append(f"MISSING  {src}"); missing += 1
            continue
        if not _copy(src, dst, report):
            missing += 1

    # The card font.
    src = azoth / FONT
    if dry_run:
        if not src.is_file():
            report.append(f"MISSING  {src}"); missing += 1
    elif not _copy(src, BOT_ROOT / DEST_FONTS / Path(FONT).name, report):
        missing += 1

    # Backgrounds: verified, never copied.
    absent = [n for n in EXPECTED_BACKGROUNDS
              if not (BOT_ROOT / DEST_BACKGROUNDS / n).is_file()]
    if absent:
        print(f"\n{len(absent)} shader-exported background(s) missing:", file=sys.stderr)
        for name in absent:
            print(f"  MISSING  {DEST_BACKGROUNDS}/{name}", file=sys.stderr)
        print(f"These are NOT copied by this script -- re-export them:\n  {EXPORT_COMMAND}",
              file=sys.stderr)
        missing += len(absent)
    else:
        print(f"{len(EXPECTED_BACKGROUNDS)} shader-exported background(s) present "
              f"(not managed by this script)")

    if not dry_run:
        out = BOT_ROOT / SYMBOL_MAP
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "_comment": "Generated by tools/sync_assets.py from Utils.replace_dict "
                        "in the azoth repo. Do not edit by hand.",
            "tokens": {t: {"file": i["file"], "color": i["color"]}
                       for t, i in sorted(tokens.items())},
        }
        out.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
        report.append(f"wrote    {out.relative_to(BOT_ROOT)}")

    changed = [r for r in report if r.startswith("updated") or r.startswith("wrote")]
    problems = [r for r in report if r.startswith("MISSING")]
    for r in problems:
        print(r, file=sys.stderr)
    print(f"{len(changed)} file(s) written, {len(problems)} missing")
    if problems:
        print("Missing files usually mean the game renamed or moved an asset -- "
              "update the paths at the top of this script.", file=sys.stderr)
    return 1 if missing else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--azoth", required=True, type=Path,
                    help="path to a local azoth checkout")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change without writing")
    args = ap.parse_args()
    return sync(args.azoth.expanduser().resolve(), args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
