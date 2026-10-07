"""Tests for /daily_reports -- the daily image of survey answers.

The contract under test: once a day per channel, one image counting every
survey shown since the last post; no message at all when there is nothing new;
the day is claimed before sending (never re-fires) while the watermark advances
only after the image is out (a failed send counts those surveys tomorrow, never
loses them). Reports and survey comments are NOT posted here any more -- see
test_live_reports.py.
"""
import asyncio

import pytest

import supabase_helpers
from azoth_commands import daily_reports as dr


class _FakeDB:
    """Just enough of postgrest's builder for the one query daily_reports makes."""

    def __init__(self, tables):
        self.tables = tables
        self.fail = False

    def table(self, name):
        fake = self

        class Q:
            def __init__(self):
                self.rows = list(fake.tables.get(name, []))
                self.cap = None

            def select(self, *a, count=None):
                return self

            def gt(self, col, v):
                self.rows = [r for r in self.rows if r[col] > v]
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
                data = self.rows[:self.cap] if self.cap is not None else self.rows
                return type("R", (), {"data": data, "count": None})()

        return Q()


def _survey(i, comment=None, **kw):
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
        self.sent.append({"embed": embed, "file": file})


class _Bot:
    def __init__(self, channel):
        self.channel = channel

    def get_channel(self, cid):
        return self.channel


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(dr, "STATE_FILE", str(tmp_path / "reports_state.json"))
    monkeypatch.setattr(supabase_helpers, "SUPABASE_ROLE", "service_role")
    fake = _FakeDB({"survey_responses": [],
                    "reports": [{"id": 1, "report_type": "bug", "description": "broke"}]})
    monkeypatch.setattr(dr, "supabase", fake)
    day = {"today": "2026-09-25"}
    monkeypatch.setattr(dr, "_today_cst_str", lambda: day["today"])
    dr._save_state({"channels": {"123": {"send_hour_utc": 0, "send_minute_utc": 0}}})
    return fake, day


def _run_day(bot):
    asyncio.run(dr._send_due_channels(bot, "test"))


def _tally_id():
    return dr._load_state()["channels"]["123"].get("last_survey_tally_id", 0)


def test_one_image_a_day_counting_everything_since_the_last_post(env):
    fake, day = env
    fake.tables["survey_responses"] = [_survey(1), _survey(2, outcome="skipped", answer=None),
                                       _survey(3, comment="too long")]
    channel = _Channel()
    bot = _Bot(channel)
    _run_day(bot)
    _run_day(bot)                                     # a second 10-minute cycle the same day
    assert channel.calls == 1
    assert channel.sent[0]["file"].filename == "survey_answers.png"
    assert _tally_id() == 3

    # Nothing new: no image the next day.
    day["today"] = "2026-09-26"
    _run_day(bot)
    assert channel.calls == 1

    # A survey shown since: counted once, in the next post.
    fake.tables["survey_responses"].append(_survey(4, outcome="ignored", answer=None))
    day["today"] = "2026-09-27"
    _run_day(bot)
    assert channel.calls == 2 and _tally_id() == 4


def test_reports_and_survey_comments_are_not_posted_here(env):
    """They moved to /live_reports; the daily post is the answers image alone."""
    fake, _ = env
    fake.tables["survey_responses"] = [_survey(1, comment="the boss took forever")]
    channel = _Channel()
    _run_day(_Bot(channel))
    assert len(channel.sent) == 1 and channel.sent[0]["file"] is not None
    assert not channel.sent[0]["embed"].fields


def test_quiet_day_is_claimed_so_it_is_checked_once(env):
    fake, _ = env
    channel = _Channel()
    _run_day(_Bot(channel))
    assert channel.calls == 0
    assert dr._load_state()["channels"]["123"]["last_sent_date"] == "2026-09-25"

    # A survey shown later that day waits for tomorrow rather than firing at once.
    fake.tables["survey_responses"] = [_survey(1)]
    _run_day(_Bot(channel))
    assert channel.calls == 0


def test_a_failed_image_is_counted_in_the_next_post_and_never_re_fires_today(env):
    fake, day = env
    fake.tables["survey_responses"] = [_survey(1)]
    channel = _Channel(fail_on={1})
    bot = _Bot(channel)
    for _ in range(5):                                # five cycles on the failing day
        _run_day(bot)
    assert channel.calls == 1                         # claimed before sending: no retry storm
    assert _tally_id() == 0

    day["today"] = "2026-09-26"
    _run_day(bot)
    assert len(channel.sent) == 1 and _tally_id() == 1


def test_fetch_error_does_not_claim_the_day(env):
    fake, _ = env
    fake.fail = True
    fake.tables["survey_responses"] = [_survey(1)]
    channel = _Channel()
    _run_day(_Bot(channel))                           # must not raise out of the sweep
    assert "last_sent_date" not in dr._load_state()["channels"]["123"]

    fake.fail = False
    _run_day(_Bot(channel))
    assert channel.calls == 1


def test_anon_key_fails_loudly_instead_of_reading_an_empty_table(env, monkeypatch):
    monkeypatch.setattr(supabase_helpers, "SUPABASE_ROLE", "anon")
    with pytest.raises(supabase_helpers.SupabaseUnreadableError):
        dr._fetch_survey_tally_rows(0)


def test_racing_passes_across_two_channels_neither_double_post_nor_lose_a_watermark(env):
    """With one channel the day claim alone blocks the second pass. With two it
    does not: each pass holds its own copy of the state across an awaited send,
    so without _SEND_LOCK one re-posts the other's channel and a stale save
    rolls a watermark back -- and those surveys are counted again the next day."""
    fake, _ = env
    fake.tables["survey_responses"] = [_survey(1), _survey(2)]
    state = dr._load_state()
    state["channels"]["456"] = {"send_hour_utc": 0, "send_minute_utc": 0}
    dr._save_state(state)
    channels = {"123": _Channel(), "456": _Channel()}

    class TwoChannelBot:
        def get_channel(self, cid):
            return channels[str(cid)]

    bot = TwoChannelBot()

    async def both():
        await asyncio.gather(dr._send_due_channels(bot, "loop"), dr._send_due_channels(bot, "startup"))

    asyncio.run(both())
    assert [c.calls for c in channels.values()] == [1, 1]
    assert {cid: c["last_survey_tally_id"] for cid, c in dr._load_state()["channels"].items()} == {"123": 2, "456": 2}


def test_disabled_channel_is_skipped(env):
    fake, _ = env
    state = dr._load_state()
    state["channels"]["123"]["disabled"] = True
    dr._save_state(state)
    fake.tables["survey_responses"] = [_survey(1)]
    channel = _Channel()
    _run_day(_Bot(channel))
    assert channel.calls == 0
