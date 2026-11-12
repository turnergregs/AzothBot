"""A weapon's numbers. A port of GameContentData.get_effective_attr's
fall-through to the Weapon property (azoth docs/WEAPONS.md).
"""
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
