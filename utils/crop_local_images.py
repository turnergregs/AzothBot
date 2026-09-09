"""
Crops all images in local directories to the tightest square bounding box
of their non-transparent content, then resizes to 1081x1081 and saves in place.

Usage:
    python utils/crop_local_images.py
"""

import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TARGET_SIZE = (1081, 1081)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

DIRECTORIES = [
    r"C:\Users\Caleb\PycharmProjects\azoth\new_combo_images\upload_now",
    r"C:\Users\Caleb\PycharmProjects\azoth\new_card_images\upload now",
]


def crop_to_square_content(img: Image.Image) -> Image.Image:
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    alpha = img.split()[3]
    bbox = alpha.getbbox()

    if bbox is None:
        return img.resize(TARGET_SIZE, Image.LANCZOS)

    left, upper, right, lower = bbox
    w = right - left
    h = lower - upper
    size = max(w, h)

    cx = (left + right) // 2
    cy = (upper + lower) // 2
    half = size // 2

    sq_left = cx - half
    sq_upper = cy - half
    sq_right = cx + half
    sq_lower = cy + half

    img_w, img_h = img.size
    if sq_left < 0:
        sq_right -= sq_left
        sq_left = 0
    if sq_upper < 0:
        sq_lower -= sq_upper
        sq_upper = 0
    if sq_right > img_w:
        sq_left -= sq_right - img_w
        sq_right = img_w
    if sq_lower > img_h:
        sq_upper -= sq_lower - img_h
        sq_lower = img_h

    cropped = img.crop((sq_left, sq_upper, sq_right, sq_lower))
    return cropped.resize(TARGET_SIZE, Image.LANCZOS)


def process_directory(directory: str) -> None:
    if not os.path.isdir(directory):
        print(f"Directory not found, skipping: {directory}")
        return

    files = [f for f in os.listdir(directory) if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS]
    if not files:
        print(f"No images found in: {directory}")
        return

    print(f"\n{directory}")
    print(f"  Found {len(files)} image(s).")

    for filename in files:
        path = os.path.join(directory, filename)
        print(f"  {filename} ...", end=" ", flush=True)
        try:
            img = Image.open(path)
            original_size = img.size
            processed = crop_to_square_content(img)
            processed.save(path, format="PNG")
            print(f"done  ({original_size} -> {TARGET_SIZE})")
        except Exception as e:
            print(f"SKIP ({e})")


def main() -> None:
    for directory in DIRECTORIES:
        process_directory(directory)
    print("\nDone.")


if __name__ == "__main__":
    main()
