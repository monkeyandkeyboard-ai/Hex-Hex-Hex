"""The 6-step atomic combat resolution flow, compendium §13.2, reproduced
formula-for-formula. All tunable constants come from combat_scaling_constants
.json -- nothing here is a magic number. Combat RNG (the two rolls below) is
purely server-side and never needs to match the client, unlike floor
generation -- the client never predicts combat outcomes (§11), so stdlib
`random` is fine here.

[INFERRED] Canon only defines the step-4 damage multiplier for two cases:
strength-scaled (melee/ranged) and arcana-scaled (A_Potency). "elemental" has
a mitigation *weighting* (step 5) but no stated step-4 multiplier of its own.
Until a weapon actually uses damage_type "elemental", this treats it like
physical (strength-scaled) -- flag to the owner before adding an elemental
weapon if that's wrong.
"""
import random


def resolve_attack(attacker, target, weapon_base_damage: float, damage_type: str, constants: dict) -> dict:
    dex_t = target.combat_stat("dexterity")
    prec_a = attacker.combat_stat("precision")
    str_a = attacker.combat_stat("strength")
    arcana_a = attacker.combat_stat("arcana")
    con_t = target.combat_stat("constitution")

    # Step 1: skill outputs
    t_evasion_rating = dex_t * constants["evasion_multiplier"]
    a_hit_chance_base = min(
        constants["hit_chance_cap"],
        constants["hit_chance_floor"] + prec_a / (prec_a + constants["precision_constant_c"]),
    )
    a_damage_multiplier = 1 + str_a / constants["strength_damage_divisor"]
    a_potency = 1 + (arcana_a ** constants["arcana_potency_exponent"]) / constants["arcana_potency_divisor"]
    t_raw_mitigation = con_t * constants["mitigation_multiplier"]

    # Step 2: evasion check
    evasion_chance = t_evasion_rating / (t_evasion_rating + prec_a) if (t_evasion_rating + prec_a) > 0 else 0
    if random.random() < evasion_chance:
        return {"type": "combat_result", "result": "dodge", "attacker": attacker.id, "target": target.id}

    # Step 3: hit check
    if random.random() >= a_hit_chance_base:
        return {"type": "combat_result", "result": "miss", "attacker": attacker.id, "target": target.id}

    # Step 4: base damage
    type_multiplier = a_potency if damage_type == "arcana" else a_damage_multiplier
    base_damage = weapon_base_damage * type_multiplier

    # Step 5: mitigation
    weighting = constants["damage_type_weighting"].get(damage_type, 1.0)
    effective_mitigation = t_raw_mitigation * weighting
    mitigation_pct = effective_mitigation / (effective_mitigation + constants["defense_soft_cap_factor"])

    # Step 6: final damage
    final_damage = base_damage * (1 - mitigation_pct)

    target.hp = max(0.0, target.hp - final_damage)
    if target.hp <= 0:
        target.alive = False

    return {
        "type": "combat_result",
        "result": "hit",
        "attacker": attacker.id,
        "target": target.id,
        "damage": final_damage,
        "target_hp": target.hp,
        "target_alive": target.alive,
    }
