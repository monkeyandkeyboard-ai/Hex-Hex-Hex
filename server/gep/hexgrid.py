"""Axial hex-coordinate utilities, shared logic with client/src/hexgrid.js.

Coordinate system: axial (q, r); cube form is (x=q, z=r, y=-x-z). A floor is
a bounded disc of radius `radius` centered on (0, 0): every tile with
max(|x|, |y|, |z|) <= radius (compendium §4.1, tile count = 3*r^2 + 3*r + 1).
"""


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
