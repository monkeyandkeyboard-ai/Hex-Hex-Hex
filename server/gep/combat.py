"""The 6-step atomic combat resolution flow, compendium §13.2.
All tunable constants come from combat_scaling_constants.json.

Field names match the real config from the prior codebase:
  constant_c, constant_divisor, defense_soft_cap_factor, evasion_multiplier,
  hit_chance_base, hit_chance_cap, damage_type_weighting.

Callers pass the attacker's baseline damage as `weapon_damage`. For players
that is the equipment state's flat `base_power`; for monsters it is still a
roll across the template's damage_min..damage_max. This function does not
care which -- it applies stat multipliers and mitigation to whatever number
it is handed.
"""
import random


def normalize_damage_type(damage_type: str | None, constants: dict) -> str:
    """Fold an arbitrary config-supplied type name onto a known one.

    The set of valid types is whatever `damage_type_weighting` declares, so
    adding a type is a config edit alone. Anything unrecognised (a typo, or a
    weapon whose `type` field is a flavour word like "Unarmed") falls back to
    `default_damage_type` rather than silently picking up a 1.0 weighting from
    a dict miss -- an unknown type should be visibly the default, not
    accidentally the least-mitigated one.
    """
    weighting = constants["damage_type_weighting"]
    default = constants.get("default_damage_type", "physical")
    if damage_type is None:
        return default
    key = str(damage_type).lower()
    return key if key in weighting else default


def resolve_attack(attacker, target, weapon_damage: float, damage_type: str, constants: dict) -> dict:
    dex_t = target.combat_stat("dexterity")
    prec_a = attacker.combat_stat("precision")
    str_a = attacker.combat_stat("strength")
    arcana_a = attacker.combat_stat("arcana")
    con_t = target.combat_stat("constitution")

    # Step 1: skill outputs
    t_evasion_rating = dex_t * constants["evasion_multiplier"]
    a_hit_chance = min(
        constants["hit_chance_cap"],
        constants["hit_chance_base"] + prec_a / (prec_a + constants["constant_c"]),
    )
    a_damage_multiplier = 1 + str_a / constants["constant_divisor"]
    a_potency = 1 + (arcana_a ** 1.1) / constants["constant_divisor"]
    t_raw_mitigation = con_t

    # Step 2: evasion check
    denom = t_evasion_rating + prec_a
    evasion_chance = t_evasion_rating / denom if denom > 0 else 0.0
    if random.random() < evasion_chance:
        return {"type": "combat_result", "result": "dodge", "attacker": attacker.id, "target": target.id}

    # Step 3: hit check
    if random.random() >= a_hit_chance:
        return {"type": "combat_result", "result": "miss", "attacker": attacker.id, "target": target.id}

    # Step 4: base damage (weapon_damage already rolled by caller).
    # Which types scale off Arcana rather than Strength is config, not a
    # hardcoded name check -- a new elemental type joins the magic scaling by
    # being listed, without touching this line.
    damage_type = normalize_damage_type(damage_type, constants)
    arcana_scaled = constants.get("arcana_scaled_damage_types", [])
    type_multiplier = a_potency if damage_type in arcana_scaled else a_damage_multiplier
    base_damage = weapon_damage * type_multiplier

    # Step 5: mitigation. The type weight is the coefficient on effective
    # mitigation *before* the soft-cap divisor, so a 0.5-weight type meets
    # half the defence a physical hit does:
    #     pct = (con * weight) / (con * weight + soft_cap_factor)
    weighting = constants["damage_type_weighting"][damage_type]
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
