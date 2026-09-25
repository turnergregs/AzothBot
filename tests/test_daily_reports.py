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


class _Channel:
    def __init__(self, fail_on=()):
        self.sent = []
        self.calls = 0
        self.fail_on = set(fail_on)

    async def send(self, content=None, embed=None, allowed_mentions=None):
        self.calls += 1
        await asyncio.sleep(0)            # a real send yields to the loop
        if self.calls in self.fail_on:
            raise RuntimeError("Discord 500")
        self.sent.append({"content": content, "embed": embed, "allowed_mentions": allowed_mentions})


class _Bot:
    def __init__(self, channel):
        self.channel = channel

    def get_channel(self, cid):
        return self.channel


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(dr, "STATE_FILE", str(tmp_path / "reports_state.json"))
    monkeypatch.setattr(supabase_helpers, "SUPABASE_ROLE", "service_role")
    fake = _FakeReports({"reports": [], "players": [{"uuid": "p1", "name": "Mira"}]})
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
