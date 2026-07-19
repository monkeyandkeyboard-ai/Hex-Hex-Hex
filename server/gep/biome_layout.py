"""Step 1 of generation: macro biome layout from an archetype template.

The point of this module is cohesion. Assigning a biome per tile by
independent weighted roll produces noise-jumble; every mode here instead
assigns biomes to *large structures* (concentric bands, elevation strata, or
Voronoi cells) so the result reads as continuous terrain.

Modes:
  radial     - concentric bands out from the centre, boundaries warped by
               low-frequency noise so they aren't perfect circles.
               ("town core -> rocky outskirts")
  elevation  - biome chosen by elevation stratum, so low ground and ridges
               form naturally continuous shapes.
               ("fungal in the lows, rocky ridges above")
  cluster    - Voronoi cells, each cell one biome, with radial constraints on
               which biomes may anchor where.

Template constraints (forbidden biomes, radial limits) are enforced after
assignment by `apply_constraints`.
"""
from gep.hexgrid import hex_distance
from gep.noise import axial_to_cartesian, fbm
from gep.prng import Mulberry32, fnv1a32
from gep.rolls import weighted_choice

Tile = tuple[int, int]
CENTER: Tile = (0, 0)


def _layout_rng(floor_seed: int) -> Mulberry32:
    return Mulberry32(fnv1a32(f"{floor_seed}:layout".encode("utf-8")))


def _radial_fraction(tile: Tile, radius: int) -> float:
    if radius <= 0:
        return 0.0
    return hex_distance(CENTER, tile) / radius


def _warped_fraction(tile: Tile, radius: int, floor_seed: int, warp: dict | None) -> float:
    """Radial position with a noise-warped boundary, so band edges are organic."""
    frac = _radial_fraction(tile, radius)
    if not warp:
        return frac
    x, y = axial_to_cartesian(*tile)
    n = fbm(floor_seed ^ 0x5A17, x, y, octaves=2, scale=warp.get("scale", 0.05))
    return frac + (n - 0.5) * 2.0 * warp.get("amount", 0.0)


def _layout_radial(
    tiles: list[Tile], radius: int, floor_seed: int, cfg: dict
) -> dict[Tile, str]:
    bands = cfg["bands"]
    warp = cfg.get("warp")
    out: dict[Tile, str] = {}
    for tile in tiles:
        frac = _warped_fraction(tile, radius, floor_seed, warp)
        biome = bands[-1]["biome"]
        for band in bands:
            if frac <= band["to"]:
                biome = band["biome"]
                break
        out[tile] = biome
    return out


def _layout_elevation(
    tiles: list[Tile], radius: int, cfg: dict, elevation: dict[Tile, float]
) -> dict[Tile, str]:
    strata = cfg["strata"]
    extremity = cfg.get("extremity")
    out: dict[Tile, str] = {}
    for tile in tiles:
        e = elevation.get(tile, 0.5)
        biome = strata[-1]["biome"]
        for band in strata:
            if e <= band["below"]:
                biome = band["biome"]
                break

        # Optional pockets at the floor's extremities (e.g. hazard only far
        # out and high up).
        if extremity:
            frac = _radial_fraction(tile, radius)
            if (frac >= extremity.get("beyond_radius_pct", 0.8)
                    and e >= extremity.get("elevation_above", 0.6)):
                biome = extremity["biome"]
        out[tile] = biome
    return out


def _layout_cluster(
    tiles: list[Tile], radius: int, floor_seed: int, cfg: dict
) -> dict[Tile, str]:
    rng = _layout_rng(floor_seed)
    count = min(cfg.get("region_count", 6), len(tiles))
    weights = cfg["biome_weights"]
    constraints = cfg.get("radial_constraints", {})

    seeds: list[Tile] = []
    chosen: set[Tile] = set()
    attempts = 0
    while len(seeds) < count and attempts < count * 20:
        idx = min(len(tiles) - 1, int(rng.next_float() * len(tiles)))
        t = tiles[idx]
        if t not in chosen:
            chosen.add(t)
            seeds.append(t)
        attempts += 1

    # Assign each seed a biome that is legal at that seed's radial position,
    # so e.g. hazard cells only ever anchor in the outer ring.
    seed_biomes: list[str] = []
    for seed in seeds:
        frac = _radial_fraction(seed, radius)
        legal = [
            (bid, w) for bid, w in weights
            if _radius_ok(bid, frac, constraints)
        ] or weights
        seed_biomes.append(weighted_choice(rng, legal))

    out: dict[Tile, str] = {}
    for tile in tiles:
        best_i, best_d = 0, None
        for i, seed in enumerate(seeds):
            d = hex_distance(tile, seed)
            if best_d is None or d < best_d:
                best_d, best_i = d, i
        out[tile] = seed_biomes[best_i]
    return out


def _radius_ok(biome_id: str, frac: float, constraints: dict) -> bool:
    c = constraints.get(biome_id)
    if not c:
        return True
    if frac < c.get("min_radius_pct", 0.0):
        return False
    if frac > c.get("max_radius_pct", 1.0):
        return False
    return True


def build_macro_layout(
    floor_seed: int,
    tiles: list[Tile],
    radius: int,
    layout_cfg: dict,
    elevation: dict[Tile, float],
) -> dict[Tile, str]:
    mode = layout_cfg.get("mode", "cluster")
    if mode == "radial":
        return _layout_radial(tiles, radius, floor_seed, layout_cfg)
    if mode == "elevation":
        return _layout_elevation(tiles, radius, layout_cfg, elevation)
    if mode == "cluster":
        return _layout_cluster(tiles, radius, floor_seed, layout_cfg)
    raise ValueError(f"unknown layout mode {mode!r}")


def apply_constraints(
    regions: dict[Tile, str],
    radius: int,
    template: dict,
) -> dict[Tile, str]:
    """Step 3a: enforce template rules the layout pass may have violated --
    globally forbidden biomes and per-biome radial limits. Violations fall
    back to the template's fallback biome.
    """
    forbidden = set(template.get("forbid_biomes", []))
    constraints = template.get("layout", {}).get("radial_constraints", {})
    fallback = template.get("fallback_biome")
    if not forbidden and not constraints:
        return regions
    if fallback is None:
        return regions

    for tile, biome in regions.items():
        if biome in forbidden:
            regions[tile] = fallback
            continue
        if constraints:
            frac = _radial_fraction(tile, radius)
            if not _radius_ok(biome, frac, constraints):
                regions[tile] = fallback
    return regions


def flatten_elevation(
    elevation: dict[Tile, float],
    radius: int,
    rules: list[dict],
) -> dict[Tile, float]:
    """Step 3b: clamp elevation inside designated zones, so a town core stays
    buildable flat ground instead of growing a mountain through the village.

    Each rule: {"to": radial_pct, "target": value, "strength": 0..1}
    """
    if not rules:
        return elevation
    for tile, e in elevation.items():
        frac = _radial_fraction(tile, radius)
        for rule in rules:
            if frac <= rule["to"]:
                target = rule.get("target", 0.5)
                strength = rule.get("strength", 1.0)
                elevation[tile] = e + (target - e) * strength
                break
    return elevation
