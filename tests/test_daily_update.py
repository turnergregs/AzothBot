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
        "act_distribution": {1: 1},
        "draft": {"total_drafts": 1, "total_picks": 1,
                  "most_picked_cards": [("N" * 400, {"rate": 1.0, "picked": 1, "offered": 1})] * 6,
                  "least_picked_cards": [], "most_picked_rites": [],
                  "least_picked_rites": [], "top_performers": []},
    }
    for embed in du._build_update_embeds(stats):
        for f in embed.fields:
            assert len(f.value) <= 1024, f"{f.name} exceeds Discord's field limit"
        assert du._embed_char_count(embed) <= 6000


def test_quiet_day_produces_one_embed():
    # "games" -> "runs" (2026-09-08): tutorial rows are no longer counted as
    # runs, so the report says which population it means everywhere.
    embeds = du._build_update_embeds({"total_games": 0})
    assert len(embeds) == 1 and "No runs were played" in embeds[0].description


# ---------------------------------------------------------------------------
# _send_due_channels  --  the silent-stop bug
# ---------------------------------------------------------------------------

def _due(**overrides):
    """A channel config whose send time has already passed today."""
    cfg = {"send_hour_utc": 0, "send_minute_utc": 0}
    cfg.update(overrides)
    return cfg


def test_a_failing_channel_does_not_kill_the_sweep(monkeypatch, tmp_path):
    """REGRESSION (2026-09-01): the daily report stopped without a word.

    `_claim_and_send` raises on a build error deliberately, so the day stays
    unclaimed and retryable -- see test_report_error_does_not_consume_the_day.
    But the raise reached nextcord's tasks.Loop, whose `_valid_exception` tuple
    covers only OSError/GatewayNotFound/ConnectionClosed/ClientError/TimeoutError.
    A RuntimeError was printed to stderr and RE-RAISED, ending the loop for the
    life of the process: the retry the unclaimed day was waiting for no longer
    existed, so every later report was lost too.

    The sweep must absorb it and keep going.
    """
    monkeypatch.setattr(du, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(du, "_today_cst_str", lambda: "2026-09-01")

    calls = []

    async def claim(bot, state, channel_id, config, today):
        calls.append(channel_id)
        if channel_id == "bad":
            raise RuntimeError("PostgREST 500")
        return True

    monkeypatch.setattr(du, "_claim_and_send", claim)
    du._save_state({"channels": {"bad": _due(), "good": _due()}})

    asyncio.run(du._send_due_channels(_Bot(object()), "loop"))   # must not raise

    assert calls == ["bad", "good"], "the failing channel must not skip the next one"
    assert "last_sent_date" not in du._load_state()["channels"]["bad"], \
        "a raised error still leaves the day unclaimed and retryable"


def test_the_sweep_retries_on_the_next_cycle_after_a_failure(monkeypatch, tmp_path):
    """The point of surviving: the very next cycle gets another go.

    Under the old code cycle 2 never ran at all, because cycle 1 took the loop
    down with it.
    """
    monkeypatch.setattr(du, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(du, "_today_cst_str", lambda: "2026-09-01")

    attempts = []

    async def claim(bot, state, channel_id, config, today):
        attempts.append(today)
        if len(attempts) == 1:
            raise RuntimeError("transient")
        config["last_sent_date"] = today
        state["channels"][channel_id] = config
        du._save_state(state)
        return True

    monkeypatch.setattr(du, "_claim_and_send", claim)
    du._save_state({"channels": {"1": _due()}})

    for _ in range(3):
        asyncio.run(du._send_due_channels(_Bot(object()), "loop"))

    assert len(attempts) == 2, "failed, retried, then stopped once the day was claimed"
    assert du._load_state()["channels"]["1"]["last_sent_date"] == "2026-09-01"


def test_a_malformed_state_entry_reads_as_config_not_as_a_crash(monkeypatch, tmp_path, capsys):
    """A non-dict channel entry is an AttributeError on config.get().

    The per-channel guard already keeps it from being fatal, so this pins the
    other half: it must be reported as the bad *config* it is, not as a stack
    trace, or whoever reads that console goes looking for a bug in the bot.
    """
    monkeypatch.setattr(du, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(du, "_today_cst_str", lambda: "2026-09-01")

    sent = []

    async def claim(bot, state, channel_id, config, today):
        sent.append(channel_id)
        return True

    monkeypatch.setattr(du, "_claim_and_send", claim)
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
    monkeypatch.setattr(du, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(du, "_today_cst_str", lambda: "2026-09-01")

    sent = []

    async def claim(bot, state, channel_id, config, today):
        sent.append(channel_id)
        return True

    monkeypatch.setattr(du, "_claim_and_send", claim)
    du._save_state({"channels": {
        "off":       _due(disabled=True),
        "sent":      _due(last_sent_date="2026-09-01"),
        "not_yet":   {"send_hour_utc": 23, "send_minute_utc": 59},
        "due":       _due(),
    }})

    asyncio.run(du._send_due_channels(_Bot(object()), "loop"))

    assert sent == ["due"]


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
    embeds = du._build_update_embeds({
        "total_games": 0, "tutorial_games": 11, "tutorial_players": 2,
        "tutorial_restarts": 10, "dropped_opening_turn": 2,
        "unique_players": 3, "new_players": 1, "restarts": 0, "coop_rows": 0,
        "max_level": 0, "max_act": 0, "max_combo": 0,
        "avg_duration_sec": 0, "avg_turns": 0, "total_playtime_sec": 0,
        "measured_games": 0, "game_results": {}, "turn_grain": {},
        "total_boss_fights": 0, "boss_wins": 0, "boss_losses": 0, "draft": {},
    })

    # Name and value both, since the 2026-09-17 rewrite labels the count with
    # the field name and puts the bare number in the value.
    text = " ".join(f"{f.name} {f.value}" for e in embeds for f in e.fields)
    assert "11" in text and "tutorial" in text.lower()
    assert not any("No runs were played" in (e.description or "") for e in embeds)


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

    stats = du._fetch_daily_stats()

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
    du._fetch_daily_stats()

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


def test_an_act_nobody_reached_still_gets_a_row():
    """A gap in the ladder is the shape worth seeing. Listing only the acts that
    occurred would draw 1, 2, 4 as three adjacent bars and hide that act 3
    stopped everyone."""
    chart = du._act_chart({1: 3, 2: 1, 4: 3})
    assert "act 3" in chart and chart.count("act ") == 4


def test_a_non_zero_act_never_draws_an_empty_bar():
    """One run out of a hundred rounds to zero blocks, which reads as nobody got
    there -- the opposite of what the row says."""
    chart = du._act_chart({1: 100, 5: 1})
    act_5 = [ln for ln in chart.splitlines() if ln.startswith("act 5")][0]
    assert "█" in act_5, act_5
