"""Reserved tile types: the small vocabulary of tiles the generator assigns
by identity rather than by biome.

Biomes describe what a tile is made of, and every tile has one. A reserved
tile type describes what a tile *is for*, and almost no tiles have one. The
two are separate overlays on purpose: the stairs on a fungal floor are still
standing on fungal ground, and making "stairs" a biome would mean inventing a
biome that has no terrain, no colour, and no spawn rules.

These ids are structural, not content. Unlike biomes and prefabs they are not
config-declared, because the generator has to be able to name the tile it just
placed -- a config file cannot rename `tile_stairs_up` without breaking the
exit-placement code that assigns it. Anything a config author should be able
to invent belongs in config/prefabs/ instead.
"""

STAIRS_UP = "tile_stairs_up"
STAIRS_DOWN = "tile_stairs_down"

# Every reserved id, for validation and for the client's renderer to switch on.
RESERVED_TILE_TYPES = (STAIRS_UP, STAIRS_DOWN)
