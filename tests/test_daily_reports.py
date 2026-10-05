"""Tests for /daily_reports -- the daily post of new player-submitted reports.

The contract under test: up to REPORT_LIMIT non-crash reports per channel per
day, oldest first; no message at all when there is nothing unsent; the day is
claimed before sending (never re-fires) while the id watermark advances only
after a message is out (a failed send retries tomorrow, never skips).
"""
import asyncio

import nextcord
import pytest

import supabase_helpers
from azoth_commands import daily_reports as dr


class _FakeReports:
    """Just enough of postgrest's builder for the two queries daily_reports makes."""

    def __init__(self, tables):
        self.tables = tables
        self.fail = False
        # Tables that do not exist yet, as a database before a migration.
        self.missing = set()
        self.columns = {}

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
                if col not in fake.columns.get(name, {col}):
                    raise RuntimeError(f"column {col} does not exist")
                self.rows = [r for r in self.rows if r.get(col) == v]
                return self

            def neq(self, col, v):
                # SQL: NULL <> 'crash' is NULL, so the row is dropped.
                self.rows = [r for r in self.rows if r.get(col) is not None and r[col] != v]
                return self

            def in_(self, col, vals):
                self.rows = [r for r in self.rows if r.get(col) in vals]
                return self

            def order(self, col):
                self.rows.sort(key=lambda r: r[col])
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


def _report(i, report_type="feature_request", **kw):
    row = {"id": i, "player_uuid": "p1", "report_type": report_type, "category": "Feature Request",
           "description": f"idea {i}", "contact_info": None, "game_version": "0.9.1",
           "created_at": "2026-09-20T12:00:00+00:00"}
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
        self.sent.append({"content": content, "embed": embed, "file": file,
                          "allowed_mentions": allowed_mentions})


class _Bot:
    def __init__(self, channel):
        self.channel = channel

    def get_channel(self, cid):
        return self.channel


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(dr, "STATE_FILE", str(tmp_path / "reports_state.json"))
    monkeypatch.setattr(supabase_helpers, "SUPABASE_ROLE", "service_role")
    fake = _FakeReports({"reports": [], "survey_responses": [],
                         "players": [{"uuid": "p1", "name": "Mira"}]})
    monkeypatch.setattr(dr, "supabase", fake)
    day = {"today": "2026-09-25"}
    monkeypatch.setattr(dr, "_today_cst_str", lambda: day["today"])
    dr._save_state({"channels": {"123": {"send_hour_utc": 0, "send_minute_utc": 0, "last_report_id": 0}}})
    return fake, day


def _posted_ids(channel):
    return [int(f.name.split("#", 1)[1].split(" ")[0]) for m in channel.sent for f in m["embed"].fields]


def _run_day(bot):
    asyncio.run(dr._send_due_channels(bot, "test"))


def test_crash_reports_are_never_posted(env):
    fake, _ = env
    fake.tables["reports"] = [_report(1, "crash"), _report(2, "bug"), _report(3, "crash"),
                              _report(4, "content_idea")]
    channel = _Channel()
    _run_day(_Bot(channel))
    assert _posted_ids(channel) == [2, 4]


def test_backlog_drains_ten_a_day_oldest_first_then_goes_quiet(env):
    """23 unsent -> 10, 10, 3, then no message at all on the fourth day."""
    fake, day = env
    fake.tables["reports"] = [_report(i) for i in range(1, 24)] + [_report(100, "crash")]
    channel = _Channel()
    bot = _Bot(channel)

    per_day = []
    for d in ("2026-09-25", "2026-09-26", "2026-09-27", "2026-09-28"):
        day["today"] = d
        before = len(_posted_ids(channel))
        _run_day(bot)
        _run_day(bot)                           # a second 10-minute cycle the same day
        per_day.append(_posted_ids(channel)[before:])

    assert per_day[0] == list(range(1, 11))
    assert per_day[1] == list(range(11, 21))
    assert per_day[2] == [21, 22, 23]
    assert per_day[3] == []
    assert channel.calls == 3                   # one message a day; the quiet day sent nothing


def test_one_embed_counts_what_is_still_waiting(env):
    fake, _ = env
    fake.tables["reports"] = [_report(i) for i in range(1, 24)]
    channel = _Channel()
    _run_day(_Bot(channel))
    embed = channel.sent[0]["embed"]
    assert len(channel.sent) == 1 and len(embed.fields) == 10
    assert embed.title == "10 new player reports"
    assert "13 more waiting" in embed.footer.text


def test_no_footer_when_nothing_is_waiting(env):
    fake, _ = env
    fake.tables["reports"] = [_report(1)]
    channel = _Channel()
    _run_day(_Bot(channel))
    embed = channel.sent[0]["embed"]
    assert embed.title == "1 new player report"
    assert not embed.footer.text


def test_quiet_day_is_claimed_so_it_is_checked_once(env):
    fake, _ = env
    channel = _Channel()
    _run_day(_Bot(channel))
    assert channel.calls == 0
    assert dr._load_state()["channels"]["123"]["last_sent_date"] == "2026-09-25"

    # A report filed later that day waits for tomorrow rather than firing at once.
    fake.tables["reports"] = [_report(1)]
    _run_day(_Bot(channel))
    assert channel.calls == 0


def test_failed_send_retries_the_same_reports_next_day_and_never_re_fires_today(env):
    fake, day = env
    fake.tables["reports"] = [_report(1), _report(2)]
    channel = _Channel(fail_on={1})
    bot = _Bot(channel)

    for _ in range(5):                          # five cycles on the failing day
        _run_day(bot)
    assert channel.calls == 1                   # claimed before sending: no retry storm
    assert dr._load_state()["channels"]["123"]["last_report_id"] == 0

    day["today"] = "2026-09-26"
    _run_day(bot)
    assert _posted_ids(channel) == [1, 2]       # retried, not skipped


def test_reports_that_do_not_fit_wait_and_the_watermark_stops_before_them(env, monkeypatch):
    fake, day = env
    monkeypatch.setattr(dr, "EMBED_CHAR_LIMIT", 900)
    fake.tables["reports"] = [_report(i, description="z" * 150) for i in range(1, 6)]
    channel = _Channel()
    bot = _Bot(channel)
    _run_day(bot)
    first = _posted_ids(channel)
    assert 0 < len(first) < 5
    embed = channel.sent[0]["embed"]
    assert embed.title == f"{len(first)} new player reports"
    assert f"{5 - len(first)} more waiting" in embed.footer.text
    assert dr._load_state()["channels"]["123"]["last_report_id"] == first[-1]

    day["today"] = "2026-09-26"
    _run_day(bot)
    assert _posted_ids(channel)[len(first)] == first[-1] + 1     # picks up where it stopped


def test_fetch_error_does_not_claim_the_day(env):
    fake, _ = env
    fake.fail = True
    channel = _Channel()
    _run_day(_Bot(channel))                     # must not raise out of the sweep
    assert "last_sent_date" not in dr._load_state()["channels"]["123"]

    fake.fail = False
    fake.tables["reports"] = [_report(1)]
    _run_day(_Bot(channel))
    assert _posted_ids(channel) == [1]


def test_anon_key_fails_loudly_instead_of_reading_an_empty_table(env, monkeypatch):
    fake, _ = env
    monkeypatch.setattr(supabase_helpers, "SUPABASE_ROLE", "anon")
    fake.tables["reports"] = [_report(1)]
    with pytest.raises(supabase_helpers.SupabaseUnreadableError):
        dr._fetch_unsent_reports(0)


def test_startup_and_loop_passes_racing_post_once(env):
    """The loop and the startup catch-up both fire as the bot comes up."""
    fake, _ = env
    fake.tables["reports"] = [_report(1), _report(2)]
    channel = _Channel()
    bot = _Bot(channel)

    async def both():
        await asyncio.gather(dr._send_due_channels(bot, "loop"), dr._send_due_channels(bot, "startup"))

    asyncio.run(both())
    assert _posted_ids(channel) == [1, 2]


def test_racing_passes_across_two_channels_neither_double_post_nor_lose_a_watermark(env):
    """With one channel the day claim alone blocks the second pass. With two it
    does not: each pass holds its own copy of the state across an awaited send,
    so without _SEND_LOCK one re-posts the other's channel and a stale save
    rolls a watermark back -- and those reports post again the next day."""
    fake, day = env
    fake.tables["reports"] = [_report(1), _report(2)]
    state = dr._load_state()
    state["channels"]["456"] = {"send_hour_utc": 0, "send_minute_utc": 0, "last_report_id": 0}
    dr._save_state(state)
    channels = {"123": _Channel(), "456": _Channel()}

    class TwoChannelBot:
        def get_channel(self, cid):
            return channels[str(cid)]

    bot = TwoChannelBot()

    async def both():
        await asyncio.gather(dr._send_due_channels(bot, "loop"), dr._send_due_channels(bot, "startup"))

    asyncio.run(both())
    assert [_posted_ids(c) for c in channels.values()] == [[1, 2], [1, 2]]
    assert {cid: c["last_report_id"] for cid, c in dr._load_state()["channels"].items()} == {"123": 2, "456": 2}


def test_player_text_cannot_ping_or_restyle(env):
    fake, _ = env
    fake.tables["reports"] = [_report(1, description="@everyone **URGENT** " + "x" * 2000,
                                      contact_info="@here")]
    channel = _Channel()
    _run_day(_Bot(channel))

    msg = channel.sent[0]
    mentions = msg["allowed_mentions"]
    assert isinstance(mentions, nextcord.AllowedMentions)
    assert not mentions.everyone and not mentions.users and not mentions.roles

    value = msg["embed"].fields[0].value
    summary, meta = value.split("\n")
    assert "@everyone" not in summary and "@here" not in meta
    assert "**URGENT**" not in summary
    assert summary.endswith("…")
    assert len(summary) < dr.SUMMARY_LIMIT * 2


def test_summary_line_shows_who_when_and_how_to_reach_them(env):
    fake, _ = env
    fake.tables["reports"] = [
        _report(1, "bug", category="Bug Report", contact_info="mira#0001"),
        _report(2, "accessibility", category="Vision", description=""),
    ]
    channel = _Channel()
    _run_day(_Bot(channel))
    first, second = channel.sent[0]["embed"].fields
    assert first.name == "Bug #1"                     # "Bug Report" repeats the type: dropped
    assert "Mira" in first.value and "v0.9.1" in first.value
    assert "<t:" in first.value and "contact: mira#0001" in first.value
    assert second.name == "Accessibility #2 · Vision"
    assert "(no description)" in second.value


def test_ten_worst_case_reports_stay_under_discords_caps():
    """Every character a markdown character, so escaping doubles all of it."""
    worst = [_report(i, category="*" * 500, description="_" * 2000, game_version="~" * 500,
                     contact_info="`" * 500) for i in range(1, 11)]
    names = {"p1": "|" * 500}
    embed, max_id = dr._build_reports_embed(worst, 10, names)
    assert 0 < len(embed.fields) <= 10
    assert all(len(f.name) <= 256 and len(f.value) <= 1024 for f in embed.fields)
    total = len(embed.title) + len(embed.footer.text or "") + sum(len(f.name) + len(f.value) for f in embed.fields)
    assert total <= 6000
    assert max_id == len(embed.fields)


def test_disabled_channel_is_skipped(env):
    fake, _ = env
    state = dr._load_state()
    state["channels"]["123"]["disabled"] = True
    dr._save_state(state)
    fake.tables["reports"] = [_report(1)]
    channel = _Channel()
    _run_day(_Bot(channel))
    assert channel.calls == 0


# --- Survey comments ---------------------------------------------------------

def _survey_message(channel):
    return [m for m in channel.sent if "survey comment" in (m["embed"].title or "")]


def _answers_image(channel):
    return [m for m in channel.sent if m["file"] is not None]


def test_survey_comments_follow_the_reports_as_their_own_message(env):
    fake, _ = env
    fake.tables["reports"] = [_report(1)]
    fake.tables["survey_responses"] = [_survey(1), _survey(2, comment=None, answer="just_right")]
    channel = _Channel()
    _run_day(_Bot(channel))

    assert len(channel.sent) == 3                    # reports, answers image, comments
    assert channel.sent[1]["file"].filename == "survey_answers.png"
    survey = _survey_message(channel)[0]["embed"]
    assert survey.title == "1 new survey comment"     # one-click answers are not posted
    field = survey.fields[0]
    assert field.name == "That run's difficulty felt: #1"
    assert "the boss took forever" in field.value and "Too hard" in field.value
    assert "Mira" in field.value and "v0.9.12" in field.value
    state = dr._load_state()["channels"]["123"]
    assert state["last_report_id"] == 1 and state["last_survey_id"] == 1
    assert state["last_survey_tally_id"] == 2          # the one-click answer is counted


def test_the_answers_image_counts_everything_since_the_last_post(env):
    fake, day = env
    fake.tables["survey_responses"] = [_survey(1, comment=None), _survey(2, comment=None,
                                                                        outcome="skipped", answer=None)]
    channel = _Channel()
    bot = _Bot(channel)
    _run_day(bot)
    assert len(_answers_image(channel)) == 1 and not _survey_message(channel)
    assert dr._load_state()["channels"]["123"]["last_survey_tally_id"] == 2

    # Nothing new: no image the next day.
    day["today"] = "2026-09-26"
    _run_day(bot)
    assert len(_answers_image(channel)) == 1

    # A survey shown since: counted once, in the next post.
    fake.tables["survey_responses"].append(_survey(3, comment=None, outcome="ignored", answer=None))
    day["today"] = "2026-09-27"
    _run_day(bot)
    assert len(_answers_image(channel)) == 2
    assert dr._load_state()["channels"]["123"]["last_survey_tally_id"] == 3


def test_a_failed_answers_image_is_retried_with_the_next_post(env):
    fake, day = env
    fake.tables["survey_responses"] = [_survey(1, comment=None)]
    channel = _Channel(fail_on={1})
    _run_day(_Bot(channel))
    assert dr._load_state()["channels"]["123"].get("last_survey_tally_id", 0) == 0
    day["today"] = "2026-09-26"
    _run_day(_Bot(channel))
    assert len(_answers_image(channel)) == 1


def test_a_score_answer_reads_as_a_score(env):
    fake, _ = env
    fake.tables["survey_responses"] = [_survey(1, question_id="fun", answer="2")]
    channel = _Channel()
    _run_day(_Bot(channel))
    field = _survey_message(channel)[0]["embed"].fields[0]
    assert field.name == "How fun was that run? #1"
    assert "2/5" in field.value


def test_an_unknown_question_shows_its_id(env):
    fake, _ = env
    fake.tables["survey_responses"] = [_survey(1, question_id="brand_new", answer="maybe")]
    channel = _Channel()
    _run_day(_Bot(channel))
    field = _survey_message(channel)[0]["embed"].fields[0]
    assert field.name.startswith("brand")
    assert "maybe" in field.value


def test_survey_comments_drain_on_their_own_watermark(env):
    fake, day = env
    fake.tables["survey_responses"] = [_survey(i) for i in range(1, 14)]
    channel = _Channel()
    bot = _Bot(channel)
    _run_day(bot)
    assert len(_survey_message(channel)[0]["embed"].fields) == 10
    assert dr._load_state()["channels"]["123"]["last_survey_id"] == 10

    day["today"] = "2026-09-26"
    _run_day(bot)
    assert [f.name.rsplit("#", 1)[1] for f in _survey_message(channel)[1]["embed"].fields] == ["11", "12", "13"]


def test_a_database_without_the_survey_table_still_posts_reports(env):
    fake, _ = env
    fake.missing.add("survey_responses")
    fake.tables["reports"] = [_report(1)]
    channel = _Channel()
    _run_day(_Bot(channel))
    assert _posted_ids(channel) == [1]
    assert dr._load_state()["channels"]["123"].get("last_survey_id", 0) == 0


def test_a_failed_survey_send_does_not_hold_back_the_reports_watermark(env):
    fake, day = env
    fake.tables["reports"] = [_report(1)]
    fake.tables["survey_responses"] = [_survey(1)]
    channel = _Channel(fail_on={3})                     # reports, image, then comments
    _run_day(_Bot(channel))
    state = dr._load_state()["channels"]["123"]
    assert state["last_report_id"] == 1
    assert state["last_survey_tally_id"] == 1
    assert state.get("last_survey_id", 0) == 0          # retried tomorrow

    day["today"] = "2026-09-26"
    _run_day(_Bot(channel))
    assert len(_survey_message(channel)) == 1


def test_survey_comments_cannot_ping_or_restyle(env):
    fake, _ = env
    fake.tables["survey_responses"] = [_survey(1, comment="@everyone **look** " + "x" * 2000)]
    channel = _Channel()
    _run_day(_Bot(channel))
    value = _survey_message(channel)[0]["embed"].fields[0].value
    comment = value.split("\n")[0]
    assert "@everyone" not in comment and "**look**" not in comment
    assert comment.endswith("…")
