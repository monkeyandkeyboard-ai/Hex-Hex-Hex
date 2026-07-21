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


def power_from(entity, scaling: dict) -> float:
    """The ceiling of an attack's damage potential: a weighted sum of whichever
    stats a source draws on. `scaling` is {stat: coefficient} -- a weapon
    class's block from power_scaling.json, or an ability's own `power` block.
    One implementation so weapons and abilities scale off stats identically;
    `combat_stat` applies equipment modifiers for players and is a plain lookup
    for monsters, so this serves both.
    """
    return sum(entity.combat_stat(stat) * coeff for stat, coeff in scaling.items())


def resolve_attack(attacker, target, weapon_damage: float, damage_type: str,
                   constants: dict, apply_stat_scaling: bool = True) -> dict:
    """Resolve one hit. `weapon_damage` is the pre-mitigation damage.

    `apply_stat_scaling` says whether this function should still scale that
    number by the attacker's strength/arcana. A player swing and every ability
    already fold the driving stat in through `power_from`, so they pass False --
    scaling again here is the double-count that made damage rise with the stat
    squared. A monster's *basic* strike hands over a flat roll and passes True,
    so its strength/arcana still matter. The type routing below is unchanged;
    only whether it multiplies damage (vs merely picking a mitigation weight)
    depends on the flag.
    """
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
    # Armor folds into raw mitigation alongside constitution -- a real defence
    # now that build_stats banks an item's base armor (was previously inert).
    t_raw_mitigation = con_t + target.derived_stat("armor", 0.0)

    # Step 2: evasion check
    denom = t_evasion_rating + prec_a
    evasion_chance = t_evasion_rating / denom if denom > 0 else 0.0
    if random.random() < evasion_chance:
        return {"type": "combat_result", "result": "dodge", "attacker": attacker.id, "target": target.id}

    # Step 3: hit check
    if random.random() >= a_hit_chance:
        return {"type": "combat_result", "result": "miss", "attacker": attacker.id, "target": target.id}

    # Step 4: base damage (weapon_damage already rolled by caller). Which types
    # scale off Arcana rather than Strength is config, not a hardcoded name
    # check. The stat multiplier applies only when the caller has NOT already
    # folded the stat in (a monster basic strike); pre-scaled sources take the
    # roll as-is so the driving stat is counted exactly once.
    damage_type = normalize_damage_type(damage_type, constants)
    if apply_stat_scaling:
        arcana_scaled = constants.get("arcana_scaled_damage_types", [])
        a_damage_multiplier = 1 + str_a / constants["constant_divisor"]
        a_potency = 1 + (arcana_a ** 1.1) / constants["constant_divisor"]
        type_multiplier = a_potency if damage_type in arcana_scaled else a_damage_multiplier
        base_damage = weapon_damage * type_multiplier
    else:
        base_damage = weapon_damage

    # Step 4b: critical strike. Chance and bonus multiplier are non-skill stats
    # (0 unless gear grants them), read through the same stable accessor buffs
    # use, so crit is dormant until an item rolls it -- no balance shift today.
    crit_chance = attacker.derived_stat("critical_strike_chance", 0.0)
    crit = crit_chance > 0 and random.random() < crit_chance
    if crit:
        crit_mult = (constants.get("critical_strike_multiplier_base", 1.5)
                     + attacker.derived_stat("critical_strike_multiplier", 0.0))
        base_damage *= crit_mult

    # Step 5: mitigation. The type weight is the coefficient on effective
    # mitigation *before* the soft-cap divisor, so a 0.5-weight type meets
    # half the defence a physical hit does:
    #     pct = (con * weight) / (con * weight + soft_cap_factor)
    weighting = constants["damage_type_weighting"][damage_type]
    effective_mitigation = t_raw_mitigation * weighting
    mitigation_pct = effective_mitigation / (effective_mitigation + constants["defense_soft_cap_factor"])

    # Step 6: final damage. A per-type resistance (dormant at 0) reduces this
    # hit's damage type specifically, on top of the flat mitigation above -- so
    # fire_resistance blunts fire and nothing else. Then take_damage spends any
    # absorb shield before HP; it is the one entity method combat calls into.
    final_damage = base_damage * (1 - mitigation_pct)
    resist = target.derived_stat(f"{damage_type}_resistance", 0.0)
    if resist:
        final_damage *= max(0.0, 1 - resist / 100.0)

    absorbed = target.take_damage(final_damage)

    # Step 7: on-hit stat effects, all dormant at 0. Leech and life-on-hit heal
    # the attacker off the blow; thorns reflects a flat hit back. Thorns routes
    # through take_damage (not resolve_attack), so it cannot re-trigger itself.
    leech_pct = attacker.derived_stat("life_leech", 0.0)
    life_on_hit = attacker.derived_stat("life_on_hit", 0.0)
    healed = attacker.heal(final_damage * leech_pct / 100.0 + life_on_hit)
    thorns = target.derived_stat("thorns", 0.0)
    if thorns > 0:
        attacker.take_damage(thorns)

    return {
        "type": "combat_result",
        "result": "hit",
        "attacker": attacker.id,
        "target": target.id,
        "damage": final_damage,
        "absorbed": absorbed,
        "crit": crit,
        "leeched": healed,
        "thorns": thorns,
        "target_hp": target.hp,
        "target_alive": target.alive,
    }
