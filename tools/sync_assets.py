"""Copy the card-rendering assets AzothBot needs out of a local azoth checkout.

AzothBot renders cards from vendored copies of the game's art so it can run
standalone -- the bot host has no Godot and no game repo. That means the copies
DRIFT whenever the game's art changes. This script is how you refresh them:

    python -m tools.sync_assets --azoth ../azoth

It also regenerates three files from the game's source rather than having them
transcribed by hand:

  * `assets/card_art/card_symbols.json` from `Utils.replace_dict` in
    `scripts/autoloads/utils.gd` -- the `[1life]`-style token map;
  * `assets/card_art/border_blobs.json` from
    `scenes/cards/card_border_blobs.gdshaderinc` -- the card border's four
    blobs, which the game draws as signed distance fields (it stopped loading
    the per-element border PNGs);
  * `assets/card_art/weapon_text.json` from the game's translations -- the
    words a weapon's escalate badge is set in (`WEAPON_ESCALATES_*`).

Run it after any change to the card border, card symbols, the symbol tokens or
the weapon badge's wording.
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

# The Background TextureRect in scenes/cards/card.tscn. Its alpha is the card's
# silhouette; the border blobs are painted inside it (card_render._border).
BACKGROUND = "assets/images/cards/borders/blurred_card_background2.png"

FONT = "assets/fonts/Aldrich-Regular.ttf"

# The circle family's Bessel table, which procedural art (image_data.art) reads
# exactly as eigenfunctions.gdshaderinc samples it. See azoth_logic/procedural_art.py.
BESSEL_TABLE = "assets/shaders/bessel_lookup.exr"
DEST_BESSEL = "assets/card_art/bessel_lookup.exr"

# Where vendored copies land, relative to the bot root.
DEST_BORDERS = "assets/card_art/borders"
DEST_BACKGROUNDS = "assets/card_art/backgrounds"
DEST_SYMBOLS = "assets/card_art/symbols"
DEST_FONTS = "assets/fonts"
SYMBOL_MAP = "assets/card_art/card_symbols.json"

# The card border's blobs: card_border_dim.gdshader draws these, and nothing
# else, since the per-element border PNGs were retired.
BORDER_BLOBS = "scenes/cards/card_border_blobs.gdshaderinc"
DEST_BORDER_BLOBS = "assets/card_art/border_blobs.json"

# The weapon badge's words, from the game's translation table.
TRANSLATIONS = "assets/localization/translations.csv"
WEAPON_TEXT_PREFIX = "WEAPON_ESCALATES_"
DEST_WEAPON_TEXT = "assets/card_art/weapon_text.json"

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


# One edge half-plane of a blob:  float v0 = dot(p, vec2(a, b)) - o;
_PLANE = re.compile(
    r"float\s+v\d+\s*=\s*dot\(p,\s*vec2\(\s*(?P<a>-?[\d.]+),\s*(?P<b>-?[\d.]+)\s*\)\)"
    r"\s*-\s*(?P<o>-?[\d.]+)\s*;")
_BLOB = re.compile(r"float\s+border_blob_(?P<i>\d+)\(vec2 p\)\s*\{(?P<body>.*?)\n\}", re.S)
_BLOB_K = re.compile(r"const\s+float\s+K\s*=\s*(?P<k>[\d.]+)\s*;")
_BLOB_SIZE = re.compile(r"BORDER_PX\s*=\s*vec2\(\s*(?P<w>[\d.]+),\s*(?P<h>[\d.]+)\s*\)")


def parse_border_blobs(shader_inc: Path) -> dict:
    """The blobs as {"size": [w, h], "blobs": [{"planes": [[a, b, o], ...], "k": K}]}.

    Each blob is a log-sum-exp smooth maximum of its planes `dot(p, n) - o`,
    softened by K, in a w x h border-pixel space. The file is generated (fitted
    to the old border PNG) and could be refitted, which is why it is parsed
    rather than copied into Python by hand.
    """
    text = shader_inc.read_text(encoding="utf-8")
    size = _BLOB_SIZE.search(text)
    blobs = []
    for m in sorted(_BLOB.finditer(text), key=lambda m: int(m.group("i"))):
        body = m.group("body")
        planes = [[float(p.group("a")), float(p.group("b")), float(p.group("o"))]
                  for p in _PLANE.finditer(body)]
        k = _BLOB_K.search(body)
        blobs.append({"planes": planes, "k": float(k.group("k"))})
    if size is None or not blobs:
        raise ValueError(f"no border blobs found in {shader_inc}")
    return {"size": [float(size.group("w")), float(size.group("h"))], "blobs": blobs}


def parse_weapon_text(translations_csv: Path) -> dict:
    """Every WEAPON_ESCALATES_* key's English, as {key: text}."""
    import csv
    out = {}
    with translations_csv.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key = row.get("keys") or ""
            if key.startswith(WEAPON_TEXT_PREFIX):
                out[key] = row.get("en") or ""
    return out


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

    # The card background. The border is no longer a file: see BORDER_BLOBS.
    src = azoth / BACKGROUND
    dst = BOT_ROOT / DEST_BORDERS / Path(BACKGROUND).name
    if dry_run:
        if not src.is_file():
            report.append(f"MISSING  {src}"); missing += 1
    elif not _copy(src, dst, report):
        missing += 1

    # The card font.
    src = azoth / FONT
    if dry_run:
        if not src.is_file():
            report.append(f"MISSING  {src}"); missing += 1
    elif not _copy(src, BOT_ROOT / DEST_FONTS / Path(FONT).name, report):
        missing += 1

    # The Bessel table for procedural circle art.
    src = azoth / BESSEL_TABLE
    if dry_run:
        if not src.is_file():
            report.append(f"MISSING  {src}"); missing += 1
    elif not _copy(src, BOT_ROOT / DEST_BESSEL, report):
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

    for rel, dest, parse, what in (
            (BORDER_BLOBS, DEST_BORDER_BLOBS, parse_border_blobs, "border blobs"),
            (TRANSLATIONS, DEST_WEAPON_TEXT, parse_weapon_text, "weapon badge text")):
        src = azoth / rel
        if not src.is_file():
            report.append(f"MISSING  {src}")
            missing += 1
            continue
        parsed = parse(src)
        print(f"parsed {what} from {rel}")
        if dry_run:
            continue
        out = BOT_ROOT / dest
        payload = {"_comment": f"Generated by tools/sync_assets.py from {rel} in the "
                               "azoth repo. Do not edit by hand."}
        payload["strings" if parse is parse_weapon_text else "border"] = parsed
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
