"""Axial hex-coordinate utilities, shared logic with client/src/hexgrid.js.

Coordinate system: axial (q, r); cube form is (x=q, z=r, y=-x-z). A floor is
a bounded disc of radius `radius` centered on (0, 0): every tile with
max(|x|, |y|, |z|) <= radius (compendium §4.1, tile count = 3*r^2 + 3*r + 1).
"""


# Facing names shared with the client's spritesheet column order. Derived from
# the client's hexToPixel: +q moves right-and-down the screen, +r moves
# straight down. Keep in sync with FACINGS in client/src/renderer.js.
FACING_BY_DELTA = {
    (0, 1): "down",
    (1, 0): "right-down",
    (1, -1): "right-up",
    (0, -1): "up",
    (-1, 0): "left-up",
    (-1, 1): "left-down",
}


def facing_from_delta(origin: tuple[int, int], target: tuple[int, int]) -> str | None:
    """Facing for a step between adjacent tiles, or None if not neighbours."""
    delta = (target[0] - origin[0], target[1] - origin[1])
    return FACING_BY_DELTA.get(delta)


def tile_count(radius: int) -> int:
    return 3 * radius * radius + 3 * radius + 1


def tiles_in_radius(radius: int) -> list[tuple[int, int]]:
    """All (q, r) in the disc, in canonical order: q ascending, then r
    ascending. Both server and client MUST iterate in this exact order for
    any per-tile-sequential generation step to match.
    """
    tiles = []
    for q in range(-radius, radius + 1):
        r_lo = max(-radius, -q - radius)
        r_hi = min(radius, -q + radius)
        for r in range(r_lo, r_hi + 1):
            tiles.append((q, r))
    return tiles


def hex_distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    aq, ar = a
    bq, br = b
    ax, az = aq, ar
    ay = -ax - az
    bx, bz = bq, br
    by = -bx - bz
    return max(abs(ax - bx), abs(ay - by), abs(az - bz))


def ring_tiles(radius: int, ring_radius: int) -> list[tuple[int, int]]:
    """Tiles at exactly `ring_radius` from center, in canonical order.
    `ring_radius` must be <= radius.
    """
    return [t for t in tiles_in_radius(radius) if hex_distance((0, 0), t) == ring_radius]
