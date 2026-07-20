"""Deterministic value noise over hex coordinates.

Server-side only (generation is authoritative), so there is no cross-language
mirror to maintain. Uses integer hash mixing rather than the Mulberry32
stream: noise must be sampleable at an arbitrary (x, y) independently of
iteration order, which a sequential PRNG cannot provide.

Output of `fbm` is normalised to [0, 1].

--- Dual-paradigm standard for all generation modules (immutable) ---

Map generation deliberately uses two distinct, non-interchangeable sources of
determinism, chosen per access pattern rather than uniformly:

  1. Stateless hash sampling (this module): keyed by (seed, x, y), for any
     value that must be evaluated at an arbitrary coordinate independent of
     visitation order -- spatial noise fields, and anything sampled off of
     them at non-sequential points (e.g. biome_layout.py's boundary warp and
     radial-constraint checks). Forcing these onto a mutated PRNG stream
     would impose an artificial tile-visitation order on what is conceptually
     a pure function of position, and would make sampling at a new point
     depend on how many prior points happened to be sampled first.

  2. Mutated PRNG stream (gep/prng.py's Mulberry32): for sequential
     generation logic where "what comes next" legitimately depends on
     consumption order -- exit placement, Voronoi seed placement
     (biome_layout.py's _layout_cluster), A* tie-breaking, prefab/variant
     selection. These are inherently ordered decisions, so a stream is the
     correct (and simpler) tool.

Do not collapse these into a single paradigm. Both are 100% deterministic
given the same seed; the choice between them is about access pattern, not
about determinism strength. New generation modules should pick whichever of
the two matches how their values are accessed, not default to one.
"""
import math

MASK32 = 0xFFFFFFFF


def _hash2(seed: int, ix: int, iy: int) -> float:
    """Hash a lattice point to [0, 1). Cheap integer mixing, no allocation."""
    h = (seed ^ (ix * 374761393) ^ (iy * 668265263)) & MASK32
    h = (h ^ (h >> 13)) & MASK32
    h = (h * 1274126177) & MASK32
    h = (h ^ (h >> 16)) & MASK32
    return h / 4294967296.0


def _smoothstep(t: float) -> float:
    return t * t * (3.0 - 2.0 * t)


def value_noise(seed: int, x: float, y: float) -> float:
    """Bilinear-interpolated value noise in [0, 1)."""
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    sx, sy = _smoothstep(fx), _smoothstep(fy)

    v00 = _hash2(seed, ix, iy)
    v10 = _hash2(seed, ix + 1, iy)
    v01 = _hash2(seed, ix, iy + 1)
    v11 = _hash2(seed, ix + 1, iy + 1)

    top = v00 + (v10 - v00) * sx
    bottom = v01 + (v11 - v01) * sx
    return top + (bottom - top) * sy


def fbm(
    seed: int, x: float, y: float,
    octaves: int = 4, scale: float = 0.1,
    lacunarity: float = 2.0, gain: float = 0.5,
) -> float:
    """Fractal brownian motion, normalised to [0, 1]."""
    total = 0.0
    amplitude = 1.0
    frequency = scale
    norm = 0.0
    for _ in range(max(1, octaves)):
        total += value_noise(seed, x * frequency, y * frequency) * amplitude
        norm += amplitude
        amplitude *= gain
        frequency *= lacunarity
    return total / norm if norm else 0.0


def axial_to_cartesian(q: int, r: int) -> tuple[float, float]:
    """Flat-top axial hex -> 2D sampling coordinates, so noise is isotropic
    across the grid rather than skewed along the axial axes.
    """
    x = 1.5 * q
    y = math.sqrt(3.0) * (r + q / 2.0)
    return x, y


def build_field(
    seed: int, tiles: list[tuple[int, int]],
    octaves: int, scale: float,
) -> dict[tuple[int, int], float]:
    """Sample an fbm field over every tile. Returns tile -> value in [0, 1]."""
    field = {}
    for (q, r) in tiles:
        x, y = axial_to_cartesian(q, r)
        field[(q, r)] = fbm(seed, x, y, octaves=octaves, scale=scale)
    return field


def normalise(field: dict) -> dict:
    """Rescale a field to span the full [0, 1] range (fbm tends to cluster
    around the middle, which would wash out threshold-based biome rules).
    """
    if not field:
        return field
    lo = min(field.values())
    hi = max(field.values())
    span = hi - lo
    if span <= 1e-9:
        return {k: 0.5 for k in field}
    return {k: (v - lo) / span for k, v in field.items()}
