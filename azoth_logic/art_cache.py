"""On-disk caches for card art and for animated renders.

Art fetching dominates every render path -- measured at 0.68s per item
downloading versus 0.04s drawing -- so the same EXR being pulled once per card
per command is where the time goes. A 110-card deck or a 20-result search hits
the same handful of files repeatedly.

Two caches, deliberately keyed differently:

  ART      keyed by (bucket, filename). Storage uploads are flat-named and
           upserting, so a filename's CONTENT can change when art is
           regenerated -- see supabase_storage.upload_image. Entries therefore
           expire (ART_TTL) rather than living forever.

  RENDER   keyed by a CONTENT HASH of the item's rendered fields plus the art
           bytes plus RENDERER_VERSION. Editing a card's text, regenerating its
           art, or changing the renderer all produce a different key, so a stale
           render can't be served. Only ANIMATED renders are cached: a still is
           0.04s and not worth the bookkeeping, while a GIF is 1.3-2.8s.

Both live under `cache/`, which is gitignored. Deleting it is always safe.

EVICTION is size-capped and happens ON WRITE, not on a timer. Two reasons it is
not a daily sweep: the bot is hand-started and not reliably always-on, so a timer
may not fire for weeks -- exactly when growth has accumulated -- and growth is
bursty rather than time-proportional, since one bulk edit plus a re-render sweep
can add hundreds of megabytes in a minute. Tying eviction to the thing that
CAUSES growth makes the cap an actual ceiling.

Both caps order by mtime, oldest first, but mtime MEANS something different in
each -- see ART_MAX_BYTES and RENDER_MAX_BYTES.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

CACHE_ROOT = Path(__file__).resolve().parent.parent / "cache"
ART_DIR = CACHE_ROOT / "art"
RENDER_DIR = CACHE_ROOT / "renders"

# How long a cached art file is trusted. Art changes only when someone
# regenerates it, which is rare, but a flat-named upsert means the same key can
# point at new bytes -- so this is a staleness bound, not a size control.
ART_TTL = 7 * 24 * 3600

# Bump when the renderer's output changes, to invalidate every cached render.
# The card layout, symbol sizing and outline weights all feed this.
#
# BUMP IT IN THE SAME COMMIT AS THE CHANGE. Forgetting is invisible in testing --
# a fresh cache renders correctly every time -- and shows up only as a deployed
# bot serving an image that no longer matches the code. That happened on
# 2026-08-28: the comparison stopped squashing its faces, the version did not
# move, and every already-cached comparison kept its distortion.
RENDERER_VERSION = "2026-09-02.1"      # {...} display placeholders resolve

# Size caps, enforced on write. Exceeding one evicts oldest-first down to
# EVICT_TO of the cap, so a write does not trigger a scan every time.
#
# ART is naturally BOUNDED: the key is (bucket, filename) and the content pool is
# finite, so it holds at most one file per item -- measured at ~579 KB per `.exr`
# across ~395 animatable items, so it settles near 250 MB and stops. This cap sits
# above that as a backstop, and should effectively never fire.
#
# RENDERS are NOT bounded: the key is a content hash, so every edit to an item
# leaves its previous render orphaned forever, at ~1.96 MB a time. This cap is the
# real control.
ART_MAX_BYTES = 300 * 1024 * 1024
RENDER_MAX_BYTES = 400 * 1024 * 1024
EVICT_TO = 0.8

# Fields that affect a rendered face. Anything not here can change without
# invalidating a render.
_RENDERED_FIELDS = ("name", "text", "element", "valence", "subtypes", "split",
                    "image", "image_data")


def _measure(directory: Path):
    """[(path, stat), ...] for the real cache files, ignoring temp writes."""
    try:
        return [(f, f.stat()) for f in directory.iterdir()
                if f.is_file() and f.suffix != ".tmp"]
    except OSError:
        return []


def _evict(directory: Path, max_bytes: int, keep: Path = None) -> int:
    """Delete oldest-first until the directory is under `max_bytes` * EVICT_TO.

    Ordering is by mtime, which means LAST FETCHED for art and LAST USED for
    renders -- see get_render, which touches on a hit, and get_art, which
    deliberately does not.

    `keep` is never evicted. Without it a single file larger than the target
    would delete itself immediately after being written, and the cache would
    thrash instead of caching.

    Returns the number of files removed.
    """
    entries = _measure(directory)
    total = sum(st.st_size for _, st in entries)
    if total <= max_bytes:
        return 0

    target = int(max_bytes * EVICT_TO)
    entries.sort(key=lambda e: e[1].st_mtime)          # oldest first
    removed = 0
    for path, st in entries:
        if total <= target:
            break
        if keep is not None and path == keep:
            continue
        try:
            path.unlink()
        except OSError:
            continue                # a cache is never worth failing a render over
        total -= st.st_size
        removed += 1
    return removed


def _safe(name: str) -> str:
    """A filesystem-safe key. Hashed rather than sanitised so two different
    names can never collide into one cache entry."""
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Art
# ---------------------------------------------------------------------------

def art_path(bucket: str, filename: str) -> Path:
    suffix = Path(filename).suffix or ".bin"
    return ART_DIR / f"{_safe(bucket + '/' + filename)}{suffix}"


def get_art(bucket: str, filename: str) -> bytes | None:
    path = art_path(bucket, filename)
    try:
        if not path.is_file():
            return None
        if time.time() - path.stat().st_mtime > ART_TTL:
            return None
        return path.read_bytes()
    except OSError:
        return None


def put_art(bucket: str, filename: str, data: bytes) -> None:
    """Write via a temp file + os.replace so a crash mid-write cannot leave a
    truncated file that later reads as valid art."""
    if not data:
        return
    path = art_path(bucket, filename)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        return                      # a cache is never worth failing a render over
    _evict(ART_DIR, ART_MAX_BYTES, keep=path)


def forget_art(bucket: str, filename: str) -> None:
    """Drop one cached art file. Call after RE-UPLOADING art under that name.

    `supabase_storage.upload_image` writes a FLAT name with `x-upsert: true`, so
    regenerating a card's art replaces the bytes behind an unchanged filename --
    the one case the (bucket, filename) key cannot see. Without this, a
    `regenerate_image=True` would keep serving the previous art for up to
    ART_TTL, which is exactly the stale-image bug the render cache was keyed to
    avoid.
    """
    if not filename:
        return
    try:
        art_path(bucket, filename).unlink(missing_ok=True)
    except OSError:
        pass


def fetch_art_cached(bucket: str, filename: str, download) -> bytes | None:
    """Cache-aware fetch. `download(bucket, filename)` is called on a miss."""
    if not filename:
        return None
    hit = get_art(bucket, filename)
    if hit is not None:
        return hit
    data = download(bucket, filename)
    put_art(bucket, filename, data)
    return data


# ---------------------------------------------------------------------------
# Rendered animations
# ---------------------------------------------------------------------------

def render_key(item: dict, art: bytes | None, kind: str, **params) -> str:
    """A key that changes whenever the output would.

    Covers the item's rendered fields, the art bytes, the renderer version and
    any render parameters (fps, duration). Keying on name alone -- what the old
    renderer did -- serves a stale image after any edit.
    """
    payload = {
        "kind": kind,
        "version": RENDERER_VERSION,
        "params": sorted(params.items()),
        "fields": {k: item.get(k) for k in _RENDERED_FIELDS},
        "art": hashlib.sha256(art).hexdigest() if art else None,
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:32]


def get_render(key: str, ext: str) -> bytes | None:
    path = RENDER_DIR / f"{key}.{ext}"
    try:
        if not path.is_file():
            return None
        data = path.read_bytes()
    except OSError:
        return None

    # Touch, so mtime means LAST USED and eviction is true LRU.
    #
    # Filesystem atime cannot be relied on -- most systems mount relatime or
    # noatime -- so the access time has to be written explicitly. Renders are
    # keyed by a CONTENT HASH and so can never go stale, which is what frees
    # mtime up to mean this. get_art must NOT do the same: there mtime is the
    # fetch time that ART_TTL measures, and touching it would mean art never
    # expires.
    try:
        os.utime(path, None)
    except OSError:
        pass
    return data


def put_render(key: str, ext: str, data: bytes) -> None:
    if not data:
        return
    path = RENDER_DIR / f"{key}.{ext}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        return
    _evict(RENDER_DIR, RENDER_MAX_BYTES, keep=path)


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

def stats() -> dict:
    """Size and headroom for both caches. Surfaced by `/cache status`."""
    def measure(d: Path):
        entries = _measure(d)
        return len(entries), sum(st.st_size for _, st in entries)

    a_n, a_b = measure(ART_DIR)
    r_n, r_b = measure(RENDER_DIR)
    return {
        "art_files": a_n, "art_bytes": a_b, "art_max_bytes": ART_MAX_BYTES,
        "render_files": r_n, "render_bytes": r_b, "render_max_bytes": RENDER_MAX_BYTES,
        "total_bytes": a_b + r_b,
    }


def clear() -> None:
    """Drop both caches. Always safe -- they rebuild on the next render."""
    import shutil
    shutil.rmtree(CACHE_ROOT, ignore_errors=True)


def clear_dir(which: str) -> None:
    """Drop just one cache. `which` is "art" or "renders".

    Renders alone is the useful case: art is expensive to re-download and bounded
    anyway, while a render is the thing you want to force a redraw of.
    """
    import shutil
    target = {"art": ART_DIR, "renders": RENDER_DIR}.get(which)
    if target is None:
        raise ValueError(f"unknown cache: {which!r}")
    shutil.rmtree(target, ignore_errors=True)
