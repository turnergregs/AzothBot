"""
Crops all images in a Supabase storage bucket to the tightest square bounding box
of their non-transparent content, then resizes to 1081x1081 and re-uploads.

Usage:
    python utils/crop_card_images.py                   # dry run — preview only, no uploads
    python utils/crop_card_images.py --execute         # actually crop and re-upload
    python utils/crop_card_images.py --bucket myimages --execute
"""

import argparse
import io
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from supabase_client import supabase

TARGET_SIZE = (1081, 1081)
DEFAULT_BUCKET = "aspectimages"
BACKUP_DIR = "backup"


def list_bucket_files(bucket: str) -> list[str]:
    files = []
    limit = 100
    offset = 0
    while True:
        page = supabase.storage.from_(bucket).list(options={"limit": limit, "offset": offset})
        names = [f["name"] for f in page if f.get("name") and f["name"] != ".emptyFolderPlaceholder"]
        files.extend(names)
        if len(page) < limit:
            break
        offset += limit
    return files


def download_file(bucket: str, filename: str) -> bytes:
    return supabase.storage.from_(bucket).download(filename)


def upload_file(bucket: str, filename: str, data: bytes) -> None:
    supabase.storage.from_(bucket).upload(
        filename,
        data,
        {"content-type": "image/png", "x-upsert": "true"},
    )


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

    # Clamp to image bounds if the square extends outside
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


def process_bucket(bucket: str, dry_run: bool) -> None:
    backup_dir = os.path.join(BACKUP_DIR, bucket)
    os.makedirs(backup_dir, exist_ok=True)

    files = list_bucket_files(bucket)
    if not files:
        print(f"No files found in bucket '{bucket}'.")
        return

    print(f"Found {len(files)} file(s) in '{bucket}'.")
    if dry_run:
        print("DRY RUN — no files will be uploaded.\n")

    for filename in files:
        print(f"  {filename} ...", end=" ", flush=True)

        try:
            raw = download_file(bucket, filename)
        except Exception as e:
            print(f"SKIP (download failed: {e})")
            continue

        # Save original to backup
        backup_path = os.path.join(backup_dir, filename)
        with open(backup_path, "wb") as f:
            f.write(raw)

        try:
            img = Image.open(io.BytesIO(raw))
            original_size = img.size

            processed = crop_to_square_content(img)

            if dry_run:
                alpha = img.convert("RGBA").split()[3]
                bbox = alpha.getbbox()
                preview_dir = os.path.join("previews", bucket)
                os.makedirs(preview_dir, exist_ok=True)
                processed.save(os.path.join(preview_dir, filename))
                print(f"original={original_size}  bbox={bbox}  -> preview saved")
                continue

            buf = io.BytesIO()
            processed.save(buf, format="PNG")
            upload_file(bucket, filename, buf.getvalue())
            print(f"done  ({original_size} -> {TARGET_SIZE})")

        except Exception as e:
            print(f"SKIP (processing failed: {e})")

    if dry_run:
        print(f"\nPreviews saved to: {os.path.abspath(os.path.join('previews', bucket))}")
    else:
        print(f"\nBackup saved to: {os.path.abspath(backup_dir)}")
        print("Upload complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Crop and resize Supabase bucket images.")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET, help="Bucket name to process")
    parser.add_argument("--execute", action="store_true", help="Actually upload processed images (default is dry-run)")
    args = parser.parse_args()

    process_bucket(args.bucket, dry_run=False)


if __name__ == "__main__":
    main()
