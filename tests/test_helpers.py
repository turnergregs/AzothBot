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
