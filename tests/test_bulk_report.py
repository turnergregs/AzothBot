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


# ---------------------------------------------------------------------------
# The mechanic blobs are quiet
# ---------------------------------------------------------------------------
# They used to print their shape on every record -- `properties`: 1 entry → 0
# entries -- which says nothing anyone can act on. A real nine-card update came
# back 36 lines long, 27 of them shape. What changed is in the rules text.

@pytest.mark.parametrize("field", ["actions", "triggers", "properties",
                                   "upgrades", "image_data"])
def test_a_mechanic_blob_is_not_diffed_field_by_field(field):
    lines = br.diff({field: [1, 2]}, {field: [1, 2, 3]})
    assert lines == [f"*{field} updated*"], "shape is not worth a line of its own"


@pytest.mark.parametrize("field", ["actions", "triggers", "properties",
                                   "upgrades", "image_data"])
def test_blob_contents_never_leak(field):
    """An actions array runs to hundreds of characters and would bury every
    other change in the report."""
    line = br.diff({field: []}, {field: [{"deeply": {"nested": "payload"}}]})[0]
    assert "nested" not in line


def test_several_blobs_collapse_into_one_note():
    lines = br.diff({"actions": [1], "properties": [1], "upgrades": [1]},
                    {"actions": [2], "properties": [], "upgrades": [2]})
    assert lines == ["*actions, properties, upgrades updated*"]


def test_a_blob_change_is_silent_when_the_text_moved():
    """The whole point: an edit to `actions` shows up as an edit to `text`, so
    saying it twice is what made the report long."""
    lines = br.diff({"text": "Recall 1, Exhaust", "properties": [{"p": 1}], "upgrades": [1]},
                    {"text": "Recall 1", "properties": [], "upgrades": [2]})
    assert lines == ["`text`: Recall 1, Exhaust → Recall 1"]


def test_a_blob_change_is_reported_when_the_text_did_not_move():
    """The case the collapse must NOT swallow: mechanics changed and nothing
    player-facing did, so the report is the only place it is visible."""
    lines = br.diff({"text": "Recall 1", "actions": [1]},
                    {"text": "Recall 1", "actions": [2]})
    assert lines == ["*actions updated*"]


def test_an_unchanged_blob_is_not_announced():
    assert br.diff({"text": "a", "actions": [1]}, {"text": "b", "actions": [1]}) \
        == ["`text`: a → b"]


def test_text_absent_from_the_payload_counts_as_unmoved():
    """A partial update that touches only `properties` never mentions `text`.
    Treating a missing key as 'changed' would silence the note entirely."""
    assert br.diff({"properties": [1]}, {"properties": []}) == ["*properties updated*"]


# ---------------------------------------------------------------------------
# Player-facing fields are still diffed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("field,old,new", [
    ("text", "Draw 1", "Draw 2"),
    ("name", "Recall", "Reclaim"),
    ("element", "sol", "luna"),
    ("valence", 2, 3),
    ("subtypes", ["Sacred"], ["Wild"]),
])
def test_player_facing_fields_show_old_and_new(field, old, new):
    assert br.diff({field: old}, {field: new}) == [f"`{field}`: {old} → {new}"]


def test_split_is_diffed_as_a_face_not_counted():
    """`split` is the one jsonb column that is player-facing -- it IS a second
    element and valence -- so it stays out of `_QUIET`."""
    line = br.diff({"split": None}, {"split": {"element": "sol", "valence": 4}})[0]
    assert line == "`split`: ∅ → Sol valence 4"


def test_an_unanticipated_column_is_shown_rather_than_dropped():
    """Only the listed blobs are quiet. A column nobody thought about is louder
    than it needs to be, which is the safe direction for a write report."""
    assert br.diff({"rarity": "common"}, {"rarity": "rare"}) == \
        ["`rarity`: common → rare"]


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


# ---------------------------------------------------------------------------
# Labels: the table is named once, not on every record
# ---------------------------------------------------------------------------
# A nine-card update read "Accretion · cards", "Confluence · cards", nine times.
# The suffix disambiguates nothing when every row is a card -- but it cannot
# just be dropped, because names DO collide across types (`deck_contents` is a
# universal join table for exactly that reason).

def _update_groups(*names, table="cards"):
    return {(table, n): ["`text`: a → b"] for n in names}


def test_a_single_table_update_labels_records_by_name_alone():
    labels = [f[0] for f in br.report_fields(_update_groups("Recall", "Grasp"))]
    assert labels == ["Recall", "Grasp"]


def test_a_single_table_update_names_its_table_in_the_footer():
    assert br.table_note(_update_groups("Recall")) == "All records are in cards."


def test_a_multi_table_update_keeps_the_table_on_every_label():
    """Two things called Recall in different tables are two different things."""
    groups = {("cards", "Recall"): ["x"], ("aspects", "Recall"): ["y"]}
    assert [f[0] for f in br.report_fields(groups)] == ["Recall · cards", "Recall · aspects"]


def test_a_multi_table_update_has_no_footer_note():
    """It would have to name both tables, which the labels already do."""
    assert br.table_note({("cards", "A"): ["x"], ("aspects", "B"): ["y"]}) is None


def test_an_insert_group_still_counts_its_rows():
    """bulk_insert keys by TABLE with no record name -- one field per table,
    holding a line per new row. That label is unaffected."""
    groups = {("cards", ""): ["• A", "• B"]}
    assert br.report_fields(groups)[0][0] == "cards (2)"
    assert br.table_note(groups) is None, "the label already says it"


def test_a_record_with_no_changes_says_so():
    """An empty field value is a Discord 400. It also has to read as 'nothing
    changed' rather than as a rendering failure."""
    label, value, _ = br.report_fields({("cards", "Grasp"): []}, "no field changed")[0]
    assert (label, value) == ("Grasp", "*no field changed*")
