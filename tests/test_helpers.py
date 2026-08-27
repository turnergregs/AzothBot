"""Tests for the smaller helper layers: key-role detection, autocomplete
degradation, and filename slugging."""
import pytest

from supabase_client import _decode_key_role
from azoth_commands import autocomplete as ac
from azoth_commands.helpers import to_snake_case, get_local_image_path
from supabase_storage import generate_image_filename, generate_local_filename
import supabase_helpers as h


# ---------------------------------------------------------------------------
# Which key am I holding?
# ---------------------------------------------------------------------------
# The whole guard layer keys off this. Misreading a key as service_role
# reinstates the silent-empty failure it exists to prevent.

def _jwt(role):
    import base64, json
    body = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).rstrip(b"=").decode()
    return f"header.{body}.signature"


@pytest.mark.parametrize("key,expected", [
    (_jwt("anon"), "anon"),
    (_jwt("service_role"), "service_role"),
    ("sb_secret_abc123", "service_role"),        # newer opaque key formats
    ("sb_publishable_abc123", "anon"),
])
def test_known_key_formats_are_classified(key, expected):
    assert _decode_key_role(key) == expected


@pytest.mark.parametrize("key", ["garbage", "", "a.b.c", "...", _jwt(None)])
def test_unclassifiable_keys_are_unknown_not_privileged(key):
    # "unknown" must never be treated as service_role downstream -- see
    # test_supabase_helpers.test_unknown_role_is_treated_as_not_proven.
    assert _decode_key_role(key) == "unknown"


def test_unknown_is_guarded_like_anon():
    assert "unknown" != "service_role"


# ---------------------------------------------------------------------------
# Autocomplete degradation
# ---------------------------------------------------------------------------
# Discord autocomplete has no error channel: a raised exception yields no
# suggestions with nothing to explain why. This is the ONE place that catches
# SupabaseError -- and it must log, so an empty dropdown is diagnosable.

def test_autocomplete_returns_empty_and_logs_on_failure(monkeypatch, capsys):
    def boom(*a, **k):
        raise h.SupabaseQueryError("table 'game_stats' not found")
    monkeypatch.setattr(ac, "fetch_all", boom)
    assert ac.autocomplete_from_table("game_stats", "", "version") == []
    assert "AUTOCOMPLETE FAILED" in capsys.readouterr().out


def test_autocomplete_does_not_swallow_non_supabase_errors(monkeypatch):
    # A bug in our own code should surface, not be mistaken for "no matches".
    def boom(*a, **k):
        raise KeyError("typo in column name")
    monkeypatch.setattr(ac, "fetch_all", boom)
    with pytest.raises(KeyError):
        ac.autocomplete_from_table("cards", "a")


def test_autocomplete_matches_substring_case_insensitively(monkeypatch):
    monkeypatch.setattr(ac, "fetch_all",
                        lambda *a, **k: [{"name": "Salvage"}, {"name": "Reflection"}, {"name": "sale"}])
    assert ac.autocomplete_from_table("cards", "sal") == ["sale", "Salvage"]


def test_autocomplete_skips_rows_missing_the_column(monkeypatch):
    monkeypatch.setattr(ac, "fetch_all", lambda *a, **k: [{"name": "A"}, {}, {"other": "B"}])
    assert ac.autocomplete_from_table("cards", "") == ["A"]


# ---------------------------------------------------------------------------
# Filename slugging
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Catalyst of Anima", "catalyst_of_anima.png"),
    ("Ascender's Bane", "ascender_s_bane.png"),
    ("  Spaced  Out  ", "spaced_out.png"),
    ("Non-Alpha!!!", "non_alpha.png"),
])
def test_local_filenames_are_slugged(name, expected):
    assert generate_local_filename(name) == expected


def test_versioned_filename_appends_the_version():
    assert generate_image_filename("Catalyst of Anima", 2) == "catalyst_of_anima_2.png"


@pytest.mark.parametrize("remote,expected_base", [
    ("catalyst_of_anima_2.png", "catalyst_of_anima.png"),   # version suffix stripped
    ("catalyst_of_anima.png", "catalyst_of_anima.png"),
])
def test_download_path_strips_the_version_suffix(remote, expected_base):
    # Uploads are flat and upserting, so the local cache name must collapse
    # versions -- otherwise the cache never hits.
    assert get_local_image_path(remote, "dir").endswith(expected_base)


@pytest.mark.parametrize("raw,expected", [
    ("Card Name", "card_name"),
    ("9Lives", "_9lives"),      # leading digit is escaped for identifier safety
    ("a-b", "a_b"),
])
def test_snake_case(raw, expected):
    assert to_snake_case(raw) == expected


# ---------------------------------------------------------------------------
# Embed packing
# ---------------------------------------------------------------------------
# A field caps at 1024 characters, but the WHOLE embed caps at 6000 across title
# + description + every field + footer. Going over is a 400 on send, which loses
# the ENTIRE reply -- so /bulk_update's report used to vanish on a large payload
# while reporting nothing about why.

from azoth_commands.helpers import (embed_char_count, pack_fields_into_embeds,
                                    missing_asset_hint, MAX_FIELD_CHARS)


def _fields(n, size=900):
    return [(f"field {i}", "x" * size, False) for i in range(n)]


def test_a_small_report_stays_in_one_embed():
    embeds = pack_fields_into_embeds(_fields(3, 100), "Bulk update", 0x2ecc71)
    assert len(embeds) == 1
    assert len(embeds[0].fields) == 3


def test_a_large_report_splits_rather_than_truncating():
    """24 full fields is ~24k against a 6000 limit. Every field must survive."""
    embeds = pack_fields_into_embeds(_fields(24), "Bulk update", 0x2ecc71)
    assert len(embeds) > 1
    assert sum(len(e.fields) for e in embeds) == 24, "no field may be dropped"


def test_no_embed_exceeds_discord_s_limit():
    for embed in pack_fields_into_embeds(_fields(40), "Bulk update", 0x2ecc71):
        assert embed_char_count(embed) <= 6000


def test_no_embed_exceeds_discord_s_field_count():
    for embed in pack_fields_into_embeds(_fields(40, size=10), "Bulk update", 0x2ecc71):
        assert len(embed.fields) <= 25


def test_an_overlong_field_is_truncated_not_dropped():
    embeds = pack_fields_into_embeds([("f", "y" * 5000, False)], "T", 0)
    value = embeds[0].fields[0].value
    assert len(value) <= MAX_FIELD_CHARS
    assert value.endswith("...")


def test_continuation_embeds_are_labelled():
    embeds = pack_fields_into_embeds(_fields(24), "Bulk update", 0x2ecc71)
    assert embeds[0].title == "Bulk update"
    assert all(e.title.endswith("(cont.)") for e in embeds[1:])


def test_the_footer_lands_on_the_last_embed():
    """It reads as the end of the report, not the end of page one."""
    embeds = pack_fields_into_embeds(_fields(24), "Bulk insert", 0, footer="why no art")
    assert embeds[-1].footer.text == "why no art"
    assert all(e.footer.text is None for e in embeds[:-1])


def test_no_fields_still_returns_an_embed():
    """A bulk action with nothing to report must still reply."""
    assert len(pack_fields_into_embeds([], "Bulk update", 0)) == 1


# ---------------------------------------------------------------------------
# Missing-asset guidance
# ---------------------------------------------------------------------------
# Two different things go missing under assets/card_art/, restored by two
# different tools. Sending someone to sync_assets for a shader-exported
# background points them at a script that cannot produce it.

def test_a_missing_background_points_at_the_godot_exporter():
    err = FileNotFoundError("assets/card_art/backgrounds/rite_background_rest.png")
    hint = missing_asset_hint(err)
    assert "BackgroundExportTool" in hint
    # It names sync_assets only to say it CANNOT help -- never as the instruction.
    assert "cannot produce it" in hint
    assert "Run `python -m tools.sync_assets" not in hint


def test_a_missing_symbol_points_at_sync_assets():
    assert "sync_assets" in missing_asset_hint(FileNotFoundError("symbols/1life.png"))


def test_windows_paths_are_recognised():
    """The bot is deployed on Windows, where the separator is a backslash."""
    err = FileNotFoundError(r"C:\bot\assets\card_art\backgrounds\aspect_background.png")
    assert "BackgroundExportTool" in missing_asset_hint(err)
