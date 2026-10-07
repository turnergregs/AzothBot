"""Tests for /live_reports -- player reports, survey comments and crashes every 10 minutes.

The contract under test: every non-crash report, every survey row with a
comment, and every crash not from a developer account is posted, each kind as
its own message; each cycle posts what is new and nothing when there is
nothing; a watermark advances only after a message is out and only past the
rows it carried (a failed send retries next cycle, never skips), and past
developer crashes it read and passed over; the kinds never hold each other
back; a new channel starts where /daily_reports stopped, never at id 0.
"""
import asyncio

import nextcord
import pytest

import supabase_helpers
from azoth_commands import daily_reports as dr
from azoth_commands import live_reports as lr


class _FakeDB:
    """Just enough of postgrest's builder for the queries live_reports makes."""

    def __init__(self, tables):
        self.tables = tables
        self.fail = False
        # Tables that do not exist yet, as a database before a migration.
        self.missing = set()

    def table(self, name):
        fake = self

        class Q:
            def __init__(self):
                self.rows = list(fake.tables.get(name, []))
                self.want_count = False
                self.cap = None

            def select(self, *a, count=None):
                self.want_count = count == "exact"
                return self

            def gt(self, col, v):
                self.rows = [r for r in self.rows if r[col] > v]
                return self

            def eq(self, col, v):
                # SQL: NULL = x is NULL, so the row is dropped.
                self.rows = [r for r in self.rows if r.get(col) is not None and r[col] == v]
                return self

            def neq(self, col, v):
                # SQL: NULL <> 'crash' is NULL, so the row is dropped.
                self.rows = [r for r in self.rows if r.get(col) is not None and r[col] != v]
                return self

            def in_(self, col, vals):
                self.rows = [r for r in self.rows if r.get(col) in vals]
                return self

            def order(self, col, desc=False):
                self.rows.sort(key=lambda r: r[col], reverse=desc)
                return self

            def limit(self, n):
                self.cap = n
                return self

            def execute(self):
                if fake.fail:
                    raise RuntimeError("PostgREST 503")
                if name in fake.missing:
                    raise RuntimeError(f"relation public.{name} does not exist")
                count = len(self.rows) if self.want_count else None
                data = self.rows[:self.cap] if self.cap is not None else self.rows
                return type("R", (), {"data": data, "count": count})()

        return Q()


def _report(i, report_type="bug", **kw):
    row = {"id": i, "player_uuid": "p1", "report_type": report_type, "category": "Bug Report",
           "description": f"broke {i}", "contact_info": None, "game_version": "0.9.1",
           "created_at": "2026-09-20T12:00:00+00:00"}
    row.update(kw)
    return row


def _crash(i, player="p1", error="SCRIPT ERROR: Stack overflow.\n  at: res://scripts/a.gd:3", **kw):
    row = {"id": i, "player_uuid": player, "report_type": "crash", "category": None,
           "description": "(auto-captured — no player description)", "error_message": error,
           "game_state": "RESOLVING_CARDS", "os_info": "Windows", "game_version": "0.9.12",
           "created_at": "2026-10-07T12:00:00+00:00"}
    row.update(kw)
    return row


def _survey(i, comment="the boss took forever", **kw):
    row = {"id": i, "player_uuid": "p1", "question_id": "difficulty", "answer": "too_hard",
           "comment": comment, "has_comment": bool(comment and comment.strip()),
           "outcome": "answered", "moment": "loss", "run_number": 7,
           "version": "0.9.12", "created_at": "2026-10-04T12:00:00+00:00"}
    row.update(kw)
    return row


class _Channel:
    def __init__(self, fail_on=()):
        self.sent = []
        self.calls = 0
        self.fail_on = set(fail_on)

    async def send(self, content=None, embed=None, file=None, allowed_mentions=None):
        self.calls += 1
        await asyncio.sleep(0)            # a real send yields to the loop
        if self.calls in self.fail_on:
            raise RuntimeError("Discord 500")
        self.sent.append({"embed": embed, "allowed_mentions": allowed_mentions})


class _Bot:
    def __init__(self, channel):
        self.channel = channel

    def get_channel(self, cid):
        return self.channel


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(lr, "STATE_FILE", str(tmp_path / "live_state.json"))
    monkeypatch.setattr(dr, "STATE_FILE", str(tmp_path / "daily_state.json"))
    monkeypatch.setattr(supabase_helpers, "SUPABASE_ROLE", "service_role")
    fake = _FakeDB({"reports": [], "survey_responses": [],
                    "players": [{"uuid": "p1", "name": "Mira", "developer": False},
                                {"uuid": "dev", "name": "Turner", "developer": True}]})
    monkeypatch.setattr(lr, "supabase", fake)
    lr._save_state({"channels": {"123": {"last_report_id": 0, "last_survey_id": 0, "last_crash_id": 0}}})
    return fake


def _ids(channel, noun):
    return [int(f.name.rsplit("#", 1)[1].split(" ")[0])
            for m in channel.sent if noun in m["embed"].title for f in m["embed"].fields]


def _cycle(bot):
    asyncio.run(lr._post_all_channels(bot, "test"))


def _watermarks():
    c = lr._load_state()["channels"]["123"]
    return c.get("last_report_id"), c.get("last_survey_id"), c.get("last_crash_id")


# --- Player reports ----------------------------------------------------------

def test_every_report_but_crashes_is_a_player_report(env):
    env.tables["reports"] = [_report(1, "crash"), _report(2), _report(3, "feature_request"),
                             _report(4, "content_idea"), _report(5, None)]
    channel = _Channel()
    _cycle(_Bot(channel))
    assert _ids(channel, "player report") == [2, 3, 4]
    assert _ids(channel, "crash") == [1]
    assert [f.name for f in channel.sent[0]["embed"].fields][1:] == [
        "Feature Request #3 · Bug Report", "Content Idea #4 · Bug Report"]


def test_each_cycle_posts_only_what_is_new_and_nothing_when_quiet(env):
    env.tables["reports"] = [_report(1)]
    channel = _Channel()
    bot = _Bot(channel)
    _cycle(bot)
    _cycle(bot)                                   # nothing new: no message
    assert channel.calls == 1

    env.tables["reports"].append(_report(2))
    env.tables["survey_responses"].append(_survey(1))
    _cycle(bot)
    assert _ids(channel, "player report") == [1, 2]
    assert _ids(channel, "survey comment") == [1]
    assert _watermarks() == (2, 1, 0)


def test_a_backlog_drains_oldest_first_without_flooding(env):
    """35 waiting -> three messages of 10 this cycle, the last 5 the next."""
    env.tables["reports"] = [_report(i) for i in range(1, 36)]
    channel = _Channel()
    bot = _Bot(channel)
    _cycle(bot)
    assert channel.calls == lr.MESSAGES_PER_CYCLE == 3
    assert _ids(channel, "player report") == list(range(1, 31))
    assert channel.sent[0]["embed"].title == "10 new player reports"
    assert "25 more waiting" in channel.sent[0]["embed"].footer.text
    assert "5 more waiting" in channel.sent[2]["embed"].footer.text

    _cycle(bot)
    assert _ids(channel, "player report") == list(range(1, 36))
    assert channel.sent[-1]["embed"].title == "5 new player reports"
    assert not channel.sent[-1]["embed"].footer.text


def test_a_failed_send_retries_the_same_rows_next_cycle(env):
    env.tables["reports"] = [_report(1), _report(2)]
    channel = _Channel(fail_on={1})
    bot = _Bot(channel)
    _cycle(bot)
    assert _watermarks()[0] == 0
    _cycle(bot)
    assert _ids(channel, "player report") == [1, 2]   # retried, not skipped
    assert _watermarks()[0] == 2


def test_a_failed_report_send_does_not_hold_back_the_other_kinds(env):
    env.tables["reports"] = [_report(1), _crash(2)]
    env.tables["survey_responses"] = [_survey(1)]
    channel = _Channel(fail_on={1})
    _cycle(_Bot(channel))
    assert _watermarks() == (0, 1, 2)
    assert _ids(channel, "survey comment") == [1]
    assert _ids(channel, "crash") == [2]


def test_the_report_line_shows_who_when_and_how_to_reach_them(env):
    env.tables["reports"] = [_report(1, contact_info="mira#0001"),
                             _report(2, category="Visual", description="")]
    channel = _Channel()
    _cycle(_Bot(channel))
    first, second = channel.sent[0]["embed"].fields
    assert first.name == "Bug #1"                     # "Bug Report" repeats the type: dropped
    assert "Mira" in first.value and "v0.9.1" in first.value
    assert "<t:" in first.value and "contact: mira#0001" in first.value
    assert second.name == "Bug #2 · Visual"
    assert "(no description)" in second.value


# --- Crashes -----------------------------------------------------------------

def test_developer_crashes_are_left_out_and_read_past(env):
    env.tables["reports"] = [_crash(1, "dev"), _crash(2), _crash(3, None), _crash(4, "dev")]
    channel = _Channel()
    _cycle(_Bot(channel))
    assert _ids(channel, "crash") == [2, 3]           # no player yet: a new install, posted
    assert channel.sent[0]["embed"].title == "2 new crashes"
    assert _watermarks()[2] == 4                      # past the developer crash after them


def test_only_developer_crashes_post_nothing_but_move_the_watermark(env):
    env.tables["reports"] = [_crash(i, "dev") for i in range(1, 6)]
    channel = _Channel()
    _cycle(_Bot(channel))
    assert channel.calls == 0
    assert _watermarks()[2] == 5


def test_a_crash_backlog_mixed_with_developer_crashes_posts_each_once_in_order(env):
    """Player crashes interleaved with developer ones, more than one cycle holds."""
    env.tables["reports"] = [_crash(i, "dev" if i % 2 else "p1") for i in range(1, 101)]
    channel = _Channel()
    bot = _Bot(channel)
    _cycle(bot)
    _cycle(bot)
    _cycle(bot)
    assert _ids(channel, "crash") == list(range(2, 101, 2))
    assert _watermarks()[2] == 100


def test_a_crash_flood_does_not_delay_a_player_report(env):
    env.tables["reports"] = [_crash(i) for i in range(1, 60)] + [_report(60)]
    channel = _Channel()
    _cycle(_Bot(channel))
    assert _ids(channel, "player report") == [60]
    assert channel.sent[0]["embed"].title == "1 new player report"


@pytest.mark.parametrize("error, expected", [
    ("SCRIPT ERROR: Stack overflow.\n  at: res://scripts/a.gd:3\n  backtrace:\n    [0] f",
     "SCRIPT ERROR: Stack overflow.\nat: res://scripts/a.gd:3"),
    ("Previous session ended without shutting down cleanly.\n\n--- What went wrong ---\n"
     "SCRIPT ERROR: Compile Error: Identifier not found: SurveyManager\n  at: res://scripts/b.gd:73\n\n"
     "--- Previous session log (tail) ---\nGodot Engine v4.6",
     "Unclean exit: SCRIPT ERROR: Compile Error: Identifier not found: SurveyManager\nat: res://scripts/b.gd:73"),
    ("Previous session ended without shutting down cleanly.\n\n--- Previous session log (tail) ---\n"
     "Godot Engine v4.6\nPattern database loaded.",
     "Game closed without shutting down cleanly (no error found in the log)"),
    (None, "(no error message)"),
])
def test_a_crash_shows_what_broke_and_where(error, expected):
    assert lr._crash_summary(error) == expected


def test_the_crash_line_shows_state_who_and_where(env):
    env.tables["reports"] = [_crash(1, error="SCRIPT ERROR: **bad** @everyone\n  at: res://x.gd:1")]
    channel = _Channel()
    _cycle(_Bot(channel))
    field = channel.sent[0]["embed"].fields[0]
    assert field.name == "Crash #1 · RESOLVING\\_CARDS"
    error, at, meta = field.value.split("\n")
    assert "@everyone" not in error and "**bad**" not in error
    assert at == "at: res://x.gd:1"
    assert "Mira" in meta and "v0.9.12" in meta and "Windows" in meta


# --- Survey comments ---------------------------------------------------------

def test_survey_rows_without_a_comment_are_not_posted(env):
    env.tables["survey_responses"] = [_survey(1, comment=None, answer="just_right"), _survey(2),
                                      _survey(3, comment="   ")]
    channel = _Channel()
    _cycle(_Bot(channel))
    embed = channel.sent[0]["embed"]
    assert embed.title == "1 new survey comment"
    field = embed.fields[0]
    assert field.name == "That run's difficulty felt: #2"
    assert "the boss took forever" in field.value and "Too hard" in field.value
    assert "Mira" in field.value and "v0.9.12" in field.value


def test_a_score_answer_reads_as_a_score(env):
    env.tables["survey_responses"] = [_survey(1, question_id="fun", answer="2")]
    channel = _Channel()
    _cycle(_Bot(channel))
    field = channel.sent[0]["embed"].fields[0]
    assert field.name == "How fun was that run? #1"
    assert "2/5" in field.value


def test_an_unknown_question_shows_its_id(env):
    env.tables["survey_responses"] = [_survey(1, question_id="brand_new", answer="maybe")]
    channel = _Channel()
    _cycle(_Bot(channel))
    field = channel.sent[0]["embed"].fields[0]
    assert field.name.startswith("brand")
    assert "maybe" in field.value


def test_a_database_without_the_survey_table_still_posts_reports(env):
    env.missing.add("survey_responses")
    env.tables["reports"] = [_report(1)]
    channel = _Channel()
    _cycle(_Bot(channel))
    assert _ids(channel, "player report") == [1]
    assert _watermarks() == (1, 0, 0)


# --- Failure handling and safety ---------------------------------------------

def test_a_fetch_error_posts_nothing_moves_nothing_and_does_not_escape(env):
    env.fail = True
    env.tables["reports"] = [_report(1)]
    channel = _Channel()
    _cycle(_Bot(channel))                         # must not raise out of the sweep
    assert channel.calls == 0 and _watermarks() == (0, 0, 0)

    env.fail = False
    _cycle(_Bot(channel))
    assert _ids(channel, "player report") == [1]


def test_anon_key_fails_loudly_instead_of_reading_an_empty_table(env, monkeypatch):
    monkeypatch.setattr(supabase_helpers, "SUPABASE_ROLE", "anon")
    env.tables["reports"] = [_report(1)]
    with pytest.raises(supabase_helpers.SupabaseUnreadableError):
        lr._fetch_player_reports(0)
    with pytest.raises(supabase_helpers.SupabaseUnreadableError):
        lr._fetch_crashes(0)


def test_disabled_channel_is_skipped(env):
    state = lr._load_state()
    state["channels"]["123"]["disabled"] = True
    lr._save_state(state)
    env.tables["reports"] = [_report(1)]
    channel = _Channel()
    _cycle(_Bot(channel))
    assert channel.calls == 0


def test_startup_and_loop_passes_racing_post_once(env):
    env.tables["reports"] = [_report(1), _report(2), _crash(3)]
    channel = _Channel()
    bot = _Bot(channel)

    async def both():
        await asyncio.gather(lr._post_all_channels(bot, "loop"), lr._post_all_channels(bot, "startup"))

    asyncio.run(both())
    assert _ids(channel, "player report") == [1, 2]
    assert _ids(channel, "crash") == [3]


def test_player_text_cannot_ping_or_restyle(env):
    env.tables["reports"] = [_report(1, description="@everyone **URGENT** " + "x" * 2000,
                                     contact_info="@here")]
    env.tables["survey_responses"] = [_survey(1, comment="@everyone **look** " + "x" * 2000)]
    channel = _Channel()
    _cycle(_Bot(channel))

    for msg in channel.sent:
        mentions = msg["allowed_mentions"]
        assert isinstance(mentions, nextcord.AllowedMentions)
        assert not mentions.everyone and not mentions.users and not mentions.roles
        text, meta = msg["embed"].fields[0].value.split("\n")
        assert "@everyone" not in text and "**" not in text.replace("\\*", "")
        assert "@here" not in meta
        assert text.endswith("…") and len(text) < lr.SUMMARY_LIMIT * 2


@pytest.mark.parametrize("field_for, row", [
    (lr._report_field, _report(0, category="*" * 500, description="_" * 2000, game_version="~" * 500,
                               contact_info="`" * 500)),
    (lr._crash_field, _crash(0, error="_" * 5000 + "\n  at: " + "*" * 5000, game_state="|" * 500,
                             os_info="~" * 500, game_version="`" * 500)),
])
def test_ten_worst_case_rows_stay_under_discords_caps(field_for, row):
    """Every character a markdown character, so escaping doubles all of it."""
    worst = [dict(row, id=i) for i in range(1, 11)]
    names = {"p1": "|" * 500}
    fields = [field_for(r, names) for r in worst]
    embed, shown = lr._build_embed(worst, fields, 10, "crash", lr.CRASH_COLOR)
    assert 0 < shown == len(embed.fields) <= 10
    assert all(len(f.name) <= 256 and len(f.value) <= 1024 for f in embed.fields)
    total = len(embed.title) + len(embed.footer.text or "") + sum(len(f.name) + len(f.value) for f in embed.fields)
    assert total <= 6000


# --- Where a new channel starts ----------------------------------------------

def test_a_new_channel_starts_where_the_daily_post_stopped(env):
    dr._save_state({"channels": {"9": {"last_report_id": 40, "last_survey_id": 6},
                                 "8": {"last_report_id": 52, "last_survey_tally_id": 99}}})
    env.tables["reports"] = [_report(i) for i in range(1, 60)] + [_crash(70)]
    env.tables["survey_responses"] = [_survey(i) for i in range(1, 10)]
    config = {}
    assert lr._start_missing_watermarks(config)
    # The daily post never carried crashes: those start at the newest row.
    assert config == {"last_report_id": 52, "last_survey_id": 6, "last_crash_id": 70}


def test_without_daily_state_a_new_channel_starts_at_the_newest_row(env):
    env.tables["reports"] = [_crash(3), _report(17), _report(9)]
    config = {}
    lr._start_missing_watermarks(config)
    assert config == {"last_report_id": 17, "last_survey_id": 0, "last_crash_id": 17}


def test_an_unreadable_table_leaves_its_watermark_for_next_cycle_not_zero(env):
    env.missing.add("survey_responses")
    env.tables["reports"] = [_report(5)]
    config = {}
    lr._start_missing_watermarks(config)
    assert "last_survey_id" not in config and config["last_report_id"] == 5


def test_a_channel_missing_a_watermark_is_started_not_dumped(env):
    """A channel from before a kind existed gets it at the newest row."""
    lr._save_state({"channels": {"123": {"last_report_id": 0, "last_survey_id": 0}}})
    env.tables["reports"] = [_crash(i) for i in range(1, 30)]
    channel = _Channel()
    _cycle(_Bot(channel))
    assert channel.calls == 0
    assert _watermarks()[2] == 29
