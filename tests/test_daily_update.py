"""Tests for the daily activity report.

Two production incidents are pinned here as regression tests, both named at the
site: the June 30 TypeError and the June 19 duplicate-message flood. The rest
covers the turn-grain aggregation, where the denominators are easy to get wrong
in ways that produce plausible numbers.
"""
import asyncio
import json
import math

import pytest

import azoth_commands.daily_update as du


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
    data = {"turns": turns, "turn_nodes": nodes, "levelups": levelups}

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

class _FlakyChannel:
    """Emits the first embed, then fails -- the June 19 failure mode."""
    def __init__(self):
        self.sent = []

    async def send(self, embed=None):
        self.sent.append(embed)
        raise RuntimeError("Discord 500 mid-send")


class _Bot:
    def __init__(self, channel):
        self._channel = channel

    def get_channel(self, cid):
        return self._channel


def _stub_report(monkeypatch):
    monkeypatch.setattr(du, "_fetch_daily_stats", lambda: {"total_games": 0})
    monkeypatch.setattr(du, "_build_update_embeds", lambda s: ["e1", "e2", "e3"])


def test_failed_send_does_not_re_fire(monkeypatch, tmp_path):
    """REGRESSION (2026-06-19): ~30 duplicate messages.

    The old code persisted `last_sent_date` only AFTER a successful send. When
    channel.send raised partway through the embed list, the messages already out
    stayed out, nothing was claimed, and the 10-minute loop retried forever.
    """
    monkeypatch.setattr(du, "STATE_FILE", str(tmp_path / "state.json"))
    _stub_report(monkeypatch)
    channel = _FlakyChannel()
    bot = _Bot(channel)
    du._save_state({"channels": {"123": {"send_hour_utc": 0, "send_minute_utc": 0}}})

    for _ in range(6):                                   # six 10-minute cycles
        state = du._load_state()
        cfg = state["channels"]["123"]
        if cfg.get("last_sent_date") == "2026-06-19":
            continue
        asyncio.run(du._claim_and_send(bot, state, "123", cfg, "2026-06-19"))

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
            observed["on_disk"] = json.loads(state_file.read_text())

    du._save_state({"channels": {"1": {"send_hour_utc": 0, "send_minute_utc": 0}}})
    state = du._load_state()
    asyncio.run(du._claim_and_send(_Bot(Channel()), state, "1", state["channels"]["1"], "2026-06-19"))
    assert observed["on_disk"]["channels"]["1"]["last_sent_date"] == "2026-06-19"


def test_unresolvable_channel_makes_no_claim(monkeypatch, tmp_path):
    """A channel the bot cannot see yet (startup, before the cache fills) must
    stay retryable -- otherwise the day is silently burned."""
    monkeypatch.setattr(du, "STATE_FILE", str(tmp_path / "state.json"))
    _stub_report(monkeypatch)
    du._save_state({"channels": {"1": {}}})
    state = du._load_state()
    attempted = asyncio.run(
        du._claim_and_send(_Bot(None), state, "1", state["channels"]["1"], "2026-06-19"))
    assert attempted is False
    assert "last_sent_date" not in du._load_state()["channels"]["1"]


def test_report_error_does_not_consume_the_day(monkeypatch, tmp_path):
    """Stats are built BEFORE the claim, so a data error stays retryable."""
    monkeypatch.setattr(du, "STATE_FILE", str(tmp_path / "state.json"))
    def boom():
        raise RuntimeError("bad data")
    monkeypatch.setattr(du, "_fetch_daily_stats", boom)
    du._save_state({"channels": {"1": {}}})
    state = du._load_state()

    class Channel:
        async def send(self, embed=None):
            pass

    with pytest.raises(RuntimeError):
        asyncio.run(du._claim_and_send(_Bot(Channel()), state, "1", state["channels"]["1"], "2026-06-19"))
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

def test_oversized_field_values_are_truncated():
    """Discord rejects a field value over 1024 chars -- and a rejected send was
    the trigger for the June 19 retry loop."""
    stats = {
        "total_games": 1, "unique_players": 1, "new_players": 0, "measured_games": 1,
        "restarts": 0, "coop_rows": 0, "max_level": 1, "max_act": 1, "max_combo": 1,
        "avg_duration_sec": 1, "total_playtime_sec": 1, "avg_turns": 1,
        "game_results": {"death": 1}, "total_boss_fights": 0, "boss_wins": 0,
        "boss_losses": 0, "boss_error": None, "turn_grain": {},
        "draft": {"total_drafts": 1, "total_picks": 1,
                  "most_picked": [("N" * 400, {"rate": 1.0, "picked": 1, "offered": 1})] * 6,
                  "least_picked": [], "top_performers": []},
    }
    for embed in du._build_update_embeds(stats):
        for f in embed.fields:
            assert len(f.value) <= 1024, f"{f.name} exceeds Discord's field limit"
        assert du._embed_char_count(embed) <= 6000


def test_quiet_day_produces_one_embed():
    embeds = du._build_update_embeds({"total_games": 0})
    assert len(embeds) == 1 and "No games" in embeds[0].description
