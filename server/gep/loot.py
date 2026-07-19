"""Loot selection (compendium §13.4: loot tables are weighted drop rolls
defined in config).

Deliberately pure: this module decides *what* drops and nothing else. It
touches no entity, no floor, no tick engine, and awards nothing. The caller
takes the result and does whatever awarding it wants. That keeps drop-rate
tuning a change to weights and this file only -- never a change that can
disturb combat resolution or monster behaviour.

Table format, straight from the monster config:

    "loot_table": [["nothing", 8], ["copper_ore", 1]]

`nothing` is a first-class entry rather than a magic empty-string: making a
no-drop outcome carry explicit weight is what lets a designer tune drop rate
by moving a single number, without a separate "drop chance" field that could
disagree with the table.
"""
import random

from gep.rolls import weighted_pick

# Reserved table entry meaning "this roll produced no item".
NOTHING = "nothing"


def roll_loot(template: dict, rng: random.Random | None = None) -> list[str]:
    """Roll a monster template's loot table and return the item ids dropped.

    One entry per successful roll; `nothing` outcomes are dropped from the
    result rather than represented. `loot_rolls` in the template controls how
    many independent attempts are made (default 1) -- the "attempts allowed
    against specific loot tables" knob.
    """
    table = template.get("loot_table") or []
    if not table:
        return []

    entries = [(entry[0], entry[1]) for entry in table]
    if sum(weight for _, weight in entries) <= 0:
        return []  # a table of all-zero weights drops nothing, rather than
                   # silently handing out the last entry

    roll = rng or random
    attempts = int(template.get("loot_rolls", 1))

    drops: list[str] = []
    for _ in range(max(0, attempts)):
        item_id = weighted_pick(roll.random(), entries)
        if item_id != NOTHING:
            drops.append(item_id)
    return drops
