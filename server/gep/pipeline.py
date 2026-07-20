"""The generation pipeline: an ordered, config-composed stack of stages.

`floorgen.generate_floor` no longer hardcodes the sequence of generation
steps. Instead each archetype in config/floor_archetypes.json names its own
ordered `pipeline` array of stage ids, and this module maps those ids to the
functions that implement them (STAGE_REGISTRY). Composing a new floor type is
therefore a config edit -- reorder the stack, drop a stage, or insert one --
and adding a genuinely new *kind* of step is one function plus one registry
entry, with no change to the caller.

Every stage has the same shape: `fn(ctx: GenContext) -> None`, mutating the
context in place. That uniformity is what makes them composable; a stage that
needed a bespoke signature could not be ordered by data.

Two stages are NOT composable and deliberately so. `roads` (path carving) and
`connectivity` (reachability validation) are the guarantee that a floor is
playable at all, so they are appended by `run_pipeline` after the configured
stages regardless of what the config says, and are rejected if a config tries
to schedule them itself. A floor whose config forgot to carve paths would
generate happily and strand the player -- that failure is silent, ships, and
is exactly what config-driven composition must not be allowed to cause.

Stage ordering is otherwise the config author's responsibility: stages read
what earlier stages wrote (`macro_layout` needs `noise_fields` to have run if
the layout mode is "elevation"). Missing prerequisites raise from the stage
itself rather than producing a quietly wrong floor.
"""
from dataclasses import dataclass, field

from gep.biome_layout import apply_constraints, build_macro_layout, flatten_elevation
from gep.constraints import enforce_biome_adjacency, validate_connectivity
from gep.noise import build_field, normalise
from gep.prefabs import PlacedPrefab, place_prefabs
from gep.roads import build_roads

Tile = tuple[int, int]

# Salts that separate the structural noise fields from each other and from the
# floor's own seed. Config supplies octaves/scale per field (see
# `noise_fields`); these constants only guarantee the fields are decorrelated,
# and changing one silently reshapes every floor ever generated -- they are
# part of the seed contract, not a tuning knob, so they stay in code.
_ELEVATION_SALT = 0xE1E7
_ROUGHNESS_SALT = 0x9E55

# Path carving and reachability validation. Always run, always last, never
# schedulable from config -- see the module docstring.
TERMINAL_STAGES = ("roads", "connectivity")


@dataclass
class GenContext:
    """Everything a stage may read or write.

    Populated by `floorgen.generate_floor` before the first stage runs; the
    fields below the divider start empty and are filled in by stages.
    """
    floor_seed: int
    radius: int
    tiles: list[Tile]
    tile_set: set[Tile]
    up_exit: Tile
    down_exit: Tile | None
    params: dict                      # the archetype's config block
    prefab_defs: dict | None = None

    # --- written by stages ---
    elevation: dict[Tile, float] = field(default_factory=dict)
    roughness: dict[Tile, float] = field(default_factory=dict)
    regions: dict[Tile, str] = field(default_factory=dict)
    roads: set[Tile] = field(default_factory=set)
    prefabs: list[PlacedPrefab] = field(default_factory=list)


# --- Stages ---------------------------------------------------------------
# Each reads its parameters from ctx.params, never from module constants, so
# the same function drives every archetype that schedules it.

def stage_noise_fields(ctx: GenContext) -> None:
    """Sample the raw structural fields. Pure functions of seed+coordinates,
    so this can run before anything else -- and must, when the macro layout
    derives biomes from elevation strata.

    Octaves and scale are required config (validated at load), not defaulted
    here: a floor generated from a number nobody chose is indistinguishable
    from a floor generated from a number someone chose, which is exactly the
    kind of drift that only shows up as "the terrain feels different now".
    """
    elev_cfg = ctx.params["elevation"]
    rough_cfg = ctx.params["roughness"]
    ctx.elevation = normalise(build_field(
        ctx.floor_seed ^ _ELEVATION_SALT, ctx.tiles,
        octaves=elev_cfg["octaves"], scale=elev_cfg["scale"],
    ))
    ctx.roughness = normalise(build_field(
        ctx.floor_seed ^ _ROUGHNESS_SALT, ctx.tiles,
        octaves=rough_cfg["octaves"], scale=rough_cfg["scale"],
    ))


def stage_macro_layout(ctx: GenContext) -> None:
    """Partition the grid into large continuous biome structures."""
    ctx.regions = build_macro_layout(
        ctx.floor_seed, ctx.tiles, ctx.radius, ctx.params["layout"], ctx.elevation
    )


def stage_template_constraints(ctx: GenContext) -> None:
    """Enforce forbidden/radially-constrained biomes from the template."""
    ctx.regions = apply_constraints(ctx.regions, ctx.radius, ctx.params)


def stage_flatten_elevation(ctx: GenContext) -> None:
    """Clamp elevation inside protected zones (e.g. a town core)."""
    rules = ctx.params.get("elevation", {}).get("flatten", [])
    ctx.elevation = flatten_elevation(ctx.elevation, ctx.radius, rules)


def stage_biome_adjacency(ctx: GenContext) -> None:
    """Repair template-forbidden biome pairs sitting next to each other.

    Scheduled before roads/connectivity so a repaired tile cannot reintroduce
    a reachability problem after validation has already passed.
    """
    ctx.regions = enforce_biome_adjacency(
        ctx.regions,
        ctx.params.get("forbid_adjacent_biomes", []),
        ctx.params.get("fallback_biome"),
    )


def stage_prefabs(ctx: GenContext) -> None:
    """Stamp fixed-footprint structures onto the finished terrain.

    Ordering exception: this is the one configured stage that runs *after*
    the terminal pair (see run_pipeline). It has to, because the
    `forbid_road_adjacent` constraint needs the final road set -- and it
    safely can, because prefabs never add or remove tiles and so cannot
    invalidate the connectivity check that just passed.
    """
    if not ctx.prefab_defs:
        return
    ctx.prefabs = place_prefabs(
        ctx.floor_seed, ctx, ctx.prefab_defs, ctx.params.get("prefabs", [])
    )


def stage_roads(ctx: GenContext) -> None:
    ctx.roads = build_roads(ctx.tile_set, ctx.up_exit, ctx.down_exit)


def stage_connectivity(ctx: GenContext) -> None:
    exits = [ctx.up_exit] + ([ctx.down_exit] if ctx.down_exit else [])
    spawn_point = ctx.down_exit if ctx.down_exit is not None else (0, 0)
    validate_connectivity(ctx.tile_set, spawn_point, exits)


STAGE_REGISTRY = {
    "noise_fields": stage_noise_fields,
    "macro_layout": stage_macro_layout,
    "template_constraints": stage_template_constraints,
    "flatten_elevation": stage_flatten_elevation,
    "biome_adjacency": stage_biome_adjacency,
    "prefabs": stage_prefabs,
    "roads": stage_roads,
    "connectivity": stage_connectivity,
}


def run_pipeline(ctx: GenContext, stage_ids: list[str]) -> None:
    """Run the archetype's configured stages, then the terminal guarantees.

    `prefabs` is hoisted to run after the terminal pair (see stage_prefabs);
    everything else runs in the order config gives.
    """
    configured = [s for s in stage_ids if s != "prefabs"]
    for stage_id in configured:
        STAGE_REGISTRY[stage_id](ctx)

    for stage_id in TERMINAL_STAGES:
        STAGE_REGISTRY[stage_id](ctx)

    if "prefabs" in stage_ids:
        STAGE_REGISTRY["prefabs"](ctx)
