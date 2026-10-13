"""`/announce`: what is refused before it reaches every player.

An announcement is shown to each player on their next launch, and an edited row
is never shown again to anyone who confirmed it, so a mistake cannot be fixed
in place. These pin the checks that stand between a typo and that.
"""
from datetime import datetime, timedelta, timezone

import pytest

from azoth_logic import announcements as ann
from azoth_logic.announcements import AnnouncementError

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def build(body="News.", **kwargs):
    return ann.build_row(body, now=NOW, **kwargs)


# ---------------------------------------------------------------------------
# The body
# ---------------------------------------------------------------------------

def test_the_body_is_stored_trimmed():
    assert build("  Welcome back!  ")["body"] == "Welcome back!"


@pytest.mark.parametrize("body", ["", "   ", None])
def test_an_empty_body_is_refused(body):
    with pytest.raises(AnnouncementError):
        build(body)


def test_the_body_limit_is_the_tables():
    # The CHECK is on the trimmed length, so padding does not count against it.
    assert build(" " + "x" * ann.MAX_BODY + " ")["body"] == "x" * ann.MAX_BODY
    with pytest.raises(AnnouncementError, match="401"):
        build("x" * (ann.MAX_BODY + 1))


def test_paired_terms_pass():
    body = "[term]Update 0.11[/term] is in, with a new [term]hero[/term]."
    assert build(body)["body"] == body


@pytest.mark.parametrize("body", [
    "An [term]unclosed term.",          # colours the rest of the line
    "A stray[/term] close.",
    "[term]One [term]inside[/term] another.[/term]",
    "[/term]Backwards[term]",
])
def test_unpaired_terms_are_refused(body):
    # The game swaps the tags for colour tags by plain replacement, so it does
    # not notice a missing one; the player does.
    with pytest.raises(AnnouncementError):
        build(body)


def test_a_term_tag_in_the_wrong_case_is_refused():
    # The game matches `[term]` exactly; `[Term]` would be printed as text.
    with pytest.raises(AnnouncementError, match="lower case"):
        build("[Term]Update[/Term]")


def test_tags_do_not_count_toward_the_length_a_player_reads():
    assert ann.visible_length("[term]Hi[/term] [b]you[/b]") == len("Hi you")
    # Not a tag: a player reads the brackets.
    assert ann.visible_length("Costs [3] life") == len("Costs [3] life")


# ---------------------------------------------------------------------------
# Times
# ---------------------------------------------------------------------------

def test_only_what_was_given_is_in_the_row():
    # A column left out takes the table's default. Sending `starts_at` for a
    # post meant for "now" would stamp it with this machine's clock instead.
    assert build() == {"body": "News."}


@pytest.mark.parametrize("typed,expected", [
    ("2026-10-20", "2026-10-20T00:00:00+00:00"),
    ("2026-10-20 18:00", "2026-10-20T18:00:00+00:00"),
    ("2026-10-20T18:00:00Z", "2026-10-20T18:00:00+00:00"),
    ("2026-10-20 18:00-05:00", "2026-10-20T23:00:00+00:00"),   # an offset is honoured
])
def test_a_time_is_stored_in_utc(typed, expected):
    assert build(starts=typed)["starts_at"] == expected


def test_a_time_that_cannot_be_read_is_refused_by_name():
    with pytest.raises(AnnouncementError, match="ends"):
        build(ends="next friday")


def test_an_end_must_follow_the_start():
    with pytest.raises(AnnouncementError, match="after"):
        build(starts="2026-10-20", ends="2026-10-20")
    row = build(starts="2026-10-20", ends="2026-10-27")
    assert row["ends_at"] == "2026-10-27T00:00:00+00:00"


def test_an_end_already_past_is_refused():
    # Live window closed before it opened: no player would ever be shown it.
    with pytest.raises(AnnouncementError, match="never be shown"):
        build(ends=(NOW - timedelta(hours=1)).isoformat())
    with pytest.raises(AnnouncementError, match="never be shown"):
        build(starts="2026-10-01", ends="2026-10-02")


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("version", ["v0.11.0", "0.11-beta", "0.11.", "latest", "0,11"])
def test_a_version_must_be_dotted_numbers(version):
    # The game reads each part with int(), so anything else compares as zero
    # and the announcement reaches the wrong builds without an error anywhere.
    with pytest.raises(AnnouncementError, match="min_version"):
        build(min_version=version)


def test_versions_are_ordered_by_number_as_the_game_orders_them():
    assert ann.version_key("0.10.0") > ann.version_key("0.9.9")
    assert ann.version_key("0.11") == ann.version_key("0.11.0")
    assert ann.version_key("0.11.1") > ann.version_key("0.11")


def test_a_version_range_no_build_is_in_is_refused():
    with pytest.raises(AnnouncementError, match="no build"):
        build(min_version="0.10.0", max_version="0.9.9")
    row = build(min_version=" 0.11.0 ", max_version="0.11")
    assert (row["min_version"], row["max_version"]) == ("0.11.0", "0.11")


# ---------------------------------------------------------------------------
# The speaker
# ---------------------------------------------------------------------------

HEROES = [{"id": 4, "name": "Alpha"}, {"id": 9, "name": "Beta"}]


def test_a_speaker_is_found_whatever_its_case():
    assert ann.find_hero(" beta ", HEROES)["id"] == 9
    assert build(speaker_hero_id=9)["speaker_hero_id"] == 9


def test_the_default_speaker_is_found_by_id_not_by_name():
    heroes = HEROES + [{"id": ann.DEFAULT_SPEAKER_ID, "name": "Renamed"}]
    assert ann.default_hero(heroes)["name"] == "Renamed"
    # Archived, so not among the heroes read: the speaker is left to the game.
    assert ann.default_hero(HEROES) is None


def test_an_unknown_speaker_is_refused_with_the_ones_there_are():
    with pytest.raises(AnnouncementError, match="Alpha, Beta"):
        ann.find_hero("Gamma", HEROES)


# ---------------------------------------------------------------------------
# The reply
# ---------------------------------------------------------------------------

def test_a_preview_says_nothing_was_written_and_a_post_gives_its_id():
    row = build("[term]Update[/term] is in.")
    preview = ann.describe(row, now=NOW)
    assert "Nothing was written" in preview and "#" not in preview

    posted = ann.describe(row, "Beta", row_id=31, now=NOW)
    assert "#31" in posted and "Beta" in posted
    assert "> **Update** is in." in posted


def test_the_reply_says_when_and_for_whom():
    row = build(starts="2026-10-20 18:00", ends="2026-10-27", min_version="0.11.0")
    reply = ann.describe(row, now=NOW)
    starts = int(datetime(2026, 10, 20, 18, tzinfo=timezone.utc).timestamp())
    assert f"from <t:{starts}:F>" in reply
    assert "0.11.0 and up" in reply
    assert "it is pulled" not in reply


def test_a_backdated_start_is_called_out():
    # A player is never shown news dated before they installed, so backdating
    # hides it from everyone newer than the date.
    reply = ann.describe(build(starts="2026-10-01"), now=NOW)
    assert "installed after" in reply


def test_a_long_body_is_warned_about_not_refused():
    reply = ann.describe(build("x" * (ann.BUBBLE_CHARS + 1)), now=NOW)
    assert "⚠️" in reply
    assert "⚠️" not in ann.describe(build("x" * ann.BUBBLE_CHARS), now=NOW)


# ---------------------------------------------------------------------------
# The command
# ---------------------------------------------------------------------------

class _Interaction:
    """Enough of a Discord interaction to run the command's body."""

    def __init__(self, user_id):
        self.user = type("User", (), {"id": user_id})()
        self.sent = []
        self.response = self.followup = self

    async def send_message(self, text, **kwargs):
        self.sent.append(text)

    async def send(self, text, **kwargs):
        self.sent.append(text)

    async def defer(self):
        pass


def _run(monkeypatch, user_id=1, heroes=HEROES, **options):
    """Run /announce as `user_id`; returns (replies, inserted rows)."""
    import asyncio
    from azoth_commands import AzothCommands, announcements as module

    inserted = []

    def create_record(table, data):
        inserted.append((table, data))
        return [{**data, "id": 31}]

    monkeypatch.setattr(module, "create_record", create_record)
    monkeypatch.setattr(module, "fetch_all", lambda *a, **k: heroes)

    options = {"body": "News.", "speaker": None, "starts": None, "ends": None,
               "min_version": None, "max_version": None, "preview": False, **options}
    interaction = _Interaction(user_id)
    asyncio.run(AzothCommands.announce_cmd.callback(None, interaction, **options))
    return interaction.sent, inserted


def test_only_an_authorized_user_can_post(monkeypatch):
    """The bot holds the service key and the row reaches every player, so
    `require_authorized` is the only guard there is. conftest authorizes 1 and 2."""
    replies, inserted = _run(monkeypatch, user_id=999)
    assert inserted == []
    assert "not authorized" in replies[0]


def test_posting_inserts_the_row_and_replies_with_its_id(monkeypatch):
    replies, inserted = _run(monkeypatch, speaker="Beta", min_version="0.11.0")
    assert inserted == [("announcements",
                         {"body": "News.", "speaker_hero_id": 9, "min_version": "0.11.0"})]
    assert "#31" in replies[0]


def test_no_speaker_picked_writes_the_default_one(monkeypatch):
    heroes = HEROES + [{"id": ann.DEFAULT_SPEAKER_ID, "name": "Default"}]
    replies, inserted = _run(monkeypatch, heroes=heroes)
    assert inserted[0][1] == {"body": "News.", "speaker_hero_id": ann.DEFAULT_SPEAKER_ID}
    assert "Default" in replies[0]


def test_a_missing_default_speaker_leaves_the_column_to_the_game(monkeypatch):
    # NULL is the game's own fallback to the same hero, so the post still goes.
    _, inserted = _run(monkeypatch, heroes=HEROES)
    assert inserted[0][1] == {"body": "News."}


def test_a_preview_writes_nothing(monkeypatch):
    replies, inserted = _run(monkeypatch, preview=True)
    assert inserted == []
    assert "Nothing was written" in replies[0]


def test_a_refused_announcement_writes_nothing(monkeypatch):
    replies, inserted = _run(monkeypatch, body="An [term]unclosed term.")
    assert inserted == []
    assert replies[0].startswith("❌")
