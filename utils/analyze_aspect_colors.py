"""
Utility script: analyze aspect art colors and write them to Supabase.

For each image in the aspects images directory, finds the two non-transparent
colors (primary = more pixels, secondary = fewer pixels) and updates the
corresponding aspect's `image_data` field in Supabase.

Usage (from project root):
    python utils/analyze_aspect_colors.py [--dry-run]
"""

import os
import sys
import argparse
from pathlib import Path
from collections import Counter
from PIL import Image

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from supabase_helpers import fetch_all, update_record

IMAGES_DIR = Path(r"C:\Users\Caleb\Documents\GitHub\AzothBot\utils\backup\aspectimages\In Use")
ALPHA_THRESHOLD = 10  # pixels with alpha <= this are treated as transparent


def extract_two_colors(image_path: Path) -> tuple[tuple, tuple] | None:
    """
    Returns (primary_color, secondary_color) as (R, G, B) tuples.
    Primary has more pixels. Returns None if fewer than 2 colors found.
    """
    img = Image.open(image_path).convert("RGBA")
    pixels = list(img.getdata())

    counts = Counter(
        (r, g, b)
        for r, g, b, a in pixels
        if a > ALPHA_THRESHOLD
    )

    if len(counts) < 2:
        print(f"  WARNING: fewer than 2 colors found in {image_path.name} ({len(counts)} found)")
        return None

    if len(counts) > 2:
        # Take the two most common — handles stray antialiasing pixels
        top_two = counts.most_common(2)
        print(f"  NOTE: {image_path.name} has {len(counts)} unique colors; using top 2")
    else:
        top_two = counts.most_common(2)

    primary = top_two[0][0]
    secondary = top_two[1][0]
    return primary, secondary


def main():
    parser = argparse.ArgumentParser(description="Analyze aspect image colors and update Supabase.")
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing to Supabase.")
    args = parser.parse_args()

    if not IMAGES_DIR.exists():
        print(f"ERROR: Images directory not found: {IMAGES_DIR}")
        sys.exit(1)

    image_files = list(IMAGES_DIR.glob("*.png")) + list(IMAGES_DIR.glob("*.jpg"))
    if not image_files:
        print(f"No images found in {IMAGES_DIR}")
        sys.exit(0)

    print(f"Found {len(image_files)} image(s) in {IMAGES_DIR}\n")

    # Fetch all aspects that have an image assigned
    aspects = fetch_all("aspects", filters=None)
    aspects_by_image = {
        a["image"]: a
        for a in aspects
        if a.get("image")
    }
    print(f"Fetched {len(aspects)} aspect(s) from Supabase ({len(aspects_by_image)} with images)\n")

    updated = 0
    skipped = 0
    errors = 0

    for image_path in sorted(image_files):
        filename = image_path.name
        print(f"Processing: {filename}")

        colors = extract_two_colors(image_path)
        if colors is None:
            errors += 1
            continue

        primary, secondary = colors
        print(f"  Primary:   {primary}")
        print(f"  Secondary: {secondary}")

        aspect = aspects_by_image.get(filename)
        if aspect is None:
            print(f"  SKIP: no aspect found with image='{filename}'")
            skipped += 1
            continue

        print(f"  Aspect:    {aspect['name']}")

        existing_image_data = aspect.get("image_data") or {}
        new_image_data = {
            **existing_image_data,
            "primary_color": list(primary),
            "secondary_color": list(secondary),
        }

        if args.dry_run:
            print(f"  [dry-run] Would update image_data: {new_image_data}")
        else:
            result = update_record("aspects", aspect["id"], {"image_data": new_image_data})
            if result:
                print(f"  Updated successfully.")
                updated += 1
            else:
                print(f"  ERROR: update_record failed.")
                errors += 1
        print()

    print("=" * 40)
    if args.dry_run:
        print(f"Dry run complete. Would update: {len(image_files) - skipped - errors}, skipped: {skipped}, errors: {errors}")
    else:
        print(f"Done. Updated: {updated}, skipped: {skipped}, errors: {errors}")


if __name__ == "__main__":
    main()
