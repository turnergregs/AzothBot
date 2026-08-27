"""Tests for the art and render caches.

The two are keyed differently on purpose, and the difference is the point: art
is keyed by filename with a TTL, renders by a content hash. Getting the render
key wrong is how the OLD renderer served stale images -- it keyed on card name
alone, so any edit kept showing the previous picture.
"""
import time

import pytest

from azoth_logic import art_cache


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(art_cache, "CACHE_ROOT", tmp_path)
    monkeypatch.setattr(art_cache, "ART_DIR", tmp_path / "art")
    monkeypatch.setattr(art_cache, "RENDER_DIR", tmp_path / "renders")


# ---------------------------------------------------------------------------
# Art
# ---------------------------------------------------------------------------

def test_round_trip():
    art_cache.put_art("eigenfunctions", "a.exr", b"bytes")
    assert art_cache.get_art("eigenfunctions", "a.exr") == b"bytes"


def test_miss_returns_none():
    assert art_cache.get_art("eigenfunctions", "absent.exr") is None


def test_bucket_is_part_of_the_key():
    """The same filename can exist in two buckets -- an aspect PNG and a card
    PNG both named `x.png` must not collide."""
    art_cache.put_art("cardimages", "x.png", b"card")
    art_cache.put_art("aspectimages", "x.png", b"aspect")
    assert art_cache.get_art("cardimages", "x.png") == b"card"
    assert art_cache.get_art("aspectimages", "x.png") == b"aspect"


def test_extension_is_preserved():
    art_cache.put_art("eigenfunctions", "a.exr", b"x")
    assert art_cache.art_path("eigenfunctions", "a.exr").suffix == ".exr"


def test_stale_entries_expire(monkeypatch):
    """Storage uploads are flat-named and upserting, so a key's CONTENT can
    change when art is regenerated. The TTL bounds how long that goes unnoticed."""
    art_cache.put_art("b", "a.png", b"old")
    monkeypatch.setattr(art_cache, "ART_TTL", -1)
    assert art_cache.get_art("b", "a.png") is None


def test_fetch_calls_through_only_on_a_miss():
    calls = []

    def download(bucket, name):
        calls.append(name)
        return b"downloaded"

    assert art_cache.fetch_art_cached("b", "a.png", download) == b"downloaded"
    assert art_cache.fetch_art_cached("b", "a.png", download) == b"downloaded"
    assert calls == ["a.png"], "second call must be served from disk"


def test_no_filename_short_circuits():
    def boom(*a):
        raise AssertionError("should not download")
    assert art_cache.fetch_art_cached("b", "", boom) is None
    assert art_cache.fetch_art_cached("b", None, boom) is None


def test_empty_payload_is_not_cached():
    art_cache.put_art("b", "a.png", b"")
    assert art_cache.get_art("b", "a.png") is None


def test_cache_failure_never_breaks_a_render(monkeypatch):
    """A cache is not worth failing a render over."""
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(art_cache.Path, "mkdir", boom)
    art_cache.put_art("b", "a.png", b"x")          # must not raise


def test_writes_leave_no_temp_files(tmp_path):
    art_cache.put_art("b", "a.png", b"x")
    assert not list(art_cache.ART_DIR.glob("*.tmp"))


# ---------------------------------------------------------------------------
# Renders
# ---------------------------------------------------------------------------

CARD = {"name": "Ablution", "text": "Draw 1", "element": "sol", "valence": 2,
        "subtypes": ["Sacred"], "image": "a.exr"}


def test_render_key_changes_when_the_card_changes():
    """The failure mode this exists to prevent: the OLD renderer keyed on name
    alone, so editing a card kept serving its previous image."""
    base = art_cache.render_key(CARD, b"art", "card")
    for field, value in [("text", "Draw 2"), ("valence", 3), ("element", "blood"),
                         ("subtypes", ["Wild"]), ("name", "Other")]:
        assert art_cache.render_key({**CARD, field: value}, b"art", "card") != base, \
            f"editing {field} must invalidate the render"


def test_render_key_changes_when_the_art_changes():
    assert (art_cache.render_key(CARD, b"one", "card")
            != art_cache.render_key(CARD, b"two", "card"))


def test_render_key_changes_with_the_renderer_version(monkeypatch):
    base = art_cache.render_key(CARD, b"art", "card")
    monkeypatch.setattr(art_cache, "RENDERER_VERSION", "different")
    assert art_cache.render_key(CARD, b"art", "card") != base


def test_render_key_changes_with_render_params():
    assert (art_cache.render_key(CARD, b"a", "card", fps=15)
            != art_cache.render_key(CARD, b"a", "card", fps=30))


def test_render_key_ignores_fields_that_do_not_render():
    """`updated_at` and `id` change without altering the picture."""
    noisy = {**CARD, "updated_at": "2026-01-01", "id": 999, "created_by": 3}
    assert art_cache.render_key(noisy, b"art", "card") == art_cache.render_key(CARD, b"art", "card")


def test_render_key_is_stable_across_calls():
    assert art_cache.render_key(CARD, b"a", "card") == art_cache.render_key(CARD, b"a", "card")


def test_render_round_trip():
    art_cache.put_render("k", "gif", b"GIF89a...")
    assert art_cache.get_render("k", "gif") == b"GIF89a..."
    assert art_cache.get_render("k", "png") is None, "extension is part of the lookup"


def test_stats_and_clear():
    art_cache.put_art("b", "a.png", b"1234")
    art_cache.put_render("k", "gif", b"12345")
    s = art_cache.stats()
    assert s["art_files"] == 1 and s["render_files"] == 1
    assert s["art_bytes"] == 4 and s["render_bytes"] == 5
    art_cache.clear()
    assert art_cache.stats()["art_files"] == 0


# ---------------------------------------------------------------------------
# Invalidating art after a re-upload
# ---------------------------------------------------------------------------

def test_forget_art_drops_one_entry():
    """The (bucket, filename) key cannot see a re-upload.

    `supabase_storage.upload_image` writes a FLAT name with `x-upsert: true`, so
    `regenerate_image=True` replaces the bytes behind an unchanged filename. Only
    an explicit drop makes the next render fetch the new art; the 7-day ART_TTL
    is a staleness bound, not a fix.
    """
    art_cache.put_art("cardimages", "spark.png", b"old")
    art_cache.put_art("cardimages", "other.png", b"keep")

    art_cache.forget_art("cardimages", "spark.png")

    assert art_cache.get_art("cardimages", "spark.png") is None
    assert art_cache.get_art("cardimages", "other.png") == b"keep", "only the named entry goes"


def test_forget_art_is_scoped_to_its_bucket():
    """Two buckets can hold the same filename; dropping one must not drop both."""
    art_cache.put_art("cardimages", "same.png", b"flat")
    art_cache.put_art("aspectimages", "same.png", b"aspect")
    art_cache.forget_art("cardimages", "same.png")
    assert art_cache.get_art("aspectimages", "same.png") == b"aspect"


def test_forget_art_tolerates_a_miss():
    """Called on every create, including the first upload of a new filename."""
    art_cache.forget_art("cardimages", "never_cached.png")
    art_cache.forget_art("cardimages", "")


# ---------------------------------------------------------------------------
# Eviction
# ---------------------------------------------------------------------------
# Size-capped, on WRITE, not on a timer. The bot is hand-started and not reliably
# always-on, so a daily sweep may not fire for weeks -- and growth is bursty (one
# bulk edit plus a re-render sweep can add hundreds of MB in a minute), so a
# timer permits an unbounded spike between runs.

import os
import time


def _age(path, seconds_old):
    """Backdate a cache file so ordering is deterministic rather than racing."""
    t = time.time() - seconds_old
    os.utime(path, (t, t))


def test_renders_evict_when_over_the_cap(monkeypatch):
    monkeypatch.setattr(art_cache, "RENDER_MAX_BYTES", 1000)
    for i in range(5):
        art_cache.put_render(f"k{i}", "gif", b"x" * 300)
        _age(art_cache.RENDER_DIR / f"k{i}.gif", 100 - i)

    art_cache.put_render("new", "gif", b"x" * 300)
    assert art_cache.stats()["render_bytes"] <= art_cache.RENDER_MAX_BYTES


def test_eviction_takes_the_oldest_first(monkeypatch):
    monkeypatch.setattr(art_cache, "RENDER_MAX_BYTES", 1000)
    for i in range(4):
        art_cache.put_render(f"k{i}", "gif", b"x" * 300)
        _age(art_cache.RENDER_DIR / f"k{i}.gif", 100 - i)   # k0 oldest, k3 newest

    art_cache.put_render("new", "gif", b"x" * 300)

    assert art_cache.get_render("k0", "gif") is None, "the oldest should go first"
    assert art_cache.get_render("new", "gif") is not None, "the file just written must survive"


def test_the_file_just_written_is_never_evicted(monkeypatch):
    """A single item larger than the evict target would otherwise delete itself
    the instant it was written, and the cache would thrash instead of caching."""
    monkeypatch.setattr(art_cache, "RENDER_MAX_BYTES", 100)
    art_cache.put_render("huge", "gif", b"x" * 5000)
    assert art_cache.get_render("huge", "gif") == b"x" * 5000


def test_eviction_goes_below_the_cap_not_just_to_it(monkeypatch):
    """Evicting to exactly the cap means the very next write is over it again, so
    every render pays a full directory scan. EVICT_TO leaves headroom.

    Asserted with NO tolerance, and the file sizes are small relative to the
    target on purpose. An earlier version of this test allowed one file's slack,
    which made the assertion `<= cap` — satisfied whether the headroom existed or
    not. It passed against a mutant that removed EVICT_TO entirely.
    """
    monkeypatch.setattr(art_cache, "RENDER_MAX_BYTES", 1000)
    for i in range(10):
        art_cache.put_render(f"k{i}", "gif", b"x" * 100)
        _age(art_cache.RENDER_DIR / f"k{i}.gif", 100 - i)
    art_cache.put_render("new", "gif", b"x" * 100)      # 1100 > the 1000 cap

    used = art_cache.stats()["render_bytes"]
    target = art_cache.RENDER_MAX_BYTES * art_cache.EVICT_TO
    assert used <= target, (
        f"evicted to {used}B against a {art_cache.RENDER_MAX_BYTES}B cap; "
        f"EVICT_TO should have taken it to {target}B or below")


def test_art_evicts_on_its_own_cap(monkeypatch):
    monkeypatch.setattr(art_cache, "ART_MAX_BYTES", 1000)
    for i in range(5):
        art_cache.put_art("b", f"a{i}.png", b"x" * 300)
        _age(art_cache.art_path("b", f"a{i}.png"), 100 - i)
    art_cache.put_art("b", "new.png", b"x" * 300)
    assert art_cache.stats()["art_bytes"] <= art_cache.ART_MAX_BYTES


def test_nothing_is_evicted_under_the_cap():
    for i in range(5):
        art_cache.put_render(f"k{i}", "gif", b"x" * 10)
    assert all(art_cache.get_render(f"k{i}", "gif") is not None for i in range(5))


# ---------------------------------------------------------------------------
# mtime means two different things
# ---------------------------------------------------------------------------

def test_reading_a_render_touches_it():
    """Renders are keyed by CONTENT HASH so they can never go stale, which frees
    mtime to mean last-used. Filesystem atime cannot be relied on — most systems
    mount relatime or noatime — so the access has to be written explicitly.

    Without this, eviction is oldest-WRITTEN, and the card you render every day
    is evicted ahead of one drawn once and never touched again.
    """
    art_cache.put_render("hot", "gif", b"data")
    path = art_cache.RENDER_DIR / "hot.gif"
    _age(path, 10_000)
    before = path.stat().st_mtime

    art_cache.get_render("hot", "gif")

    assert path.stat().st_mtime > before, "a cache hit must refresh last-used"


def test_reading_art_does_NOT_touch_it():
    """The opposite rule, and the reason the two caches cannot share one policy.

    For art, mtime is the FETCH time that ART_TTL measures. Touching it on a hit
    would mean art never expires — and since Storage uploads are flat-named and
    upserting, never expiring means never noticing that the bytes changed.
    """
    art_cache.put_art("b", "a.png", b"data")
    path = art_cache.art_path("b", "a.png")
    _age(path, 10_000)
    before = path.stat().st_mtime

    art_cache.get_art("b", "a.png")

    assert path.stat().st_mtime == before, "touching art would defeat ART_TTL"


def test_art_past_its_ttl_is_a_miss():
    """Regression guard paired with the above: if someone adds a touch to
    get_art, this is the test that also has to be deleted to make it pass."""
    art_cache.put_art("b", "old.png", b"data")
    _age(art_cache.art_path("b", "old.png"), art_cache.ART_TTL + 60)
    assert art_cache.get_art("b", "old.png") is None


# ---------------------------------------------------------------------------
# Partial clear
# ---------------------------------------------------------------------------

def test_clear_dir_drops_only_what_it_names():
    art_cache.put_art("b", "a.png", b"art")
    art_cache.put_render("k", "gif", b"render")

    art_cache.clear_dir("renders")

    assert art_cache.get_render("k", "gif") is None
    assert art_cache.get_art("b", "a.png") == b"art", "art is expensive to refetch"


def test_clear_dir_rejects_an_unknown_name():
    with pytest.raises(ValueError, match="unknown cache"):
        art_cache.clear_dir("everything")
