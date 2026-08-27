"""Tests for the `/bulk_insert` and `/bulk_update` reports.

Both used to reply with a count -- "Updated 5 record(s) in `cards`" -- which
confirms the write landed but not what it did. These pin the report that
replaced it, and in particular the two things a write report must never do:
show a change that did not happen, or hide one that did.
"""
import pytest

from azoth_logic import bulk_report as br


# ---------------------------------------------------------------------------
# Diffing
# ---------------------------------------------------------------------------

def test_only_changed_fields_appear():
    lines = br.diff({"text": "a", "valence": 2}, {"text": "b", "valence": 2})
    assert lines == ["`text`: a → b"], "an unchanged field is not a change"


def test_bookkeeping_columns_are_ignored():
    """`updated_at` changes on EVERY write. Reporting it would put a spurious
    line on every record and bury the real edits."""
    lines = br.diff({"updated_at": "x", "created_at": "c", "created_by": 1, "id": 5},
                    {"updated_at": "y", "created_at": "c", "created_by": 2, "id": 5})
    assert lines == []


def test_structured_fields_report_shape_not_contents():
    """An actions array runs to hundreds of characters. Printing it would bury
    every other change in the report."""
    line = br.diff({"actions": [1, 2]}, {"actions": [1, 2, 3]})[0]
    assert line == "`actions`: 2 entries → 3 entries"
    assert "1" not in line.replace("2 entries", "").replace("3 entries", "")


@pytest.mark.parametrize("field", ["actions", "triggers", "properties", "upgrades",
                                   "image_data", "split"])
def test_every_jsonb_field_is_treated_as_structured(field):
    line = br.diff({field: []}, {field: [{"deeply": {"nested": "payload"}}]})[0]
    assert "nested" not in line


def test_long_values_are_truncated():
    line = br.diff({"text": "a"}, {"text": "x" * 500})[0]
    assert len(line) < 120 and line.endswith("…")


def test_empty_values_read_as_empty_not_none():
    """`None → Draw 1` is clearer than `null → Draw 1`, and an empty list should
    not print as `[]`."""
    assert "∅" in br.diff({"text": None}, {"text": "Draw 1"})[0]
    assert "∅" in br.diff({"subtypes": []}, {"subtypes": ["Wild"]})[0]


def test_a_field_added_by_the_update_counts_as_a_change():
    assert br.diff({}, {"valence": 3}) == ["`valence`: ∅ → 3"]


def test_no_changes_gives_no_lines():
    assert br.diff({"text": "a"}, {"text": "a"}) == []


# ---------------------------------------------------------------------------
# Insert summaries
# ---------------------------------------------------------------------------

def test_new_row_names_its_identifying_attributes():
    line = br.summarize_new("cards", {"id": 501, "name": "Newbie", "element": "sol",
                                      "valence": 3, "subtypes": ["Wild"], "image": "a.exr"})
    assert "Newbie" in line and "#501" in line
    assert "Sol" in line and "v3" in line and "Wild" in line


def test_missing_art_is_flagged():
    """Art is uploaded AFTER an insert, so `no art` is the expected state --
    and it is the reason inserts are not rendered."""
    assert "no art" in br.summarize_new("cards", {"id": 1, "name": "X", "image": None})
    assert "no art" not in br.summarize_new("cards", {"id": 1, "name": "X", "image": "a.exr"})


def test_colourless_is_named():
    assert "Colourless" in br.summarize_new("cards", {"id": 1, "name": "X", "element": None})


def test_aspect_and_rite_show_their_own_stat():
    assert "attune 2" in br.summarize_new("aspects", {"id": 1, "name": "A", "attunement": 2})
    assert "foresight 3" in br.summarize_new("events", {"id": 1, "name": "R", "foresight": 3})


def test_zero_valence_is_shown():
    assert "v0" in br.summarize_new("cards", {"id": 1, "name": "X", "valence": 0})


def test_unnamed_row_does_not_crash():
    assert "(unnamed)" in br.summarize_new("cards", {"id": 1})


# ---------------------------------------------------------------------------
# Fitting to Discord's limits
# ---------------------------------------------------------------------------

def test_truncation_is_always_announced():
    """Silent truncation is the worst case for a write report: it reads as
    'that is everything that changed' when it is not."""
    out = br.fit(["x" * 200] * 20)
    assert len(out) <= 1024
    assert "more" in out


def test_short_lists_pass_through_whole():
    assert br.fit(["a", "b"]) == "a\nb"


def test_empty_list_has_a_placeholder():
    assert br.fit([]) == "—"


def test_renderable_tables_map_to_kinds():
    """Only these three can be drawn; a bosses or custom_actions row cannot."""
    assert br.RENDERABLE == {"cards": "card", "aspects": "aspect", "events": "rite"}
    assert "bosses" not in br.RENDERABLE


def test_render_cap_is_bounded():
    from azoth_commands.misc import MAX_RENDERED
    assert 1 <= MAX_RENDERED <= 25, "rendering is ~0.7s each on a cold cache"
