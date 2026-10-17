"""A weapon's numbers and the escalate text its face leads with.

Ports of GameContentData.get_effective_attr's fall-through to the Weapon
property and CardTextComposer._lead_with_escalate (azoth docs/WEAPONS.md).
"""
import pytest

from azoth_logic import weapons


def card(**weapon):
    return {"name": "W", "text": "Draw 1", "valence": 3, "element": "sol",
            "properties": [dict({"name": "Weapon"}, **weapon)]}


def test_a_card_with_damage_is_a_weapon():
    assert weapons.is_weapon(card(damage=20))
    assert not weapons.is_weapon({"properties": [{"name": "Magnify", "amount": 1}]})
    assert not weapons.is_weapon({"properties": None})


def test_a_key_on_the_row_wins_over_the_property():
    """Where an upgrade tier writes (`{"damage": 150}`)."""
    row = dict(card(damage=100), damage=150)
    assert weapons.damage(row) == 150


def test_a_weapon_that_does_not_escalate_adds_no_text():
    assert weapons.lead(card(damage=20)) == ""
    assert weapons.text_with_lead(card(damage=20), "Draw 1") == "Draw 1"


def test_escalating_on_play_is_the_icon_alone():
    assert weapons.lead(card(damage=20, escalate=3)) == "[escalate3]"
    assert weapons.text_with_lead(card(damage=20, escalate=3), "Draw 1") == "[escalate3], Draw 1"


def test_a_card_that_prints_nothing_else_is_the_badge_alone():
    assert weapons.text_with_lead(card(damage=20, escalate=3), "") == "[escalate3]"


@pytest.mark.parametrize("on,expected", [
    ("discarded", "[escalate4] when discarded"),
    ("held", "Held: [escalate4]"),
    ("life_lost", "Held: [escalate4] per life lost"),
    ("played", "[escalate4]"),
    ("nonsense", "[escalate4]"),     # the game warns and reads it as played
])
def test_when_it_escalates_uses_the_games_wording(on, expected):
    assert weapons.lead(card(damage=1, escalate=4, escalate_on=on)) == expected


def test_what_a_step_counts_follows_the_badge():
    assert (weapons.lead(card(damage=1, escalate=2, escalate_per="held"))
            == "[escalate2] per card held")


def test_life_lost_counts_by_its_own_amount_so_per_is_ignored():
    row = card(damage=1, escalate=1, escalate_on="life_lost", escalate_per="link")
    assert weapons.lead(row) == "Held: [escalate1] per life lost"


def test_every_escalate_on_has_wording():
    """A new escalate_on needs its translation vendored by sync_assets."""
    strings = weapons._text()
    for on in weapons.ESCALATE_ON:
        assert "WEAPON_ESCALATES_WHEN_" + on.upper() in strings
    for per in weapons.ESCALATE_PER:
        assert "WEAPON_ESCALATES_PER_" + per.upper() in strings
