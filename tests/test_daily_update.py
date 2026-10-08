"""Tests for the daily activity report.

Two production incidents are pinned here as regression tests, both named at the
site: the June 30 TypeError and the June 19 duplicate-message flood. The rest
covers the turn-grain aggregation, where the denominators are easy to get wrong
in ways that produce plausible numbers.
"""
import asyncio
import json
import math

from datetime import date, timedelta

import pytest

import azoth_commands.daily_update as du
from azoth_logic import stats_cards
from azoth_logic import stats_charts as sc


@pytest.fixture(autouse=True)
def _no_paths_read(monkeypatch):
    """The paths card's read goes through fetch_all, not the `supabase` the
    end-to-end tests here replace: without this they would send it to the
    network. Tests of the paths themselves install their own."""
    from azoth_logic import run_paths
    monkeypatch.setattr(du, "new_player_paths",
                        lambda until, days, runs: (run_paths.Paths(0), until - timedelta(days=days)))


# ---------------------------------------------------------------------------
# _to_number  --  the June 30 crash
# ---------------------------------------------------------------------------

def test_text_combo_plus_int_level_no_longer_raises():
    """REGRESSION (2026-06-30): `unsupported operand type(s) for +: 'int' and 'str'`.

    The draft score was `level_reached + highest_combo`. `level_reached` is
    bigint -> int; `highest_combo` is a **text** column (serialised BigNum) ->
    str. Turning the daily update on crashed on that line.
    """
    game = {"level_reached": 10, "highest_combo": "5510"}

    with pytest.raises(TypeError):                       # what the old code did
        (game["level_reached"] or 0) + (game["highest_combo"] or 0)

    assert du._to_number(game["level_reached"]) + du._to_number(game["highest_combo"]) == 5520


def test_draft_stats_survives_a_text_combo_end_to_end(monkeypatch):
    """The June 30 crash again, through the REAL code path.

    Testing `_to_number` alone is not enough: the bug was at the call site, and
    a mutation putting `level_reached + highest_combo` back was not detected by
    the unit-level test. This exercises _fetch_draft_stats with a `highest_combo`
    shaped the way PostgREST actually returns it -- a decimal STRING.
    """
    data = {
        "drafts": [{"uuid": "d1", "game_uuid": "g1"}],
        "draft_items": [
            {"id": 1, "draft_uuid": "d1", "item_type": "card", "item_id": 10, "picked": True},
            {"id": 2, "draft_uuid": "d1", "item_type": "card", "item_id": 11, "picked": False},
        ],
        "cards": [{"id": 10, "name": "Salvage"}, {"id": 11, "name": "Excess"}],
    }

    class Q:
        def __init__(self, t):
            self.rows = data.get(t, [])
        def select(self, *a, **k):
            return self
        def in_(self, col, vals):
            self.rows = [r for r in self.rows if r[col] in vals]
            return self
        def execute(self):
            return type("R", (), {"data": self.rows})()

    monkeypatch.setattr(du, "supabase", type("S", (), {"table": staticmethod(Q)})())

    games_by_uuid = {"g1": {"uuid": "g1", "level_reached": 10, "highest_combo": "5510"}}
    # Two picked appearances are needed for an item to reach `top_performers`.
    data["draft_items"].append(
        {"id": 3, "draft_uuid": "d1", "item_type": "card", "item_id": 10, "picked": True})

    stats = du._fetch_draft_stats(["g1"], games_by_uuid)   # must not raise TypeError

    assert stats["total_drafts"] == 1
    perf = dict(stats["top_performers"])
    assert "Salvage" in perf
    assert perf["Salvage"]["avg_combo_log10"] == pytest.approx(math.log10(5510))


@pytest.mark.parametrize("value,expected", [
    ("123", 123),          # PostgREST returns large numerics as strings
    (123, 123),
    (1.5, 1.5),
    ("1.5", 1.5),
    (None, 0),
    ("", 0),
    ("not-a-number", 0),
    ({"bignum": 1}, 0),    # a serialised BigNum object must not crash the report
    (True, 0),             # bool is an int subclass; counting it as 1 would be wrong
])
def test_to_number_coerces_or_falls_back(value, expected):
    assert du._to_number(value) == expected


# ---------------------------------------------------------------------------
# Turn-grain aggregation
# ---------------------------------------------------------------------------

def _install_turn_grain(monkeypatch, turns, nodes, levelups, role="service_role"):
    # `bosses` for the per-boss record's names (2026-09-29).
    data = {"turns": turns, "turn_nodes": nodes, "levelups": levelups,
            "bosses": [{"id": 7, "name": "Veln"}]}

    class Q:
        def __init__(self, t):
            self.rows = data[t]

        def select(self, *a, **k):
            return self

        def in_(self, col, vals):
            self.rows = [r for r in self.rows if r[col] in vals]
            return self

        def execute(self):
            return type("R", (), {"data": self.rows})()

    monkeypatch.setattr(du, "SUPABASE_ROLE", role)
    monkeypatch.setattr(du, "supabase", type("S", (), {"table": staticmethod(Q)})())


# One game: 3 regular turns with 2 / 0 / 5 links, and a boss turn with 11 links
# plus 4 skips. The zero-node turn and the skips are the whole point.
TURNS = [
    {"uuid": "t1", "game_uuid": "g1", "boss_id": None, "boss_result": None},
    {"uuid": "t2", "game_uuid": "g1", "boss_id": None, "boss_result": None},
    {"uuid": "t3", "game_uuid": "g1", "boss_id": None, "boss_result": None},
    {"uuid": "t4", "game_uuid": "g1", "boss_id": 7, "boss_result": "win"},
]
NODES = ([{"turn_uuid": "t1", "kind": "link"}] * 2
         + [{"turn_uuid": "t3", "kind": "link"}] * 5
         + [{"turn_uuid": "t4", "kind": "link"}] * 11
         + [{"turn_uuid": "t4", "kind": "skip"}] * 4)
LEVELUPS = [
    {"turn_uuid": "t1", "options": ["Life", "Power", "Hero"], "chosen": ["Hero"]},
    {"turn_uuid": "t3", "options": ["Life", "Power", "Luck"], "chosen": ["Power"]},
    {"turn_uuid": "t4", "options": ["Life", "Hero"], "chosen": ["Hero"]},
]


def test_zero_node_turns_stay_in_the_denominator(monkeypatch):
    """t2 produced no nodes and MUST still count.

    This is the entire reason the `turns` table exists. Counting only turns that
    produced nodes reintroduces the survivorship bias it was built to remove --
    and it inflates the average, so the result looks plausible while being wrong.
    """
    _install_turn_grain(monkeypatch, TURNS, NODES, LEVELUPS)
    r = du._fetch_turn_grain_stats(["g1"])
    assert r["regular_turns"] == 3, "t2 has no nodes but is still a turn"
    assert r["avg_links_regular"] == pytest.approx((2 + 0 + 5) / 3)
    assert r["avg_links_regular"] != pytest.approx((2 + 5) / 2), "must not drop t2"


def test_the_links_chart_keeps_zero_link_turns(monkeypatch):
    """The spread behind the average: t2's zero links is a bar, not a gap."""
    _install_turn_grain(monkeypatch, TURNS, NODES, LEVELUPS)
    r = du._fetch_turn_grain_stats(["g1"])
    assert r["links_distribution"] == {2: 1, 0: 1, 5: 1}


def test_skips_are_not_links(monkeypatch):
    """A node is a link OR a skip; both consume a timeline slot."""
    _install_turn_grain(monkeypatch, TURNS, NODES, LEVELUPS)
    r = du._fetch_turn_grain_stats(["g1"])
    assert r["avg_links_boss"] == pytest.approx(11.0), "4 skips excluded from 15 nodes"


def test_boss_and_regular_turns_are_kept_apart(monkeypatch):
    """A boss fight IS one turn and runs until someone dies, so it holds many
    times the nodes of a regular turn. Pooling makes both averages meaningless."""
    _install_turn_grain(monkeypatch, TURNS, NODES, LEVELUPS)
    r = du._fetch_turn_grain_stats(["g1"])
    assert r["regular_turns"] == 3 and r["boss_turn_count"] == 1
    pooled = (2 + 0 + 5 + 11) / 4
    assert r["avg_links_regular"] != pytest.approx(pooled)
    assert r["avg_links_boss"] != pytest.approx(pooled)


def test_boss_outcome_comes_from_turns(monkeypatch):
    """`boss_fights` was frozen 2026-08-25; reading it reported zero forever."""
    _install_turn_grain(monkeypatch, TURNS, NODES, LEVELUPS)
    r = du._fetch_turn_grain_stats(["g1"])
    assert (r["boss_turns"], r["boss_wins"], r["boss_losses"]) == (1, 1, 0)


def test_reward_rate_divides_by_offers_not_picks(monkeypatch):
    """Raw pick counts are uninterpretable: common rewards are offered far more
    often and would top any volume-ranked list. Life is offered 3x and never
    taken; Hero is offered 2x and taken both times."""
    _install_turn_grain(monkeypatch, TURNS, NODES, LEVELUPS)
    rates = dict(du._fetch_turn_grain_stats(["g1"])["top_rewards"])
    assert rates["Hero"] == {"taken": 2, "offered": 2, "rate": 1.0}
    assert rates["Life"]["offered"] == 3 and rates["Life"]["taken"] == 0
    assert rates["Life"]["rate"] == 0.0
    assert list(rates)[0] == "Hero", "ranked by rate, not by raw picks"


def test_turn_grain_refuses_without_service_role(monkeypatch):
    """`turns` is INSERT-only for anon, so a blocked read returns HTTP 200 and an
    empty array -- identical in shape to 'nothing happened yesterday'. Reporting
    0 for an unreadable table is exactly what hid the frozen boss_fights bug."""
    _install_turn_grain(monkeypatch, TURNS, NODES, LEVELUPS, role="anon")
    r = du._fetch_turn_grain_stats(["g1"])
    assert "error" in r and "service-role" in r["error"]
    assert "boss_turns" not in r, "must not report a count it could not read"


def test_no_games_returns_empty_not_an_error(monkeypatch):
    _install_turn_grain(monkeypatch, [], [], [])
    assert du._fetch_turn_grain_stats([]) == {}


def test_query_failure_is_reported_not_swallowed(monkeypatch):
    class Boom:
        def table(self, t):
            raise RuntimeError("connection reset")
    monkeypatch.setattr(du, "SUPABASE_ROLE", "service_role")
    monkeypatch.setattr(du, "supabase", Boom())
    r = du._fetch_turn_grain_stats(["g1"])
    assert "connection reset" in r["error"]


# ---------------------------------------------------------------------------
# _claim_and_send  --  the June 19 flood
# ---------------------------------------------------------------------------

DAY = date(2026, 6, 18)          # the report date; it is sent on the 19th


class _FlakyChannel:
    """Emits the first embed, then fails -- the June 19 failure mode."""
    def __init__(self):
        self.sent = []

    async def send(self, embed=None, file=None):
        self.sent.append(embed)
        raise RuntimeError("Discord 500 mid-send")


class _Channel:
    """Records every message it is sent."""
    def __init__(self):
        self.sent = []

    async def send(self, embed=None, file=None):
        self.sent.append(embed)


class _Bot:
    def __init__(self, channel):
        self._channel = channel

    def get_channel(self, cid):
        return self._channel


def _stub_report(monkeypatch):
    monkeypatch.setattr(du, "_fetch_daily_stats", lambda day: {"total_games": 0})
    monkeypatch.setattr(du, "_build_update_messages",
                        lambda s, day: [{"embed": f"{day}:1"}, {"embed": f"{day}:2"},
                                        {"embed": f"{day}:3"}])


def test_failed_send_does_not_re_fire(monkeypatch, tmp_path):
    """REGRESSION (2026-06-19): ~30 duplicate messages.

    The old code persisted `last_sent_date` only AFTER a successful send. When
    channel.send raised partway through the embed list, the messages already out
    stayed out, nothing was claimed, and the 10-minute loop retried forever.
    """
    monkeypatch.setattr(du, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(du, "_today_cst", lambda: DAY + timedelta(days=1))
    _stub_report(monkeypatch)
    channel = _FlakyChannel()
    du._save_state({"channels": {"123": _due()}})

    for _ in range(6):                                   # six 10-minute cycles
        asyncio.run(du._send_due_channels(_Bot(channel), "loop"))

    assert len(channel.sent) == 1, "one attempt, then the day is claimed"
    assert du._load_state()["channels"]["123"]["last_sent_date"] == "2026-06-19"


def test_claim_is_persisted_before_the_first_send(monkeypatch, tmp_path):
    """The ordering IS the fix -- verify it from inside send()."""
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(du, "STATE_FILE", str(state_file))
    _stub_report(monkeypatch)
    observed = {}

    class Channel:
        async def send(self, embed=None):
            observed.setdefault("on_disk", json.loads(state_file.read_text()))

    du._save_state({"channels": {"1": {"send_hour_utc": 0, "send_minute_utc": 0}}})
    state = du._load_state()
    asyncio.run(du._claim_and_send(Channel(), state, "1", state["channels"]["1"], DAY))
    assert observed["on_disk"]["channels"]["1"]["last_sent_date"] == "2026-06-19"


def test_unresolvable_channel_makes_no_claim(monkeypatch, tmp_path):
    """A channel the bot cannot see yet (startup, before the cache fills) must
    stay retryable -- otherwise the day is silently burned."""
    monkeypatch.setattr(du, "STATE_FILE", str(tmp_path / "state.json"))
    _stub_report(monkeypatch)
    du._save_state({"channels": {"1": {}}})
    state = du._load_state()
    sent = asyncio.run(du._send_backlog(_Bot(None), state, "1", state["channels"]["1"],
                                        DAY + timedelta(days=1), True))
    assert sent is None
    assert "last_sent_date" not in du._load_state()["channels"]["1"]


def test_report_error_does_not_consume_the_day(monkeypatch, tmp_path):
    """Stats are built BEFORE the claim, so a data error stays retryable."""
    monkeypatch.setattr(du, "STATE_FILE", str(tmp_path / "state.json"))
    def boom(day):
        raise RuntimeError("bad data")
    monkeypatch.setattr(du, "_fetch_daily_stats", boom)
    du._save_state({"channels": {"1": {}}})
    state = du._load_state()

    with pytest.raises(RuntimeError):
        asyncio.run(du._claim_and_send(_Channel(), state, "1", state["channels"]["1"], DAY))
    assert "last_sent_date" not in du._load_state()["channels"]["1"]


# ---------------------------------------------------------------------------
# State file
# ---------------------------------------------------------------------------

def test_corrupt_state_file_does_not_crash(monkeypatch, tmp_path):
    f = tmp_path / "state.json"
    f.write_text("{not json")
    monkeypatch.setattr(du, "STATE_FILE", str(f))
    assert du._load_state() == {"channels": {}}


def test_missing_state_file_does_not_crash(monkeypatch, tmp_path):
    monkeypatch.setattr(du, "STATE_FILE", str(tmp_path / "absent.json"))
    assert du._load_state() == {"channels": {}}


def test_save_is_atomic_and_leaves_no_temp_files(monkeypatch, tmp_path):
    """A truncated write would be read back as 'no channels registered',
    silently unregistering everyone."""
    monkeypatch.setattr(du, "STATE_FILE", str(tmp_path / "state.json"))
    du._save_state({"channels": {"1": {"send_hour_utc": 18}}})
    assert du._load_state()["channels"]["1"]["send_hour_utc"] == 18
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


# ---------------------------------------------------------------------------
# Scheduling arithmetic
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("local,offset,expected", [
    ("12:00", -6, (18, 0)),      # noon CST
    ("00:30", -6, (6, 30)),
    ("23:00", -6, (5, 0)),       # wraps past midnight UTC
    ("08:00", 8, (0, 0)),        # positive offset
    ("09", -6, (15, 0)),         # bare hour
])
def test_send_time_converts_to_utc(local, offset, expected):
    assert du._parse_send_time(local, offset) == expected


@pytest.mark.parametrize("bad", ["25:00", "12:99", "abc", "-1:00"])
def test_invalid_send_times_are_rejected(bad):
    with pytest.raises((ValueError, IndexError)):
        du._parse_send_time(bad, -6)


@pytest.mark.parametrize("local,offset", [("12:00", -6), ("23:45", 8), ("00:00", 0)])
def test_utc_conversion_round_trips(local, offset):
    h, m = du._parse_send_time(local, offset)
    assert du._format_utc_to_local(h, m, offset) == local.zfill(5)


# ---------------------------------------------------------------------------
# Embed limits
# ---------------------------------------------------------------------------

def test_a_day_with_runs_is_one_image_message():
    """Drawn as an image since 2026-09-29 (stats_cards.daily_card): one embed
    carrying the picture, so Discord's field and embed limits no longer apply."""
    messages = du._build_update_messages({
        "total_games": 1, "unique_players": 1, "new_players": 0, "total_playtime_sec": 60,
        "act_distribution": {1: 1}, "turn_grain": {}, "draft": {},
    }, DAY)
    assert len(messages) == 1
    assert messages[0]["file"].filename == "daily.png"
    assert messages[0]["embed"].image.url == "attachment://daily.png"


def test_quiet_day_produces_one_embed():
    # "games" -> "runs" (2026-09-08): tutorial rows are no longer counted as
    # runs, so the report says which population it means everywhere.
    messages = du._build_update_messages({"total_games": 0}, DAY)
    assert len(messages) == 1 and "No runs were played" in messages[0]["embed"].description
    assert "file" not in messages[0]


def _paths_stats():
    from datetime import datetime, timezone
    from azoth_logic import run_paths
    until = datetime(2026, 6, 19, 5, tzinfo=timezone.utc)
    t = {"started_at": (until - timedelta(days=5)).isoformat(), "format": "tutorial",
         "act_reached": 2, "result": "death"}
    return run_paths.build({"p": [t]}, until), until - timedelta(days=14), until


def test_the_paths_image_follows_the_days_image():
    """2026-10-08: the new player paths card (/stats paths) is the report's
    second image."""
    messages = du._build_update_messages({
        "total_games": 1, "unique_players": 1, "new_players": 0, "total_playtime_sec": 60,
        "act_distribution": {1: 1}, "turn_grain": {}, "draft": {}, "paths": _paths_stats(),
    }, DAY)
    assert [m["file"].filename for m in messages] == ["daily.png", "paths.png"]


def test_a_quiet_day_still_posts_the_paths():
    """The paths cover two weeks, so a day with no runs does not empty them."""
    messages = du._build_update_messages({"total_games": 0, "paths": _paths_stats()}, DAY)
    assert len(messages) == 2 and messages[1]["file"].filename == "paths.png"


def test_paths_that_could_not_be_read_cost_only_their_image(monkeypatch):
    """An extra beside the day's own figures: a failed read is a console line,
    not a report held back."""
    def boom(*a):
        raise du.SupabaseError("connection reset")
    monkeypatch.setattr(du, "new_player_paths", boom)
    assert du._fetch_paths(DAY) is None
    messages = du._build_update_messages({"total_games": 0, "paths": None}, DAY)
    assert len(messages) == 1


def test_the_paths_end_where_the_reported_day_ends(monkeypatch):
    """A backfilled report shows the paths as they stood on its day."""
    seen = {}
    monkeypatch.setattr(du, "new_player_paths",
                        lambda until, days, runs: seen.setdefault("until", until) and (None, None))
    du._fetch_paths(DAY)
    assert seen["until"].isoformat() == du._day_range_utc(DAY)[1]


# ---------------------------------------------------------------------------
# _send_due_channels  --  the silent-stop bug
# ---------------------------------------------------------------------------

def _due(**overrides):
    """A channel config whose send time has already passed today."""
    cfg = {"send_hour_utc": 0, "send_minute_utc": 0}
    cfg.update(overrides)
    return cfg


TODAY = date(2026, 9, 1)


def _sweep_day(monkeypatch, tmp_path, today=TODAY):
    monkeypatch.setattr(du, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(du, "_today_cst", lambda: today)


def test_a_failing_channel_does_not_kill_the_sweep(monkeypatch, tmp_path):
    """REGRESSION (2026-09-01): the daily report stopped without a word.

    An error from one channel reached nextcord's tasks.Loop, whose
    `_valid_exception` tuple covers only OSError/GatewayNotFound/
    ConnectionClosed/ClientError/TimeoutError. A RuntimeError was printed to
    stderr and RE-RAISED, ending the loop for the life of the process: the retry
    the unclaimed day was waiting for no longer existed, so every later report
    was lost too.

    The sweep must absorb it and keep going.
    """
    _sweep_day(monkeypatch, tmp_path)
    calls = []

    async def backlog(bot, state, channel_id, config, today, past):
        calls.append(channel_id)
        if channel_id == "bad":
            raise RuntimeError("PostgREST 500")
        return 1

    monkeypatch.setattr(du, "_send_backlog", backlog)
    du._save_state({"channels": {"bad": _due(), "good": _due()}})

    asyncio.run(du._send_due_channels(_Bot(object()), "loop"))   # must not raise

    assert calls == ["bad", "good"], "the failing channel must not skip the next one"


def test_the_sweep_retries_on_the_next_cycle_after_a_failure(monkeypatch, tmp_path):
    """The point of surviving: the very next cycle gets another go.

    Under the old code cycle 2 never ran at all, because cycle 1 took the loop
    down with it.
    """
    _sweep_day(monkeypatch, tmp_path)
    _stub_report(monkeypatch)
    attempts = []

    def stats(day):
        attempts.append(day)
        if len(attempts) == 1:
            raise RuntimeError("transient")
        return {"total_games": 0}

    monkeypatch.setattr(du, "_fetch_daily_stats", stats)
    du._save_state({"channels": {"1": _due()}})

    for _ in range(3):
        asyncio.run(du._send_due_channels(_Bot(_Channel()), "loop"))

    assert len(attempts) == 2, "failed, retried, then stopped once the day was claimed"
    assert du._load_state()["channels"]["1"]["last_sent_date"] == "2026-09-01"


def test_a_malformed_state_entry_reads_as_config_not_as_a_crash(monkeypatch, tmp_path, capsys):
    """A non-dict channel entry is an AttributeError on config.get().

    The per-channel guard already keeps it from being fatal, so this pins the
    other half: it must be reported as the bad *config* it is, not as a stack
    trace, or whoever reads that console goes looking for a bug in the bot.
    """
    _sweep_day(monkeypatch, tmp_path)
    sent = []

    async def backlog(bot, state, channel_id, config, today, past):
        sent.append(channel_id)
        return 1

    monkeypatch.setattr(du, "_send_backlog", backlog)
    du._save_state({"channels": {"junk": "not a dict", "ok": _due()}})

    asyncio.run(du._send_due_channels(_Bot(object()), "loop"))

    assert sent == ["ok"], "the good channel still goes out"
    out = capsys.readouterr()
    assert "malformed state entry" in out.out
    assert "AttributeError" not in out.out + out.err, \
        "a config problem must not surface as a traceback"


def test_an_unreadable_state_file_does_not_kill_the_sweep(monkeypatch, tmp_path):
    """_load_state survives corrupt JSON, but a top-level list is valid JSON and
    made setdefault() raise. Nothing above the per-channel loop may escape either."""
    monkeypatch.setattr(du, "STATE_FILE", str(tmp_path / "state.json"))
    (tmp_path / "state.json").write_text("[1, 2, 3]")

    asyncio.run(du._send_due_channels(_Bot(object()), "startup"))   # must not raise


def test_the_sweep_still_honours_every_skip_rule(monkeypatch, tmp_path):
    """Disabled, already-sent and not-yet-due channels stay skipped -- the
    dedup rules survived being moved into the shared sweep."""
    _sweep_day(monkeypatch, tmp_path)
    _stub_report(monkeypatch)
    du._save_state({"channels": {
        "1": _due(disabled=True),                                   # off
        "2": _due(last_sent_date="2026-09-01"),                     # already sent
        "3": {"send_hour_utc": 23, "send_minute_utc": 59,
              "last_sent_date": "2026-08-31"},                      # not yet due
        "4": _due(),                                                # due
    }})
    sent = []

    async def claim(channel, state, channel_id, config, day):
        sent.append(channel_id)

    monkeypatch.setattr(du, "_claim_and_send", claim)
    asyncio.run(du._send_due_channels(_Bot(_Channel()), "loop"))

    assert sent == ["4"]


# ---------------------------------------------------------------------------
# The backlog  --  2026-09-29 and -30 never arrived
# ---------------------------------------------------------------------------
#
# A pack offer made the report raise on 2026-09-29 and -30. The schedule did
# its job and retried every 10 minutes, but the day it was retrying moved on
# at midnight and nothing ever went back: only the most recent day was ever
# sent, and the failure was a console line nobody saw.

def test_only_the_newest_report_is_due_on_an_ordinary_day():
    cfg = {"last_sent_date": "2026-09-30"}
    assert du._reports_due(cfg, date(2026, 10, 1), True) == [date(2026, 9, 30)]
    assert du._reports_due(cfg, date(2026, 10, 1), False) == []


def test_every_missed_day_is_owed_oldest_first():
    """Last sent on the 29th (covering the 28th); today is 10-02, past send time."""
    cfg = {"last_sent_date": "2026-09-29"}
    assert du._reports_due(cfg, date(2026, 10, 2), True) == [
        date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 1)]


def test_a_missed_day_is_owed_before_todays_send_time():
    """Down all of yesterday: yesterday's report goes out as soon as the bot is
    back, not at today's send time."""
    cfg = {"last_sent_date": "2026-09-30"}
    assert du._reports_due(cfg, date(2026, 10, 2), False) == [date(2026, 9, 30)]


def test_the_backlog_is_capped():
    cfg = {"last_sent_date": "2026-08-01"}
    due = du._reports_due(cfg, date(2026, 10, 2), True)
    assert len(due) == du.MAX_BACKFILL_DAYS
    assert due[-1] == date(2026, 10, 1)


def test_a_new_channel_is_not_owed_history():
    assert du._reports_due({}, date(2026, 10, 2), True) == [date(2026, 10, 1)]
    assert du._reports_due({}, date(2026, 10, 2), False) == []


def test_the_backlog_sends_each_day_in_order_and_claims_each(monkeypatch, tmp_path):
    _sweep_day(monkeypatch, tmp_path, today=date(2026, 10, 2))
    _stub_report(monkeypatch)
    channel = _Channel()
    du._save_state({"channels": {"1": _due(last_sent_date="2026-09-29")}})

    asyncio.run(du._send_due_channels(_Bot(channel), "startup"))
    asyncio.run(du._send_due_channels(_Bot(channel), "loop"))   # nothing left

    assert [e.split(":")[0] for e in channel.sent[::3]] == ["2026-09-29", "2026-09-30", "2026-10-01"]
    assert len(channel.sent) == 9
    assert du._load_state()["channels"]["1"]["last_sent_date"] == "2026-10-02"


def test_a_report_that_will_not_build_holds_the_later_days_and_says_so(monkeypatch, tmp_path):
    """The failure is posted ONCE, the day stays owed, and the days after it
    wait rather than jumping the watermark past it."""
    _sweep_day(monkeypatch, tmp_path, today=date(2026, 10, 1))
    _stub_report(monkeypatch)
    broken = {date(2026, 9, 29)}

    def stats(day):
        if day in broken:
            raise RuntimeError("Could not find the table 'public.packs'")
        return {"total_games": 0}

    monkeypatch.setattr(du, "_fetch_daily_stats", stats)
    channel = _Channel()
    du._save_state({"channels": {"1": _due(last_sent_date="2026-09-29")}})

    for _ in range(3):
        asyncio.run(du._send_due_channels(_Bot(channel), "loop"))

    assert len(channel.sent) == 1, "one failure notice, nothing else, across three cycles"
    assert "2026-09-29 failed" in channel.sent[0].title
    assert "public.packs" in channel.sent[0].description
    assert du._load_state()["channels"]["1"]["last_sent_date"] == "2026-09-29"

    broken.clear()                                          # the fix ships
    asyncio.run(du._send_due_channels(_Bot(channel), "loop"))
    assert [e.split(":")[0] for e in channel.sent[1::3]] == ["2026-09-29", "2026-09-30"]


def test_two_passes_at_once_do_not_send_a_day_twice(monkeypatch, tmp_path):
    """The loop and the startup pass both run at ready. A backlog awaits between
    reports, so without the lock the second pass reads the state mid-backlog."""
    _sweep_day(monkeypatch, tmp_path, today=date(2026, 10, 2))
    _stub_report(monkeypatch)

    class SlowChannel(_Channel):
        async def send(self, embed=None, file=None):
            await asyncio.sleep(0)
            self.sent.append(embed)

    channel = SlowChannel()
    du._save_state({"channels": {"1": _due(last_sent_date="2026-09-29")}})

    async def both():
        await asyncio.gather(du._send_due_channels(_Bot(channel), "loop"),
                             du._send_due_channels(_Bot(channel), "startup"))
    asyncio.run(both())

    assert len(channel.sent) == 9, "three days, three messages each, once"


def test_re_enabling_does_not_hand_over_the_days_it_was_off():
    state = {"channels": {"1": {"disabled": True, "last_sent_date": "2026-09-01"}}}
    cfg = du._enable_channel(state, "1", 18, 0, date(2026, 10, 2))
    assert "disabled" not in cfg
    assert du._reports_due(cfg, date(2026, 10, 2), True) == [date(2026, 10, 1)]


def test_re_enabling_the_same_day_does_not_re_send():
    state = {"channels": {"1": {"disabled": True, "last_sent_date": "2026-10-02"}}}
    cfg = du._enable_channel(state, "1", 18, 0, date(2026, 10, 2))
    assert du._reports_due(cfg, date(2026, 10, 2), True) == []


def test_changing_the_send_time_keeps_a_live_backlog():
    """Only a DISABLED channel's watermark moves; re-running the command on a
    live channel must not throw away days it genuinely missed."""
    state = {"channels": {"1": {"last_sent_date": "2026-09-29"}}}
    cfg = du._enable_channel(state, "1", 18, 0, date(2026, 10, 2))
    assert len(du._reports_due(cfg, date(2026, 10, 2), True)) == 3


@pytest.mark.parametrize("text", ["2026-10-02", "2026-10-03", "yesterday", "10/01/2026"])
def test_a_repost_needs_a_finished_past_day(text):
    with pytest.raises(ValueError):
        du._parse_report_date(text, date(2026, 10, 2))


def test_a_repost_takes_an_iso_day():
    assert du._parse_report_date(" 2026-09-29 ", date(2026, 10, 2)) == date(2026, 9, 29)


# ---------------------------------------------------------------------------
# Population split  --  opening-turn restarts and tutorial runs
# ---------------------------------------------------------------------------
#
# Added 2026-09-08 after a daily report read "15 games started, of which 13 were
# restarts". 13 of those 15 rows were runs on the Tutorial Deck: nine of them a
# developer iterating on the tutorial (which does NOT trip
# TestingConfig.is_testing(), so it uploads like any other run) and the rest a
# genuine new player's first walkthrough. The report was describing tutorial
# iteration as if it were play.

TUTORIAL_DECK = 31


def _g(**kw):
    """A games row with the fields _partition_games actually reads."""
    row = {"uuid": "g", "player_uuid": "p", "result": None,
           "turns_played": 5, "starter_deck": 29, "game_type": "solo"}
    row.update(kw)
    return row


def test_turn_one_is_abandoned_during_the_first_turn_not_after_it():
    """The off-by-one this whole split turns on.

    `GlobalVars.turn_count` is incremented at the START of a turn
    (main.gd::handle_turn_start), and SaveManager.clear_save reports the value
    stored in the save. So turns_played == 1 means the run was abandoned DURING
    turn 1 and never reached its first draft, while turns_played == 2 already
    means one COMPLETED turn plus its draft.

    Reading the boundary as "<= 2" silently deletes real one-turn runs; reading
    it as "< 1" deletes nothing at all. Both mutations must fail here.
    """
    rows = [
        _g(uuid="dropped", result="restart", turns_played=1),
        _g(uuid="kept", result="restart", turns_played=2),
    ]
    regular, tutorial, dropped = du._partition_games(rows, set())

    assert [g["uuid"] for g in dropped] == ["dropped"]
    assert [g["uuid"] for g in regular] == ["kept"]
    assert tutorial == []


def test_only_restarts_are_dropped_on_turn_one():
    """A death on turn 1 is a real outcome, not an abandoned run."""
    rows = [
        _g(uuid="death", result="death", turns_played=1),
        _g(uuid="open", result=None, turns_played=1),
        _g(uuid="restart", result="restart", turns_played=1),
    ]
    regular, _, dropped = du._partition_games(rows, set())

    assert [g["uuid"] for g in dropped] == ["restart"]
    assert {g["uuid"] for g in regular} == {"death", "open"}


def test_unknown_turn_count_is_not_assumed_to_be_turn_one():
    """NULL turns_played means "unknown", not "turn 1" -- keep the row."""
    regular, _, dropped = du._partition_games(
        [_g(uuid="x", result="restart", turns_played=None)], set())

    assert dropped == []
    assert [g["uuid"] for g in regular] == ["x"]


def test_tutorial_runs_are_split_out_but_not_dropped():
    """A player working through the tutorial is still activity -- it just isn't
    a run whose duration, act or combo is comparable to a drafted one."""
    rows = [
        _g(uuid="tut", starter_deck=TUTORIAL_DECK, turns_played=6),
        _g(uuid="real", starter_deck=29, turns_played=6),
    ]
    regular, tutorial, dropped = du._partition_games(rows, {TUTORIAL_DECK})

    assert [g["uuid"] for g in tutorial] == ["tut"]
    assert [g["uuid"] for g in regular] == ["real"]
    assert dropped == []


def test_the_opening_turn_drop_is_applied_before_the_tutorial_split():
    """Order matters: a tutorial run abandoned on turn 1 is excluded outright,
    not counted on the tutorial line."""
    regular, tutorial, dropped = du._partition_games(
        [_g(uuid="x", starter_deck=TUTORIAL_DECK, result="restart", turns_played=1)],
        {TUTORIAL_DECK},
    )

    assert [g["uuid"] for g in dropped] == ["x"]
    assert tutorial == [] and regular == []


def test_unknown_deck_stays_in_the_regular_population():
    """186 historical rows have a NULL starter_deck. Unclassifiable is counted,
    not hidden -- over-reporting activity beats silently deleting it."""
    regular, tutorial, _ = du._partition_games([_g(uuid="x", starter_deck=None)], {TUTORIAL_DECK})

    assert [g["uuid"] for g in regular] == ["x"]
    assert tutorial == []


def test_an_unreadable_decks_table_classifies_nothing_as_tutorial(monkeypatch):
    """Same failure direction as the live-content filter in content_index: an
    empty set filters nothing rather than hiding everything. Reporting a real
    run as a tutorial because a read failed is the worse error."""
    class Boom:
        def select(self, *a, **k):
            return self
        def eq(self, *a, **k):
            return self
        def execute(self):
            raise RuntimeError("PostgREST is down")

    monkeypatch.setattr(du, "supabase", type("S", (), {"table": staticmethod(lambda t: Boom())})())

    assert du._fetch_tutorial_deck_ids() == set()


def test_a_tutorial_only_day_is_not_reported_as_a_quiet_day():
    """total_games counts the regular population only, so a day of pure tutorial
    play has total_games == 0. Printing "no runs were played" would hide exactly
    the rows this split exists to make visible."""
    stats = {
        "total_games": 0, "tutorial_games": 11, "tutorial_players": 2,
        "tutorial_restarts": 10, "dropped_opening_turn": 2,
        "unique_players": 3, "new_players": 1, "total_playtime_sec": 0,
        "turn_grain": {}, "draft": {},
    }
    messages = du._build_update_messages(stats, DAY)
    assert "file" in messages[0], "a tutorial-only day is drawn, not reported as quiet"
    tiles = dict(t for b in stats_cards.daily_card(stats, "2026-09-28").blocks
                 if isinstance(b, sc.StatTiles) for t in b.tiles)
    assert tiles["Tutorial"] == "11"


def test_daily_stats_derives_every_number_from_the_regular_population(monkeypatch):
    """End-to-end through _fetch_daily_stats.

    The turn-grain and draft fetches used to be handed the raw day. A scripted
    tutorial turn is no more comparable to a drafted one than a boss turn is to
    a regular one, so they must receive the regular uuids only.
    """
    games = [
        _g(uuid="real", player_uuid="p1", result="death", turns_played=12,
           elapsed_sec=1507, level_reached=9, act_reached=3, highest_combo="1024"),
        _g(uuid="tut", player_uuid="p2", starter_deck=TUTORIAL_DECK,
           result="restart", turns_played=2, elapsed_sec=60,
           level_reached=1, act_reached=1, highest_combo="2"),
        _g(uuid="bail", player_uuid="p2", starter_deck=TUTORIAL_DECK,
           result="restart", turns_played=1, elapsed_sec=31,
           level_reached=0, act_reached=1, highest_combo="1"),
    ]

    class Q:
        def __init__(self, t):
            self.t = t
            self.rows = {"games": games,
                         "players": [],
                         "decks": [{"id": TUTORIAL_DECK, "usage_type": "tutorial"}]}.get(t, [])
        def select(self, *a, **k):
            return self
        def gte(self, *a, **k):
            return self
        def lt(self, *a, **k):
            return self
        def eq(self, *a, **k):
            return self
        def in_(self, *a, **k):
            return self
        def execute(self):
            return type("R", (), {"data": self.rows})()

    monkeypatch.setattr(du, "supabase", type("S", (), {"table": staticmethod(Q)})())

    seen = {}
    monkeypatch.setattr(du, "_fetch_turn_grain_stats",
                        lambda uuids: seen.setdefault("turn_grain", list(uuids)) and {} or {})
    monkeypatch.setattr(du, "_fetch_draft_stats",
                        lambda uuids, by: seen.setdefault("draft", list(uuids)) and {} or {})

    stats = du._fetch_daily_stats(DAY)

    assert stats["total_games"] == 1              # the tutorial rows are not runs
    assert stats["tutorial_games"] == 1           # 'tut' -- 'bail' was dropped first
    assert stats["dropped_opening_turn"] == 1
    assert stats["unique_players"] == 2           # a tutorial-only player still played
    assert stats["max_level"] == 9                # not the tutorial's 1
    assert stats["total_playtime_sec"] == 1507    # not 1598
    assert stats["game_results"] == {"death": 1}
    # The downstream fetches saw the regular uuids ONLY.
    assert seen["turn_grain"] == ["real"]
    assert seen["draft"] == ["real"]


def test_the_day_is_bucketed_on_started_at_not_finished_at(monkeypatch):
    """REGRESSION (2026-09-08): `games.finished_at` was never a finish time.

    Nothing in the game wrote that column until 2026-09-08, and it carries
    `default now()`, so it held the moment `open_run()` INSERTED the stub row --
    a median of 8 seconds into turn 1. It equalled `created_at` to the
    microsecond on all 124 rows measured.

    `started_at` is the true run start and is what this report claims to count.
    It is also the only column that works on both sides of
    `db/migrations/2026-09-08_games_finished_at.sql`: once that drops the
    default, an abandoned run has `finished_at` NULL and a finished_at window
    silently drops the abandoned-run population `open_run` exists to expose.
    """
    filtered_on = []

    class Q:
        def __init__(self, t):
            self.t = t
        def select(self, *a, **k):
            return self
        def gte(self, col, _v):
            if self.t == "games":
                filtered_on.append(col)
            return self
        def lt(self, col, _v):
            if self.t == "games":
                filtered_on.append(col)
            return self
        def eq(self, *a, **k):
            return self
        def in_(self, *a, **k):
            return self
        def execute(self):
            return type("R", (), {"data": []})()

    monkeypatch.setattr(du, "supabase", type("S", (), {"table": staticmethod(Q)})())
    du._fetch_daily_stats(DAY)

    assert filtered_on == ["started_at", "started_at"]
    assert "finished_at" not in filtered_on


# ---------------------------------------------------------------------------
# The 2026-09-17 report rewrite
# ---------------------------------------------------------------------------

def _draft_rows(monkeypatch, items, names):
    """Install one draft holding `items`, with `names` resolving every id."""
    data = {"drafts": [{"uuid": "d1", "game_uuid": "g1"}], "draft_items": items}
    data.update(names)

    class Q:
        def __init__(self, t):
            self.rows = data.get(t, [])
        def select(self, *a, **k):
            return self
        def in_(self, col, vals):
            self.rows = [r for r in self.rows if r[col] in vals]
            return self
        def execute(self):
            return type("R", (), {"data": self.rows})()

    monkeypatch.setattr(du, "supabase", type("S", (), {"table": staticmethod(Q)})())


@pytest.mark.parametrize("rite_type,table", [("rite", "rites"), ("event", "events")])
def test_cards_and_aspects_rank_together_and_rites_apart(
        monkeypatch, rite_type, table):
    """Cards and aspects share the Cards list; a Rite appears only in Rites.

    Migrations are hand-applied with no history, so the item_type is `event` on
    one side of the rename and `rite` on the other. Matching only the new
    spelling would silently drop every Rite from its list on a database that
    has not been migrated yet.
    """
    _draft_rows(monkeypatch, [
        {"id": 1, "draft_uuid": "d1", "item_type": "card", "item_id": 10, "picked": True},
        {"id": 2, "draft_uuid": "d1", "item_type": "card", "item_id": 10, "picked": True},
        {"id": 3, "draft_uuid": "d1", "item_type": rite_type, "item_id": 20, "picked": True},
        {"id": 4, "draft_uuid": "d1", "item_type": rite_type, "item_id": 20, "picked": True},
        {"id": 5, "draft_uuid": "d1", "item_type": "aspect", "item_id": 30, "picked": True},
        {"id": 6, "draft_uuid": "d1", "item_type": "aspect", "item_id": 30, "picked": True},
    ], {"cards": [{"id": 10, "name": "Salvage"}],
        "aspects": [{"id": 30, "name": "Verdance"}],
        table: [{"id": 20, "name": "Sealing"}]})

    stats = du._fetch_draft_stats(["g1"], {"g1": {"uuid": "g1", "highest_combo": "1"}})

    assert sorted(n for n, _ in stats["most_picked_cards"]) == ["Salvage", "Verdance"]
    assert [n for n, _ in stats["most_picked_rites"]] == ["Sealing"]


def test_a_name_shared_by_a_card_and_a_rite_is_not_pooled(monkeypatch):
    """Names collide across content types -- the reason `deck_contents` refs are
    encoded. Keying the pick counts by name alone would merge a card's offers
    with a Rite's into one bogus rate."""
    _draft_rows(monkeypatch, [
        {"id": 1, "draft_uuid": "d1", "item_type": "card", "item_id": 10, "picked": True},
        {"id": 2, "draft_uuid": "d1", "item_type": "card", "item_id": 10, "picked": True},
        {"id": 3, "draft_uuid": "d1", "item_type": "rite", "item_id": 20, "picked": False},
        {"id": 4, "draft_uuid": "d1", "item_type": "rite", "item_id": 20, "picked": False},
    ], {"cards": [{"id": 10, "name": "Echo"}], "rites": [{"id": 20, "name": "Echo"}]})

    stats = du._fetch_draft_stats(["g1"], {"g1": {"uuid": "g1", "highest_combo": "1"}})

    assert dict(stats["most_picked_cards"])["Echo"]["picked"] == 2
    assert dict(stats["most_picked_rites"])["Echo"]["picked"] == 0



def test_a_pack_is_named_by_its_kind_and_never_looked_up(monkeypatch):
    """REGRESSION (2026-09-29/30): two daily reports never arrived.

    A booster pack is a draft item with `item_type 'pack'` and `item_id` NULL.
    The name lookup built a table from the type and asked PostgREST for
    `public.packs`, which raised on every cycle of every day a pack was offered.
    The fake raises on any table it does not hold, as PostgREST does.
    """
    data = {"drafts": [{"uuid": "d1", "game_uuid": "g1"}],
            "draft_items": [
                {"id": 1, "draft_uuid": "d1", "item_type": "pack", "item_id": None,
                 "pack_type": "card", "picked": True},
                {"id": 2, "draft_uuid": "d1", "item_type": "pack", "item_id": None,
                 "pack_type": "card", "picked": False},
                {"id": 3, "draft_uuid": "d1", "item_type": "shopreward", "item_id": None,
                 "pack_type": None, "picked": True},
                {"id": 4, "draft_uuid": "d1", "item_type": "shopreward", "item_id": None,
                 "pack_type": None, "picked": True},
                {"id": 5, "draft_uuid": "d1", "item_type": "card", "item_id": 10, "picked": True},
                {"id": 6, "draft_uuid": "d1", "item_type": "card", "item_id": 10, "picked": False}],
            "cards": [{"id": 10, "name": "Salvage"}]}

    class Q:
        def __init__(self, t):
            if t not in data:
                raise RuntimeError(f"Could not find the table 'public.{t}'")
            self.rows = data[t]
        def select(self, *a, **k):
            return self
        def in_(self, col, vals):
            self.rows = [r for r in self.rows if r[col] in vals]
            return self
        def execute(self):
            return type("R", (), {"data": self.rows})()

    monkeypatch.setattr(du, "supabase", type("S", (), {"table": staticmethod(Q)})())

    stats = du._fetch_draft_stats(["g1"], {"g1": {"uuid": "g1", "highest_combo": "1"}})

    rates = {(i["item_type"], i["item_name"]): (i["picked"], i["offered"])
             for i in stats["item_rates"]}
    assert rates == {("pack", "Atoms"): (1, 2), ("card", "Salvage"): (1, 2)}, \
        "packs ranked by their printed name; a shop level-up is a reward, not a pick"


# ---------------------------------------------------------------------------
# The image report (2026-09-29)
# ---------------------------------------------------------------------------

def test_the_boss_record_is_per_boss_by_name(monkeypatch):
    """The text report gave one total; a day where Veln won every fight hid in it."""
    _install_turn_grain(monkeypatch, TURNS, NODES, LEVELUPS)
    assert du._fetch_turn_grain_stats(["g1"])["boss_record"] == {"Veln": [1, 1]}


def test_an_unfinished_fight_is_not_in_the_boss_record(monkeypatch):
    turns = TURNS[:3] + [dict(TURNS[3], boss_result=None)]
    _install_turn_grain(monkeypatch, turns, NODES, LEVELUPS)
    assert du._fetch_turn_grain_stats(["g1"])["boss_record"] == {}


def _daily(**extra):
    stats = {"total_games": 11, "unique_players": 6, "new_players": 2,
             "total_playtime_sec": 18720, "act_distribution": {1: 3, 2: 4, 3: 3, 4: 1},
             "turn_grain": {"boss_record": {"Veln": [0, 3], "Marrox": [3, 3]},
                            "top_rewards": [("Hero", {"taken": 5, "offered": 6, "rate": 5 / 6})],
                            "levelup_packs": 18},
             "draft": {"item_rates": [
                 {"item_type": "card", "item_name": "Circumvent", "picked": 4, "offered": 4},
                 {"item_type": "rite", "item_name": "Pyre", "picked": 2, "offered": 3},
                 {"item_type": "card", "item_name": "Thorn", "picked": 0, "offered": 4}]}}
    stats.update(extra)
    return stats_cards.daily_card(stats, "2026-09-28")


def _heads(card):
    return [b.label for b in card.blocks if isinstance(b, sc.SectionHeader)]


def test_the_daily_card_has_every_section_with_data():
    assert _heads(_daily()) == ["How far runs got", "Boss fights", "Level-up picks", "Draft"]


def test_a_section_with_nothing_in_it_is_not_drawn():
    assert _heads(_daily(turn_grain={}, draft={})) == ["How far runs got"]


def test_reaching_act_4_counts_as_beating_act_3():
    strip = next(b for b in _daily().blocks if isinstance(b, sc.ActStripRow))
    assert strip.cleared == 1 and sum(strip.acts.values()) == 11


def test_one_day_flags_nothing():
    """A digest, not a verdict: one day is too little for a red or blue flag."""
    assert not any(getattr(b, "marker", "") for b in _daily().blocks)


def test_unavailable_turn_data_is_said_rather_than_shown_as_zero():
    card = _daily(turn_grain={"error": "needs the service-role key"})
    notes = [b.text for b in card.blocks if isinstance(b, sc.Note)]
    assert any("unavailable" in n for n in notes)


def test_co_op_counts_sessions_not_participant_rows():
    """Three friends in one co-op run write three rows: one run."""
    games = [{"uuid": "a", "game_type": "coop", "shared_run_id": "s1"},
             {"uuid": "b", "game_type": "coop", "shared_run_id": "s1"},
             {"uuid": "c", "game_type": "coop", "shared_run_id": "s1"},
             {"uuid": "d", "game_type": "coop", "shared_run_id": "s2"},
             {"uuid": "e", "game_type": "solo"}]
    assert du._count_runs(games) == (1, 2, 0)


def test_a_co_op_row_with_no_session_id_still_counts():
    assert du._count_runs([{"uuid": "a", "game_type": "coop", "shared_run_id": None}]) == (0, 1, 0)


def test_legacy_counts_solo_runs_and_co_op_sessions_once_each():
    games = [{"uuid": "a", "game_type": "solo", "format": "legacy"},
             {"uuid": "b", "game_type": "coop", "shared_run_id": "s1", "format": "legacy"},
             {"uuid": "c", "game_type": "coop", "shared_run_id": "s1", "format": "legacy"},
             {"uuid": "d", "game_type": "solo", "format": "standard"}]
    assert du._count_runs(games) == (2, 1, 2)


def test_co_op_and_legacy_tiles_show_even_at_zero():
    """Whether anyone played them is the answer, so 0 is shown, not hidden."""
    card = _daily(solo_runs=11, coop_sessions=0, legacy_runs=0)
    tiles = dict(t for b in card.blocks if isinstance(b, sc.StatTiles) for t in b.tiles)
    assert (tiles["Solo runs"], tiles["Co-op runs"], tiles["Legacy runs"]) == ("11", "0", "0")
